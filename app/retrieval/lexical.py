"""Lexical retrieval: PostgreSQL full-text search plus trigram similarity.

Two signals, because they fail differently:

  * `to_tsvector('swedish', ...)` handles term overlap with Swedish stemming
    and stop words, and is what matches "astma ospecificerad" to
    "Astma, ospecificerad". It cannot match a misspelling.
  * `pg_trgm` word similarity compares the query's character trigrams against
    the best-matching extent of the concept text, so "hjartinfarkt" still
    finds "hjärtinfarkt". It has no notion of words.

Word similarity rather than plain `similarity()`: plain similarity normalises
over the whole target string, so a concept whose search text carries a Latin
term and three inclusion terms scores lower than a bare one for the same
query. That penalises exactly the richly-described concepts that ought to
match best. `word_similarity` compares against the best matching extent
instead, which is the right question for a short query against a long
concept document.

A concept enters the candidate set if *either* signal fires, and the score
reported is the stronger of the two, on a comparable [0, 1) scale. Both
components are also returned separately so nothing about the decision is
hidden from the audit record.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.models.candidate import Candidate

# ts_rank_cd normalization flag 32 divides the rank by itself plus one, mapping
# an unbounded rank onto [0, 1) so it is comparable with trigram similarity.
_NORMALIZATION = 32

_SQL = sa.text(
    """
    WITH q AS (
        SELECT websearch_to_tsquery('swedish', :query) AS tsq
    )
    SELECT
        c.code,
        c.preferred_term,
        c.synonyms,
        c.chapter,
        c.is_leaf,
        ts_rank_cd(to_tsvector('swedish', c.search_text), q.tsq, :norm) AS ts_rank,
        word_similarity(:query, c.search_text) AS trgm_similarity
    FROM concepts c, q
    WHERE c.system = :system
      AND c.version = :version
      -- A code interval ("I10-I15") names a group, not an assignable code.
      AND c.code NOT LIKE '%-%'
      AND (
            to_tsvector('swedish', c.search_text) @@ q.tsq
            -- `<%` honours pg_trgm.word_similarity_threshold, set below, and
            -- can use the GIN trigram index; a bare function call could not.
            OR :query <% c.search_text
      )
    ORDER BY GREATEST(
        ts_rank_cd(to_tsvector('swedish', c.search_text), q.tsq, :norm),
        word_similarity(:query, c.search_text)
    ) DESC, c.code ASC
    LIMIT :limit
    """
)


def lexical_search(
    session: Session,
    *,
    query: str,
    system: str,
    version: str,
    top_k: int,
    trigram_threshold: float,
) -> list[Candidate]:
    """Return up to `top_k` candidates, best first.

    `query` should already be normalized (see `app.normalize.swedish`).
    """
    if not query.strip():
        return []

    # Transaction-local: `true` scopes the setting to the surrounding
    # transaction, so a concurrent request cannot observe another's threshold.
    session.execute(
        sa.text("SELECT set_config('pg_trgm.word_similarity_threshold', :value, true)"),
        {"value": str(trigram_threshold)},
    )

    rows = session.execute(
        _SQL,
        {
            "query": query,
            "system": system,
            "version": version,
            "norm": _NORMALIZATION,
            "limit": top_k,
        },
    ).all()

    candidates: list[Candidate] = []
    for rank, row in enumerate(rows, start=1):
        ts_rank = float(row.ts_rank or 0.0)
        trgm = float(row.trgm_similarity or 0.0)
        candidates.append(
            Candidate(
                system=system,
                version=version,
                code=row.code,
                preferred_term=row.preferred_term,
                synonyms=list(row.synonyms or []),
                chapter=row.chapter,
                is_leaf=row.is_leaf,
                sources=["lexical"],
                lexical_score=max(ts_rank, trgm),
                lexical_rank=rank,
                ts_rank=ts_rank,
                trgm_similarity=trgm,
            )
        )
    return candidates

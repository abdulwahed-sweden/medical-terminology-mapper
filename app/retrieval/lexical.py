"""Lexical retrieval: PostgreSQL full-text search plus trigram similarity.

Two signals, because they fail differently:

  * `to_tsvector('swedish', ...)` handles term overlap with Swedish stemming and
    stop words. It cannot match a misspelling.
  * `pg_trgm` compares character trigrams, so "hjartinfarkt" still finds
    "hjärtinfarkt". It has no notion of words or of meaning.

A concept enters the candidate set if *either* signal fires, and the score
reported is the stronger of the two on a comparable [0, 1) scale. Both
components are returned separately, because the retrieval gate reads them
separately: a full-text match and a fuzzy one are different strengths of
evidence, and collapsing them would throw away the distinction the gate needs.

WHY `strict_word_similarity` RATHER THAN `word_similarity`
----------------------------------------------------------
`word_similarity` finds the best-matching *extent* of the target, and that
extent may start and end mid-word. Measured against the real KVÅ 2026 release,
that let the query "banan" score 0.833 against "An**nan ban**dningsoperation" --
higher than the legitimate misspelling "hjartinfarkt" scores against its own
concept (0.625). No threshold can separate those two, so no gate built on
`word_similarity` could work.

`strict_word_similarity` requires the extent to sit on word boundaries. The same
"banan" case drops to 0.571 while every correctly-spelled query and every
measured misspelling is unchanged or barely moved. See ARCHITECTURE.md for the
measurement.

FIELD WEIGHTS
-------------
The generated `search_vector` column weights the preferred term `A`, synonyms
`B`, and the publisher's description `D`. Trigram matching runs only against
names (`search_text`), never the description: it exists to survive a misspelled
name, and running it over prose yields noise rather than tolerance.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.models.candidate import Candidate, MatchedField

# ts_rank_cd normalization flag 32 divides the rank by itself plus one, mapping
# an unbounded rank onto [0, 1) so it is comparable with trigram similarity.
_NORMALIZATION = 32

# ts_rank weight arrays are ordered {D, C, B, A}.
_WEIGHTS_WITH_DESCRIPTION = "{0.1, 0.2, 0.4, 1.0}"
_WEIGHTS_NAMES_ONLY = "{0.0, 0.0, 0.4, 1.0}"

_SQL = """
    WITH q AS (
        SELECT websearch_to_tsquery('swedish', :query) AS tsq
    )
    SELECT
        c.code,
        c.preferred_term,
        c.synonyms,
        c.chapter,
        c.is_leaf,
        ts_rank_cd('{weights}'::float4[], c.search_vector, q.tsq, :norm)       AS ts_rank,
        ts_rank_cd('{{0,0,0,1}}'::float4[], c.search_vector, q.tsq, :norm)     AS ts_title,
        ts_rank_cd('{{0,0,1,0}}'::float4[], c.search_vector, q.tsq, :norm)     AS ts_synonym,
        ts_rank_cd('{{1,0,0,0}}'::float4[], c.search_vector, q.tsq, :norm)     AS ts_description,
        strict_word_similarity(:query, c.search_text)                          AS trgm,
        strict_word_similarity(:query, c.preferred_term)                       AS trgm_title,
        strict_word_similarity(:query, c.synonym_text)                         AS trgm_synonym
    FROM concepts c, q
    WHERE c.system = :system
      AND c.version = :version
      -- A code interval ("I10-I15") names a group, not an assignable code.
      AND c.code NOT LIKE '%-%'
      AND (
            ({match_condition})
            -- `<<%` honours pg_trgm.strict_word_similarity_threshold, set below.
            OR :query <<% c.search_text
      )
    ORDER BY GREATEST(
        ts_rank_cd('{weights}'::float4[], c.search_vector, q.tsq, :norm),
        strict_word_similarity(:query, c.search_text)
    ) DESC, c.code ASC
    LIMIT :limit
"""

# With descriptions indexed, any full-text hit counts. Without, a hit that comes
# only from the D-weighted description ranks 0 under the names-only weights and
# is therefore excluded.
_MATCH_ANY = "c.search_vector @@ q.tsq"
_MATCH_NAMES_ONLY = (
    "c.search_vector @@ q.tsq "
    "AND ts_rank_cd('" + _WEIGHTS_NAMES_ONLY + "'::float4[], c.search_vector, q.tsq, :norm) > 0"
)


def _statement(index_descriptions: bool) -> sa.TextClause:
    weights = _WEIGHTS_WITH_DESCRIPTION if index_descriptions else _WEIGHTS_NAMES_ONLY
    condition = _MATCH_ANY if index_descriptions else _MATCH_NAMES_ONLY
    return sa.text(_SQL.format(weights=weights, match_condition=condition))


def _matched_field(row: sa.Row[Any], index_descriptions: bool) -> MatchedField:
    """Attribute the hit to the part of the concept that produced it."""
    if float(row.ts_title or 0.0) > 0.0:
        return "title"
    if float(row.ts_synonym or 0.0) > 0.0:
        return "synonym"
    if index_descriptions and float(row.ts_description or 0.0) > 0.0:
        return "description"
    # No full-text match: this row came in on trigram similarity, which runs
    # only over names, so attribute it to whichever name matched better.
    if float(row.trgm_synonym or 0.0) > float(row.trgm_title or 0.0):
        return "synonym"
    return "title"


def lexical_search(
    session: Session,
    *,
    query: str,
    system: str,
    version: str,
    top_k: int,
    trigram_threshold: float,
    index_descriptions: bool = True,
) -> list[Candidate]:
    """Return up to `top_k` candidates, best first.

    `query` should already be normalized (see `app.normalize.swedish`).
    """
    if not query.strip():
        return []

    # Transaction-local: `true` scopes the setting to the surrounding
    # transaction, so a concurrent request cannot observe another's threshold.
    session.execute(
        sa.text("SELECT set_config('pg_trgm.strict_word_similarity_threshold', :value, true)"),
        {"value": str(trigram_threshold)},
    )

    rows = session.execute(
        _statement(index_descriptions),
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
        trgm = float(row.trgm or 0.0)
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
                strict_similarity=trgm,
                matched_field=_matched_field(row, index_descriptions),
            )
        )
    return candidates

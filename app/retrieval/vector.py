"""Vector retrieval over pgvector.

Concept embeddings are computed once per `(system, version, provider, model)`
by `scripts/embed_terminology.py`; the query is embedded at request time with
the same provider, and neighbours are found by cosine distance.

The `(provider, model)` filter is not optional. Vectors from two different
models occupy unrelated spaces, and comparing across them produces confident
nonsense -- so a search always names the space it is searching, and that name
is recorded on the proposal.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.models.candidate import Candidate

_SQL = sa.text(
    """
    SELECT
        c.code,
        c.preferred_term,
        c.synonyms,
        c.chapter,
        c.is_leaf,
        -- pgvector's <=> is cosine *distance* in [0, 2]. Report similarity so
        -- larger is better, matching every other score in the pipeline.
        1 - (e.embedding <=> CAST(:query_vector AS vector)) AS cosine_similarity
    FROM concept_embeddings e
    JOIN concepts c
      ON c.system = e.system AND c.version = e.version AND c.code = e.code
    WHERE e.system = :system
      AND e.version = :version
      AND e.provider = :provider
      AND e.model = :model
      AND c.code NOT LIKE '%-%'
    ORDER BY e.embedding <=> CAST(:query_vector AS vector)
    LIMIT :limit
    """
)


def vector_search(
    session: Session,
    *,
    query_vector: list[float],
    system: str,
    version: str,
    provider: str,
    model: str,
    top_k: int,
) -> list[Candidate]:
    """Return up to `top_k` nearest concepts, closest first."""
    if not query_vector:
        return []

    literal = "[" + ",".join(repr(float(value)) for value in query_vector) + "]"
    rows = session.execute(
        _SQL,
        {
            "query_vector": literal,
            "system": system,
            "version": version,
            "provider": provider,
            "model": model,
            "limit": top_k,
        },
    ).all()

    return [
        Candidate(
            system=system,
            version=version,
            code=row.code,
            preferred_term=row.preferred_term,
            synonyms=list(row.synonyms or []),
            chapter=row.chapter,
            is_leaf=row.is_leaf,
            sources=["vector"],
            vector_score=float(row.cosine_similarity),
            vector_rank=rank,
            matched_field="vector",
        )
        for rank, row in enumerate(rows, start=1)
    ]

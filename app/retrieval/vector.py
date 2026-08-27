"""Vector retrieval over pgvector.

Concept embeddings are computed once per `(system, version, provider, model)`
by `scripts/embed_terminology.py`; the query is embedded at request time with
the same provider, and neighbours are found by cosine distance.

The `(provider, model)` filter is not optional. Vectors from two different
models occupy unrelated spaces, and comparing across them produces confident
nonsense -- so a search always names the space it is searching, and that name
is recorded on the proposal.

That filter is also why this query resolves the embedding space *before*
ordering by distance, instead of letting the HNSW index answer the ORDER BY.
The index covers `embedding` alone, so Postgres can only apply the space filter
to whatever the index hands back. pgvector walks the graph, returns at most
`hnsw.ef_search` (40 by default) entries, and stops; if those entries belong to
another space -- or are dead tuples that VACUUM has not reclaimed yet -- the
filter removes all of them and the search returns nothing while a sequential
scan over the same rows returns every match. Which plan Postgres picks depends
on table statistics, so the same query returned different answers from one run
to the next. Resolving the space first makes the result exact and identical
under every plan, which is the property this tool actually needs.

The cost is real and worth stating plainly: vector search is now exact rather
than approximate. Measured on 12k concepts in one space -- about the size of
ICD-10-SE or KVA -- the median is 71 ms, against 2.2 ms when the HNSW index
answers the ordering. That is the price of an answer that does not depend on
which plan the planner chose, in a tool whose whole claim is that a human can
audit what it proposed and why; the LLM rerank in the same request costs
seconds. It stops being a reasonable trade somewhere in the low hundreds of
thousands of concepts, where a sequential scan over the space gets expensive.
ARCHITECTURE.md records the decision and what to do at that size.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.models.candidate import Candidate

_SQL = sa.text(
    """
    -- MATERIALIZED is load-bearing. Without it Postgres may answer the
    -- ORDER BY from the HNSW index and apply the space filter afterwards,
    -- which silently drops matching rows -- see the module docstring.
    WITH space AS MATERIALIZED (
        SELECT e.code, e.embedding
        FROM concept_embeddings e
        WHERE e.system = :system
          AND e.version = :version
          AND e.provider = :provider
          AND e.model = :model
    )
    SELECT
        c.code,
        c.preferred_term,
        c.synonyms,
        c.chapter,
        c.is_leaf,
        c.not_primary_diagnosis,
        -- pgvector's <=> is cosine *distance* in [0, 2]. Report similarity so
        -- larger is better, matching every other score in the pipeline.
        1 - (s.embedding <=> CAST(:query_vector AS vector)) AS cosine_similarity
    FROM space s
    JOIN concepts c
      ON c.system = :system AND c.version = :version AND c.code = s.code
    WHERE c.assignable
      AND NOT c.placeholder
    ORDER BY s.embedding <=> CAST(:query_vector AS vector)
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
            not_primary_diagnosis=row.not_primary_diagnosis,
            sources=["vector"],
            vector_score=float(row.cosine_similarity),
            vector_rank=rank,
            matched_field="vector",
        )
        for rank, row in enumerate(rows, start=1)
    ]

"""Vector search must return the same rows whichever plan Postgres chooses.

The HNSW index covers `embedding` alone, while every search also filters by
`(system, version, provider, model)`. If Postgres answers the ORDER BY from the
index, that filter is applied to whatever the index hands back -- at most
`hnsw.ef_search` entries -- so rows belonging to another embedding space, or
dead tuples awaiting VACUUM, can consume the whole budget and the search returns
nothing while the rows it should have found sit in the table. That is what made
three tests fail intermittently for weeks.

`app.retrieval.vector` resolves the embedding space in a MATERIALIZED CTE before
ordering, which takes the index out of the ordering decision entirely. These
tests hold that in place: the plan assertion is the one that fails immediately
if the CTE is dropped, and the others check the behaviour it buys.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import ConceptEmbeddingRow, ConceptRow
from app.embeddings.fake import FakeEmbeddingProvider
from app.retrieval.vector import vector_search

pytestmark = pytest.mark.requires_db

SETTINGS = get_settings()

# Comfortably more than hnsw.ef_search (40): the decoys must be able to consume
# the whole candidate budget on their own.
DECOY_ROWS = 120


def _force_the_index_plan(session: Session) -> None:
    session.execute(sa.text("SET LOCAL enable_seqscan = off"))
    session.execute(sa.text("SET LOCAL enable_sort = off"))
    session.execute(sa.text("SET LOCAL enable_bitmapscan = off"))


def _assignable_kva_codes(session: Session, version: str) -> list[str]:
    return list(
        session.scalars(
            sa.select(ConceptRow.code)
            .where(
                ConceptRow.system == "kva",
                ConceptRow.version == version,
                ConceptRow.assignable,
                ConceptRow.placeholder.is_(False),
            )
            .order_by(ConceptRow.code)
        )
    )


def _crowd_the_graph(session: Session, version: str, query_vector: list[float]) -> None:
    """Fill the index with nearer vectors from a *different* embedding space.

    Each decoy sits exactly on the query vector, so every one of them is closer
    than any real row. They differ from the searched space only by `model`,
    which is precisely the filter the index cannot apply itself.
    """
    code = _assignable_kva_codes(session, version)[0]
    session.add_all(
        [
            ConceptEmbeddingRow(
                system="kva",
                version=version,
                code=code,
                provider="fake",
                model=f"decoy-{n:04d}",
                dim=len(query_vector),
                embedding=query_vector,
            )
            for n in range(DECOY_ROWS)
        ]
    )
    session.flush()


def test_vector_search_returns_every_match_when_the_index_plan_is_used(
    db_session: Session, kva_embedded: str
) -> None:
    provider = FakeEmbeddingProvider(dim=SETTINGS.embedding_dim)
    query_vector = provider.embed(["incisioner i tonsiller"])[0]

    _crowd_the_graph(db_session, kva_embedded, query_vector)
    _force_the_index_plan(db_session)

    expected = len(_assignable_kva_codes(db_session, kva_embedded))
    top_k = 20

    results = vector_search(
        db_session,
        query_vector=query_vector,
        system="kva",
        version=kva_embedded,
        provider=provider.provider_id,
        model=provider.model_id,
        top_k=top_k,
    )

    assert len(results) == min(top_k, expected)
    assert all(candidate.code != "EMA" for candidate in results)


def test_the_index_plan_agrees_with_a_sequential_scan(
    db_session: Session, kva_embedded: str
) -> None:
    """The same rows come back whichever plan Postgres chooses.

    The planner's choice varies with table statistics, so a result that depends
    on it is a result that changes under load. Membership, not order, is what is
    compared: concepts at an identical distance tie, and the query deliberately
    has no tiebreaker -- adding one to `ORDER BY` would force a sort over the
    whole space at full size for no gain in correctness.
    """
    provider = FakeEmbeddingProvider(dim=SETTINGS.embedding_dim)
    query_vector = provider.embed(["tonsillektomi"])[0]
    _crowd_the_graph(db_session, kva_embedded, query_vector)

    def search() -> list[str]:
        return [
            candidate.code
            for candidate in vector_search(
                db_session,
                query_vector=query_vector,
                system="kva",
                version=kva_embedded,
                provider=provider.provider_id,
                model=provider.model_id,
                # Above the number of matching rows on purpose: with a cut
                # partway through the results, concepts tied at the same
                # distance could land on either side of it and the comparison
                # would be testing tie order rather than agreement.
                top_k=20,
            )
        ]

    db_session.execute(sa.text("SET LOCAL enable_indexscan = off"))
    sequential = search()

    db_session.execute(sa.text("SET LOCAL enable_indexscan = on"))
    _force_the_index_plan(db_session)
    indexed = search()

    assert sequential
    assert sorted(indexed) == sorted(sequential)


def test_the_embedding_space_is_resolved_before_ordering(
    db_session: Session, kva_embedded: str
) -> None:
    """The HNSW index must not be what satisfies the ORDER BY.

    This is the assertion that fails the moment someone drops MATERIALIZED from
    the query, and it fails deterministically -- unlike the bug itself, which
    only appeared when the planner happened to prefer the index.
    """
    from app.retrieval.vector import _SQL

    provider = FakeEmbeddingProvider(dim=SETTINGS.embedding_dim)
    query_vector = provider.embed(["incisioner i tonsiller"])[0]
    _crowd_the_graph(db_session, kva_embedded, query_vector)
    _force_the_index_plan(db_session)

    plan = "\n".join(
        row[0]
        for row in db_session.execute(
            sa.text("EXPLAIN " + str(_SQL)),
            {
                "query_vector": "[" + ",".join(repr(float(v)) for v in query_vector) + "]",
                "system": "kva",
                "version": kva_embedded,
                "provider": provider.provider_id,
                "model": provider.model_id,
                "limit": 20,
            },
        ).all()
    )

    assert "ix_concept_embeddings_hnsw" not in plan, plan

"""Benchmark arms: what each one runs, and what it records.

Phase 3 compares three pipelines. `lexical` is lexical retrieval only, `hybrid`
adds vector retrieval and the RRF merge, `full` adds the LLM rerank. Comparing
`lexical` with `hybrid` answers what vector retrieval adds; comparing `hybrid`
with `full` answers what the LLM adds.

The load-bearing assertion in this module is the call counter. An arm that
quietly consulted the model would not fail anything visible -- it would just
report the full pipeline's accuracy under another name, and the benchmark would
be measuring nothing. So the count is asserted, not assumed.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.config import get_settings
from app.embeddings.fake import FakeEmbeddingProvider
from app.llm.base import PromptSpec, load_prompt
from app.llm.fake import FakeLLMProvider
from app.models.candidate import Candidate
from app.models.rerank import RerankResult
from app.pipeline.map_term import map_term

pytestmark = pytest.mark.requires_db

SETTINGS = get_settings()
ARMS = ["lexical", "hybrid", "full"]


class CountingLLM:
    """A fake reranker that records how often it was asked."""

    provider_id = "fake"
    model_id = "fake-rerank-v1"

    def __init__(self) -> None:
        self.calls = 0
        self._inner = FakeLLMProvider()

    def rerank(self, query: str, candidates: list[Candidate], prompt: PromptSpec) -> RerankResult:
        self.calls += 1
        return self._inner.rerank(query, candidates, prompt)


def _map(session: Session, version: str, text: str, arm: str, llm: CountingLLM):  # type: ignore[no-untyped-def]
    return map_term(
        session,
        text=text,
        target_system="icd10se",
        version=version,
        trace_id=f"test-arm-{arm}",
        settings=SETTINGS,
        embedding_provider=FakeEmbeddingProvider(dim=SETTINGS.embedding_dim),
        llm_provider=llm,  # type: ignore[arg-type]
        prompt=load_prompt("rerank_v1"),
        arm=arm,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize("arm", ARMS)
def test_every_arm_suggests_a_code_for_a_term_that_is_there(
    db_session: Session, icd10se_embedded: str, arm: str
) -> None:
    llm = CountingLLM()
    outcome = _map(db_session, icd10se_embedded, "högt blodtryck", arm, llm)

    assert outcome.proposal.status == "pending"
    assert outcome.proposal.suggested_code == "I10"
    assert outcome.arm == arm
    assert outcome.proposal.arm == arm


@pytest.mark.parametrize("arm", ARMS)
def test_the_gate_fires_identically_in_every_arm(
    db_session: Session, icd10se_embedded: str, arm: str
) -> None:
    """One gate, one outcome, whichever arm asked. A per-arm gate would make the
    arms incomparable, which is the opposite of the point."""
    llm = CountingLLM()
    outcome = _map(db_session, icd10se_embedded, "banan", arm, llm)

    assert outcome.gate.fired
    assert outcome.proposal.status == "no_good_match"
    assert outcome.proposal.suggested_code is None
    # The gate short-circuits before the model in every arm, `full` included.
    assert llm.calls == 0


@pytest.mark.parametrize(("arm", "expected_calls"), [("lexical", 0), ("hybrid", 0), ("full", 1)])
def test_only_the_full_arm_calls_the_model(
    db_session: Session, icd10se_embedded: str, arm: str, expected_calls: int
) -> None:
    llm = CountingLLM()
    _map(db_session, icd10se_embedded, "högt blodtryck", arm, llm)

    assert llm.calls == expected_calls


@pytest.mark.parametrize("arm", ["lexical", "hybrid"])
def test_a_measurement_arm_records_no_rerank_and_no_confidence(
    db_session: Session, icd10se_embedded: str, arm: str
) -> None:
    """No model ran, so there is nothing to record. Writing a rerank record
    anyway would be a fabrication in an append-only audit table."""
    llm = CountingLLM()
    outcome = _map(db_session, icd10se_embedded, "högt blodtryck", arm, llm)

    assert outcome.proposal.rerank is None
    assert outcome.proposal.model_confidence is None
    assert outcome.rerank is None


def test_the_lexical_arm_retrieves_no_vector_candidates(
    db_session: Session, icd10se_embedded: str
) -> None:
    llm = CountingLLM()
    outcome = _map(db_session, icd10se_embedded, "högt blodtryck", "lexical", llm)

    assert outcome.candidates
    assert all("vector" not in c.sources for c in outcome.candidates)


def test_the_hybrid_arm_can_retrieve_vector_candidates(
    db_session: Session, icd10se_embedded: str
) -> None:
    llm = CountingLLM()
    outcome = _map(db_session, icd10se_embedded, "högt blodtryck", "hybrid", llm)

    assert any("vector" in c.sources for c in outcome.candidates)


def test_the_arm_defaults_to_full(db_session: Session, icd10se_embedded: str) -> None:
    """Every product surface takes this path without asking for it."""
    llm = CountingLLM()
    outcome = map_term(
        db_session,
        text="högt blodtryck",
        target_system="icd10se",
        version=icd10se_embedded,
        trace_id="test-arm-default",
        settings=SETTINGS,
        embedding_provider=FakeEmbeddingProvider(dim=SETTINGS.embedding_dim),
        llm_provider=llm,  # type: ignore[arg-type]
        prompt=load_prompt("rerank_v1"),
    )

    assert outcome.proposal.arm == "full"
    assert llm.calls == 1


def test_existing_proposals_read_back_as_full(db_session: Session) -> None:
    """The migration's backfill: a row written without an arm is a full-pipeline
    row, because until now there was no other way to produce one."""
    column = db_session.execute(
        sa.text(
            "SELECT column_default, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'proposals' AND column_name = 'arm'"
        )
    ).one()

    assert "full" in column.column_default
    assert column.is_nullable == "NO"


def test_only_the_three_arm_names_are_storable(db_session: Session) -> None:
    """The arm names are fixed. A fourth one is a schema decision, not a typo
    someone gets to make in a run script."""
    constraint = db_session.execute(
        sa.text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'ck_proposals_arm'"
        )
    ).scalar_one()

    for arm in ARMS:
        assert f"'{arm}'" in constraint
    assert "vector" not in constraint


# ------------------------------------------------------- not a product feature


def test_the_api_does_not_accept_an_arm() -> None:
    """The arm is an evaluation instrument. A validator always gets the full
    pipeline, and cannot be handed a cheaper one by a caller."""
    from app.models.api import MapRequest

    assert "arm" not in MapRequest.model_fields


def test_the_api_ignores_an_arm_that_is_sent_anyway(
    client: object, db_session: Session, icd10se_embedded: str
) -> None:
    """Sending it changes nothing: the proposal on file is a full-pipeline one.

    The request model ignores unknown fields rather than rejecting them, which
    is existing API behaviour this change should not alter. What matters is that
    a caller cannot hand a validator a cheaper pipeline than the one the page
    claims to show.
    """
    response = client.post(  # type: ignore[attr-defined]
        "/map",
        json={
            "text": "högt blodtryck",
            "target_system": "icd10se",
            "version": icd10se_embedded,
            "arm": "lexical",
        },
    )

    assert response.status_code == 201
    stored = db_session.execute(
        sa.text("SELECT arm FROM proposals WHERE id = :id"),
        {"id": response.json()["id"]},
    ).scalar_one()
    assert stored == "full"


@pytest.mark.anyio
async def test_no_mcp_tool_exposes_an_arm() -> None:
    from mcp import Client

    from mcp_server.server import build_server

    async with Client(build_server()) as mcp_client:
        for tool in (await mcp_client.list_tools()).tools:
            assert "arm" not in (tool.input_schema.get("properties") or {}), tool.name

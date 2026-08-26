"""The decision layer, tested directly rather than through HTTP.

`test_api.py` covers the endpoints; this covers the rules themselves, plus the
interaction between a decision and the append-only guarantee.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

from app.audit.writer import get_decision_for
from app.config import get_settings
from app.embeddings.fake import FakeEmbeddingProvider
from app.llm.base import load_prompt
from app.llm.fake import FakeLLMProvider
from app.pipeline.map_term import map_term
from app.validation.decisions import (
    DecisionConflict,
    InvalidDecision,
    ProposalNotFound,
    record_decision,
)

pytestmark = pytest.mark.requires_db

SETTINGS = get_settings()


@pytest.fixture
def proposal_id(db_session: Session, icd10se_embedded: str) -> uuid.UUID:
    outcome = map_term(
        db_session,
        text="högt blodtryck",
        target_system="icd10se",
        version=icd10se_embedded,
        trace_id="t",
        settings=SETTINGS,
        embedding_provider=FakeEmbeddingProvider(dim=SETTINGS.embedding_dim),
        llm_provider=FakeLLMProvider(),
        prompt=load_prompt(),
    )
    return outcome.proposal.id


def test_accept_uses_the_proposals_own_suggestion(
    db_session: Session, proposal_id: uuid.UUID
) -> None:
    row = record_decision(db_session, proposal_id=proposal_id, decision="accept", validator_id="c")
    assert row.decision == "accept"
    assert row.final_code == "I10"


def test_resolution_is_derived_not_stored(db_session: Session, proposal_id: uuid.UUID) -> None:
    """A proposal is resolved because a decision references it -- never because
    a flag was flipped. A flag would need an UPDATE, which the trigger forbids.
    """
    from app.audit.writer import get_proposal

    assert get_decision_for(db_session, proposal_id) is None
    before = get_proposal(db_session, proposal_id)
    assert before is not None and before.status == "pending"

    record_decision(db_session, proposal_id=proposal_id, decision="reject", validator_id="c")

    after = get_proposal(db_session, proposal_id)
    assert after is not None
    assert after.status == "pending"  # unchanged: the proposal row is immutable
    assert get_decision_for(db_session, proposal_id) is not None


def test_second_decision_is_refused(db_session: Session, proposal_id: uuid.UUID) -> None:
    record_decision(db_session, proposal_id=proposal_id, decision="accept", validator_id="a")
    with pytest.raises(DecisionConflict, match="already been decided"):
        record_decision(db_session, proposal_id=proposal_id, decision="reject", validator_id="b")


def test_correct_requires_a_valid_and_existing_code(
    db_session: Session, proposal_id: uuid.UUID
) -> None:
    with pytest.raises(InvalidDecision, match="not a valid icd10se code format"):
        record_decision(
            db_session,
            proposal_id=proposal_id,
            decision="correct",
            final_code="nonsense",
            validator_id="c",
        )
    with pytest.raises(InvalidDecision, match="does not exist in version"):
        record_decision(
            db_session,
            proposal_id=proposal_id,
            decision="correct",
            final_code="Z99.9",
            validator_id="c",
        )


def test_correct_normalises_case(db_session: Session, proposal_id: uuid.UUID) -> None:
    row = record_decision(
        db_session,
        proposal_id=proposal_id,
        decision="correct",
        final_code=" i15.9 ",
        validator_id="c",
    )
    assert row.final_code == "I15.9"


def test_unknown_proposal(db_session: Session, icd10se_embedded: str) -> None:
    with pytest.raises(ProposalNotFound):
        record_decision(db_session, proposal_id=uuid.uuid4(), decision="reject", validator_id="c")


def test_accept_is_impossible_without_a_suggestion(
    db_session: Session, icd10se_embedded: str
) -> None:
    """A rerank_failed proposal has nothing to accept -- only reject or correct."""
    from app.llm.base import PromptSpec, RerankFailed
    from app.models.candidate import Candidate
    from app.models.rerank import RerankResult

    class Broken:
        provider_id = "broken"
        model_id = "broken-v1"

        def rerank(
            self, query: str, candidates: list[Candidate], prompt: PromptSpec
        ) -> RerankResult:
            raise RerankFailed("simulated")

    outcome = map_term(
        db_session,
        text="astma",
        target_system="icd10se",
        version=icd10se_embedded,
        trace_id="t",
        settings=SETTINGS,
        embedding_provider=FakeEmbeddingProvider(dim=SETTINGS.embedding_dim),
        llm_provider=Broken(),  # type: ignore[arg-type]
        prompt=load_prompt(),
    )
    assert outcome.proposal.status == "rerank_failed"

    with pytest.raises(InvalidDecision, match="no suggested code to accept"):
        record_decision(
            db_session,
            proposal_id=outcome.proposal.id,
            decision="accept",
            validator_id="c",
        )

    # ...but the human can still correct it themselves.
    row = record_decision(
        db_session,
        proposal_id=outcome.proposal.id,
        decision="correct",
        final_code="J45",
        validator_id="c",
    )
    assert row.final_code == "J45"


def test_empty_note_is_stored_as_null(db_session: Session, proposal_id: uuid.UUID) -> None:
    row = record_decision(
        db_session,
        proposal_id=proposal_id,
        decision="reject",
        validator_note="   ",
        validator_id="c",
    )
    assert row.validator_note is None

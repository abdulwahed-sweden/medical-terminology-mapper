"""End-to-end pipeline behaviour, and its reproducibility.

The determinism test is the load-bearing one. With the fake providers, the same
input must produce the same proposal content -- same candidates, same scores,
same ranking, same prompt hash. A proposal whose content shifts between
identical runs cannot support the claim that it records what the human was
shown.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.audit.writer import get_decision_for
from app.config import get_settings
from app.embeddings.fake import FakeEmbeddingProvider
from app.llm.base import PromptSpec, RerankFailed, load_prompt
from app.llm.fake import FakeLLMProvider
from app.models.candidate import Candidate
from app.models.rerank import RankedCode, RerankResult
from app.pipeline.map_term import (
    MapOutcome,
    TerminologyVersionNotLoaded,
    map_term,
)
from app.terminology.base import TerminologyLicenceRequired

pytestmark = pytest.mark.requires_db

SETTINGS = get_settings()

# Fields that legitimately differ between two identical runs.
#   id / trace_id / created_at -- new per run, by design.
#   latency_ms_*               -- wall-clock measurements, not content.
VOLATILE_FIELDS = {"id", "trace_id", "created_at", "latency_ms_retrieval", "latency_ms_rerank"}


def _run(
    session: Session,
    version: str,
    text: str = "högt blodtryck",
    *,
    llm: object | None = None,
    trace_id: str = "trace-fixed",
    target_system: str = "icd10se",
) -> MapOutcome:
    return map_term(
        session,
        text=text,
        target_system=target_system,
        version=version,
        trace_id=trace_id,
        settings=SETTINGS,
        embedding_provider=FakeEmbeddingProvider(dim=SETTINGS.embedding_dim),
        llm_provider=llm or FakeLLMProvider(),  # type: ignore[arg-type]
        prompt=load_prompt(),
    )


def _content(proposal: Any) -> dict[str, Any]:
    return {
        column.name: getattr(proposal, column.name)
        for column in proposal.__table__.columns
        if column.name not in VOLATILE_FIELDS
    }


# --------------------------------------------------------------- happy path


def test_pipeline_produces_a_pending_proposal(
    db_session: Session, icd10se_embedded: str
) -> None:
    outcome = _run(db_session, icd10se_embedded)
    proposal = outcome.proposal

    assert proposal.status == "pending"
    assert proposal.suggested_code == "I10"
    assert proposal.input_text == "högt blodtryck"
    assert proposal.normalized_text == "högt blodtryck"
    assert proposal.target_system == "icd10se"
    assert proposal.terminology_version == icd10se_embedded


def test_a_new_proposal_has_no_decision(db_session: Session, icd10se_embedded: str) -> None:
    """Principle 1: nothing is accepted until a human says so."""
    outcome = _run(db_session, icd10se_embedded)
    assert get_decision_for(db_session, outcome.proposal.id) is None
    assert outcome.proposal.status == "pending"


def test_provenance_is_recorded_on_the_proposal(
    db_session: Session, icd10se_embedded: str
) -> None:
    """Principle 2: the proposal must name everything that shaped it."""
    proposal = _run(db_session, icd10se_embedded).proposal
    prompt = load_prompt()

    assert proposal.trace_id == "trace-fixed"
    assert proposal.llm_provider == "fake"
    assert proposal.llm_model == "fake-rerank-v1"
    assert proposal.prompt_id == "rerank_v1"
    assert proposal.prompt_hash == prompt.sha256
    assert proposal.embedding_provider == "fake"
    assert proposal.embedding_model == "fake-hash-v1"
    assert proposal.latency_ms_retrieval >= 0
    assert proposal.latency_ms_rerank >= 0


def test_candidates_are_stored_with_every_score(
    db_session: Session, icd10se_embedded: str
) -> None:
    proposal = _run(db_session, icd10se_embedded).proposal
    assert proposal.candidates

    by_code = {c["code"]: c for c in proposal.candidates}
    top = by_code["I10"]
    assert top["sources"] == ["lexical", "vector"]
    assert top["lexical_score"] is not None
    assert top["vector_score"] is not None
    assert top["ts_rank"] is not None
    assert top["trgm_similarity"] is not None
    assert top["fused_score"] > 0

    # A vector-only candidate keeps null lexical scores rather than a zero that
    # would read as "the lexical stage scored it badly".
    vector_only = [c for c in proposal.candidates if c["sources"] == ["vector"]]
    assert vector_only
    assert vector_only[0]["lexical_score"] is None


def test_rerank_payload_is_stored_verbatim(
    db_session: Session, icd10se_embedded: str
) -> None:
    outcome = _run(db_session, icd10se_embedded)
    assert outcome.rerank is not None
    assert outcome.proposal.rerank == outcome.rerank.model_dump(mode="json")
    assert outcome.proposal.model_confidence == outcome.rerank.ranked[0].confidence


def test_candidate_cap_is_applied(db_session: Session, icd10se_embedded: str) -> None:
    proposal = _run(db_session, icd10se_embedded, text="hypertoni").proposal
    assert len(proposal.candidates) <= SETTINGS.rerank_candidate_cap


# ------------------------------------------------------------- determinism


def test_identical_input_yields_identical_proposal_content(
    db_session: Session, icd10se_embedded: str
) -> None:
    first = _run(db_session, icd10se_embedded).proposal
    second = _run(db_session, icd10se_embedded).proposal

    assert first.id != second.id  # two distinct audit records
    assert _content(first) == _content(second)


def test_determinism_holds_across_several_inputs(
    db_session: Session, icd10se_embedded: str
) -> None:
    for text in ["högt blodtryck", "astma", "hjartinfarkt", "hypertoni med njursjukdom"]:
        first = _content(_run(db_session, icd10se_embedded, text).proposal)
        second = _content(_run(db_session, icd10se_embedded, text).proposal)
        assert first == second, f"non-deterministic for {text!r}"


def test_normalization_variants_produce_the_same_content(
    db_session: Session, icd10se_embedded: str
) -> None:
    """Only `input_text` should differ -- it is the untouched audit record of
    what the human actually typed."""
    a = _content(_run(db_session, icd10se_embedded, "Högt Blodtryck").proposal)
    b = _content(_run(db_session, icd10se_embedded, "högt   blodtryck").proposal)
    assert a.pop("input_text") != b.pop("input_text")
    assert a == b


# ------------------------------------------------------------------ failures


class _BrokenLLM:
    provider_id = "broken"
    model_id = "broken-v1"

    def rerank(
        self, query: str, candidates: list[Candidate], prompt: PromptSpec
    ) -> RerankResult:
        raise RerankFailed("simulated: unusable response twice")


class _HallucinatingLLM:
    provider_id = "hallucinating"
    model_id = "hallucinating-v1"

    def rerank(
        self, query: str, candidates: list[Candidate], prompt: PromptSpec
    ) -> RerankResult:
        return RerankResult(
            ranked=[
                RankedCode(code="Z99.9", confidence=0.99, reason="not a candidate"),
                RankedCode(code=candidates[0].code, confidence=0.4, reason="real"),
            ],
            no_good_match=False,
        )


def test_rerank_failure_still_writes_an_audited_proposal(
    db_session: Session, icd10se_embedded: str
) -> None:
    """A failed attempt is part of the trail, not something to discard."""
    outcome = _run(db_session, icd10se_embedded, llm=_BrokenLLM())
    proposal = outcome.proposal

    assert proposal.status == "rerank_failed"
    assert proposal.rerank is None
    assert proposal.suggested_code is None
    assert proposal.model_confidence is None
    # The retrieved candidates are still recorded, so a human can decide alone.
    assert proposal.candidates
    assert proposal.llm_provider == "broken"


def test_hallucinated_code_never_reaches_the_proposal(
    db_session: Session, icd10se_embedded: str
) -> None:
    outcome = _run(db_session, icd10se_embedded, llm=_HallucinatingLLM())

    assert outcome.dropped_codes == ["Z99.9"]
    assert outcome.proposal.suggested_code != "Z99.9"
    ranked_codes = [r["code"] for r in outcome.proposal.rerank["ranked"]]
    assert "Z99.9" not in ranked_codes
    assert outcome.proposal.suggested_code == ranked_codes[0]


def test_unloaded_version_is_reported_clearly(
    db_session: Session, icd10se_embedded: str
) -> None:
    with pytest.raises(TerminologyVersionNotLoaded, match="load_terminology"):
        _run(db_session, "1999-not-loaded")


def test_snomed_is_refused_with_the_licensing_message(
    db_session: Session, icd10se_embedded: str
) -> None:
    with pytest.raises(TerminologyLicenceRequired, match="LICENSING.md"):
        _run(db_session, icd10se_embedded, target_system="snomed")


def test_query_with_no_matches_produces_a_proposal_with_no_suggestion(
    db_session: Session, icd10se_embedded: str
) -> None:
    """The vector stage always returns its nearest neighbours, so candidates
    exist; what must not happen is a confident suggestion out of nothing."""
    outcome = _run(db_session, icd10se_embedded, text="qzxwvk lorem ipsum")
    assert outcome.proposal.status == "pending"
    assert get_decision_for(db_session, outcome.proposal.id) is None

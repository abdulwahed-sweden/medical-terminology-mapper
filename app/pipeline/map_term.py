"""The mapping pipeline: normalize -> retrieve -> merge -> rerank -> proposal.

One function, one transaction, one audit row. The proposal is written whether
the run succeeded or the reranker failed, because a failed mapping attempt is
part of the trail too -- silently dropping it would leave a gap exactly where
someone later asks "what happened when I typed that?".

Nothing here decides anything. The output is always a proposal awaiting a human.

The `arm` argument exists for the phase 3 comparison and nothing else. Every
product surface -- the validator page, the API, the MCP server -- runs `full`,
which is the pipeline described above. `lexical` and `hybrid` stop before the
model so that "what does vector retrieval add" and "what does the LLM add" can
be answered separately, and the arm is written onto the proposal so the answer
can be reconstructed from the audit trail rather than from someone's memory of
how they configured a run.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.audit.models import ProposalRow
from app.audit.writer import insert_proposal
from app.config import Settings
from app.db.models import ConceptRow
from app.embeddings.base import EmbeddingProvider
from app.llm.base import LLMProvider, PromptSpec, RerankFailed, enforce_candidate_codes
from app.models.candidate import Candidate
from app.models.rerank import RerankResult
from app.normalize.swedish import normalize
from app.pipeline.gate import GATE_ID, GATE_VERSION, GateOutcome, evaluate_gate
from app.services.terminology import retrieve
from app.terminology.base import TerminologyLicenceRequired

logger = logging.getLogger(__name__)


class TerminologyVersionNotLoaded(RuntimeError):
    """The requested (system, version) has no concepts.

    Raised rather than returning an empty proposal: "no candidates" and "you
    never loaded this terminology" look identical to a user staring at an empty
    result panel, and only one of them is their problem.
    """


# The three things phase 3 compares. A vector-only arm is deliberately absent:
# no production configuration would run it, so measuring it would spend
# gold-set signal on a question nobody is asking.
Arm = Literal["lexical", "hybrid", "full"]


@dataclass(frozen=True)
class MapOutcome:
    proposal: ProposalRow
    candidates: list[Candidate]
    rerank: RerankResult | None
    dropped_codes: list[str]
    gate: GateOutcome
    arm: Arm


def map_term(
    session: Session,
    *,
    text: str,
    target_system: str,
    version: str | None,
    trace_id: str,
    settings: Settings,
    embedding_provider: EmbeddingProvider,
    llm_provider: LLMProvider,
    prompt: PromptSpec,
    origin: str = "api",
    requested_by: str | None = None,
    arm: Arm = "full",
) -> MapOutcome:
    if target_system == "snomed":
        raise TerminologyLicenceRequired(
            "SNOMED CT content is not shipped with this repository and cannot be "
            "mapped against in Phase 1. See LICENSING.md."
        )

    terminology_version = version or settings.default_terminology_version
    _require_loaded(session, target_system, terminology_version)

    normalized = normalize(text)
    logger.info(
        "map_started",
        extra={
            "target_system": target_system,
            "terminology_version": terminology_version,
            "normalized_text": normalized.normalized,
            "arm": arm,
        },
    )

    # ------------------------------------------------------------- retrieval
    # One retrieval implementation, shared with the MCP server's read tools.
    found = retrieve(
        session,
        query=normalized.normalized,
        system=target_system,
        version=terminology_version,
        settings=settings,
        embedding_provider=embedding_provider,
        include_vector=arm != "lexical",
    )
    candidates = found.candidates
    latency_ms_retrieval = found.latency_ms
    logger.info(
        "retrieval_finished",
        extra={
            "lexical_count": found.lexical_count,
            "vector_count": found.vector_count,
            "merged_count": len(candidates),
            "latency_ms": latency_ms_retrieval,
        },
    )

    # ------------------------------------------------------------- the gate
    gate = evaluate_gate(
        candidates,
        settings=settings,
        embedding_provider_id=embedding_provider.provider_id,
        embedding_model_id=embedding_provider.model_id,
        query=normalized.normalized,
        tokens=normalized.tokens,
    )
    logger.info(
        "gate_evaluated",
        extra={"gate_fired": gate.fired, "gate_reason": gate.reason, **gate.values},
    )

    # -------------------------------------------------------------- reranking
    rerank_started = time.perf_counter()
    status = "pending"
    result: RerankResult | None = None
    dropped: list[str] = []

    if gate.fired:
        # The LLM is not called at all. Asking a model to rank evidence that is
        # not there invites a confident answer built from noise, which is the
        # failure this whole path exists to prevent. No call, no cost, no
        # opportunity.
        #
        # The gate runs identically in every arm, and fires identically. It is
        # lexical-evidence-based today, which is an asymmetry between the arms;
        # phase 3 reports it rather than compensating for it per arm, because a
        # per-arm gate would make the arms incomparable. See ARCHITECTURE.md.
        status = "no_good_match"
    elif arm != "full":
        # The measurement arms stop here. `retrieval` is the answer being
        # measured, so consulting the model would be measuring something else.
        pass
    else:
        try:
            raw_result = llm_provider.rerank(normalized.normalized, candidates, prompt)
            result, dropped = enforce_candidate_codes(raw_result, candidates)
        except RerankFailed as exc:
            # The proposal is still written, with status rerank_failed. A human
            # sees the retrieved candidates and can decide without the model.
            status = "rerank_failed"
            logger.error("rerank_failed", extra={"reason": str(exc)})

        if result is not None and result.no_good_match:
            # The reranker's own verdict is a second, independent signal. Both
            # are recorded: the gate admitted this, and the model still declined.
            status = "no_good_match"

    latency_ms_rerank = _elapsed_ms(rerank_started)

    top = result.top if result is not None else None
    if arm == "full":
        suggested_code = None if status != "pending" or result is None or top is None else top.code
    else:
        # Top-1 by the arm's own ordering: lexical rank for `lexical`, the RRF
        # merge for `hybrid`. `rerank` stays null -- there was no rerank, and
        # writing a record of one would be a fabrication in an audit table.
        suggested_code = candidates[0].code if status == "pending" and candidates else None

    provider_kind = "fake" if llm_provider.provider_id == "fake" else "live"
    # A deterministic stand-in has no confidence to report. Its raw reply is
    # still stored verbatim in `rerank`; what is not stored is a number in the
    # column everything downstream reads as "the model's confidence" -- a number
    # in a screenshot travels without its caveat.
    # Null for the measurement arms too: no model ran, so there is no confidence
    # to report, and a number here would be read as one.
    model_confidence = (
        None
        if arm != "full" or suggested_code is None or top is None or provider_kind == "fake"
        else top.confidence
    )

    # ------------------------------------------------------------- audit row
    proposal = insert_proposal(
        session,
        trace_id=trace_id,
        input_text=text,
        normalized_text=normalized.normalized,
        target_system=target_system,
        terminology_version=terminology_version,
        candidates=[c.model_dump(mode="json") for c in candidates],
        rerank=result.model_dump(mode="json") if result is not None else None,
        suggested_code=suggested_code,
        model_confidence=model_confidence,
        llm_provider=llm_provider.provider_id,
        llm_model=llm_provider.model_id,
        prompt_id=prompt.prompt_id,
        prompt_hash=prompt.sha256,
        embedding_provider=embedding_provider.provider_id,
        embedding_model=embedding_provider.model_id,
        latency_ms_retrieval=latency_ms_retrieval,
        latency_ms_rerank=latency_ms_rerank,
        status=status,
        provider_kind=provider_kind,
        gate_id=GATE_ID,
        gate_version=GATE_VERSION,
        gate_fired=gate.fired,
        gate_values={**gate.values, "reason": gate.reason},
        origin=origin,
        arm=arm,
        requested_by=requested_by,
    )

    return MapOutcome(
        proposal=proposal,
        candidates=candidates,
        rerank=result,
        dropped_codes=dropped,
        gate=gate,
        arm=arm,
    )


def _require_loaded(session: Session, system: str, version: str) -> None:
    count = session.execute(
        sa.select(sa.func.count())
        .select_from(ConceptRow)
        .where(ConceptRow.system == system, ConceptRow.version == version)
    ).scalar_one()
    if not count:
        raise TerminologyVersionNotLoaded(
            f"no concepts loaded for system {system!r} version {version!r}. "
            f"Run scripts/load_terminology.py, then scripts/embed_terminology.py."
        )


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)

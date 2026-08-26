"""The mapping pipeline: normalize -> retrieve -> merge -> rerank -> proposal.

One function, one transaction, one audit row. The proposal is written whether
the run succeeded or the reranker failed, because a failed mapping attempt is
part of the trail too -- silently dropping it would leave a gap exactly where
someone later asks "what happened when I typed that?".

Nothing here decides anything. The output is always a proposal awaiting a human.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

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
from app.retrieval.lexical import lexical_search
from app.retrieval.merge import merge_candidates
from app.retrieval.vector import vector_search
from app.terminology.base import TerminologyLicenceRequired

logger = logging.getLogger(__name__)


class TerminologyVersionNotLoaded(RuntimeError):
    """The requested (system, version) has no concepts.

    Raised rather than returning an empty proposal: "no candidates" and "you
    never loaded this terminology" look identical to a user staring at an empty
    result panel, and only one of them is their problem.
    """


@dataclass(frozen=True)
class MapOutcome:
    proposal: ProposalRow
    candidates: list[Candidate]
    rerank: RerankResult | None
    dropped_codes: list[str]


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
        },
    )

    # ------------------------------------------------------------- retrieval
    retrieval_started = time.perf_counter()

    lexical = lexical_search(
        session,
        query=normalized.normalized,
        system=target_system,
        version=terminology_version,
        top_k=settings.lexical_top_k,
        trigram_threshold=settings.trigram_threshold,
    )

    query_vector = embedding_provider.embed([normalized.normalized])[0]
    vector = vector_search(
        session,
        query_vector=query_vector,
        system=target_system,
        version=terminology_version,
        provider=embedding_provider.provider_id,
        model=embedding_provider.model_id,
        top_k=settings.vector_top_k,
    )

    candidates = merge_candidates(
        lexical,
        vector,
        rrf_k=settings.rrf_k,
        cap=settings.rerank_candidate_cap,
    )
    latency_ms_retrieval = _elapsed_ms(retrieval_started)
    logger.info(
        "retrieval_finished",
        extra={
            "lexical_count": len(lexical),
            "vector_count": len(vector),
            "merged_count": len(candidates),
            "latency_ms": latency_ms_retrieval,
        },
    )

    # -------------------------------------------------------------- reranking
    rerank_started = time.perf_counter()
    status = "pending"
    result: RerankResult | None = None
    dropped: list[str] = []

    try:
        raw_result = llm_provider.rerank(normalized.normalized, candidates, prompt)
        result, dropped = enforce_candidate_codes(raw_result, candidates)
    except RerankFailed as exc:
        # The proposal is still written, with status rerank_failed. A human sees
        # the retrieved candidates and can decide without the model's help.
        status = "rerank_failed"
        logger.error("rerank_failed", extra={"reason": str(exc)})

    latency_ms_rerank = _elapsed_ms(rerank_started)

    top = result.top if result is not None else None
    suggested_code = None if result is None or result.no_good_match else (
        top.code if top else None
    )
    model_confidence = None if suggested_code is None or top is None else top.confidence

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
    )

    return MapOutcome(
        proposal=proposal, candidates=candidates, rerank=result, dropped_codes=dropped
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
    return int(round((time.perf_counter() - started) * 1000))

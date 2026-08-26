"""Turn audit rows into API responses.

Kept apart from the routes so the shape a reviewer sees is defined in one place
and the routes stay about HTTP.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.audit.models import DecisionRow, ProposalRow
from app.audit.writer import get_decision_for
from app.models.api import DecisionOut, ProposalOut, RankedCodeOut, ValidatedMapping


def serialize_decision(row: DecisionRow) -> DecisionOut:
    return DecisionOut(
        id=row.id,
        proposal_id=row.proposal_id,
        created_at=row.created_at,
        decision=row.decision,
        final_code=row.final_code,
        validator_note=row.validator_note,
        validator_id=row.validator_id,
    )


def serialize_proposal(session: Session, proposal: ProposalRow) -> ProposalOut:
    terms = {
        candidate["code"]: candidate.get("preferred_term") for candidate in proposal.candidates
    }
    rerank: dict[str, Any] = proposal.rerank or {}

    ranked = [
        RankedCodeOut(
            code=entry["code"],
            preferred_term=terms.get(entry["code"]),
            model_confidence=entry["confidence"],
            reason=entry["reason"],
        )
        for entry in rerank.get("ranked", [])
    ]

    decision_row = get_decision_for(session, proposal.id)
    decision = serialize_decision(decision_row) if decision_row else None

    validated: ValidatedMapping | None = None
    if decision_row is not None and decision_row.final_code is not None:
        validated = ValidatedMapping(
            system=proposal.target_system,
            version=proposal.terminology_version,
            code=decision_row.final_code,
            decision_id=decision_row.id,
        )

    return ProposalOut(
        id=proposal.id,
        trace_id=proposal.trace_id,
        created_at=proposal.created_at,
        status=proposal.status,
        input_text=proposal.input_text,
        normalized_text=proposal.normalized_text,
        target_system=proposal.target_system,
        terminology_version=proposal.terminology_version,
        suggested_code=proposal.suggested_code,
        suggested_term=terms.get(proposal.suggested_code or ""),
        model_confidence=proposal.model_confidence,
        no_good_match=bool(rerank.get("no_good_match", proposal.rerank is None)),
        notes=rerank.get("notes"),
        ranked=ranked,
        candidates=proposal.candidates,
        llm_provider=proposal.llm_provider,
        llm_model=proposal.llm_model,
        prompt_id=proposal.prompt_id,
        prompt_hash=proposal.prompt_hash,
        embedding_provider=proposal.embedding_provider,
        embedding_model=proposal.embedding_model,
        latency_ms_retrieval=proposal.latency_ms_retrieval,
        latency_ms_rerank=proposal.latency_ms_rerank,
        decision=decision,
        validated_mapping=validated,
    )

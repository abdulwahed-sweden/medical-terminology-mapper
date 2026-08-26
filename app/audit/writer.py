"""The only access point to the audit tables.

This module exposes inserts and reads. It deliberately exposes no update and no
delete, and none may be added: `proposals` and `decisions` are append-only, and
the database enforces that with a trigger (see the initial migration). The
absence here is the application-level half of the same guarantee -- code that
cannot express a mutation cannot accidentally attempt one.

If a mapping turns out to be wrong, the answer is a new proposal and a new
decision, not an edited old one. The record of what was decided, and on what
evidence, is the product.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.audit.models import DecisionRow, ProposalRow

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Inserts
# --------------------------------------------------------------------------- #


def insert_proposal(
    session: Session,
    *,
    trace_id: str,
    input_text: str,
    normalized_text: str,
    target_system: str,
    terminology_version: str,
    candidates: list[dict[str, Any]],
    rerank: dict[str, Any] | None,
    suggested_code: str | None,
    model_confidence: float | None,
    llm_provider: str,
    llm_model: str,
    prompt_id: str,
    prompt_hash: str,
    embedding_provider: str,
    embedding_model: str,
    latency_ms_retrieval: int,
    latency_ms_rerank: int,
    status: str,
    provider_kind: str,
    gate_id: str,
    gate_version: str,
    gate_fired: bool,
    gate_values: dict[str, Any],
) -> ProposalRow:
    """Record one mapping attempt.

    Status is `pending`, `rerank_failed`, or `no_good_match`. There is no status
    meaning "accepted": a proposal becomes resolved only by the existence of a
    decision row referencing it (principle 1).
    """
    row = ProposalRow(
        id=uuid.uuid4(),
        trace_id=trace_id,
        input_text=input_text,
        normalized_text=normalized_text,
        target_system=target_system,
        terminology_version=terminology_version,
        candidates=candidates,
        rerank=rerank,
        suggested_code=suggested_code,
        model_confidence=model_confidence,
        llm_provider=llm_provider,
        llm_model=llm_model,
        prompt_id=prompt_id,
        prompt_hash=prompt_hash,
        embedding_provider=embedding_provider,
        embedding_model=embedding_model,
        latency_ms_retrieval=latency_ms_retrieval,
        latency_ms_rerank=latency_ms_rerank,
        status=status,
        provider_kind=provider_kind,
        gate_id=gate_id,
        gate_version=gate_version,
        gate_fired=gate_fired,
        gate_values=gate_values,
    )
    session.add(row)
    session.flush()
    logger.info(
        "proposal_recorded",
        extra={
            "proposal_id": str(row.id),
            "status": status,
            "target_system": target_system,
            "terminology_version": terminology_version,
            "suggested_code": suggested_code,
            "candidate_count": len(candidates),
            "gate_fired": gate_fired,
        },
    )
    return row


def insert_decision(
    session: Session,
    *,
    proposal_id: uuid.UUID,
    decision: str,
    final_code: str | None,
    validator_note: str | None,
    validator_id: str,
) -> DecisionRow:
    """Record the human decision on a proposal. One per proposal, ever."""
    row = DecisionRow(
        id=uuid.uuid4(),
        proposal_id=proposal_id,
        decision=decision,
        final_code=final_code,
        validator_note=validator_note,
        validator_id=validator_id,
    )
    session.add(row)
    session.flush()
    logger.info(
        "decision_recorded",
        extra={
            "proposal_id": str(proposal_id),
            "decision_id": str(row.id),
            "decision": decision,
            "final_code": final_code,
            "validator_id": validator_id,
        },
    )
    return row


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #


def get_proposal(session: Session, proposal_id: uuid.UUID) -> ProposalRow | None:
    return session.get(ProposalRow, proposal_id)


def get_decision_for(session: Session, proposal_id: uuid.UUID) -> DecisionRow | None:
    """The decision on a proposal, if a human has made one.

    Resolution is derived by this lookup and never stored as a flag on the
    proposal: a flag would need an UPDATE, which the trigger forbids and which
    would overwrite part of the audit trail.
    """
    return session.execute(
        sa.select(DecisionRow).where(DecisionRow.proposal_id == proposal_id)
    ).scalar_one_or_none()


def recent_proposals(session: Session, limit: int = 20) -> list[ProposalRow]:
    return list(
        session.execute(
            sa.select(ProposalRow).order_by(ProposalRow.created_at.desc()).limit(limit)
        ).scalars()
    )


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)

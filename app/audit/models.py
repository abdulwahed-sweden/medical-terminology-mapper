"""Audit tables: `proposals` and `decisions`.

Both are append-only, enforced by a `BEFORE UPDATE OR DELETE` trigger created in
the migration (principle 3). Nothing in the application may modify a row here;
`app.audit.writer` deliberately exposes inserts only.

A proposal is "resolved" when a decision row references it. That is derived by
join, never stored as a mutable flag -- a mutable flag would need an UPDATE,
which the trigger forbids, and which would destroy the audit trail.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

PROPOSAL_STATUSES = ("pending", "rerank_failed")
DECISION_KINDS = ("accept", "reject", "correct")


class ProposalRow(Base):
    __tablename__ = "proposals"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'rerank_failed')", name="ck_proposals_status"),
        Index("ix_proposals_trace_id", "trace_id"),
        Index("ix_proposals_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    input_text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)

    target_system: Mapped[str] = mapped_column(String(16), nullable=False)
    terminology_version: Mapped[str] = mapped_column(String(32), nullable=False)

    candidates: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    rerank: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    suggested_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    llm_provider: Mapped[str] = mapped_column(String(32), nullable=False)
    llm_model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_id: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    embedding_provider: Mapped[str] = mapped_column(String(32), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(128), nullable=False)

    latency_ms_retrieval: Mapped[int] = mapped_column(Integer, nullable=False)
    latency_ms_rerank: Mapped[int] = mapped_column(Integer, nullable=False)

    status: Mapped[str] = mapped_column(String(16), nullable=False)


class DecisionRow(Base):
    __tablename__ = "decisions"
    __table_args__ = (
        # One decision per proposal. This is what makes "the human decided" a
        # single, unambiguous, unrepeatable event.
        UniqueConstraint("proposal_id", name="uq_decisions_proposal_id"),
        CheckConstraint("decision IN ('accept', 'reject', 'correct')", name="ck_decisions_kind"),
        # A reject records no code; accept and correct must record one.
        CheckConstraint(
            "(decision = 'reject' AND final_code IS NULL)"
            " OR (decision IN ('accept', 'correct') AND final_code IS NOT NULL)",
            name="ck_decisions_final_code",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    proposal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("proposals.id"), nullable=False
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    final_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    validator_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    validator_id: Mapped[str] = mapped_column(String(128), nullable=False)

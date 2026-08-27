"""Request and response schemas for the HTTP surface.

`model_confidence` appears here rather than `confidence`: at the boundary where
a human reads the number, it must be unmistakable that it is the model's own
self-report and not a calibrated probability.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# pydantic reserves the `model_` prefix for its own attributes; these fields are
# domain vocabulary, so the protection is switched off explicitly rather than
# renaming the field to something less clear.
_ALLOW_MODEL_PREFIX = ConfigDict(protected_namespaces=())

TargetSystem = Literal["icd10se", "kva", "snomed"]
DecisionKind = Literal["accept", "reject", "correct"]


class MapRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500, description="Free-text clinical phrase.")
    target_system: TargetSystem
    version: str | None = Field(
        default=None, description="Terminology version; defaults to the configured one."
    )


class RankedCodeOut(BaseModel):
    model_config = _ALLOW_MODEL_PREFIX

    code: str
    preferred_term: str | None = None
    # Null whenever the reranking was done by the deterministic stand-in: it has
    # no confidence to report, and a number that looks like one would be
    # screenshotted without its caveat.
    model_confidence: float | None = None
    reason: str


class DecisionOut(BaseModel):
    id: uuid.UUID
    proposal_id: uuid.UUID
    created_at: dt.datetime
    decision: DecisionKind
    final_code: str | None
    validator_note: str | None
    validator_id: str


class ValidatedMapping(BaseModel):
    """What leaves this system once a human has validated a mapping.

    Deliberately four fields. Free text stays local; only the code, the system
    and version that give it meaning, and the decision that vouches for it, are
    fit to cross an organisational boundary.
    """

    system: str
    version: str
    code: str
    decision_id: uuid.UUID


class HierarchyNode(BaseModel):
    """One step in a code's ancestor chain. `title` is null when the release
    carries the code but not a row for that ancestor."""

    code: str
    title: str | None = None


class GateOut(BaseModel):
    """The retrieval gate's verdict, so "nothing was found" stays checkable."""

    id: str
    version: str
    fired: bool
    reason: str | None = None
    values: dict[str, Any] = Field(default_factory=dict)


class ProposalOut(BaseModel):
    model_config = _ALLOW_MODEL_PREFIX

    id: uuid.UUID
    trace_id: str
    created_at: dt.datetime
    status: Literal["pending", "rerank_failed", "no_good_match"]
    # "fake" means a deterministic stand-in produced the ranking.
    provider_kind: Literal["fake", "live"] = "live"
    gate: GateOut | None = None

    input_text: str
    normalized_text: str
    target_system: str
    terminology_version: str

    suggested_code: str | None
    suggested_term: str | None
    suggested_hierarchy: list[HierarchyNode] = Field(default_factory=list)
    suggested_parent_source: Literal["column", "derived"] | None = None
    model_confidence: float | None
    no_good_match: bool
    notes: str | None

    ranked: list[RankedCodeOut]
    candidates: list[dict[str, Any]]

    llm_provider: str
    llm_model: str
    prompt_id: str
    prompt_hash: str
    embedding_provider: str
    embedding_model: str
    latency_ms_retrieval: int
    latency_ms_rerank: int

    decision: DecisionOut | None = None
    validated_mapping: ValidatedMapping | None = None


class DecisionRequest(BaseModel):
    proposal_id: uuid.UUID
    decision: DecisionKind
    final_code: str | None = Field(default=None, max_length=32)
    validator_note: str | None = Field(default=None, max_length=500)
    validator_id: str = Field(min_length=1, max_length=128)


class ErrorOut(BaseModel):
    detail: str
    trace_id: str | None = None

"""Schemas for the reranking step.

The wire schema is strict: `extra="forbid"`, a bounded confidence, and a
required reason for every ranked code. A model that returns something else is
treated as a malformed response and repaired or failed -- it is not quietly
coerced, because a silently coerced ranking is exactly the kind of thing a
reviewer cannot audit.

On the name `confidence`: the field is called `confidence` here because that is
what the model is asked to emit and what it literally returned, and this object
is stored verbatim as the audit record. Everywhere the value is *presented* or
stored as its own column it is called `model_confidence`, to keep it clear that
it is the model's self-report and not a calibrated probability.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class RankedCode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str


class RerankResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ranked: list[RankedCode] = Field(default_factory=list)
    no_good_match: bool = False
    notes: str | None = None

    @property
    def top(self) -> RankedCode | None:
        return self.ranked[0] if self.ranked else None

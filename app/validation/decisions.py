"""The human decision step: accept, reject, correct.

This is the point of the whole system. Everything upstream produces evidence;
this records what a person concluded from it, once, permanently.

The rules here are strict on purpose. A decision is the thing that turns a
model's suggestion into a code that may end up in a patient record, a statistic
or an invoice, so every way of recording an incoherent one is closed off:
accepting a suggestion that does not exist, correcting to a code that is not
valid in the target system, or deciding twice.
"""

from __future__ import annotations

import logging
import uuid
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.audit.models import DecisionRow, ProposalRow
from app.audit.writer import get_decision_for, get_proposal, insert_decision
from app.db.models import ConceptRow
from app.terminology.base import TerminologySystem
from app.terminology.icd10se import ICD10SE
from app.terminology.kva import KVA
from app.terminology.snomed import SnomedCT

logger = logging.getLogger(__name__)

DecisionKind = Literal["accept", "reject", "correct"]

VALIDATORS: dict[str, TerminologySystem] = {
    "icd10se": ICD10SE(),
    "kva": KVA(),
    "snomed": SnomedCT(),
}


class ProposalNotFound(LookupError):
    pass


class DecisionConflict(RuntimeError):
    """A decision already exists for this proposal.

    Not an error to route around: a second decision would either overwrite the
    first (forbidden -- audit rows are immutable) or sit beside it, leaving the
    record ambiguous about what was actually decided.
    """


class InvalidDecision(ValueError):
    """The decision is internally inconsistent or names an unusable code."""


def record_decision(
    session: Session,
    *,
    proposal_id: uuid.UUID,
    decision: DecisionKind,
    final_code: str | None = None,
    validator_note: str | None = None,
    validator_id: str,
) -> DecisionRow:
    proposal = get_proposal(session, proposal_id)
    if proposal is None:
        raise ProposalNotFound(f"no proposal with id {proposal_id}")

    if get_decision_for(session, proposal_id) is not None:
        raise DecisionConflict(
            f"proposal {proposal_id} has already been decided; "
            f"map the term again to record a new opinion"
        )

    resolved_code = _resolve_final_code(session, proposal, decision, final_code)

    row = insert_decision(
        session,
        proposal_id=proposal_id,
        decision=decision,
        final_code=resolved_code,
        validator_note=(validator_note.strip() or None) if validator_note else None,
        validator_id=validator_id,
    )
    return row


def _resolve_final_code(
    session: Session,
    proposal: ProposalRow,
    decision: DecisionKind,
    final_code: str | None,
) -> str | None:
    if decision == "reject":
        # Rejecting means "none of this is right". Any code supplied alongside
        # it is a contradiction, not an extra.
        if final_code:
            raise InvalidDecision(
                "a reject records no code; use 'correct' to supply the right one"
            )
        return None

    if decision == "accept":
        if proposal.suggested_code is None:
            raise InvalidDecision(
                f"proposal {proposal.id} has no suggested code to accept "
                f"(status {proposal.status!r}); use 'correct' or 'reject'"
            )
        if final_code and final_code.strip().upper() != proposal.suggested_code.upper():
            # Accepting *something else* is a correction. Naming it correctly
            # matters: the two mean different things when the trail is audited.
            raise InvalidDecision(
                f"accept records the suggested code {proposal.suggested_code!r}; "
                f"to record {final_code!r} instead, use 'correct'"
            )
        return proposal.suggested_code

    # correct
    if not final_code or not final_code.strip():
        raise InvalidDecision("a correction must supply the correct code")
    return _validate_code(session, proposal, final_code.strip().upper())


def _validate_code(session: Session, proposal: ProposalRow, code: str) -> str:
    system = proposal.target_system
    validator = VALIDATORS.get(system)
    if validator is None:  # pragma: no cover - target_system is constrained upstream
        raise InvalidDecision(f"unknown target system {system!r}")

    if not validator.validate_code_format(code):
        raise InvalidDecision(
            f"{code!r} is not a valid {system} code format"
        )

    # Beyond format: the code must actually exist in the version this proposal
    # was computed against. A well-formed code that is not in the release would
    # still be an invalid mapping, and this is the last point at which anything
    # can catch it.
    exists = session.execute(
        sa.select(sa.func.count())
        .select_from(ConceptRow)
        .where(
            ConceptRow.system == system,
            ConceptRow.version == proposal.terminology_version,
            ConceptRow.code == code,
        )
    ).scalar_one()
    if not exists:
        raise InvalidDecision(
            f"{code!r} is a well-formed {system} code but does not exist in "
            f"version {proposal.terminology_version!r} as loaded"
        )
    return code

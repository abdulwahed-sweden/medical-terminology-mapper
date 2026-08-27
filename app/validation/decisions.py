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

from sqlalchemy.orm import Session

from app.audit.models import DecisionRow, ProposalRow
from app.audit.writer import get_decision_for, get_proposal, insert_decision
from app.services.terminology import inspect_code
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


class PlaceholderCodeNotAcknowledged(ValueError):
    """The code is a reserved U-code placeholder.

    Not a refusal: a human may deliberately record one. The caller repeats the
    request with `acknowledge_placeholder`, which is what the page's confirm
    step does.
    """


class DecisionNotApplicable(RuntimeError):
    """This kind of decision cannot apply to this proposal at all.

    Distinct from `InvalidDecision`, which is about the payload. Accepting a
    proposal that carries no suggestion is not a malformed request; it is a
    request for something that does not exist.
    """


def record_decision(
    session: Session,
    *,
    proposal_id: uuid.UUID,
    decision: DecisionKind,
    final_code: str | None = None,
    validator_note: str | None = None,
    validator_id: str,
    acknowledge_placeholder: bool = False,
) -> DecisionRow:
    proposal = get_proposal(session, proposal_id)
    if proposal is None:
        raise ProposalNotFound(f"no proposal with id {proposal_id}")

    if decision == "accept" and proposal.status == "no_good_match":
        raise DecisionNotApplicable(
            "det finns inget förslag att godkänna: systemet hittade ingen "
            "tillräcklig träff. Välj 'reject' för att bekräfta att ingen kod "
            "finns, eller 'correct' för att ange rätt kod."
        )

    if get_decision_for(session, proposal_id) is not None:
        raise DecisionConflict(
            f"proposal {proposal_id} has already been decided; "
            f"map the term again to record a new opinion"
        )

    resolved_code = _resolve_final_code(
        session,
        proposal,
        decision,
        final_code,
        acknowledge_placeholder=acknowledge_placeholder,
    )

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
    *,
    acknowledge_placeholder: bool = False,
) -> str | None:
    if decision == "reject":
        # Rejecting means "none of this is right". Any code supplied alongside
        # it is a contradiction, not an extra.
        if final_code:
            raise InvalidDecision("a reject records no code; use 'correct' to supply the right one")
        return None

    if decision == "accept":
        if proposal.suggested_code is None:
            raise InvalidDecision(
                f"förslaget {proposal.id} har ingen föreslagen kod att godkänna "
                f"(status {proposal.status!r}); använd 'correct' eller 'reject'"
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
    return _validate_code(
        session,
        proposal,
        final_code.strip().upper(),
        acknowledge_placeholder=acknowledge_placeholder,
    )


def _validate_code(
    session: Session,
    proposal: ProposalRow,
    code: str,
    *,
    acknowledge_placeholder: bool = False,
) -> str:
    """Decide whether a human-supplied code may be recorded.

    The judgement itself lives in `services.terminology.inspect_code`, so the
    validator page, the HTTP API and the MCP server all reach the same verdict
    and quote the same sentence. This function adds only what is specific to
    *recording* one: a placeholder may be recorded deliberately, so it is a
    warning to acknowledge rather than a refusal.
    """
    verdict = inspect_code(
        session,
        system=proposal.target_system,
        version=proposal.terminology_version,
        code=code,
    )

    if verdict.verdict == "placeholder" and not acknowledge_placeholder:
        raise PlaceholderCodeNotAcknowledged(verdict.message or "")
    if verdict.verdict in {"bad_format", "not_present", "heading"}:
        raise InvalidDecision(verdict.message or "")

    return verdict.code

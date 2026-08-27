"""POST /decisions and GET /proposals/{id}."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy.exc import IntegrityError

from app.api.deps import SessionDep
from app.api.serializers import serialize_proposal
from app.audit.writer import get_proposal
from app.models.api import DecisionRequest, ProposalOut
from app.validation.decisions import (
    DecisionConflict,
    DecisionNotApplicable,
    InvalidDecision,
    PlaceholderCodeNotAcknowledged,
    ProposalNotFound,
    record_decision,
)

# Starlette renamed its 422 constant between versions; the number is what the
# API contract actually specifies, so it is spelled out once here.
HTTP_422_UNPROCESSABLE = 422

router = APIRouter(tags=["validation"])


@router.get(
    "/proposals/{proposal_id}",
    response_model=ProposalOut,
    summary="Fetch a proposal and its decision, if one has been made",
)
def read_proposal(proposal_id: uuid.UUID, session: SessionDep) -> ProposalOut:
    proposal = get_proposal(session, proposal_id)
    if proposal is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"no proposal {proposal_id}")
    return serialize_proposal(session, proposal)


@router.post(
    "/decisions",
    response_model=ProposalOut,
    status_code=status.HTTP_201_CREATED,
    summary="Record the human decision on a proposal",
    description=(
        "Accept, reject, or correct. Exactly one decision may be recorded per "
        "proposal, and it can never be edited or removed -- to record a "
        "different conclusion, map the term again."
    ),
)
def create_decision(payload: DecisionRequest, session: SessionDep) -> ProposalOut:
    try:
        record_decision(
            session,
            proposal_id=payload.proposal_id,
            decision=payload.decision,
            final_code=payload.final_code,
            validator_note=payload.validator_note,
            validator_id=payload.validator_id,
            acknowledge_placeholder=payload.acknowledge_placeholder,
        )
    except ProposalNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except DecisionConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except DecisionNotApplicable as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except PlaceholderCodeNotAcknowledged as exc:
        raise HTTPException(HTTP_422_UNPROCESSABLE, detail=str(exc)) from exc
    except InvalidDecision as exc:
        raise HTTPException(HTTP_422_UNPROCESSABLE, detail=str(exc)) from exc
    except IntegrityError as exc:
        # The unique constraint is the last line of defence against two
        # decisions racing for the same proposal.
        session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"proposal {payload.proposal_id} has already been decided",
        ) from exc

    proposal = get_proposal(session, payload.proposal_id)
    assert proposal is not None  # record_decision would have raised
    return serialize_proposal(session, proposal)

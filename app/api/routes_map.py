"""POST /map -- create a proposal."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from app.api.deps import EmbeddingDep, LLMDep, PromptDep, SessionDep, SettingsDep
from app.api.serializers import serialize_proposal
from app.logging_setup import get_trace_id, new_trace_id
from app.models.api import MapRequest, ProposalOut
from app.pipeline.map_term import TerminologyVersionNotLoaded, map_term
from app.terminology.base import TerminologyLicenceRequired

router = APIRouter(tags=["mapping"])


@router.post(
    "/map",
    response_model=ProposalOut,
    status_code=status.HTTP_201_CREATED,
    summary="Map a free-text clinical term to candidate codes",
    description=(
        "Creates a proposal. The proposal is a suggestion awaiting human "
        "validation; it is never a completed mapping. Record the human decision "
        "with POST /decisions."
    ),
)
def create_mapping_proposal(
    payload: MapRequest,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    embeddings: EmbeddingDep,
    llm: LLMDep,
    prompt: PromptDep,
) -> ProposalOut:
    try:
        outcome = map_term(
            session,
            text=payload.text,
            target_system=payload.target_system,
            version=payload.version,
            trace_id=get_trace_id() or new_trace_id(),
            settings=settings,
            embedding_provider=embeddings,
            llm_provider=llm,
            prompt=prompt,
            # The validator page sends X-Client: ui. Anything else reaching
            # this route is a direct API caller. The MCP server does not come
            # through here at all -- it calls map_term in process.
            origin="ui" if request.headers.get("X-Client") == "ui" else "api",
        )
    except TerminologyLicenceRequired as exc:
        # Not implemented rather than bad request: the client asked for
        # something coherent that this build deliberately does not provide.
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)) from exc
    except TerminologyVersionNotLoaded as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    # Commit before the response is built, not in the dependency's teardown.
    # FastAPI runs a yield-dependency's exit code *after* the response has gone
    # out, so a client that posts a decision immediately can arrive before the
    # proposal is durable and be told the proposal does not exist. Measured:
    # roughly one immediate follow-up in five.
    session.commit()
    return serialize_proposal(session, outcome.proposal)

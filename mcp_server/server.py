"""The terminology-mcp tool surface.

WHY THERE IS NO DECISION TOOL
-----------------------------
This server can read the terminologies and file a proposal. It cannot accept,
reject or correct one, and that is not a gap to fill in a later phase.

Phase 1 established the product's central rule: a mapping becomes valid only
when a human records a decision, and that decision is written to an append-only
table as evidence of who concluded what. An MCP client is, by construction, a
model. A tool that let it accept a code would let a model decide, and the audit
trail would then carry a human-shaped row with no human behind it -- which is
worse than having no trail at all, because it looks like one.

So an agent that wants a code validated files a proposal with
`propose_mapping` and tells its human to open the validator page. The
proposal is real, audited and waiting; the decision is theirs.

Everything here runs in-process against the same settings, session factory,
providers and pipeline functions as the FastAPI app. It does not call the HTTP
API: one behaviour, one audit trail, no second network hop.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, TypeVar

from mcp.server.mcpserver import MCPServer
from sqlalchemy.orm import Session

from app.audit.writer import get_decision_for, get_proposal
from app.config import Settings, get_settings
from app.db.session import session_scope
from app.embeddings import build_embedding_provider
from app.llm import build_llm_provider
from app.llm.base import load_prompt
from app.logging_setup import new_trace_id, trace_context
from app.normalize.swedish import normalize
from app.pipeline.gate import evaluate_gate
from app.pipeline.map_term import map_term
from app.services.terminology import (
    KNOWN_SYSTEMS,
    TerminologyServiceError,
    candidate_payload,
    concept_detail,
    inspect_code,
    list_loaded,
    resolve_system,
    resolve_version,
    retrieve,
)

logger = logging.getLogger(__name__)

SERVER_NAME = "terminology-mcp"

DEFAULT_LIMIT = 10
MAX_LIMIT = 50

# Shown with every proposal so the calling agent can tell its human where to go.
VALIDATOR_URL_PATTERN = "http://localhost:8000/  (open the validator page, then look up proposal {proposal_id})"

T = TypeVar("T")


def _error(code: str, message: str) -> dict[str, Any]:
    """Errors are returned in the payload, not raised.

    The SDK turns an exception into a generic "Error executing tool X" string
    with the detail appended, which an agent has to parse out of prose. A
    returned object keeps `code` machine-readable and the message intact.
    """
    return {"ok": False, "error": {"code": code, "message": message}}


def _clamp(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_LIMIT
    return max(1, min(int(limit), MAX_LIMIT))


@contextmanager
def _tool(name: str, **fields: Any) -> Iterator[str]:
    """Log one invocation as a structured line, with a trace id and latency."""
    trace_id = new_trace_id()
    started = time.perf_counter()
    with trace_context(trace_id):
        logger.info("mcp_tool_started", extra={"tool": name, **fields})
        try:
            yield trace_id
        finally:
            logger.info(
                "mcp_tool_finished",
                extra={
                    "tool": name,
                    "latency_ms": round((time.perf_counter() - started) * 1000),
                    **fields,
                },
            )


def _guarded(fn: Callable[[Session, Settings], dict[str, Any]]) -> dict[str, Any]:
    """Run a read against a session, turning service errors into payloads."""
    settings = get_settings()
    try:
        with session_scope() as session:
            return fn(session, settings)
    except TerminologyServiceError as exc:
        return _error(exc.code, str(exc))


def build_server() -> MCPServer:
    server = MCPServer(
        name=SERVER_NAME,
        instructions=(
            "Swedish clinical terminology (ICD-10-SE diagnoses, KVÅ procedures). "
            "You can look codes up and file a mapping proposal for a human to "
            "review. You cannot accept, reject or correct a proposal: a mapping "
            "is only valid once a person has decided, and there is deliberately "
            "no tool that lets a model make that decision. After calling "
            "propose_mapping, tell the user to open the validator page."
        ),
    )

    # ----------------------------------------------------------------- 2.1
    @server.tool(
        description=(
            "Lists the code systems and versions currently loaded, with how many "
            "concepts each holds, split into codes you can assign, headings "
            "(chapters, sections and groups, which are never valid mappings) and "
            "placeholder codes. Also reports whether the publisher's descriptions "
            "are searchable and which embedding space is available. Call this "
            "first if you are unsure what to pass as `system` or `version`."
        )
    )
    def list_terminologies() -> dict[str, Any]:
        with _tool("list_terminologies"):
            settings = get_settings()
            with session_scope() as session:
                loaded = list_loaded(session)
            return {
                "ok": True,
                "default_version": settings.default_terminology_version,
                "descriptions_indexed": settings.index_descriptions,
                "terminologies": [
                    {
                        "system": item.system,
                        "version": item.version,
                        "status": "loaded",
                        "concepts": {
                            "total": item.total,
                            "assignable": item.assignable,
                            "headings": item.headings,
                            "placeholders": item.placeholders,
                            "with_description": item.with_description,
                        },
                        "embedding_spaces": item.embedding_spaces,
                    }
                    for item in loaded
                ]
                + [
                    {
                        "system": "snomed",
                        "version": None,
                        "status": "licence_required",
                        "message": (
                            "SNOMED CT content is not shipped with this repository. "
                            "It requires an affiliate licence; see LICENSING.md."
                        ),
                    }
                ],
            }

    # ----------------------------------------------------------------- 2.2
    @server.tool(
        description=(
            "Searches a code system for concepts matching a clinical phrase, "
            "using both word matching and vector similarity. Returns candidates "
            "with their scores and which field matched (term, synonym, "
            "description or vector). "
            "This is a lookup: no language model is called, nothing is written, "
            "and the scores measure how the text matched, not whether the code is "
            "clinically correct. To file something for a human to review, use "
            "propose_mapping."
        )
    )
    def search_concepts(
        system: str, query: str, version: str | None = None, limit: int | None = None
    ) -> dict[str, Any]:
        with _tool("search_concepts", system=system):

            def run(session: Session, settings: Settings) -> dict[str, Any]:
                name = resolve_system(system)
                resolved = resolve_version(session, name, version, settings)
                found = retrieve(
                    session,
                    query=normalize(query).normalized,
                    system=name,
                    version=resolved,
                    settings=settings,
                    embedding_provider=build_embedding_provider(settings),
                    limit=_clamp(limit),
                )
                return {
                    "ok": True,
                    "system": name,
                    "version": resolved,
                    "query": query,
                    "normalized_query": normalize(query).normalized,
                    "count": len(found.candidates),
                    "candidates": [candidate_payload(c) for c in found.candidates],
                    "evidence_note": "retrieval scores only; not a ranking of correctness",
                }

            return _guarded(run)

    # ----------------------------------------------------------------- 2.3
    @server.tool(
        description=(
            "Returns everything known about one code: its preferred term, "
            "synonyms, the publisher's description, where it sits in the "
            "hierarchy (each ancestor with its title), its direct children, and "
            "the flags that decide how it may be used -- whether it is assignable "
            "at all, whether the publisher forbids it as a primary diagnosis, and "
            "whether it is a reserved placeholder. Use this after search_concepts "
            "to check a specific code before proposing it."
        )
    )
    def get_concept(system: str, code: str, version: str | None = None) -> dict[str, Any]:
        with _tool("get_concept", system=system):

            def run(session: Session, settings: Settings) -> dict[str, Any]:
                name = resolve_system(system)
                resolved = resolve_version(session, name, version, settings)
                detail = concept_detail(session, system=name, version=resolved, code=code)
                return {
                    "ok": True,
                    "system": detail.system,
                    "version": detail.version,
                    "code": detail.code,
                    "preferred_term": detail.preferred_term,
                    "synonyms": detail.synonyms,
                    "description": detail.description,
                    "hierarchy": detail.hierarchy,
                    "parent_code": detail.parent_code,
                    "parent_source": detail.parent_source,
                    "chapter": detail.chapter,
                    "children": detail.children,
                    "children_truncated": detail.children_truncated,
                    "flags": {
                        "assignable": detail.assignable,
                        "is_leaf": detail.is_leaf,
                        "not_primary_diagnosis": detail.not_primary_diagnosis,
                        "placeholder": detail.placeholder,
                    },
                    "hierarchy_note": (
                        "parent_source 'derived' means the link was read from the "
                        "code's own prefix structure, not stated by the publisher"
                    ),
                }

            return _guarded(run)

    # ----------------------------------------------------------------- 2.4
    @server.tool(
        description=(
            "Like search_concepts, but also reports what the retrieval gate would "
            "conclude about the evidence: whether it is strong enough to be worth "
            "ranking at all, and the values behind that judgement. Use it to find "
            "out whether a phrase has any real support in the code system before "
            "proposing it. No language model is called and nothing is written."
        )
    )
    def find_similar_concepts(
        system: str, term: str, version: str | None = None, limit: int | None = None
    ) -> dict[str, Any]:
        with _tool("find_similar_concepts", system=system):

            def run(session: Session, settings: Settings) -> dict[str, Any]:
                name = resolve_system(system)
                resolved = resolve_version(session, name, version, settings)
                embeddings = build_embedding_provider(settings)
                normalized = normalize(term)
                found = retrieve(
                    session,
                    query=normalized.normalized,
                    system=name,
                    version=resolved,
                    settings=settings,
                    embedding_provider=embeddings,
                    limit=_clamp(limit),
                )
                gate = evaluate_gate(
                    found.candidates,
                    settings=settings,
                    embedding_provider_id=embeddings.provider_id,
                    embedding_model_id=embeddings.model_id,
                    query=normalized.normalized,
                    tokens=normalized.tokens,
                )
                return {
                    "ok": True,
                    "system": name,
                    "version": resolved,
                    "term": term,
                    "count": len(found.candidates),
                    "candidates": [candidate_payload(c) for c in found.candidates],
                    "gate": {
                        "fired": gate.fired,
                        "reason": gate.reason,
                        "values": gate.values,
                        "note": (
                            "gate_fired true means the evidence is too weak to rank; "
                            "propose_mapping would return no_good_match"
                        ),
                    },
                    "evidence_note": "retrieval scores only; not a ranking of correctness",
                }

            return _guarded(run)

    # ----------------------------------------------------------------- 2.5
    @server.tool(
        description=(
            "Checks whether a code may be used as a mapping in a given code "
            "system and version, using exactly the rules the validator interface "
            "applies. Reports whether the format is valid, whether the code "
            "exists in that release, whether it is assignable or a heading, "
            "whether it is a placeholder, and whether the publisher forbids it as "
            "a primary diagnosis -- with the message a human reviewer would see. "
            "Nothing is written."
        )
    )
    def validate_code(system: str, code: str, version: str | None = None) -> dict[str, Any]:
        with _tool("validate_code", system=system):

            def run(session: Session, settings: Settings) -> dict[str, Any]:
                name = resolve_system(system)
                resolved = resolve_version(session, name, version, settings)
                verdict = inspect_code(session, system=name, version=resolved, code=code)
                return {
                    "ok": True,
                    "system": verdict.system,
                    "version": verdict.version,
                    "code": verdict.code,
                    "verdict": verdict.verdict,
                    "format_valid": verdict.format_valid,
                    "exists": verdict.exists,
                    "assignable": verdict.assignable,
                    "not_primary_diagnosis": verdict.not_primary_diagnosis,
                    "placeholder": verdict.placeholder,
                    "usable_as_final_code": verdict.usable_as_final_code,
                    "message": verdict.message,
                }

            return _guarded(run)

    # ----------------------------------------------------------------- 2.6
    @server.tool(
        description=(
            "Runs a clinical phrase through the full mapping pipeline and files "
            "the result as a proposal: it searches, checks the evidence, asks the "
            "ranking model when there is enough evidence to be worth it, and "
            "writes an auditable record. "
            "Creates an auditable proposal. This is not a validated mapping; a "
            "human must review it in the validator interface. There is "
            "deliberately no tool to accept or reject proposals. "
            "After calling this, tell the user the proposal id and ask them to "
            "open the validator page. Use `requested_by` to say which client you "
            "are; it is recorded on the proposal and is not a reviewer identity."
        )
    )
    def propose_mapping(
        text: str,
        system: str,
        version: str | None = None,
        requested_by: str | None = None,
    ) -> dict[str, Any]:
        with _tool("propose_mapping", system=system) as trace_id:
            settings = get_settings()
            try:
                with session_scope() as session:
                    name = resolve_system(system)
                    resolved = resolve_version(session, name, version, settings)
                    outcome = map_term(
                        session,
                        text=text,
                        target_system=name,
                        version=resolved,
                        trace_id=trace_id,
                        settings=settings,
                        embedding_provider=build_embedding_provider(settings),
                        llm_provider=build_llm_provider(settings),
                        prompt=load_prompt("rerank_v1"),
                        origin="mcp",
                        requested_by=requested_by,
                    )
                    proposal = outcome.proposal
                    ranked = (proposal.rerank or {}).get("ranked", [])
                    terms = {c["code"]: c.get("preferred_term") for c in proposal.candidates}
                    is_fake = proposal.provider_kind == "fake"
                    return {
                        "ok": True,
                        "proposal_id": str(proposal.id),
                        "trace_id": proposal.trace_id,
                        "status": proposal.status,
                        "system": proposal.target_system,
                        "version": proposal.terminology_version,
                        "input_text": proposal.input_text,
                        "suggested_code": proposal.suggested_code,
                        "suggested_term": terms.get(proposal.suggested_code or ""),
                        "model_confidence": None if is_fake else proposal.model_confidence,
                        "provider_kind": proposal.provider_kind,
                        "provider": f"{proposal.llm_provider}/{proposal.llm_model}",
                        "gate": {
                            "id": proposal.gate_id,
                            "version": proposal.gate_version,
                            "fired": proposal.gate_fired,
                            "reason": (proposal.gate_values or {}).get("reason"),
                        },
                        "top_candidates": [
                            {
                                "code": entry["code"],
                                "preferred_term": terms.get(entry["code"]),
                                "reason": entry["reason"],
                                "model_confidence": None if is_fake else entry["confidence"],
                            }
                            for entry in ranked[:5]
                        ],
                        "next_step": (
                            "This is a proposal, not a mapping. Ask a human to review it "
                            "in the validator interface; no tool here can accept it."
                        ),
                        "validator_url": VALIDATOR_URL_PATTERN.format(
                            proposal_id=proposal.id
                        ),
                        "test_mode_note": (
                            "The ranking provider is a deterministic stand-in, so no "
                            "confidence is reported and the ordering means nothing "
                            "clinically."
                        )
                        if is_fake
                        else None,
                    }
            except TerminologyServiceError as exc:
                return _error(exc.code, str(exc))

    # ----------------------------------------------------------------- 2.7
    @server.tool(
        description=(
            "Looks up a proposal you filed earlier and reports whether a human "
            "has decided on it yet. If they have, returns the decision, who made "
            "it, when, and the validated mapping. Read only -- this cannot make "
            "or change a decision."
        )
    )
    def get_proposal_status(proposal_id: str) -> dict[str, Any]:
        with _tool("get_proposal_status"):
            import uuid

            try:
                key = uuid.UUID(proposal_id)
            except ValueError:
                return _error("invalid_argument", f"{proposal_id!r} is not a valid proposal id")

            with session_scope() as session:
                proposal = get_proposal(session, key)
                if proposal is None:
                    return _error("not_found", f"no proposal with id {proposal_id}")
                decision = get_decision_for(session, key)
                terms = {c["code"]: c.get("preferred_term") for c in proposal.candidates}
                is_fake = proposal.provider_kind == "fake"
                return {
                    "ok": True,
                    "proposal_id": str(proposal.id),
                    "status": proposal.status,
                    "system": proposal.target_system,
                    "version": proposal.terminology_version,
                    "input_text": proposal.input_text,
                    "suggested_code": proposal.suggested_code,
                    "suggested_term": terms.get(proposal.suggested_code or ""),
                    "model_confidence": None if is_fake else proposal.model_confidence,
                    "origin": proposal.origin,
                    "requested_by": proposal.requested_by,
                    "decided": decision is not None,
                    "decision": None
                    if decision is None
                    else {
                        "decision": decision.decision,
                        "final_code": decision.final_code,
                        "validator_id": decision.validator_id,
                        "validator_note": decision.validator_note,
                        "decided_at": decision.created_at.isoformat(),
                    },
                    "validated_mapping": None
                    if decision is None or decision.final_code is None
                    else {
                        "system": proposal.target_system,
                        "version": proposal.terminology_version,
                        "code": decision.final_code,
                        "decision_id": str(decision.id),
                    },
                    "note": "still waiting for a human decision"
                    if decision is None
                    else "decided; this record cannot be changed",
                }

    return server


__all__ = ["KNOWN_SYSTEMS", "SERVER_NAME", "build_server"]

"""The MCP server: tool contract, behaviour, and the decision boundary.

Everything runs through the SDK's in-memory client against a real database, so
the tools are exercised exactly as a client would call them -- no subprocess, no
HTTP.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from sqlalchemy.orm import Session

from mcp_server.server import build_server

pytestmark = pytest.mark.requires_db

SNAPSHOT = Path(__file__).parent / "snapshots" / "mcp_tools.json"


@pytest.fixture
def call(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Call a tool in-memory, with the server bound to the test transaction."""
    from contextlib import contextmanager

    import mcp_server.server as server_module

    @contextmanager
    def scope():  # type: ignore[no-untyped-def]
        yield db_session

    monkeypatch.setattr(server_module, "session_scope", scope)

    async def _call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        async with Client(build_server()) as client:
            result = await client.call_tool(name, arguments)
            return json.loads(result.content[0].text)

    return _call


# --------------------------------------------------------------- the contract


@pytest.mark.anyio
async def test_tool_registry_matches_the_snapshot() -> None:
    """Tool names, arguments and descriptions are the contract an agent reads.

    A description is a prompt: changing it changes how a model behaves. This
    fails when any of them drifts, so the change has to be deliberate.
    """
    server = build_server()
    tools = await server.list_tools()
    actual = {
        tool.name: {
            "description": tool.description,
            "arguments": sorted((tool.input_schema or {}).get("properties", {})),
            "required": sorted((tool.input_schema or {}).get("required", [])),
        }
        for tool in tools
    }
    if not SNAPSHOT.exists():  # pragma: no cover - first run writes the snapshot
        SNAPSHOT.write_text(json.dumps(actual, indent=2, ensure_ascii=False) + "\n", "utf-8")
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert actual == expected, (
        "The MCP tool contract changed. If that was deliberate, update "
        f"{SNAPSHOT.relative_to(SNAPSHOT.parent.parent.parent)}."
    )


# ------------------------------------------------- the decision boundary (§4)


def test_the_package_never_imports_the_decision_writer() -> None:
    """No code path in mcp_server/ can insert into `decisions`."""
    forbidden = ("record_decision", "insert_decision", "DecisionRow", "app.validation.decisions")
    for path in Path("mcp_server").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for name in forbidden:
            assert name not in source, f"{path} references {name}"


@pytest.mark.anyio
async def test_no_tool_offers_to_decide() -> None:
    """A tool that sounded like it could accept a code would be a lie about
    where authority sits, even if it did nothing."""
    server = build_server()
    tools = await server.list_tools()
    names = {tool.name for tool in tools}
    for verb in ("accept", "reject", "correct", "decide", "record_decision", "validate_mapping"):
        assert not any(verb in name for name in names), verb

    # Descriptions may *mention* the absence; none may offer the capability.
    for tool in tools:
        text = (tool.description or "").lower()
        for claim in (
            "you can accept",
            "accepts the proposal",
            "records the decision",
            "approve the mapping",
        ):
            assert claim not in text, f"{tool.name}: {claim}"


@pytest.mark.anyio
async def test_propose_mapping_says_it_is_not_a_mapping() -> None:
    server = build_server()
    tools = {tool.name: tool.description or "" for tool in await server.list_tools()}
    description = tools["propose_mapping"]
    assert "not a validated mapping" in description
    assert "deliberately no tool to accept or reject" in description


# ------------------------------------------------------------ list & search


@pytest.mark.anyio
async def test_list_terminologies_reports_counts_and_snomed(
    call: Any, icd10se_embedded: str
) -> None:
    body = await call("list_terminologies", {})
    assert body["ok"] is True
    loaded = {t["system"]: t for t in body["terminologies"] if t["status"] == "loaded"}
    assert "icd10se" in loaded
    counts = loaded["icd10se"]["concepts"]
    assert counts["total"] == counts["assignable"] + counts["headings"]
    assert counts["headings"] > 0

    snomed = next(t for t in body["terminologies"] if t["system"] == "snomed")
    assert snomed["status"] == "licence_required"


@pytest.mark.anyio
async def test_search_concepts_returns_scored_candidates(call: Any, icd10se_embedded: str) -> None:
    body = await call(
        "search_concepts",
        {"system": "icd10se", "query": "högt blodtryck", "version": icd10se_embedded},
    )
    assert body["ok"] is True
    assert body["version"] == icd10se_embedded
    codes = [c["code"] for c in body["candidates"]]
    assert "I10" in codes
    top = next(c for c in body["candidates"] if c["code"] == "I10")
    assert top["matched_field"] in {"title", "synonym"}
    assert set(top["scores"]) == {"lexical", "vector", "ts_rank", "strict_similarity", "rrf"}
    assert body["evidence_note"] == "retrieval scores only; not a ranking of correctness"


@pytest.mark.anyio
async def test_search_never_returns_a_heading(call: Any, kva_embedded: str) -> None:
    body = await call(
        "search_concepts",
        {"system": "kva", "query": "biopsi av tonsill", "version": kva_embedded},
    )
    codes = [c["code"] for c in body["candidates"]]
    assert "EMA10" in codes
    assert "EMA" not in codes


@pytest.mark.anyio
async def test_limit_is_clamped(call: Any, icd10se_embedded: str) -> None:
    body = await call(
        "search_concepts",
        {"system": "icd10se", "query": "hypertoni", "version": icd10se_embedded, "limit": 999},
    )
    assert len(body["candidates"]) <= 50


# ---------------------------------------------------------------- get_concept


@pytest.mark.anyio
async def test_get_concept_returns_hierarchy_and_flags(call: Any, icd10se_loaded: str) -> None:
    body = await call(
        "get_concept", {"system": "icd10se", "code": "I11.0", "version": icd10se_loaded}
    )
    assert body["ok"] is True
    assert [n["code"] for n in body["hierarchy"]] == ["I00-I99", "I10-I15", "I11"]
    assert all(n["title"] for n in body["hierarchy"])
    assert body["parent_source"] == "column"
    assert body["flags"] == {
        "assignable": True,
        "is_leaf": True,
        "not_primary_diagnosis": False,
        "placeholder": False,
    }


@pytest.mark.anyio
async def test_get_concept_reports_not_primary_diagnosis(call: Any, icd10se_loaded: str) -> None:
    body = await call(
        "get_concept", {"system": "icd10se", "code": "I32", "version": icd10se_loaded}
    )
    assert body["flags"]["not_primary_diagnosis"] is True


@pytest.mark.anyio
async def test_get_concept_lists_children(call: Any, icd10se_loaded: str) -> None:
    body = await call(
        "get_concept", {"system": "icd10se", "code": "I11", "version": icd10se_loaded}
    )
    assert {c["code"] for c in body["children"]} == {"I11.0", "I11.9"}
    assert body["children_truncated"] is False


@pytest.mark.anyio
async def test_get_concept_derived_hierarchy_is_labelled(call: Any, kva_loaded: str) -> None:
    body = await call("get_concept", {"system": "kva", "code": "AF015", "version": kva_loaded})
    assert body["parent_source"] == "derived"
    assert "derived" in body["hierarchy_note"]


@pytest.mark.anyio
async def test_get_concept_not_found(call: Any, icd10se_loaded: str) -> None:
    body = await call(
        "get_concept", {"system": "icd10se", "code": "Z99.9", "version": icd10se_loaded}
    )
    assert body["ok"] is False
    assert body["error"]["code"] == "not_found"


# --------------------------------------------------------- find_similar/gate


@pytest.mark.anyio
async def test_find_similar_reports_the_gate(call: Any, icd10se_embedded: str) -> None:
    body = await call(
        "find_similar_concepts",
        {"system": "icd10se", "term": "banan", "version": icd10se_embedded},
    )
    assert body["gate"]["fired"] is True
    assert body["gate"]["values"]["best_ts_rank"] == 0.0
    assert "no_good_match" in body["gate"]["note"]


@pytest.mark.anyio
async def test_find_similar_passes_a_real_term(call: Any, icd10se_embedded: str) -> None:
    body = await call(
        "find_similar_concepts",
        {"system": "icd10se", "term": "högt blodtryck", "version": icd10se_embedded},
    )
    assert body["gate"]["fired"] is False


# --------------------------------------------------------------- validate_code


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("code", "verdict", "usable"),
    [
        ("I10", "ok", True),
        ("I10-I15", "heading", False),
        ("Z99.9", "not_present", False),
        ("NOTACODE", "bad_format", False),
    ],
)
async def test_validate_code_verdicts(
    call: Any, icd10se_loaded: str, code: str, verdict: str, usable: bool
) -> None:
    body = await call(
        "validate_code", {"system": "icd10se", "code": code, "version": icd10se_loaded}
    )
    assert body["verdict"] == verdict
    assert body["usable_as_final_code"] is usable
    if verdict != "ok":
        assert body["message"]


# ------------------------------------------------------------------- errors


@pytest.mark.anyio
@pytest.mark.parametrize(
    "tool,args",
    [
        ("search_concepts", {"system": "snomed", "query": "x"}),
        ("get_concept", {"system": "snomed", "code": "38341003"}),
        ("find_similar_concepts", {"system": "snomed", "term": "x"}),
        ("validate_code", {"system": "snomed", "code": "38341003"}),
        ("propose_mapping", {"system": "snomed", "text": "x"}),
    ],
)
async def test_snomed_is_licence_required(call: Any, tool: str, args: dict[str, Any]) -> None:
    body = await call(tool, args)
    assert body["ok"] is False
    assert body["error"]["code"] == "licence_required"
    assert "LICENSING.md" in body["error"]["message"]


@pytest.mark.anyio
async def test_unknown_system_is_invalid_argument(call: Any) -> None:
    body = await call("search_concepts", {"system": "icd11", "query": "x"})
    assert body["error"]["code"] == "invalid_argument"


@pytest.mark.anyio
async def test_unloaded_version_is_not_loaded(call: Any, icd10se_loaded: str) -> None:
    body = await call(
        "search_concepts", {"system": "icd10se", "query": "x", "version": "1999-nope"}
    )
    assert body["error"]["code"] == "not_loaded"
    assert "load_terminology" in body["error"]["message"]


# -------------------------------------------------------------- propose_mapping


@pytest.mark.anyio
async def test_propose_mapping_records_origin_mcp(
    call: Any, db_session: Session, icd10se_embedded: str
) -> None:
    body = await call(
        "propose_mapping",
        {
            "text": "högt blodtryck",
            "system": "icd10se",
            "version": icd10se_embedded,
            "requested_by": "test-client",
        },
    )
    assert body["ok"] is True
    assert body["status"] == "pending"
    assert body["suggested_code"] == "I10"
    # The stand-in has no confidence to report, on this surface as on the page.
    assert body["model_confidence"] is None
    assert body["provider_kind"] == "fake"
    assert (
        "not a validated mapping" in body["next_step"]
        or "proposal, not a mapping" in body["next_step"]
    )

    import uuid

    from app.audit.writer import get_proposal

    row = get_proposal(db_session, uuid.UUID(body["proposal_id"]))
    assert row is not None
    assert row.origin == "mcp"
    assert row.requested_by == "test-client"


@pytest.mark.anyio
async def test_propose_mapping_does_not_call_the_model_when_the_gate_fires(
    call: Any, icd10se_embedded: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gate exists so a model is never asked to rank noise. If the tool
    called it anyway, the cost and the hallucination risk would both return."""
    import app.llm.fake as fake

    calls: list[str] = []
    original = fake.FakeLLMProvider.rerank

    def counting(self, query, candidates, prompt):  # type: ignore[no-untyped-def]
        calls.append(query)
        return original(self, query, candidates, prompt)

    monkeypatch.setattr(fake.FakeLLMProvider, "rerank", counting)

    body = await call(
        "propose_mapping",
        {"text": "banan", "system": "icd10se", "version": icd10se_embedded},
    )
    assert body["status"] == "no_good_match"
    assert body["suggested_code"] is None
    assert body["gate"]["fired"] is True
    assert calls == []  # the model was never asked

    await call(
        "propose_mapping",
        {"text": "högt blodtryck", "system": "icd10se", "version": icd10se_embedded},
    )
    assert calls == ["högt blodtryck"]  # and it is asked when there is evidence


@pytest.mark.anyio
async def test_get_proposal_status_before_and_after_a_decision(
    call: Any, db_session: Session, icd10se_embedded: str
) -> None:
    """An agent can watch for its human's verdict, and only watch."""
    import uuid

    from app.validation.decisions import record_decision

    filed = await call(
        "propose_mapping",
        {"text": "högt blodtryck", "system": "icd10se", "version": icd10se_embedded},
    )
    proposal_id = filed["proposal_id"]

    waiting = await call("get_proposal_status", {"proposal_id": proposal_id})
    assert waiting["decided"] is False
    assert waiting["decision"] is None
    assert waiting["validated_mapping"] is None
    assert waiting["origin"] == "mcp"

    # A human decides through the ordinary path -- never through MCP.
    record_decision(
        db_session,
        proposal_id=uuid.UUID(proposal_id),
        decision="accept",
        validator_id="a-human",
    )

    decided = await call("get_proposal_status", {"proposal_id": proposal_id})
    assert decided["decided"] is True
    assert decided["decision"]["decision"] == "accept"
    assert decided["decision"]["validator_id"] == "a-human"
    assert decided["validated_mapping"]["code"] == "I10"
    assert "cannot be changed" in decided["note"]


@pytest.mark.anyio
async def test_get_proposal_status_errors(call: Any) -> None:
    assert (await call("get_proposal_status", {"proposal_id": "nonsense"}))["error"][
        "code"
    ] == "invalid_argument"
    import uuid

    body = await call("get_proposal_status", {"proposal_id": str(uuid.uuid4())})
    assert body["error"]["code"] == "not_found"


# ---------------------------------------------------------------- stdio


@pytest.mark.skipif(
    __import__("os").environ.get("CI") == "true",
    reason="subprocess stdio handshake is not run in CI; the in-memory client "
    "covers the same tool surface without process-startup flakiness",
)
def test_stdio_transport_completes_the_handshake() -> None:
    """Launch the real entry point and complete an MCP initialize handshake.

    This is the only test that exercises the transport a client actually uses,
    so it is worth the subprocess. It also proves logging goes to stderr: a
    single stray line on stdout would corrupt the framing and fail the parse.
    """
    import subprocess
    import sys

    request = (
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "pytest", "version": "0"},
                },
            }
        )
        + "\n"
    )
    proc = subprocess.run(
        [sys.executable, "-m", "mcp_server", "--transport", "stdio"],
        input=request,
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(Path(__file__).resolve().parent.parent),
    )
    first_line = proc.stdout.strip().splitlines()[0]
    reply = json.loads(first_line)
    assert reply["id"] == 1
    assert reply["result"]["serverInfo"]["name"] == "terminology-mcp"
    assert "capabilities" in reply["result"]

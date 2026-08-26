"""HTTP surface: mapping, fetching a proposal, and recording a decision."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

pytestmark = pytest.mark.requires_db


def _map(client: TestClient, text: str = "högt blodtryck", **kwargs: Any) -> dict[str, Any]:
    payload = {"text": text, "target_system": "icd10se", "version": "2026-sample"}
    payload.update(kwargs)
    response = client.post("/map", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------- /map


def test_map_returns_a_pending_proposal(client: TestClient, icd10se_embedded: str) -> None:
    body = _map(client)
    assert body["status"] == "pending"
    assert body["suggested_code"] == "I10"
    assert body["suggested_term"].startswith("Essentiell hypertoni")
    assert body["decision"] is None
    assert body["validated_mapping"] is None


def test_map_exposes_the_full_evidence(client: TestClient, icd10se_embedded: str) -> None:
    """A reviewer must be able to see what the model saw."""
    body = _map(client)
    assert body["candidates"]
    assert body["ranked"]
    assert body["ranked"][0]["code"] == body["suggested_code"]
    assert "model_confidence" in body["ranked"][0]
    assert body["prompt_id"] == "rerank_v1"
    assert len(body["prompt_hash"]) == 64
    assert body["llm_provider"] == "fake"
    assert body["embedding_provider"] == "fake"


def test_confidence_is_labelled_as_the_models_own(
    client: TestClient, icd10se_embedded: str
) -> None:
    """Never "probability" -- the number is a self-report."""
    body = _map(client)
    assert "model_confidence" in body
    assert "probability" not in str(body).lower()


def test_trace_id_is_returned_and_recorded(
    client: TestClient, icd10se_embedded: str
) -> None:
    response = client.post(
        "/map",
        json={"text": "astma", "target_system": "icd10se", "version": "2026-sample"},
        headers={"X-Trace-Id": "caller-supplied-trace"},
    )
    assert response.status_code == 201
    assert response.headers["X-Trace-Id"] == "caller-supplied-trace"
    assert response.json()["trace_id"] == "caller-supplied-trace"


def test_map_rejects_empty_text(client: TestClient, icd10se_embedded: str) -> None:
    response = client.post(
        "/map", json={"text": "", "target_system": "icd10se", "version": "2026-sample"}
    )
    assert response.status_code == 422


def test_map_rejects_an_unknown_system(client: TestClient, icd10se_embedded: str) -> None:
    response = client.post("/map", json={"text": "astma", "target_system": "icd11"})
    assert response.status_code == 422


def test_map_against_snomed_is_not_implemented(
    client: TestClient, icd10se_embedded: str
) -> None:
    response = client.post("/map", json={"text": "astma", "target_system": "snomed"})
    assert response.status_code == 501
    assert "LICENSING.md" in response.json()["detail"]


def test_map_against_an_unloaded_version_conflicts(
    client: TestClient, icd10se_embedded: str
) -> None:
    response = client.post(
        "/map",
        json={"text": "astma", "target_system": "icd10se", "version": "1999-nope"},
    )
    assert response.status_code == 409
    assert "load_terminology" in response.json()["detail"]


# -------------------------------------------------------- /proposals/{id}


def test_proposal_can_be_fetched(client: TestClient, icd10se_embedded: str) -> None:
    created = _map(client)
    fetched = client.get(f"/proposals/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == created["id"]
    assert fetched.json()["suggested_code"] == created["suggested_code"]


def test_unknown_proposal_is_404(client: TestClient, icd10se_embedded: str) -> None:
    assert client.get(f"/proposals/{uuid.uuid4()}").status_code == 404


# ---------------------------------------------------------------- /decisions


def test_accept_records_the_suggested_code(
    client: TestClient, icd10se_embedded: str
) -> None:
    proposal = _map(client)
    response = client.post(
        "/decisions",
        json={
            "proposal_id": proposal["id"],
            "decision": "accept",
            "validator_id": "coder-1",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["decision"]["decision"] == "accept"
    assert body["decision"]["final_code"] == proposal["suggested_code"]
    assert body["decision"]["validator_id"] == "coder-1"


def test_reject_records_no_code(client: TestClient, icd10se_embedded: str) -> None:
    proposal = _map(client)
    body = client.post(
        "/decisions",
        json={
            "proposal_id": proposal["id"],
            "decision": "reject",
            "validator_id": "coder-1",
            "validator_note": "för ospecifikt",
        },
    ).json()
    assert body["decision"]["final_code"] is None
    assert body["decision"]["validator_note"] == "för ospecifikt"
    assert body["validated_mapping"] is None


def test_correct_records_the_humans_code(
    client: TestClient, icd10se_embedded: str
) -> None:
    proposal = _map(client)
    body = client.post(
        "/decisions",
        json={
            "proposal_id": proposal["id"],
            "decision": "correct",
            "final_code": "I15.9",
            "validator_id": "coder-1",
        },
    ).json()
    assert body["decision"]["decision"] == "correct"
    assert body["decision"]["final_code"] == "I15.9"
    assert body["decision"]["final_code"] != proposal["suggested_code"]


def test_a_validated_mapping_is_four_fields(
    client: TestClient, icd10se_embedded: str
) -> None:
    """The output contract: system, version, code, decision_id -- nothing more.

    Free text stays local; only this is fit to cross a boundary.
    """
    proposal = _map(client)
    body = client.post(
        "/decisions",
        json={"proposal_id": proposal["id"], "decision": "accept", "validator_id": "c"},
    ).json()

    mapping = body["validated_mapping"]
    assert set(mapping) == {"system", "version", "code", "decision_id"}
    assert mapping["system"] == "icd10se"
    assert mapping["version"] == "2026-sample"
    assert mapping["code"] == proposal["suggested_code"]
    assert mapping["decision_id"] == body["decision"]["id"]


def test_a_second_decision_is_refused(client: TestClient, icd10se_embedded: str) -> None:
    """Principle 1 and 3 together: the human decides once, and it stands."""
    proposal = _map(client)
    first = client.post(
        "/decisions",
        json={"proposal_id": proposal["id"], "decision": "accept", "validator_id": "a"},
    )
    assert first.status_code == 201

    second = client.post(
        "/decisions",
        json={"proposal_id": proposal["id"], "decision": "reject", "validator_id": "b"},
    )
    assert second.status_code == 409
    assert "already been decided" in second.json()["detail"]

    # The original decision is untouched.
    fetched = client.get(f"/proposals/{proposal['id']}").json()
    assert fetched["decision"]["decision"] == "accept"
    assert fetched["decision"]["validator_id"] == "a"


def test_correct_rejects_a_malformed_code(
    client: TestClient, icd10se_embedded: str
) -> None:
    proposal = _map(client)
    response = client.post(
        "/decisions",
        json={
            "proposal_id": proposal["id"],
            "decision": "correct",
            "final_code": "NOT-A-CODE",
            "validator_id": "coder-1",
        },
    )
    assert response.status_code == 422
    assert "not a valid icd10se code format" in response.json()["detail"]


def test_correct_rejects_a_code_absent_from_the_loaded_version(
    client: TestClient, icd10se_embedded: str
) -> None:
    """Well-formed but not in this release: still an invalid mapping, and this
    is the last point at which anything can catch it."""
    proposal = _map(client)
    response = client.post(
        "/decisions",
        json={
            "proposal_id": proposal["id"],
            "decision": "correct",
            "final_code": "Z99.9",
            "validator_id": "coder-1",
        },
    )
    assert response.status_code == 422
    assert "does not exist in version" in response.json()["detail"]


def test_correct_without_a_code_is_refused(
    client: TestClient, icd10se_embedded: str
) -> None:
    proposal = _map(client)
    response = client.post(
        "/decisions",
        json={"proposal_id": proposal["id"], "decision": "correct", "validator_id": "c"},
    )
    assert response.status_code == 422
    assert "must supply the correct code" in response.json()["detail"]


def test_reject_with_a_code_is_refused(client: TestClient, icd10se_embedded: str) -> None:
    proposal = _map(client)
    response = client.post(
        "/decisions",
        json={
            "proposal_id": proposal["id"],
            "decision": "reject",
            "final_code": "I10",
            "validator_id": "c",
        },
    )
    assert response.status_code == 422
    assert "reject records no code" in response.json()["detail"]


def test_accepting_a_different_code_must_be_called_a_correction(
    client: TestClient, icd10se_embedded: str
) -> None:
    """The two mean different things when the trail is audited."""
    proposal = _map(client)
    response = client.post(
        "/decisions",
        json={
            "proposal_id": proposal["id"],
            "decision": "accept",
            "final_code": "I15.9",
            "validator_id": "c",
        },
    )
    assert response.status_code == 422
    assert "use 'correct'" in response.json()["detail"]


def test_decision_on_an_unknown_proposal_is_404(
    client: TestClient, icd10se_embedded: str
) -> None:
    response = client.post(
        "/decisions",
        json={"proposal_id": str(uuid.uuid4()), "decision": "reject", "validator_id": "c"},
    )
    assert response.status_code == 404


def test_decision_requires_a_validator_id(
    client: TestClient, icd10se_embedded: str
) -> None:
    proposal = _map(client)
    response = client.post(
        "/decisions", json={"proposal_id": proposal["id"], "decision": "reject"}
    )
    assert response.status_code == 422


# ---------------------------------------------------------------- other


def test_health_reports_the_running_prompt_hash(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert len(body["prompt_hash"]) == 64


def test_validator_page_renders(client: TestClient, icd10se_embedded: str) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Beslutsstöd, inte automatisk kodning" in body
    # The loaded version is offered in the selector.
    assert "2026-sample" in body


def test_validator_page_warns_when_nothing_is_loaded(
    client: TestClient, db_session: Session
) -> None:
    """Clears concepts inside the test transaction rather than assuming the
    database happens to be empty -- the assertion is about the page, not about
    whatever a previous run left behind."""
    import sqlalchemy as sa

    from app.db.models import ConceptRow

    db_session.execute(sa.delete(ConceptRow))
    db_session.flush()

    body = client.get("/").text
    assert "Ingen terminologi är laddad" in body

"""Principle 3: audit rows can be inserted and never changed or removed.

The guarantee is asserted at the level it is enforced -- raw SQL against the
database, bypassing every application-layer safeguard. That is the whole point:
application code can be bypassed by a future maintainer with psql; a trigger
cannot.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
import sqlalchemy as sa
from sqlalchemy import Connection
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.requires_db


def _insert_proposal(connection: Connection) -> uuid.UUID:
    proposal_id = uuid.uuid4()
    connection.execute(
        sa.text(
            """
            INSERT INTO proposals (
                id, trace_id, input_text, normalized_text, target_system,
                terminology_version, candidates, rerank, suggested_code,
                model_confidence, llm_provider, llm_model, prompt_id, prompt_hash,
                embedding_provider, embedding_model, latency_ms_retrieval,
                latency_ms_rerank, status
            ) VALUES (
                :id, :trace_id, 'högt blodtryck', 'högt blodtryck', 'icd10se',
                '2026', '[]'::jsonb, NULL, 'I10',
                0.9, 'fake', 'fake-rerank-v1', 'rerank_v1', 'deadbeef',
                'fake', 'fake-hash-v1', 1,
                1, 'pending'
            )
            """
        ),
        {"id": proposal_id, "trace_id": uuid.uuid4().hex},
    )
    return proposal_id


def _insert_decision(connection: Connection, proposal_id: uuid.UUID) -> None:
    connection.execute(
        sa.text(
            """
            INSERT INTO decisions (
                id, proposal_id, decision, final_code, validator_note, validator_id
            ) VALUES (:id, :proposal_id, 'accept', 'I10', NULL, 'tester')
            """
        ),
        {"id": uuid.uuid4(), "proposal_id": proposal_id},
    )


def _expect_db_error(connection: Connection, action: Callable[[], object]) -> str:
    """Assert `action` is refused by the database; return the error text.

    The failing statement runs inside a SAVEPOINT so the surrounding test
    transaction survives and can keep asserting.
    """
    nested = connection.begin_nested()
    with pytest.raises(DBAPIError) as exc:
        action()
    nested.rollback()
    return str(exc.value)


@pytest.mark.parametrize(
    ("table", "statement"),
    [
        ("proposals", "UPDATE proposals SET suggested_code = 'I15.9'"),
        ("decisions", "UPDATE decisions SET final_code = 'I15.9'"),
    ],
)
def test_update_is_rejected_at_the_database(
    connection: Connection, table: str, statement: str
) -> None:
    proposal_id = _insert_proposal(connection)
    _insert_decision(connection, proposal_id)

    message = _expect_db_error(connection, lambda: connection.execute(sa.text(statement)))
    assert "append-only table" in message
    assert "UPDATE" in message


@pytest.mark.parametrize("table", ["decisions", "proposals"])
def test_delete_is_rejected_at_the_database(connection: Connection, table: str) -> None:
    proposal_id = _insert_proposal(connection)
    _insert_decision(connection, proposal_id)

    message = _expect_db_error(
        connection, lambda: connection.execute(sa.text(f"DELETE FROM {table}"))
    )
    assert "append-only table" in message
    assert "DELETE" in message


def test_a_proposal_may_carry_only_one_decision(connection: Connection) -> None:
    """The human decides once. A second decision is a data-integrity error, not
    an amendment -- amending would mean mutating the audit trail."""
    proposal_id = _insert_proposal(connection)
    _insert_decision(connection, proposal_id)

    message = _expect_db_error(connection, lambda: _insert_decision(connection, proposal_id))
    assert "uq_decisions_proposal_id" in message


def test_reject_must_not_carry_a_final_code(connection: Connection) -> None:
    proposal_id = _insert_proposal(connection)

    def insert_bad_reject() -> object:
        return connection.execute(
            sa.text(
                "INSERT INTO decisions (id, proposal_id, decision, final_code,"
                " validator_id) VALUES (:id, :pid, 'reject', 'I10', 'tester')"
            ),
            {"id": uuid.uuid4(), "pid": proposal_id},
        )

    assert "ck_decisions_final_code" in _expect_db_error(connection, insert_bad_reject)


def test_accept_must_carry_a_final_code(connection: Connection) -> None:
    proposal_id = _insert_proposal(connection)

    def insert_bad_accept() -> object:
        return connection.execute(
            sa.text(
                "INSERT INTO decisions (id, proposal_id, decision, final_code,"
                " validator_id) VALUES (:id, :pid, 'accept', NULL, 'tester')"
            ),
            {"id": uuid.uuid4(), "pid": proposal_id},
        )

    assert "ck_decisions_final_code" in _expect_db_error(connection, insert_bad_accept)


def test_inserts_still_work(connection: Connection) -> None:
    """Append-only means append -- the tables must remain writable."""
    proposal_id = _insert_proposal(connection)
    _insert_decision(connection, proposal_id)

    count = connection.execute(
        sa.text("SELECT count(*) FROM decisions WHERE proposal_id = :pid"),
        {"pid": proposal_id},
    ).scalar_one()
    assert count == 1


# ------------------------------------------------ the gate is part of the audit


def test_gate_columns_exist_and_are_not_nullable(connection: Connection) -> None:
    rows = connection.execute(
        sa.text(
            "SELECT column_name, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'proposals' AND column_name IN "
            "('gate_id','gate_version','gate_fired','gate_values','provider_kind')"
        )
    ).all()
    assert {r.column_name for r in rows} == {
        "gate_id",
        "gate_version",
        "gate_fired",
        "gate_values",
        "provider_kind",
    }
    # A proposal without a recorded gate verdict would be an unauditable claim.
    assert all(r.is_nullable == "NO" for r in rows)


def test_no_good_match_is_an_accepted_status(connection: Connection) -> None:
    proposal_id = uuid.uuid4()
    connection.execute(
        sa.text(
            """
            INSERT INTO proposals (
                id, trace_id, input_text, normalized_text, target_system,
                terminology_version, candidates, rerank, suggested_code,
                model_confidence, llm_provider, llm_model, prompt_id, prompt_hash,
                embedding_provider, embedding_model, latency_ms_retrieval,
                latency_ms_rerank, status, provider_kind, gate_id, gate_version,
                gate_fired, gate_values
            ) VALUES (
                :id, 'trace', 'banan', 'banan', 'icd10se',
                '2026', '[]'::jsonb, NULL, NULL,
                NULL, 'fake', 'fake-rerank-v1', 'rerank_v1', 'deadbeef',
                'fake', 'fake-hash-v1', 1,
                0, 'no_good_match', 'fake', 'lexical_evidence', '1',
                true, '{"best_ts_rank": 0.0}'::jsonb
            )
            """
        ),
        {"id": proposal_id},
    )
    status = connection.execute(
        sa.text("SELECT status FROM proposals WHERE id = :id"), {"id": proposal_id}
    ).scalar_one()
    assert status == "no_good_match"


def test_a_no_good_match_proposal_is_equally_immutable(connection: Connection) -> None:
    """ "The system found nothing" must be as permanent as any other verdict."""
    test_no_good_match_is_an_accepted_status(connection)
    message = _expect_db_error(
        connection,
        lambda: connection.execute(
            sa.text("UPDATE proposals SET gate_fired = false WHERE status = 'no_good_match'")
        ),
    )
    assert "append-only table" in message


def test_an_invalid_status_is_still_rejected(connection: Connection) -> None:
    def insert_bad_status() -> object:
        return connection.execute(
            sa.text(
                "INSERT INTO proposals (id, trace_id, input_text, normalized_text,"
                " target_system, terminology_version, candidates, llm_provider,"
                " llm_model, prompt_id, prompt_hash, embedding_provider,"
                " embedding_model, latency_ms_retrieval, latency_ms_rerank, status,"
                " gate_id, gate_version, gate_fired, gate_values)"
                " VALUES (:id, 't', 'x', 'x', 'icd10se', '2026', '[]'::jsonb, 'fake',"
                " 'm', 'p', 'h', 'fake', 'm', 1, 1, 'accepted', 'g', '1', false,"
                " '{}'::jsonb)"
            ),
            {"id": uuid.uuid4()},
        )

    assert "ck_proposals_status" in _expect_db_error(connection, insert_bad_status)

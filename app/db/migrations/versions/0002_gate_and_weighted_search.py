"""Retrieval gate, provider kind, and a weighted full-text index.

Revision ID: 0002_gate
Revises: 0001_initial
Create Date: 2026-08-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_gate"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Weighted so a query matching the preferred term outranks one matching only a
# synonym, which in turn outranks one matching only the publisher's prose.
SEARCH_VECTOR = """
    setweight(to_tsvector('swedish'::regconfig, coalesce(preferred_term, '')), 'A') ||
    setweight(to_tsvector('swedish'::regconfig, coalesce(synonym_text, '')), 'B') ||
    setweight(to_tsvector('swedish'::regconfig, coalesce(description_text, '')), 'D')
"""


def upgrade() -> None:
    # ---------------------------------------------------------- concepts
    op.add_column(
        "concepts",
        sa.Column("synonym_text", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "concepts",
        sa.Column("description_text", sa.Text(), nullable=False, server_default=""),
    )
    op.execute(
        f"ALTER TABLE concepts ADD COLUMN search_vector tsvector "
        f"GENERATED ALWAYS AS ({SEARCH_VECTOR}) STORED"
    )
    op.execute("CREATE INDEX ix_concepts_search_vector ON concepts USING gin (search_vector)")
    # Superseded by the weighted, generated column above.
    op.execute("DROP INDEX IF EXISTS ix_concepts_fts_swedish")

    # --------------------------------------------------------- proposals
    # Existing rows predate the gate and predate the fake/live distinction.
    # Backfill them honestly rather than inventing values: `none` records that
    # no gate ran, not that a gate passed.
    op.add_column(
        "proposals",
        sa.Column("provider_kind", sa.String(8), nullable=False, server_default="live"),
    )
    op.add_column(
        "proposals",
        sa.Column("gate_id", sa.String(64), nullable=False, server_default="none"),
    )
    op.add_column(
        "proposals",
        sa.Column("gate_version", sa.String(16), nullable=False, server_default="0"),
    )
    op.add_column(
        "proposals",
        sa.Column(
            "gate_fired", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
    )
    op.add_column(
        "proposals",
        sa.Column(
            "gate_values",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )

    # `no_good_match` joins the status enum. The trigger forbids UPDATE on this
    # table, but a CHECK constraint is table metadata rather than row data, so
    # replacing it is allowed and touches no existing row.
    op.drop_constraint("ck_proposals_status", "proposals", type_="check")
    op.create_check_constraint(
        "ck_proposals_status",
        "proposals",
        "status IN ('pending', 'rerank_failed', 'no_good_match')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_proposals_status", "proposals", type_="check")
    op.execute("DELETE FROM proposals WHERE status = 'no_good_match'")
    op.create_check_constraint(
        "ck_proposals_status", "proposals", "status IN ('pending', 'rerank_failed')"
    )
    for column in ("gate_values", "gate_fired", "gate_version", "gate_id", "provider_kind"):
        op.drop_column("proposals", column)

    op.execute("DROP INDEX IF EXISTS ix_concepts_search_vector")
    op.drop_column("concepts", "search_vector")
    op.drop_column("concepts", "description_text")
    op.drop_column("concepts", "synonym_text")
    op.execute(
        "CREATE INDEX ix_concepts_fts_swedish ON concepts "
        "USING gin (to_tsvector('swedish', search_text))"
    )

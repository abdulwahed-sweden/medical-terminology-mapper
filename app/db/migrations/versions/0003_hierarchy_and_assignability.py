"""Parent provenance and assignability on concepts.

Revision ID: 0003_hierarchy
Revises: 0002_gate
Create Date: 2026-08-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_hierarchy"
down_revision: str | None = "0002_gate"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("concepts", sa.Column("parent_source", sa.String(8), nullable=True))
    op.add_column(
        "concepts",
        sa.Column("assignable", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    # Retrieval filters on assignability on every query, alongside the
    # (system, version) scope it already uses.
    op.create_index(
        "ix_concepts_assignable", "concepts", ["system", "version", "assignable"]
    )
    # Rows loaded before this migration predate the flag. A code interval is the
    # one heading shape we can identify with certainty after the fact; anything
    # subtler needs a reload, which is the documented way to change a release.
    op.execute("UPDATE concepts SET assignable = false WHERE code LIKE '%-%'")


def downgrade() -> None:
    op.drop_index("ix_concepts_assignable", table_name="concepts")
    op.drop_column("concepts", "assignable")
    op.drop_column("concepts", "parent_source")

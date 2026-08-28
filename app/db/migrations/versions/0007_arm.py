"""Which benchmark arm produced a proposal.

Revision ID: 0007_arm
Revises: 0006_origin
Create Date: 2026-08-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_arm"
down_revision: str | None = "0006_origin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Every proposal that already exists was produced by the full pipeline --
    # there was no other way to produce one -- so the server default backfills
    # them exactly rather than by inference.
    op.add_column(
        "proposals",
        sa.Column("arm", sa.String(16), nullable=False, server_default="full"),
    )
    op.create_index("ix_proposals_arm", "proposals", ["arm"])
    op.create_check_constraint(
        "ck_proposals_arm",
        "proposals",
        "arm IN ('lexical', 'hybrid', 'full')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_proposals_arm", "proposals", type_="check")
    op.drop_index("ix_proposals_arm", table_name="proposals")
    op.drop_column("proposals", "arm")

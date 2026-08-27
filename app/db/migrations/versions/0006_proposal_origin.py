"""Which surface filed a proposal.

Revision ID: 0006_origin
Revises: 0005_placeholder
Create Date: 2026-08-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_origin"
down_revision: str | None = "0005_placeholder"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "proposals",
        sa.Column("origin", sa.String(16), nullable=False, server_default="api"),
    )
    op.add_column("proposals", sa.Column("requested_by", sa.String(128), nullable=True))
    op.create_index("ix_proposals_origin", "proposals", ["origin"])

    # Back-fill from what the rows themselves record. Evaluation runs stamp
    # their trace_id with an `eval-` prefix, so they are recoverable exactly;
    # everything else reached the pipeline through POST /map, which is what
    # `api` means here. Nothing is guessed.
    op.execute("UPDATE proposals SET origin = 'eval' WHERE trace_id LIKE 'eval-%'")


def downgrade() -> None:
    op.drop_index("ix_proposals_origin", table_name="proposals")
    op.drop_column("proposals", "requested_by")
    op.drop_column("proposals", "origin")

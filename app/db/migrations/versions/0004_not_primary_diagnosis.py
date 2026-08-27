"""The publisher's "Ej huvuddiagnos" marker.

Revision ID: 0004_notprimary
Revises: 0003_hierarchy
Create Date: 2026-08-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_notprimary"
down_revision: str | None = "0003_hierarchy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "concepts",
        sa.Column(
            "not_primary_diagnosis",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    # No back-fill: the flag comes from a column the loader previously
    # discarded, so the honest default for existing rows is "not marked".
    # Reloading the release fills it in.


def downgrade() -> None:
    op.drop_column("concepts", "not_primary_diagnosis")

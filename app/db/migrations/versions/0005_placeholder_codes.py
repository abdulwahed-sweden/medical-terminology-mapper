"""U-code placeholders.

Revision ID: 0005_placeholder
Revises: 0004_notprimary
Create Date: 2026-08-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_placeholder"
down_revision: str | None = "0004_notprimary"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "concepts",
        sa.Column("placeholder", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    # No back-fill. U-codes were never loaded before this revision, so no
    # existing row can be one.


def downgrade() -> None:
    op.drop_column("concepts", "placeholder")

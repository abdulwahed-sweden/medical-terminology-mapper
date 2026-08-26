"""Initial schema: concepts, embeddings, and append-only audit tables.

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-26
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from app.config import get_settings

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The pgvector column is typed with a fixed dimension, so the configured
# EMBEDDING_DIM is baked into the schema. Changing providers to one with a
# different dimension is a schema change and needs a new migration -- that is a
# property of pgvector, and making it explicit here beats discovering it at
# insert time.
EMBEDDING_DIM = get_settings().embedding_dim

# Enforcement of principle 3, at the level the principle demands: the database,
# not the application. A future maintainer with psql cannot bypass this.
APPEND_ONLY_FN = """
CREATE OR REPLACE FUNCTION mtm_forbid_update_delete() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'append-only table: % is not permitted on %.%',
        TG_OP, TG_TABLE_SCHEMA, TG_TABLE_NAME
        USING ERRCODE = '42501';
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # ---------------------------------------------------------------- concepts
    op.create_table(
        "concepts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("system", sa.String(16), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("preferred_term", sa.Text(), nullable=False),
        sa.Column("synonyms", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("parent_code", sa.String(32), nullable=True),
        sa.Column("is_leaf", sa.Boolean(), nullable=False),
        sa.Column("chapter", sa.String(64), nullable=True),
        sa.Column("search_text", sa.Text(), nullable=False),
        sa.UniqueConstraint("system", "version", "code", name="uq_concepts_system_version_code"),
    )
    op.create_index("ix_concepts_system_version", "concepts", ["system", "version"])
    op.create_index("ix_concepts_parent", "concepts", ["system", "version", "parent_code"])

    # Expression index over the exact expression the lexical query uses. The
    # 'swedish' text search configuration ships with PostgreSQL.
    op.execute(
        "CREATE INDEX ix_concepts_fts_swedish ON concepts "
        "USING gin (to_tsvector('swedish', search_text))"
    )
    # Trigram index for misspelling tolerance.
    op.execute(
        "CREATE INDEX ix_concepts_trgm ON concepts USING gin (search_text gin_trgm_ops)"
    )

    # ------------------------------------------------------ concept_embeddings
    op.create_table(
        "concept_embeddings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("system", sa.String(16), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("dim", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=False),
        sa.UniqueConstraint(
            "system", "version", "code", "provider", "model",
            name="uq_concept_embeddings_identity",
        ),
    )
    op.create_index(
        "ix_concept_embeddings_space",
        "concept_embeddings",
        ["system", "version", "provider", "model"],
    )
    op.execute(
        "CREATE INDEX ix_concept_embeddings_hnsw ON concept_embeddings "
        "USING hnsw (embedding vector_cosine_ops)"
    )

    # --------------------------------------------------------------- proposals
    op.create_table(
        "proposals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("trace_id", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("input_text", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("target_system", sa.String(16), nullable=False),
        sa.Column("terminology_version", sa.String(32), nullable=False),
        sa.Column("candidates", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("rerank", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("suggested_code", sa.String(32), nullable=True),
        sa.Column("model_confidence", sa.Float(), nullable=True),
        sa.Column("llm_provider", sa.String(32), nullable=False),
        sa.Column("llm_model", sa.String(128), nullable=False),
        sa.Column("prompt_id", sa.String(64), nullable=False),
        sa.Column("prompt_hash", sa.String(64), nullable=False),
        sa.Column("embedding_provider", sa.String(32), nullable=False),
        sa.Column("embedding_model", sa.String(128), nullable=False),
        sa.Column("latency_ms_retrieval", sa.Integer(), nullable=False),
        sa.Column("latency_ms_rerank", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.CheckConstraint("status IN ('pending', 'rerank_failed')", name="ck_proposals_status"),
    )
    op.create_index("ix_proposals_trace_id", "proposals", ["trace_id"])
    op.create_index("ix_proposals_created_at", "proposals", ["created_at"])

    # --------------------------------------------------------------- decisions
    op.create_table(
        "decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("proposal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("final_code", sa.String(32), nullable=True),
        sa.Column("validator_note", sa.String(500), nullable=True),
        sa.Column("validator_id", sa.String(128), nullable=False),
        sa.ForeignKeyConstraint(["proposal_id"], ["proposals.id"], name="fk_decisions_proposal"),
        sa.UniqueConstraint("proposal_id", name="uq_decisions_proposal_id"),
        sa.CheckConstraint(
            "decision IN ('accept', 'reject', 'correct')", name="ck_decisions_kind"
        ),
        sa.CheckConstraint(
            "(decision = 'reject' AND final_code IS NULL)"
            " OR (decision IN ('accept', 'correct') AND final_code IS NOT NULL)",
            name="ck_decisions_final_code",
        ),
    )

    # ------------------------------------------------- append-only enforcement
    op.execute(APPEND_ONLY_FN)
    for table in ("proposals", "decisions"):
        op.execute(
            f"CREATE TRIGGER {table}_append_only "
            f"BEFORE UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION mtm_forbid_update_delete()"
        )


def downgrade() -> None:
    for table in ("decisions", "proposals"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
    op.execute("DROP FUNCTION IF EXISTS mtm_forbid_update_delete()")
    op.drop_table("decisions")
    op.drop_table("proposals")
    op.drop_table("concept_embeddings")
    op.drop_table("concepts")

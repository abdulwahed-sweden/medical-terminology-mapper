"""Terminology tables.

These are *content* tables, not audit tables: reloading a terminology version
may legitimately replace rows, so no append-only trigger is applied here. The
append-only guarantee (principle 3) covers `proposals` and `decisions`, which
live in `app.audit.models`.
"""

from __future__ import annotations

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.config import get_settings
from app.db.base import Base


class ConceptRow(Base):
    """One concept of one version of one terminology system."""

    __tablename__ = "concepts"
    __table_args__ = (
        UniqueConstraint("system", "version", "code", name="uq_concepts_system_version_code"),
        Index("ix_concepts_system_version", "system", "version"),
        Index("ix_concepts_parent", "system", "version", "parent_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    system: Mapped[str] = mapped_column(String(16), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    preferred_term: Mapped[str] = mapped_column(Text, nullable=False)
    synonyms: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    parent_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_leaf: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    chapter: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Denormalised "preferred term + synonyms" used by both lexical signals.
    # Kept as a plain column (not generated) so the loader controls exactly what
    # is searchable; the tsvector index is built over it in the migration.
    search_text: Mapped[str] = mapped_column(Text, nullable=False)


class ConceptEmbeddingRow(Base):
    """A vector for one concept under one (provider, model) pair.

    The same concept may carry several embeddings, one per provider/model, so a
    stored proposal can always name which vector space produced its candidates.
    """

    __tablename__ = "concept_embeddings"
    __table_args__ = (
        UniqueConstraint(
            "system",
            "version",
            "code",
            "provider",
            "model",
            name="uq_concept_embeddings_identity",
        ),
        Index("ix_concept_embeddings_space", "system", "version", "provider", "model"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    system: Mapped[str] = mapped_column(String(16), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    dim: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(
        Vector(get_settings().embedding_dim), nullable=False
    )

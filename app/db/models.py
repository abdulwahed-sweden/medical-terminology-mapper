"""Terminology tables.

These are *content* tables, not audit tables: reloading a terminology version
may legitimately replace rows, so no append-only trigger is applied here. The
append-only guarantee (principle 3) covers `proposals` and `decisions`, which
live in `app.audit.models`.
"""

from __future__ import annotations

from collections.abc import Iterable

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    delete,
    func,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.config import get_settings
from app.db.base import Base
from app.terminology.base import Concept, build_search_text, build_synonym_text


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
    # "column" = the publisher stated this parent; "derived" = it was read from
    # the code's own prefix structure. Never conflate the two.
    parent_source: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # Headings (chapter/section/group rows) are loaded for the hierarchy and
    # excluded from retrieval and decisions.
    assignable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Denormalised "preferred term + synonyms", the target for trigram
    # similarity. Kept as a plain column so the loader controls exactly what is
    # matched fuzzily.
    search_text: Mapped[str] = mapped_column(Text, nullable=False)
    # The same three fields kept apart, because the full-text index weights them
    # differently: A for the preferred term, B for synonyms, D for the
    # description. A generated `search_vector` column (see the migration) builds
    # the weighted tsvector from them.
    synonym_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    description_text: Mapped[str] = mapped_column(Text, nullable=False, default="")


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


def upsert_concepts(session: Session, concepts: Iterable[Concept]) -> int:
    """Write concepts for one `(system, version)`, replacing what is there.

    A terminology release is a unit: loading version 2026 twice must leave the
    same rows, and a code withdrawn between two loads of the same version must
    not linger. So the target `(system, version)` slice is deleted and rewritten
    rather than merged. `concepts` is *not* an audit table, so this is allowed
    -- the append-only guarantee covers proposals and decisions.
    """
    materialised = list(concepts)
    if not materialised:
        return 0

    systems = {c.system for c in materialised}
    versions = {c.version for c in materialised}
    if len(systems) != 1 or len(versions) != 1:
        raise ValueError(
            f"upsert_concepts handles one (system, version) at a time; "
            f"got systems={sorted(systems)} versions={sorted(versions)}"
        )
    system, version = systems.pop(), versions.pop()

    session.execute(
        delete(ConceptRow).where(ConceptRow.system == system, ConceptRow.version == version)
    )
    session.execute(
        delete(ConceptEmbeddingRow).where(
            ConceptEmbeddingRow.system == system, ConceptEmbeddingRow.version == version
        )
    )
    session.add_all(
        [
            ConceptRow(
                system=c.system,
                version=c.version,
                code=c.code,
                preferred_term=c.preferred_term,
                synonyms=c.synonyms,
                parent_code=c.parent_code,
                is_leaf=c.is_leaf,
                chapter=c.chapter,
                parent_source=c.parent_source,
                assignable=c.assignable,
                search_text=build_search_text(c),
                synonym_text=build_synonym_text(c),
                description_text=c.description,
            )
            for c in materialised
        ]
    )
    session.flush()
    # A freshly rewritten slice leaves the planner with statistics describing
    # the previous contents, which matters for the trigram and vector paths.
    session.execute(sa.text("ANALYZE concepts"))
    return len(materialised)


def loaded_versions(session: Session) -> list[tuple[str, str, int]]:
    """`(system, version, concept_count)` for everything currently loaded."""
    rows = session.execute(
        select(ConceptRow.system, ConceptRow.version, func.count())
        .group_by(ConceptRow.system, ConceptRow.version)
        .order_by(ConceptRow.system, ConceptRow.version)
    ).all()
    return [(system, version, count) for system, version, count in rows]


def hierarchy_for(
    session: Session, *, system: str, version: str, code: str
) -> list[dict[str, str | None]]:
    """The ancestor chain of a code, outermost first, with titles where known.

    Two sources of truth, in order. A publisher-stated `parent_code` is walked
    first. When the walk runs out -- which it does for KVA, whose workbook
    carries codes but not their ancestors -- the rest of the chain is read from
    the code's own prefix structure. Ancestors with no row in this release are
    still returned, with a null title, because the chain is real even where the
    file does not spell it out.
    """
    from app.terminology.kva import ancestors as kva_ancestors

    chain: list[str] = []
    seen: set[str] = {code}
    current = code
    for _ in range(8):
        parent = session.execute(
            sa.select(ConceptRow.parent_code).where(
                ConceptRow.system == system,
                ConceptRow.version == version,
                ConceptRow.code == current,
            )
        ).scalar_one_or_none()
        if not parent or parent in seen:
            break
        chain.append(parent)
        seen.add(parent)
        current = parent

    if system == "kva":
        structural = [c for c in kva_ancestors(code) if c not in seen]
        chain.extend(structural)

    ordered = list(reversed(chain)) if system != "kva" else sorted(set(chain), key=len)
    if not ordered:
        return []

    rows = session.execute(
        sa.select(ConceptRow.code, ConceptRow.preferred_term).where(
            ConceptRow.system == system,
            ConceptRow.version == version,
            ConceptRow.code.in_(ordered),
        )
    ).all()
    titles: dict[str, str] = {row.code: row.preferred_term for row in rows}
    return [{"code": c, "title": titles.get(c)} for c in ordered]

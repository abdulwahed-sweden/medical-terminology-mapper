"""Terminology operations shared by every surface.

The FastAPI routes and the MCP server both call these functions. Neither owns
the behaviour, and neither calls the other over HTTP: one implementation, one
audit trail, no second network hop. When a surface needs something new, it goes
here first and the surface stays a thin wrapper.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import ConceptEmbeddingRow, ConceptRow, hierarchy_for
from app.embeddings.base import EmbeddingProvider
from app.models.candidate import Candidate
from app.retrieval.lexical import lexical_search
from app.retrieval.merge import merge_candidates
from app.retrieval.vector import vector_search
from app.terminology.base import TerminologySystem
from app.terminology.icd10se import ICD10SE
from app.terminology.kva import KVA
from app.terminology.snomed import SnomedCT

KNOWN_SYSTEMS: tuple[str, ...] = ("icd10se", "kva", "snomed")
LICENSED_SYSTEMS: tuple[str, ...] = ("snomed",)

VALIDATORS: dict[str, TerminologySystem] = {
    "icd10se": ICD10SE(),
    "kva": KVA(),
    "snomed": SnomedCT(),
}

# Cap on how many children of a concept are returned. A three-character
# ICD-10-SE category can have a dozen subdivisions; a KVA chapter has hundreds,
# and returning them all would bury the concept the caller asked about.
MAX_CHILDREN = 25


class TerminologyServiceError(Exception):
    """Base class carrying a machine-readable code alongside the message."""

    code = "error"


class SystemUnknown(TerminologyServiceError):
    code = "invalid_argument"


class LicenceRequired(TerminologyServiceError):
    code = "licence_required"


class VersionNotLoaded(TerminologyServiceError):
    code = "not_loaded"


class ConceptNotFound(TerminologyServiceError):
    code = "not_found"


# --------------------------------------------------------------------------- #
# Guards
# --------------------------------------------------------------------------- #


def resolve_system(system: str) -> str:
    """Normalise and check a system name, refusing licensed ones."""
    name = (system or "").strip().lower()
    if name not in KNOWN_SYSTEMS:
        raise SystemUnknown(
            f"unknown code system {system!r}; expected one of {', '.join(KNOWN_SYSTEMS)}"
        )
    if name in LICENSED_SYSTEMS:
        raise LicenceRequired(
            f"{name} content is not shipped with this repository and cannot be "
            f"searched or proposed against. SNOMED CT requires an affiliate "
            f"licence; see LICENSING.md for the responsible authority and the "
            f"licence route."
        )
    return name


def resolve_version(session: Session, system: str, version: str | None, settings: Settings) -> str:
    """Resolve an optional version, and confirm something is loaded under it."""
    resolved = (version or settings.default_terminology_version).strip()
    loaded = session.execute(
        sa.select(sa.func.count())
        .select_from(ConceptRow)
        .where(ConceptRow.system == system, ConceptRow.version == resolved)
    ).scalar_one()
    if not loaded:
        raise VersionNotLoaded(
            f"no concepts are loaded for {system} version {resolved!r}. "
            f"Run scripts/load_terminology.py, then scripts/embed_terminology.py."
        )
    return resolved


# --------------------------------------------------------------------------- #
# What is loaded
# --------------------------------------------------------------------------- #


@dataclass
class LoadedVersion:
    system: str
    version: str
    total: int
    assignable: int
    headings: int
    placeholders: int
    with_description: int
    embedding_spaces: list[str] = field(default_factory=list)


def list_loaded(session: Session) -> list[LoadedVersion]:
    """Every `(system, version)` present, with what it actually contains."""
    rows = session.execute(
        sa.select(
            ConceptRow.system,
            ConceptRow.version,
            sa.func.count().label("total"),
            sa.func.count().filter(ConceptRow.assignable).label("assignable"),
            sa.func.count().filter(~ConceptRow.assignable).label("headings"),
            sa.func.count().filter(ConceptRow.placeholder).label("placeholders"),
            sa.func.count().filter(ConceptRow.description_text != "").label("described"),
        )
        .group_by(ConceptRow.system, ConceptRow.version)
        .order_by(ConceptRow.system, ConceptRow.version)
    ).all()

    spaces = session.execute(
        sa.select(
            ConceptEmbeddingRow.system,
            ConceptEmbeddingRow.version,
            ConceptEmbeddingRow.provider,
            ConceptEmbeddingRow.model,
        ).distinct()
    ).all()
    by_key: dict[tuple[str, str], list[str]] = {}
    for space in spaces:
        by_key.setdefault((space.system, space.version), []).append(
            f"{space.provider}/{space.model}"
        )

    return [
        LoadedVersion(
            system=row.system,
            version=row.version,
            total=row.total,
            assignable=row.assignable,
            headings=row.headings,
            placeholders=row.placeholders,
            with_description=row.described,
            embedding_spaces=sorted(by_key.get((row.system, row.version), [])),
        )
        for row in rows
    ]


# --------------------------------------------------------------------------- #
# Retrieval
# --------------------------------------------------------------------------- #


@dataclass
class Retrieval:
    candidates: list[Candidate]
    lexical_count: int
    vector_count: int
    latency_ms: int


def retrieve(
    session: Session,
    *,
    query: str,
    system: str,
    version: str,
    settings: Settings,
    embedding_provider: EmbeddingProvider,
    limit: int | None = None,
) -> Retrieval:
    """Lexical + vector retrieval, merged. No gate, no model, nothing written."""
    started = time.perf_counter()

    lexical = lexical_search(
        session,
        query=query,
        system=system,
        version=version,
        top_k=settings.lexical_top_k,
        trigram_threshold=settings.trigram_threshold,
        index_descriptions=settings.index_descriptions,
    )
    vector = vector_search(
        session,
        query_vector=embedding_provider.embed([query])[0],
        system=system,
        version=version,
        provider=embedding_provider.provider_id,
        model=embedding_provider.model_id,
        top_k=settings.vector_top_k,
    )
    merged = merge_candidates(
        lexical,
        vector,
        rrf_k=settings.rrf_k,
        cap=limit if limit is not None else settings.rerank_candidate_cap,
    )
    return Retrieval(
        candidates=merged,
        lexical_count=len(lexical),
        vector_count=len(vector),
        latency_ms=round((time.perf_counter() - started) * 1000),
    )


# --------------------------------------------------------------------------- #
# One concept
# --------------------------------------------------------------------------- #


@dataclass
class ConceptDetail:
    system: str
    version: str
    code: str
    preferred_term: str
    synonyms: list[str]
    description: str
    chapter: str | None
    parent_code: str | None
    parent_source: str | None
    is_leaf: bool
    assignable: bool
    not_primary_diagnosis: bool
    placeholder: bool
    hierarchy: list[dict[str, str | None]]
    children: list[dict[str, str]]
    children_truncated: bool


def concept_detail(session: Session, *, system: str, version: str, code: str) -> ConceptDetail:
    row = session.execute(
        sa.select(ConceptRow).where(
            ConceptRow.system == system,
            ConceptRow.version == version,
            ConceptRow.code == code.strip().upper(),
        )
    ).scalar_one_or_none()
    if row is None:
        raise ConceptNotFound(f"{code} is not present in {system} version {version}")

    children_rows = session.execute(
        sa.select(ConceptRow.code, ConceptRow.preferred_term)
        .where(
            ConceptRow.system == system,
            ConceptRow.version == version,
            ConceptRow.parent_code == row.code,
        )
        .order_by(ConceptRow.code)
        .limit(MAX_CHILDREN + 1)
    ).all()
    truncated = len(children_rows) > MAX_CHILDREN

    return ConceptDetail(
        system=system,
        version=version,
        code=row.code,
        preferred_term=row.preferred_term,
        synonyms=list(row.synonyms or []),
        description=row.description_text,
        chapter=row.chapter,
        parent_code=row.parent_code,
        parent_source=row.parent_source,
        is_leaf=row.is_leaf,
        assignable=row.assignable,
        not_primary_diagnosis=row.not_primary_diagnosis,
        placeholder=row.placeholder,
        hierarchy=hierarchy_for(session, system=system, version=version, code=row.code),
        children=[
            {"code": child.code, "preferred_term": child.preferred_term}
            for child in children_rows[:MAX_CHILDREN]
        ],
        children_truncated=truncated,
    )


# --------------------------------------------------------------------------- #
# Code inspection -- the single source of truth for "may this code be used?"
# --------------------------------------------------------------------------- #

CodeVerdict = Literal["ok", "bad_format", "not_present", "heading", "placeholder"]


@dataclass
class CodeInspection:
    """What the validator page would conclude about a code, without deciding.

    `message` is the Swedish sentence the page shows. It is produced here rather
    than in the decision layer so that every surface says the same thing --
    which is the point of having one implementation.
    """

    system: str
    version: str
    code: str
    verdict: CodeVerdict
    format_valid: bool
    exists: bool
    assignable: bool
    not_primary_diagnosis: bool
    placeholder: bool
    usable_as_final_code: bool
    message: str | None


def inspect_code(session: Session, *, system: str, version: str, code: str) -> CodeInspection:
    normalised = code.strip().upper()
    validator = VALIDATORS[system]
    format_valid = validator.validate_code_format(normalised)

    row = session.execute(
        sa.select(
            ConceptRow.assignable,
            ConceptRow.placeholder,
            ConceptRow.not_primary_diagnosis,
        ).where(
            ConceptRow.system == system,
            ConceptRow.version == version,
            ConceptRow.code == normalised,
        )
    ).one_or_none()

    def build(
        verdict: CodeVerdict,
        message: str | None,
        *,
        assignable: bool = False,
        not_primary: bool = False,
        placeholder: bool = False,
        usable: bool = False,
    ) -> CodeInspection:
        return CodeInspection(
            system=system,
            version=version,
            code=normalised,
            verdict=verdict,
            format_valid=format_valid,
            exists=row is not None,
            assignable=assignable,
            not_primary_diagnosis=not_primary,
            placeholder=placeholder,
            usable_as_final_code=usable,
            message=message,
        )

    if row is None:
        if not format_valid:
            return build("bad_format", f"koden {normalised} har inte giltigt format för {system}")
        return build(
            "not_present",
            f"koden {normalised} har giltigt format men finns inte i {system} version {version}",
        )

    if row.placeholder:
        return build(
            "placeholder",
            f"koden {normalised} är en platshållarkod (U-kod) och föreslås inte",
            assignable=row.assignable,
            not_primary=row.not_primary_diagnosis,
            placeholder=True,
        )

    if not row.assignable:
        return build(
            "heading",
            f"koden {normalised} är en rubrik i {system} {version}, inte en tilldelningsbar kod",
            not_primary=row.not_primary_diagnosis,
        )

    return build(
        "ok",
        None,
        assignable=True,
        not_primary=row.not_primary_diagnosis,
        usable=True,
    )


def candidate_payload(candidate: Candidate) -> dict[str, Any]:
    """One candidate as a stable JSON object, for any surface that returns them."""
    return {
        "code": candidate.code,
        "preferred_term": candidate.preferred_term,
        "synonyms": candidate.synonyms,
        "chapter": candidate.chapter,
        "matched_field": candidate.matched_field,
        "sources": candidate.sources,
        "not_primary_diagnosis": candidate.not_primary_diagnosis,
        "scores": {
            "lexical": candidate.lexical_score,
            "vector": candidate.vector_score,
            "ts_rank": candidate.ts_rank,
            "strict_similarity": candidate.strict_similarity,
            "rrf": candidate.fused_score,
        },
    }

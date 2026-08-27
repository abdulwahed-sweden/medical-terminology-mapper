"""ICD-10-SE loader.

ICD-10-SE is the Swedish version of WHO ICD-10. It is NOT ICD-10-CM: the US
variant has a different code space, different rules and different tooling, and
a US validator applied here would accept codes that do not exist in Sweden and
reject codes that do. See LICENSING.md.

Reads both `.xlsx` and `.tsv`; see
`app.terminology.base.read_classification_file`.

FORMAT_UNVERIFIED. The structure comes from the publisher's own file-description
document, but as of 2026-08-26 no machine-readable ICD-10-SE release is publicly
downloadable -- the ICD-10 page offers only PDFs -- so the exact header
spellings have not been checked against a real file. The header matching is
tolerant and a missing required column fails loudly. The sibling KVÅ loader,
which shares this reader and alias table, *was* verified against its real
release, which is indirect evidence that the approach holds. See LICENSING.md §1.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from pathlib import Path

from app.terminology.base import (
    Concept,
    assign_hierarchy,
    collect_synonyms,
    read_classification_file,
)

logger = logging.getLogger(__name__)

# Letter + two digits, optionally a dot and one or two alphanumerics.
# Four-position codes ("I21.9") and Swedish national five-position extensions
# ("six characters including the dot") both fit.
ICD10SE_CODE_RE = re.compile(r"^[A-Z][0-9]{2}(\.[A-Z0-9]{1,2})?$")

# Fields that name the concept in another way. Latin is an official alternative
# term; Innefattar and Exempel are inclusion terms a coder would plausibly type.
# Utesluter is excluded on purpose -- see `collect_synonyms`.
_SYNONYM_FIELDS = ("latin", "includes", "example")


# The publisher distributes 63 U-codes in a separate file: reserved slots that
# let a new code be put into use at short notice, as happened with covid-19.
# They are excluded by default -- proposing a code that stands for nothing yet
# would be wrong -- and loaded into the same (system, version) when asked for.
U_CODE_RE = re.compile(r"^U[0-9]{2}")


class ICD10SE:
    """Loader for the official ICD-10-SE code-text file (16-column TSV)."""

    system_id = "icd10se"

    def __init__(self, *, include_u_codes: bool = False) -> None:
        self.include_u_codes = include_u_codes

    def load(self, path: Path, version: str) -> Iterable[Concept]:
        concepts: list[Concept] = []
        for code, rows in read_classification_file(path):
            title = next((row["title"] for row in rows if row.get("title")), None)
            if title is None:
                # A code with no Titel carries no name to map to; skipping is
                # better than inventing one.
                continue
            is_u_code = bool(U_CODE_RE.match(code))
            if is_u_code and not self.include_u_codes:
                continue
            parent = next((row["parent"] for row in rows if row.get("parent")), None)
            # Beskrivning is prose, not a name: carried separately so it can be
            # indexed at a lower weight and never shown as a term.
            description = " ".join(
                dict.fromkeys(row["description"] for row in rows if row.get("description"))
            )
            concepts.append(
                Concept(
                    system="icd10se",
                    version=version,
                    code=code,
                    preferred_term=title,
                    synonyms=collect_synonyms(rows, extra_fields=_SYNONYM_FIELDS),
                    parent_code=parent,
                    # Both filled in by assign_hierarchy once the whole file is read.
                    is_leaf=True,
                    chapter=None,
                    description=description,
                    # Chapter and section rows carry a code interval
                    # ("I10-I15") or a manifestation marker; they name a group,
                    # not a codable concept.
                    assignable=self.validate_code_format(code),
                    placeholder=is_u_code,
                    # From the "Ej huvuddiagnos" column, whose documented
                    # content is a sentence such as "Ska inte anvandas som
                    # huvuddiagnos". Any non-empty value means the code is not
                    # to be used as a primary diagnosis.
                    not_primary_diagnosis=any(
                        row.get("not_primary") for row in rows
                    ),
                )
            )
        headings = sum(1 for concept in concepts if not concept.assignable)
        if headings:
            logger.info(
                "classification_headings_loaded",
                extra={"path": str(path), "headings": headings, "total": len(concepts)},
            )
        # ICD-10-SE states Overordnad kod for every row, so no derivation is
        # needed or wanted here.
        return assign_hierarchy(concepts)

    def validate_code_format(self, code: str) -> bool:
        return bool(ICD10SE_CODE_RE.match(code.strip().upper()))

"""ICD-10-SE loader.

ICD-10-SE is the Swedish version of ICD-10. It is NOT ICD-10-CM: the US
variant has a different code space, different rules and different tooling, and
a US validator applied here would accept codes that do not exist in Sweden and
reject codes that do. See LICENSING.md.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from app.terminology.base import (
    Concept,
    assign_hierarchy,
    collect_synonyms,
    read_classification_tsv,
)

# Letter + two digits, optionally a dot and one or two alphanumerics.
# Four-position codes ("I21.9") and Swedish national five-position extensions
# ("six characters including the dot") both fit.
ICD10SE_CODE_RE = re.compile(r"^[A-Z][0-9]{2}(\.[A-Z0-9]{1,2})?$")

# Fields that name the concept in another way. Latin is an official alternative
# term; Innefattar and Exempel are inclusion terms a coder would plausibly type.
# Utesluter is excluded on purpose -- see `collect_synonyms`.
_SYNONYM_FIELDS = ("latin", "includes", "example")


class ICD10SE:
    """Loader for the official ICD-10-SE code-text file (16-column TSV)."""

    system_id = "icd10se"

    def load(self, path: Path, version: str) -> Iterable[Concept]:
        concepts: list[Concept] = []
        for code, rows in read_classification_tsv(path):
            title = next((row["title"] for row in rows if row.get("title")), None)
            if title is None:
                # A code with no Titel carries no name to map to; skipping is
                # better than inventing one.
                continue
            parent = next((row["parent"] for row in rows if row.get("parent")), None)
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
                )
            )
        return assign_hierarchy(concepts)

    def validate_code_format(self, code: str) -> bool:
        return bool(ICD10SE_CODE_RE.match(code.strip().upper()))

"""KVÅ loader.

KVÅ (Klassifikation av vårdåtgärder) is the union of two published
classifications, distributed as two separate code-text files:

  * KKÅ -- kirurgiska åtgärder (surgical procedures), 9 columns.
  * KMÅ -- medicinska åtgärder (medical procedures), 10 columns.

Both share the Kod / Överordnad kod / Titel core, so one loader handles both:
the column set is discovered from the header rather than assumed by position,
and the two files are loaded into the same `kva` system with one
`load()` call each.

Code shape (verified against the published KVÅ 2026 release):
  * KKÅ codes are three letters + two digits  -- AAA00, FNG05, VAN33
  * KMÅ codes are two letters + three digits  -- AA001, AF015, SS104
Both are therefore five characters. Three-letter entries such as `EMA` appear
in the file as group headings; they carry a title and children but are not
assignable procedure codes, which `validate_code_format` reflects.
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

KVA_CODE_RE = re.compile(r"^([A-Z]{3}[0-9]{2}|[A-Z]{2}[0-9]{3})$")

# Naming variants only. Beskrivning, Anmärkning and Kodningsinformation are
# guidance prose rather than names for the procedure, and Utesluter states what
# the code does not cover -- see `collect_synonyms`.
_SYNONYM_FIELDS = ("abbreviations", "includes", "example")


class KVA:
    """Loader for an official KVÅ code-text file (either the KKÅ or KMÅ file)."""

    system_id = "kva"

    def load(self, path: Path, version: str) -> Iterable[Concept]:
        concepts: list[Concept] = []
        for code, rows in read_classification_tsv(path):
            title = next((row["title"] for row in rows if row.get("title")), None)
            if title is None:
                continue
            parent = next((row["parent"] for row in rows if row.get("parent")), None)
            concepts.append(
                Concept(
                    system="kva",
                    version=version,
                    code=code,
                    preferred_term=title,
                    synonyms=collect_synonyms(rows, extra_fields=_SYNONYM_FIELDS),
                    parent_code=parent,
                    is_leaf=True,
                    chapter=None,
                )
            )
        return assign_hierarchy(concepts)

    def validate_code_format(self, code: str) -> bool:
        return bool(KVA_CODE_RE.match(code.strip().upper()))

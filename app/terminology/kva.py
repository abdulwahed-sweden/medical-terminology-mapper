"""KVÅ loader.

KVÅ (Klassifikation av vårdåtgärder) is the union of two published
classifications, distributed as two separate code-text files:

  * KKÅ -- kirurgiska åtgärder (surgical procedures), 9 columns.
  * KMÅ -- medicinska åtgärder (medical procedures), 10 columns.

Both share the Kod / Överordnad kod / Titel core, so one loader handles both:
the column set is discovered from the header rather than assumed by position,
and the two files are loaded into the same `kva` system with one
`load()` call each.

Both the `.xlsx` and `.tsv` distributions are read; see
`app.terminology.base.read_classification_file`. The published workbook merges
KKÅ and KMÅ into one sheet and carries no `Överordnad kod` column, so a KVÅ
load from it has no parent links -- a property of the source, which the reader
warns about rather than papering over.

FORMAT VERIFIED (2026-08-26) against the real release
`kva-inkl-beskrivningstexter-2026.xlsx`: 11 888 concepts parsed, matching the
count the workbook states for itself, with 9 of its 10 named columns mapping to
the header aliases below. See LICENSING.md §3.

Code shape (verified against the published KVÅ 2026 release):
  * KKÅ codes are three letters + two digits  -- AAA00, FNG05, VAN33
  * KMÅ codes are two letters + three digits  -- AA001, AF015, SS104
Both are therefore five characters. Three-letter entries such as `EMA` appear
in the file as group headings; they carry a title and children but are not
assignable procedure codes, which `validate_code_format` reflects.
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

KVA_CODE_RE = re.compile(r"^([A-Z]{3}[0-9]{2}|[A-Z]{2}[0-9]{3})$")

# KVA encodes its hierarchy in the code itself, so a workbook that omits the
# Overordnad kod column is not missing the hierarchy -- it is stating it a
# different way. Reading it back out is reading the classification as designed;
# it is still recorded as `parent_source="derived"` so nobody mistakes it for a
# publisher-supplied link.
#
#   KKA (NCSP structure)   F  ->  FN   ->  FNG  ->  FNG02
#                          chapter  section  group    code
#   KMA                    AF ->  AF015
#                          chapter        code
_KKA_CODE = re.compile(r"^[A-Z]{3}[0-9]{2}$")
_KMA_CODE = re.compile(r"^[A-Z]{2}[0-9]{3}$")
_KKA_GROUP = re.compile(r"^[A-Z]{3}$")


def derive_parent(code: str) -> str | None:
    """The next level up, read from the code's prefix structure."""
    code = code.strip().upper()
    if _KKA_CODE.match(code):
        return code[:3]
    if _KMA_CODE.match(code):
        return code[:2]
    if _KKA_GROUP.match(code):
        return code[:2]
    # A two-letter code is ambiguous: it is a KKA section (parent = its letter)
    # or a KMA chapter (no parent), and the code alone cannot say which. No
    # such rows exist in the published release, so nothing is lost by declining
    # to guess.
    return None


def derive_chapter(code: str) -> str | None:
    """The topmost level, read from the code's prefix structure."""
    code = code.strip().upper()
    if _KKA_CODE.match(code) or _KKA_GROUP.match(code):
        return code[:1]
    if _KMA_CODE.match(code):
        return code[:2]
    return None


def ancestors(code: str) -> list[str]:
    """The full chain above a code, outermost first: FNG02 -> [F, FN, FNG].

    Built from the code's shape rather than by walking `derive_parent`, because
    the walk stops at a two-letter code -- that shape is ambiguous on its own,
    but inside a known KKA code it is unambiguously the section.
    """
    code = code.strip().upper()
    if _KKA_CODE.match(code):
        return [code[:1], code[:2], code[:3]]
    if _KKA_GROUP.match(code):
        return [code[:1], code[:2]]
    if _KMA_CODE.match(code):
        return [code[:2]]
    return []


# Naming variants only. Beskrivning, Anmarkning and Kodningsinformation are
# guidance prose rather than names for the procedure, and Utesluter states what
# the code does not cover -- see `collect_synonyms`.
_SYNONYM_FIELDS = ("abbreviations", "includes", "example")


class KVA:
    """Loader for an official KVÅ code-text file (either the KKÅ or KMÅ file)."""

    system_id = "kva"

    def load(self, path: Path, version: str) -> Iterable[Concept]:
        concepts: list[Concept] = []
        for code, rows in read_classification_file(path):
            title = next((row["title"] for row in rows if row.get("title")), None)
            if title is None:
                continue
            parent = next((row["parent"] for row in rows if row.get("parent")), None)
            # Beskrivning is prose, not a name: carried separately so it can be
            # indexed at a lower weight and never shown as a term.
            description = " ".join(
                dict.fromkeys(row["description"] for row in rows if row.get("description"))
            )
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
                    description=description,
                    assignable=self.validate_code_format(code),
                )
            )
        headings = sum(1 for concept in concepts if not concept.assignable)
        if headings:
            logger.info(
                "classification_headings_loaded",
                extra={"path": str(path), "headings": headings, "total": len(concepts)},
            )
        return assign_hierarchy(
            concepts, derive_parent=derive_parent, derive_chapter=derive_chapter
        )

    def validate_code_format(self, code: str) -> bool:
        return bool(KVA_CODE_RE.match(code.strip().upper()))

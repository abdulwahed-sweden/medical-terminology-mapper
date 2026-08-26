#!/usr/bin/env python
"""Load an official terminology file into the `concepts` table.

    python scripts/load_terminology.py --system icd10se --version 2026 --file ICD10SE.tsv
    python scripts/load_terminology.py --system kva --version 2026 --file KKA.tsv
    python scripts/load_terminology.py --system kva --version 2026 --file KMA.tsv

KVÅ ships as two files (KKÅ and KMÅ); run the command once per file with the
same --version. Each run replaces the whole (system, version) slice, so load
KVÅ's two files with --append on the second, or pass both with --file twice.

Terminology content is not committed to this repository. See LICENSING.md for
where the official files come from.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow `python scripts/load_terminology.py` from a checkout without installing.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.db.models import loaded_versions, upsert_concepts  # noqa: E402
from app.db.session import session_scope  # noqa: E402
from app.logging_setup import configure_logging  # noqa: E402
from app.terminology.base import Concept, TerminologySystem  # noqa: E402
from app.terminology.icd10se import ICD10SE  # noqa: E402
from app.terminology.kva import KVA  # noqa: E402
from app.terminology.snomed import SnomedCT  # noqa: E402

LOADERS: dict[str, TerminologySystem] = {
    "icd10se": ICD10SE(),
    "kva": KVA(),
    "snomed": SnomedCT(),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__ or "")
    parser.add_argument("--system", required=True, choices=sorted(LOADERS))
    parser.add_argument("--version", required=True, help='Terminology version, e.g. "2026".')
    parser.add_argument(
        "--file",
        required=True,
        action="append",
        type=Path,
        dest="files",
        help="Official code-text file. Repeat for KVÅ's two files (KKÅ and KMÅ).",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.log_level)

    loader = LOADERS[args.system]
    concepts: list[Concept] = []
    for path in args.files:
        if not path.exists():
            parser.error(f"no such file: {path}")
        found = list(loader.load(path, args.version))
        print(f"parsed {len(found):>7} concepts from {path}")
        concepts.extend(found)

    if not concepts:
        print("nothing to load", file=sys.stderr)
        return 1

    # Multiple files of one system form one release; write them together so the
    # (system, version) slice is replaced once rather than once per file.
    seen: dict[str, Concept] = {}
    for concept in concepts:
        seen[concept.code] = concept

    with session_scope() as session:
        written = upsert_concepts(session, seen.values())

    print(f"wrote   {written:>7} concepts as ({args.system}, {args.version})")
    with session_scope() as session:
        for system, version, count in loaded_versions(session):
            print(f"  loaded: {system:<10} {version:<14} {count:>7} concepts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python
"""Run the three benchmark arms over one gold set and write one run directory.

    python scripts/run_benchmark.py --system kva --version 2026 \\
        --gold evaluation/gold/kva_v1.csv

Every arm sees the identical eligible row set, decided once before any of them
runs. One invocation produces one directory under `evaluation/runs/`, holding
the per-arm results, the paired comparison, the misses, the manifest that makes
the whole thing reproducible, and the report.

Nothing here decides whether the mapper is any good. It produces the artefacts
from which that argument could be made, and refuses to make it: a run with a
fake provider is labelled a rehearsal, in the directory, in the manifest and at
the top of the report.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:  # pragma: no cover - import shim for direct execution
    sys.path.insert(0, str(REPO_ROOT))

from app.config import get_settings  # noqa: E402
from app.db.session import session_scope  # noqa: E402
from app.embeddings import build_embedding_provider  # noqa: E402
from app.llm import build_llm_provider  # noqa: E402
from app.llm.base import load_prompt  # noqa: E402
from app.logging_setup import configure_logging  # noqa: E402
from evaluation.benchmark import (  # noqa: E402
    ARMS,
    REHEARSAL_MARKER,
    ArmResult,
    EmptyClassError,
    Excluded,
    GoldRow,
    PairedChange,
    build_manifest,
    pair,
    parse_gold_rows,
    render_report,
    run_arm,
    select_eligible,
    terminology_fingerprint,
    write_manifest,
)
from evaluation.run_eval import read_gold  # noqa: E402

ARM_FIELDS = [
    "row_id",
    "arm",
    "term",
    "class",
    "expected_code",
    "suggested_code",
    "status",
    "correct",
    "expected_rank",
    "top3",
    "failure",
    "matched_field",
    "gate_fired",
    "gate_reason",
    "candidate_codes",
    "ranked_codes",
    "proposal_id",
]


def _arm_row(result: ArmResult) -> dict[str, Any]:
    return {
        "row_id": result.row_id,
        "arm": result.arm,
        "term": result.term,
        "class": result.case_class,
        "expected_code": result.expected_code,
        "suggested_code": result.suggested_code or "",
        "status": result.status,
        "correct": "yes" if result.correct else "no",
        "expected_rank": result.expected_rank or "",
        "top3": "yes" if result.top3 else "no",
        "failure": result.failure or "",
        "matched_field": result.matched_field or "",
        "gate_fired": "yes" if result.gate_fired else "no",
        "gate_reason": result.gate_reason,
        "candidate_codes": " ".join(result.candidate_codes),
        "ranked_codes": " ".join(result.ranked_codes),
        "proposal_id": result.proposal_id,
    }


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _write_misses(path: Path, results: dict[str, list[ArmResult]]) -> None:
    rows = [
        _arm_row(result) | {"gate_values": str(result.gate_values)}
        for arm in ARMS
        for result in results[arm]
        if not result.correct
    ]
    _write_csv(path, [*ARM_FIELDS, "gate_values"], rows)


def _write_paired(path: Path, changes: list[PairedChange]) -> None:
    _write_csv(
        path,
        [
            "comparison",
            "row_id",
            "term",
            "class",
            "expected_code",
            "before_arm",
            "before_code",
            "after_arm",
            "after_code",
            "verdict",
        ],
        [
            {
                "comparison": c.comparison,
                "row_id": c.row_id,
                "term": c.term,
                "class": c.case_class,
                "expected_code": c.expected_code,
                "before_arm": c.before_arm,
                "before_code": c.before_code or "",
                "after_arm": c.after_arm,
                "after_code": c.after_code or "",
                "verdict": c.verdict,
            }
            for c in changes
        ],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__ or "")
    parser.add_argument("--system", required=True, choices=["icd10se", "kva"])
    parser.add_argument("--version", required=True)
    parser.add_argument("--gold", required=True, type=Path)
    parser.add_argument(
        "--run-id",
        default=None,
        help="Directory name under evaluation/runs/. Defaults to a UTC timestamp.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=REPO_ROOT / "evaluation" / "runs",
        help="Where run directories are created.",
    )
    parser.add_argument(
        "--keep-proposals",
        action="store_true",
        help=(
            "Write the proposals to the audit trail. Off by default: three arms "
            "over a full gold set is three audit rows per term, which is noise "
            "rather than history until the run is the formal one."
        ),
    )
    args = parser.parse_args(argv)

    configure_logging("WARNING")
    settings = get_settings()

    if not args.gold.exists():
        raise SystemExit(f"no such gold file: {args.gold}")

    llm = build_llm_provider(settings)
    embeddings = build_embedding_provider(settings)
    prompt = load_prompt("rerank_v1")
    fake = [p.provider_id for p in (llm, embeddings) if p.provider_id == "fake"]
    run_kind = "rehearsal" if fake else "formal"

    run_id = args.run_id or f"{run_kind}-{_timestamp()}"
    run_dir = Path(args.out_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    raw = read_gold(args.gold, allow_negative=True)
    rows, malformed = parse_gold_rows(raw)

    with session_scope() as session:
        eligible, ineligible = select_eligible(
            session, rows, system=args.system, version=args.version
        )
        fingerprint = terminology_fingerprint(session, system=args.system, version=args.version)

    if not eligible:
        raise SystemExit(
            f"no eligible rows in {args.gold} for ({args.system}, {args.version}). "
            f"Is the terminology loaded?"
        )

    excluded: list[Excluded] = [*malformed, *ineligible]
    classes = sorted({row.case_class for row in rows if _in_run(row, args.system, args.version)})

    print(f"{run_kind.upper()}  run={run_id}")
    print(f"  {len(eligible)} eligible of {len(rows)} rows, {len(excluded)} excluded")
    if fake:
        print(f"  fake provider(s): {', '.join(sorted(set(fake)))} -- not a quality result")

    results: dict[str, list[ArmResult]] = {}
    for arm in ARMS:
        print(f"  running arm {arm} ...", flush=True)
        results[arm] = run_arm(
            session_scope,
            eligible,
            arm=arm,
            system=args.system,
            version=args.version,
            settings=settings,
            embedding_provider=embeddings,
            llm_provider=llm,
            prompt=prompt,
            run_id=run_id,
            dry_run=not args.keep_proposals,
        )
        _write_csv(run_dir / f"{arm}.csv", ARM_FIELDS, [_arm_row(r) for r in results[arm]])

    paired = [
        *pair(results["lexical"], results["hybrid"], "hybrid vs lexical"),
        *pair(results["hybrid"], results["full"], "full vs hybrid"),
    ]
    _write_paired(run_dir / "paired_changes.csv", paired)
    _write_misses(run_dir / "misses.csv", results)

    manifest = build_manifest(
        run_id=run_id,
        run_kind=run_kind,
        gold_path=args.gold,
        total_rows=len(raw),
        eligible=eligible,
        excluded=excluded,
        classes=classes,
        system=args.system,
        version=args.version,
        fingerprint=fingerprint,
        settings=settings,
        embedding_provider=embeddings,
        llm_provider=llm,
        prompt=prompt,
    )
    write_manifest(run_dir / "manifest.json", manifest)

    try:
        report = render_report(
            manifest=manifest,
            results=results,
            classes=classes,
            excluded=excluded,
            paired=paired,
        )
    except EmptyClassError as exc:
        raise SystemExit(f"ERROR: {exc}") from exc
    (run_dir / "report.md").write_text(report, encoding="utf-8")

    if run_kind == "rehearsal":
        (run_dir / "REHEARSAL-FAKE-PROVIDERS-NOT-A-QUALITY-RESULT.md").write_text(
            f"# {REHEARSAL_MARKER}\n\n"
            "This directory is a rehearsal of the benchmark machinery with the\n"
            "deterministic fake providers. The fake reranker sorts by lexical\n"
            "score and understands no language; the fake embedder hashes\n"
            "character trigrams and cannot match a paraphrase.\n\n"
            "Nothing in `report.md` is a statement about mapping quality. It\n"
            "exists to show that the instrument produces what it claims to\n"
            "produce. See `report.md` for the run itself.\n",
            encoding="utf-8",
        )

    print(f"\n  wrote {run_dir}")
    for name in sorted(p.name for p in run_dir.iterdir()):
        print(f"    {name}")
    return 0


def _in_run(row: GoldRow, system: str, version: str) -> bool:
    return row.target_system == system and (not row.version or row.version == version)


def _timestamp() -> str:
    from evaluation.benchmark import utc_now

    return utc_now().replace(":", "").replace("-", "").replace("+0000", "Z")


if __name__ == "__main__":
    raise SystemExit(main())

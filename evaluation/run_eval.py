#!/usr/bin/env python
"""Run a gold set through the pipeline and report how it did.

    python evaluation/run_eval.py --gold evaluation/gold/sample_icd10se.csv \
        --system icd10se --version 2026-sample --provider fake

This ships as an instrument, not as a result. The repository states no accuracy
figures, because a figure is only meaningful against a gold set curated with the
official coding guidance -- which is the author's work, not the tool's. This
script is what makes that curation immediately useful the day it exists.

Every evaluated row creates a real proposal in the audit trail, because it is a
real mapping attempt. Rows from one run share a trace_id prefix (`eval-<run>-`)
so they can be told apart from clinical use afterwards. `--dry-run` computes the
same metrics without writing them, for gold sets large enough that one audit row
per term is noise rather than history.
"""

from __future__ import annotations

import argparse
import csv
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings
from app.db.session import session_scope
from app.embeddings import build_embedding_provider
from app.llm import build_llm_provider
from app.llm.base import load_prompt
from app.logging_setup import configure_logging
from app.pipeline.map_term import map_term
from evaluation.metrics import RowResult, format_summary, summarize

SAMPLE_MARKER = "SAMPLE ONLY"


def read_gold(path: Path) -> list[dict[str, str]]:
    """Read a gold CSV, skipping `#` comment lines."""
    with path.open("r", encoding="utf-8", newline="") as handle:
        lines = [line for line in handle if not line.lstrip().startswith("#")]
    rows = list(csv.DictReader(lines))
    for index, row in enumerate(rows, start=1):
        for column in ("term", "target_system", "expected_code"):
            if not (row.get(column) or "").strip():
                raise SystemExit(f"{path}: row {index} has no {column}")
    return rows


def is_sample(path: Path) -> bool:
    head = path.read_text(encoding="utf-8")[:2000]
    return SAMPLE_MARKER in head or path.name.startswith("sample_")


def banner(text: str) -> str:
    rule = "!" * 74
    body = "\n".join(f"!! {line}" for line in text.strip().splitlines())
    return f"\n{rule}\n{body}\n{rule}\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__ or "")
    parser.add_argument("--gold", required=True, type=Path)
    parser.add_argument("--system", required=True, choices=["icd10se", "kva"])
    parser.add_argument("--version", required=True)
    parser.add_argument(
        "--provider",
        default=None,
        help="LLM provider for this run; overrides LLM_PROVIDER.",
    )
    parser.add_argument("--model", default=None, help="Overrides LLM_MODEL.")
    parser.add_argument("--embedding-provider", default=None, help="Overrides EMBEDDING_PROVIDER.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Compute the metrics without writing proposals. Useful for a large "
            "gold set, where one audit row per term is noise rather than "
            "history. The default records them, because an evaluated row is a "
            "real mapping attempt."
        ),
    )
    parser.add_argument(
        "--misses",
        type=Path,
        default=None,
        help="Where to write the per-row misses CSV (default: alongside --gold).",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging("WARNING")  # the report is the output; keep the noise down
    overrides: dict[str, object] = {}
    if args.provider:
        overrides["llm_provider"] = args.provider
    if args.model:
        overrides["llm_model"] = args.model
    if args.embedding_provider:
        overrides["embedding_provider"] = args.embedding_provider
    if overrides:
        settings = settings.model_copy(update=overrides)

    if not args.gold.exists():
        raise SystemExit(f"no such gold file: {args.gold}")
    gold = read_gold(args.gold)

    warnings: list[str] = []
    if is_sample(args.gold):
        warnings.append(
            f"{args.gold.name} is the bundled SAMPLE gold set.\n"
            "It is a dozen easy rows against a 25-concept fixture, built to prove\n"
            "this script runs. The numbers below measure nothing about mapping\n"
            "quality. Do not publish them."
        )
    if settings.llm_provider == "fake":
        warnings.append(
            "LLM provider is `fake`: a deterministic sort by lexical score, with\n"
            "no language understanding at all. This run measures the plumbing,\n"
            "not the mapping."
        )
    if settings.embedding_provider == "fake":
        warnings.append(
            "Embedding provider is `fake`: hashed character trigrams, not a\n"
            "semantic model. The vector stage cannot match a paraphrase here."
        )
    for warning in warnings:
        print(banner(warning))

    llm = build_llm_provider(settings)
    embeddings = build_embedding_provider(settings)
    prompt = load_prompt("rerank_v1")
    run_id = uuid.uuid4().hex[:8]

    if args.dry_run:
        print("dry run: metrics only, no proposals will be written\n")
    print(
        f"gold={args.gold}  system={args.system}  version={args.version}\n"
        f"llm={llm.provider_id}/{llm.model_id}  "
        f"embeddings={embeddings.provider_id}/{embeddings.model_id}\n"
        f"prompt={prompt.prompt_id}@{prompt.sha256[:12]}  run={run_id}\n"
    )

    results: list[RowResult] = []
    for index, row in enumerate(gold, start=1):
        if row["target_system"].strip() != args.system:
            continue
        term = row["term"].strip()
        expected = row["expected_code"].strip()

        with session_scope() as session:
            outcome = map_term(
                session,
                text=term,
                target_system=args.system,
                version=args.version,
                trace_id=f"eval-{run_id}-{index}",
                settings=settings,
                embedding_provider=embeddings,
                llm_provider=llm,
                prompt=prompt,
                origin="eval",
            )
            proposal = outcome.proposal
            ranked = [entry["code"] for entry in (proposal.rerank or {}).get("ranked", [])]
            if args.dry_run:
                # Roll the proposal back: the metrics are computed from the
                # same objects either way, only the audit row is discarded.
                session.rollback()
            results.append(
                RowResult(
                    term=term,
                    expected_code=expected,
                    suggested_code=proposal.suggested_code,
                    ranked_codes=ranked,
                    candidate_codes=[c["code"] for c in proposal.candidates],
                    status=proposal.status,
                    latency_ms_retrieval=proposal.latency_ms_retrieval,
                    latency_ms_rerank=proposal.latency_ms_rerank,
                    proposal_id=str(proposal.id),
                    note=(row.get("note") or "").strip(),
                )
            )
        print(
            f"  [{index:>3}/{len(gold)}] {term[:48]:<50} -> "
            f"{proposal.suggested_code or '-':<8} (expected {expected})"
        )

    if not results:
        raise SystemExit(f"no rows in {args.gold} target system {args.system!r}")

    summary = summarize(results)
    print("\n" + format_summary(summary))

    misses_path = args.misses or args.gold.with_name(f"{args.gold.stem}_misses_{run_id}.csv")
    _write_misses(misses_path, summary.misses)
    print(f"\n{len(summary.misses)} miss(es) written to {misses_path}")

    if warnings:
        print(banner("Reminder: see the warnings above before quoting any of these numbers."))
    return 0


def _write_misses(path: Path, misses: list[RowResult]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "term",
                "expected_code",
                "suggested_code",
                "miss_kind",
                "expected_was_retrieved",
                "ranked_codes",
                "candidate_codes",
                "status",
                "proposal_id",
                "note",
            ]
        )
        for miss in misses:
            writer.writerow(
                [
                    miss.term,
                    miss.expected_code,
                    miss.suggested_code or "",
                    miss.miss_kind,
                    "yes" if miss.retrieved else "no",
                    " ".join(miss.ranked_codes),
                    " ".join(miss.candidate_codes),
                    miss.status,
                    miss.proposal_id,
                    miss.note,
                ]
            )


if __name__ == "__main__":
    raise SystemExit(main())

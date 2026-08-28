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


def read_gold(path: Path, *, allow_negative: bool = False) -> list[dict[str, str]]:
    """Read a gold CSV, skipping `#` comment lines.

    Every row gets a `row_id`: its 1-based position among the data rows, which
    is what pairs an arm's result with the same row in another arm. Line number
    is enough -- a gold set is frozen once it is used, so positions are stable
    -- and it is recorded in the manifest so a reader can find the row again.

    `allow_negative` permits an empty `expected_code`, which is how a row states
    that the correct outcome is *no code*. `run_eval.py` does not use those
    rows; `evaluation/benchmark.py` does, because a benchmark that cannot
    measure false positives is measuring half the problem.
    """
    with path.open("r", encoding="utf-8", newline="") as handle:
        lines = [line for line in handle if not line.lstrip().startswith("#")]
    rows = list(csv.DictReader(lines))
    required = (
        ("term", "target_system") if allow_negative else ("term", "target_system", "expected_code")
    )
    for index, row in enumerate(rows, start=1):
        row["row_id"] = str(index)
        for column in required:
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
        "--arm",
        choices=["lexical", "hybrid", "full"],
        default="full",
        help=(
            "Which pipeline to measure. `lexical` is lexical retrieval only, "
            "`hybrid` adds vector retrieval and the RRF merge, `full` adds the "
            "LLM rerank. Neither `lexical` nor `hybrid` calls the model at all. "
            "lexical vs hybrid answers what vector retrieval adds; hybrid vs "
            "full answers what the LLM adds."
        ),
    )
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
    if args.arm == "full" and settings.llm_provider == "fake":
        warnings.append(
            "LLM provider is `fake`: a deterministic sort by lexical score, with\n"
            "no language understanding at all. This run measures the plumbing,\n"
            "not the mapping."
        )
    if args.arm != "full" and settings.embedding_provider == "fake" and args.arm == "hybrid":
        warnings.append(
            "Embedding provider is `fake`: hashed character trigrams, not a\n"
            "semantic model. The vector stage cannot match a paraphrase here,\n"
            "so the `hybrid` arm cannot show what a real one would add."
        )
    if args.arm == "full" and settings.embedding_provider == "fake":
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
    # The arm decides which of these actually ran, so say so rather than listing
    # a model that was never called.
    llm_line = (
        f"llm={llm.provider_id}/{llm.model_id}"
        if args.arm == "full"
        else "llm=(not called: measurement arm)"
    )
    embeddings_line = (
        f"embeddings={embeddings.provider_id}/{embeddings.model_id}"
        if args.arm != "lexical"
        else "embeddings=(not called: lexical arm)"
    )
    print(
        f"arm={args.arm}\n"
        f"gold={args.gold}  system={args.system}  version={args.version}\n"
        f"{llm_line}  {embeddings_line}\n"
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
                arm=args.arm,
            )
            proposal = outcome.proposal
            if args.arm == "full":
                ranked = [entry["code"] for entry in (proposal.rerank or {}).get("ranked", [])]
            else:
                # No rerank ran, so the arm's own ordering is its ranking:
                # lexical rank for `lexical`, the RRF merge for `hybrid`. The
                # proposal's `rerank` column stays null -- this is a metric,
                # not a record of something that happened.
                ranked = [c["code"] for c in proposal.candidates]
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
                    arm=args.arm,
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
                "arm",
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
                    miss.arm,
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

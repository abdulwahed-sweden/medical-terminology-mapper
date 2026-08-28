"""The Phase 3 benchmark: three arms, one eligible row set, one run directory.

This module is the measuring instrument. It makes no claim about mapping
quality -- it produces the artefacts from which such a claim could later be
argued, and it is deliberately noisy about the conditions under which its
numbers mean nothing.

Two properties matter more than any metric here:

*Every arm sees the identical row set.* A comparison across arms that quietly
dropped different rows would be comparing different questions. Eligibility is
decided once, before any arm runs, and is a definition rather than a judgement:
a row is eligible when its system and version match the run and its expected
code exists and is assignable in the loaded terminology. Everything else is
excluded, counted, categorised, and listed.

*Every number is reproducible from `manifest.json` plus the repository at the
recorded SHA.* That is why the manifest carries the dataset hash, the
terminology fingerprint, the prompt hash and the whole gate and retrieval
configuration, and why none of those fields is optional.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import ConceptRow
from app.embeddings.base import EmbeddingProvider
from app.llm.base import LLMProvider, PromptSpec
from app.pipeline.gate import GATE_ID, GATE_VERSION
from app.pipeline.map_term import Arm, map_term

ARMS: tuple[Arm, ...] = ("lexical", "hybrid", "full")

UNCLASSIFIED = "unclassified"

# Exclusion reasons. Both are taxonomy entries 1 and 2; they are decided before
# any arm runs, so they never appear as an arm's failure.
DATA_PROBLEM = "evaluation_data_problem"
CODE_NOT_PRESENT = "expected_code_not_present_in_loaded_terminology"

REHEARSAL_MARKER = "REHEARSAL — FAKE PROVIDERS — NOT A QUALITY RESULT"

LOW_N = 30


# --------------------------------------------------------------------------- #
# Rows
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class GoldRow:
    row_id: int
    term: str
    target_system: str
    expected_code: str
    case_class: str
    source: str
    note: str
    version: str = ""

    @property
    def is_negative(self) -> bool:
        """A row whose correct outcome is *no code at all*."""
        return not self.expected_code


@dataclass(frozen=True)
class Excluded:
    row_id: int
    term: str
    expected_code: str
    reason: str


@dataclass
class ArmResult:
    row_id: int
    arm: str
    term: str
    case_class: str
    expected_code: str
    suggested_code: str | None
    status: str
    ranked_codes: list[str]
    candidate_codes: list[str]
    matched_field: str | None
    gate_fired: bool
    gate_reason: str
    gate_values: dict[str, Any]
    proposal_id: str
    rerank_is_null: bool

    @property
    def is_negative(self) -> bool:
        return not self.expected_code

    @property
    def correct(self) -> bool:
        """Top-1 correctness, including the rule for negative rows."""
        if self.is_negative:
            # Declining to answer is the right answer here. Any code is wrong,
            # however plausible it looks.
            return self.suggested_code is None and self.status == "no_good_match"
        return self.suggested_code == self.expected_code

    @property
    def expected_rank(self) -> int | None:
        if self.is_negative or self.expected_code not in self.ranked_codes:
            return None
        return self.ranked_codes.index(self.expected_code) + 1

    @property
    def top3(self) -> bool:
        if self.is_negative:
            return self.correct
        rank = self.expected_rank
        return rank is not None and rank <= 3

    @property
    def failure(self) -> str | None:
        """First match wins, in the order the taxonomy defines."""
        if self.correct:
            return None
        if self.is_negative:
            # 3. A code was suggested where none should have been.
            return "false_match_on_negative"
        if self.status == "no_good_match":
            # 4. The expected code is in the terminology -- eligibility already
            #    established that -- so declining was wrong. Which signal did
            #    it is the interesting half.
            return "gate_false_reject" if self.gate_fired else "false_no_good_match"
        if self.expected_code not in self.candidate_codes:
            # 5. Retrieval never surfaced it; nothing downstream could recover.
            return "retrieval_miss"
        if self.arm == "full" and self.expected_code not in self.ranked_codes:
            # 6. It was retrieved and the model dropped it.
            return "rerank_miss"
        rank = self.expected_rank
        if rank is not None and rank <= 3:
            return "wrong_top1_but_expected_in_top3"
        return "wrong_top3"


# --------------------------------------------------------------------------- #
# Eligibility
# --------------------------------------------------------------------------- #


def parse_gold_rows(raw: list[dict[str, str]]) -> tuple[list[GoldRow], list[Excluded]]:
    """Turn CSV dicts into rows, setting aside the ones that are malformed."""
    rows: list[GoldRow] = []
    excluded: list[Excluded] = []
    for entry in raw:
        row_id = int(entry["row_id"])
        term = (entry.get("term") or "").strip()
        system = (entry.get("target_system") or "").strip()
        expected = (entry.get("expected_code") or "").strip()
        case_class = (entry.get("class") or "").strip() or UNCLASSIFIED

        if not term or not system:
            excluded.append(Excluded(row_id, term, expected, DATA_PROBLEM))
            continue
        rows.append(
            GoldRow(
                row_id=row_id,
                term=term,
                target_system=system,
                expected_code=expected,
                case_class=case_class,
                source=(entry.get("source") or "").strip(),
                note=(entry.get("note") or "").strip(),
                version=(entry.get("version") or "").strip(),
            )
        )
    return rows, excluded


def select_eligible(
    session: Session,
    rows: list[GoldRow],
    *,
    system: str,
    version: str,
) -> tuple[list[GoldRow], list[Excluded]]:
    """Decide once, for every arm. This is a definition, not a judgement.

    A row belongs to this run when it names this system (and this version, if it
    names one at all), and when the code it expects is one the loaded
    terminology could actually propose. A gold row expecting a code that is not
    loaded measures the loader, not the mapper, and counting it would move every
    arm's denominator for a reason unrelated to mapping.
    """
    assignable = set(
        session.scalars(
            sa.select(ConceptRow.code).where(
                ConceptRow.system == system,
                ConceptRow.version == version,
                ConceptRow.assignable,
                ConceptRow.placeholder.is_(False),
            )
        )
    )

    eligible: list[GoldRow] = []
    excluded: list[Excluded] = []
    for row in rows:
        if row.target_system != system:
            continue  # another system's row: not this run's business at all
        if row.version and row.version != version:
            continue
        if not row.is_negative and row.expected_code not in assignable:
            excluded.append(Excluded(row.row_id, row.term, row.expected_code, CODE_NOT_PRESENT))
            continue
        eligible.append(row)
    return eligible, excluded


# --------------------------------------------------------------------------- #
# Running an arm
# --------------------------------------------------------------------------- #


def run_arm(
    session_factory: Any,
    rows: list[GoldRow],
    *,
    arm: Arm,
    system: str,
    version: str,
    settings: Settings,
    embedding_provider: EmbeddingProvider,
    llm_provider: LLMProvider,
    prompt: PromptSpec,
    run_id: str,
    dry_run: bool = True,
) -> list[ArmResult]:
    """Run one arm over the eligible rows.

    `dry_run` rolls each proposal back after reading it, which assumes
    `session_factory` hands out a session per row -- as `session_scope` does.
    A caller that yields one shared session for every row must pass
    `dry_run=False`, or the rollback will discard whatever else that
    transaction was holding.
    """
    results: list[ArmResult] = []
    for row in rows:
        with session_factory() as session:
            outcome = map_term(
                session,
                text=row.term,
                target_system=system,
                version=version,
                trace_id=f"bench-{run_id}-{arm}-{row.row_id}",
                settings=settings,
                embedding_provider=embedding_provider,
                llm_provider=llm_provider,
                prompt=prompt,
                origin="eval",
                arm=arm,
            )
            proposal = outcome.proposal
            if arm == "full":
                ranked = [e["code"] for e in (proposal.rerank or {}).get("ranked", [])]
            else:
                # No rerank ran; the arm's retrieval order *is* its ranking.
                ranked = [c["code"] for c in proposal.candidates]

            results.append(
                ArmResult(
                    row_id=row.row_id,
                    arm=arm,
                    term=row.term,
                    case_class=row.case_class,
                    expected_code=row.expected_code,
                    suggested_code=proposal.suggested_code,
                    status=proposal.status,
                    ranked_codes=ranked,
                    candidate_codes=[c["code"] for c in proposal.candidates],
                    matched_field=_matched_field(proposal.candidates, row.expected_code),
                    gate_fired=proposal.gate_fired,
                    gate_reason=str((proposal.gate_values or {}).get("reason", "")),
                    gate_values={
                        k: v for k, v in (proposal.gate_values or {}).items() if k != "reason"
                    },
                    proposal_id=str(proposal.id),
                    rerank_is_null=proposal.rerank is None,
                )
            )
            if dry_run:
                session.rollback()
    return results


def _matched_field(candidates: list[dict[str, Any]], expected: str) -> str | None:
    for candidate in candidates:
        if candidate.get("code") == expected:
            value = candidate.get("matched_field")
            return str(value) if value is not None else None
    return None


# --------------------------------------------------------------------------- #
# Comparison
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PairedChange:
    row_id: int
    term: str
    case_class: str
    expected_code: str
    comparison: str
    before_arm: str
    after_arm: str
    before_code: str | None
    after_code: str | None
    verdict: Literal["improved", "worsened", "unchanged"]


def pair(before: list[ArmResult], after: list[ArmResult], label: str) -> list[PairedChange]:
    """Row-by-row, on the identical eligible set, matched by row_id."""
    by_id = {r.row_id: r for r in before}
    changes: list[PairedChange] = []
    for later in after:
        earlier = by_id[later.row_id]
        if earlier.correct == later.correct:
            verdict: Literal["improved", "worsened", "unchanged"] = "unchanged"
        elif later.correct:
            verdict = "improved"
        else:
            verdict = "worsened"
        changes.append(
            PairedChange(
                row_id=later.row_id,
                term=later.term,
                case_class=later.case_class,
                expected_code=later.expected_code,
                comparison=label,
                before_arm=earlier.arm,
                after_arm=later.arm,
                before_code=earlier.suggested_code,
                after_code=later.suggested_code,
                verdict=verdict,
            )
        )
    return changes


@dataclass
class ClassRow:
    case_class: str
    n: int
    per_arm: dict[str, tuple[float, float]] = field(default_factory=dict)


def per_class(results: dict[str, list[ArmResult]], classes: list[str]) -> list[ClassRow]:
    """One table row per class. A class present in the gold set but empty here
    is a bug in the run, not a zero -- the caller raises on it."""
    table: list[ClassRow] = []
    for case_class in classes:
        rows = [r for r in results[ARMS[0]] if r.case_class == case_class]
        entry = ClassRow(case_class=case_class, n=len(rows))
        for arm in ARMS:
            arm_rows = [r for r in results[arm] if r.case_class == case_class]
            total = len(arm_rows)
            top1 = sum(r.correct for r in arm_rows) / total if total else 0.0
            top3 = sum(r.top3 for r in arm_rows) / total if total else 0.0
            entry.per_arm[arm] = (top1, top3)
        table.append(entry)
    return table


# --------------------------------------------------------------------------- #
# Provenance
# --------------------------------------------------------------------------- #


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def terminology_fingerprint(session: Session, *, system: str, version: str) -> dict[str, Any]:
    """Concept count plus a hash over the loaded content.

    Two runs quoting the same terminology version can still have been run
    against different data -- a partial load, a re-load from a corrected file.
    The hash is what makes "same version" checkable rather than asserted.
    """
    pairs = session.execute(
        sa.select(ConceptRow.code, ConceptRow.preferred_term)
        .where(ConceptRow.system == system, ConceptRow.version == version)
        .order_by(ConceptRow.code)
    ).all()
    digest = hashlib.sha256()
    for code, preferred_term in pairs:
        digest.update(f"{code}\x1f{preferred_term}\x1e".encode())
    return {"concept_count": len(pairs), "sha256": digest.hexdigest()}


def git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=Path(__file__).resolve().parent.parent,
        ).stdout.strip()
    except Exception:  # pragma: no cover - a tarball checkout has no git
        return "unknown"


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def build_manifest(
    *,
    run_id: str,
    run_kind: str,
    gold_path: Path,
    total_rows: int,
    eligible: list[GoldRow],
    excluded: list[Excluded],
    classes: list[str],
    system: str,
    version: str,
    fingerprint: dict[str, Any],
    settings: Settings,
    embedding_provider: EmbeddingProvider,
    llm_provider: LLMProvider,
    prompt: PromptSpec,
) -> dict[str, Any]:
    excluded_by_reason: dict[str, int] = {}
    for row in excluded:
        excluded_by_reason[row.reason] = excluded_by_reason.get(row.reason, 0) + 1

    class_counts = {c: sum(1 for r in eligible if r.case_class == c) for c in classes}
    fake_providers = [
        name
        for name, provider_id in (
            ("llm", llm_provider.provider_id),
            ("embeddings", embedding_provider.provider_id),
        )
        if provider_id == "fake"
    ]

    return {
        "run_id": run_id,
        "run_kind": run_kind,
        "timestamp_utc": utc_now(),
        "git_sha": git_sha(),
        "dataset": {
            "path": str(gold_path),
            "sha256": sha256_file(gold_path),
            "total_rows": total_rows,
            "eligible_rows": len(eligible),
            "excluded_rows": len(excluded),
            "excluded_by_reason": excluded_by_reason,
            "class_counts": class_counts,
        },
        "terminology": {
            "system": system,
            "version": version,
            "fingerprint": fingerprint,
        },
        "arms": {
            "lexical": "lexical retrieval only; no vector stage; no LLM call",
            "hybrid": "lexical + vector retrieval, RRF merged; no LLM call",
            "full": "lexical + vector retrieval, RRF merged, then LLM rerank",
        },
        "providers": {
            "llm": {
                "kind": "fake" if llm_provider.provider_id == "fake" else "live",
                "provider": llm_provider.provider_id,
                "model": llm_provider.model_id,
            },
            "embeddings": {
                "kind": "fake" if embedding_provider.provider_id == "fake" else "live",
                "provider": embedding_provider.provider_id,
                "model": embedding_provider.model_id,
                "dimension": embedding_provider.dim,
            },
            "fake_providers": fake_providers,
        },
        "prompt": {"id": prompt.prompt_id, "sha256": prompt.sha256},
        "gate": {
            "id": GATE_ID,
            "version": GATE_VERSION,
            "configuration": {
                "gate_min_ts_rank": settings.gate_min_ts_rank,
                "gate_min_strict_similarity": settings.gate_min_strict_similarity,
                "gate_min_query_chars": settings.gate_min_query_chars,
                "gate_vector_floors": settings.gate_vector_floors,
            },
        },
        "retrieval": {
            "lexical_top_k": settings.lexical_top_k,
            "vector_top_k": settings.vector_top_k,
            "rerank_candidate_cap": settings.rerank_candidate_cap,
            "trigram_threshold": settings.trigram_threshold,
            "index_descriptions": settings.index_descriptions,
            "rrf_k": settings.rrf_k,
        },
    }


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# The report
# --------------------------------------------------------------------------- #


class EmptyClassError(RuntimeError):
    """A class the gold set declares produced no eligible rows.

    Raised rather than printing an empty table: a class that silently vanishes
    between the dataset and the report is how a reader concludes the benchmark
    covered something it never touched.
    """


def _pct(value: float, n: int) -> str:
    # A percentage without its n is not a measurement, so they are never
    # formatted apart.
    return f"{value * 100:.0f}% ({round(value * n)}/{n})"


def _banner(lines: list[str]) -> str:
    rule = "!" * 74
    body = "\n".join(f"!! {line}" for line in lines)
    return f"{rule}\n{body}\n{rule}"


def render_report(
    *,
    manifest: dict[str, Any],
    results: dict[str, list[ArmResult]],
    classes: list[str],
    excluded: list[Excluded],
    paired: list[PairedChange],
) -> str:
    dataset = manifest["dataset"]
    providers = manifest["providers"]
    run_kind = manifest["run_kind"]
    heading = REHEARSAL_MARKER if run_kind == "rehearsal" else "FORMAL RUN"

    table = per_class(results, classes)
    empty = [row.case_class for row in table if row.n == 0]
    if empty:
        raise EmptyClassError(
            f"classes present in {dataset['path']} produced no eligible rows: "
            f"{', '.join(sorted(empty))}. A benchmark that silently drops a class "
            f"reports on a dataset nobody chose."
        )

    out: list[str] = [f"# {heading}", ""]

    if providers["fake_providers"]:
        out += [
            _banner(
                [
                    "THESE NUMBERS ARE NOT A MAPPING-QUALITY RESULT.",
                    "",
                    f"Fake provider(s) in use: {', '.join(providers['fake_providers'])}.",
                    "The fake reranker sorts by lexical score and understands no",
                    "language. The fake embedder hashes character trigrams and cannot",
                    "match a paraphrase. What follows measures the instrument, not",
                    "the mapper.",
                ]
            ),
            "",
        ]

    out += [
        "| | |",
        "| --- | --- |",
        f"| run | `{manifest['run_id']}` |",
        f"| date (UTC) | {manifest['timestamp_utc']} |",
        f"| git SHA | `{manifest['git_sha']}` |",
        f"| system / version | {manifest['terminology']['system']} / "
        f"{manifest['terminology']['version']} |",
        f"| terminology fingerprint | {manifest['terminology']['fingerprint']['concept_count']} "
        f"concepts, `{manifest['terminology']['fingerprint']['sha256'][:16]}…` |",
        f"| gold set | `{Path(dataset['path']).name}` |",
        f"| gold rows | {dataset['total_rows']} total, {dataset['eligible_rows']} eligible, "
        f"{dataset['excluded_rows']} excluded |",
        f"| gold SHA-256 | `{dataset['sha256']}` |",
        f"| LLM | {providers['llm']['kind']}: "
        f"{providers['llm']['provider']}/{providers['llm']['model']} |",
        f"| embeddings | {providers['embeddings']['kind']}: "
        f"{providers['embeddings']['provider']}/{providers['embeddings']['model']} "
        f"({providers['embeddings']['dimension']}d) |",
        f"| prompt | {manifest['prompt']['id']} @ `{manifest['prompt']['sha256'][:12]}` |",
        f"| gate | {manifest['gate']['id']} v{manifest['gate']['version']}, "
        f"ts_rank>{manifest['gate']['configuration']['gate_min_ts_rank']}, "
        f"strict_sim≥{manifest['gate']['configuration']['gate_min_strict_similarity']}, "
        f"min_chars={manifest['gate']['configuration']['gate_min_query_chars']}, "
        f"vector_floors={manifest['gate']['configuration']['gate_vector_floors'] or 'none'} |",
        "",
    ]

    # --- A. per class, first ------------------------------------------------
    out += [
        "## A. By case class",
        "",
        "Per-class first, deliberately. An aggregate over a hand-chosen class mix",
        "describes the mix as much as the system.",
        "",
        "| class | n | | lexical | hybrid | full |",
        "| --- | ---: | --- | --- | --- | --- |",
    ]
    for row in table:
        low = "  **LOW N**" if row.n < LOW_N else ""
        out.append(
            f"| **{row.case_class}**{low} | {row.n} | Top-1 | "
            + " | ".join(_pct(row.per_arm[a][0], row.n) for a in ARMS)
            + " |"
        )
        out.append(
            "| | | Top-3 | " + " | ".join(_pct(row.per_arm[a][1], row.n) for a in ARMS) + " |"
        )
    out += [
        "",
        f"`LOW N` marks any class with fewer than {LOW_N} rows. At those sizes a",
        "single row moves the figure by several points; treat them as direction,",
        "not measurement.",
        "",
    ]

    # --- B. paired ----------------------------------------------------------
    out += [
        "## B. Paired comparison",
        "",
        "Same rows, same order, matched by `row_id`. *Improved* means Top-1 was",
        "wrong in the first arm and right in the second; *worsened* is the",
        "reverse. Row-level detail is in `paired_changes.csv`.",
        "",
        "| comparison | improved | worsened | unchanged | answers |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    answers = {
        "hybrid vs lexical": "what does vector retrieval add",
        "full vs hybrid": "what does the LLM add",
    }
    for label in ("hybrid vs lexical", "full vs hybrid"):
        rows = [c for c in paired if c.comparison == label]
        counts = {
            v: sum(1 for c in rows if c.verdict == v) for v in ("improved", "worsened", "unchanged")
        }
        out.append(
            f"| {label} | {counts['improved']} | {counts['worsened']} | "
            f"{counts['unchanged']} | {answers[label]} |"
        )
    out.append("")

    # --- C. failures --------------------------------------------------------
    out += [
        "## C. Failure breakdown",
        "",
        "| category | " + " | ".join(ARMS) + " |",
        "| --- | " + " | ".join("---:" for _ in ARMS) + " |",
    ]
    categories = [
        "false_match_on_negative",
        "gate_false_reject",
        "false_no_good_match",
        "retrieval_miss",
        "rerank_miss",
        "wrong_top1_but_expected_in_top3",
        "wrong_top3",
    ]
    for category in categories:
        per_arm_counts = [sum(1 for r in results[a] if r.failure == category) for a in ARMS]
        if not any(per_arm_counts):
            continue
        out.append(f"| `{category}` | " + " | ".join(str(c) for c in per_arm_counts) + " |")
    if not any(r.failure for arm in ARMS for r in results[arm]):
        out.append(
            "| *(no failures on the eligible set)* | " + " | ".join("0" for _ in ARMS) + " |"
        )
    out += [
        "",
        "Excluded before any arm ran, and absent from every denominator above:",
        "",
        "| reason | rows |",
        "| --- | ---: |",
    ]
    if dataset["excluded_by_reason"]:
        for reason, count in sorted(dataset["excluded_by_reason"].items()):
            out.append(f"| `{reason}` | {count} |")
    else:
        out.append("| *(none)* | 0 |")
    out.append("")

    # --- D. overall, last ---------------------------------------------------
    n = dataset["eligible_rows"]
    out += [
        "## D. Overall",
        "",
        "Last, and least informative. Read section A first.",
        "",
        "| arm | Top-1 | Top-3 |",
        "| --- | --- | --- |",
    ]
    for arm in ARMS:
        arm_rows = results[arm]
        top1 = sum(r.correct for r in arm_rows) / n if n else 0.0
        top3 = sum(r.top3 for r in arm_rows) / n if n else 0.0
        out.append(f"| {arm} | {_pct(top1, n)} | {_pct(top3, n)} |")
    out.append("")

    # --- E. misses appendix -------------------------------------------------
    out += ["## E. Misses", "", "Full detail, including gate values, in `misses.csv`.", ""]
    misses = [(a, r) for a in ARMS for r in results[a] if not r.correct]
    if misses:
        out += [
            "| arm | term | class | expected | suggested | rank | matched_field "
            "| gate | category |",
            "| --- | --- | --- | --- | --- | ---: | --- | --- | --- |",
        ]
        for arm, r in misses:
            rank = r.expected_rank
            out.append(
                f"| {arm} | {r.term} | {r.case_class} | {r.expected_code or '*(none)*'} | "
                f"{r.suggested_code or '*(none)*'} | {rank if rank else '–'} | "
                f"{r.matched_field or '–'} | "
                f"{'fired: ' + r.gate_reason if r.gate_fired else 'passed'} | `{r.failure}` |"
            )
    else:
        out.append("No misses on the eligible set.")
    if excluded:
        out += [
            "",
            "### Excluded rows",
            "",
            "| row_id | term | expected | reason |",
            "| ---: | --- | --- | --- |",
        ]
        for excluded_row in excluded:
            out.append(
                f"| {excluded_row.row_id} | {excluded_row.term} | "
                f"{excluded_row.expected_code or '*(none)*'} | `{excluded_row.reason}` |"
            )

    out += [
        "",
        "---",
        "",
        "*Numbers describe only this dataset and this run. They must not be copied",
        "into README or presented as clinical accuracy.*",
        "",
    ]
    return "\n".join(out)

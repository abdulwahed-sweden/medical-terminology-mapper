# Phase 1 build report

**Built:** 2026-08-26 · **Branch:** `phase-1-build` · **Tests:** 203 passed, 2 skipped · **Coverage:** 85%

> **Post-Phase-1 follow-up (2026-08-26):** both loaders now read `.xlsx` as well
> as `.tsv`, and the KVÅ loader has been **verified against the real published
> release**. §2 below is updated accordingly; see §7 for what moved from
> ASSUMED to verified.

Phase 1 is complete and verified against a running deployment. This report maps
what was built to the specification, then lists — without softening — everything
that had to be assumed, everywhere the build departed from the letter of the
spec, and the questions that need the author's answer.

---

## 1. What was built

| Spec § | Deliverable | Status |
| --- | --- | --- |
| §3 | Repository structure | Complete; additions listed in §3 below |
| §4.1 | `Concept` model + `TerminologySystem` protocol | Complete, exactly as specified |
| §4.2 | ICD-10-SE loader (`.tsv` + `.xlsx`) | Complete — format from the publisher's own file description; still `FORMAT_UNVERIFIED`, see §2 |
| §4.2 | KVÅ loader (`.tsv` + `.xlsx`) | Complete and **format-verified** against the real 2026 release |
| §4.3 | SNOMED CT adapter stub | Complete — `load()` raises `TerminologyLicenceRequired` |
| §5 | Swedish normalization | Complete; no stemming, recorded as a limitation |
| §6.1 | Lexical retrieval (FTS + pg_trgm) | Complete |
| §6.2 | Vector retrieval (pgvector) | Complete |
| §6.3 | Merge (RRF, dedupe, cap, both scores kept) | Complete |
| §7.1 | `LLMProvider` protocol + strict JSON schema | Complete |
| §7.1 | Hallucinated-code guard, tested | Complete |
| §7.1 | Malformed JSON → one repair → `rerank_failed` | Complete |
| §7.2 | Prompt versioning by SHA-256 | Complete |
| §7.3 | Deterministic fake reranker | Complete |
| §8.1 | `proposals` / `decisions` tables | Complete, every specified column present |
| §8.2 | `POST /map`, `GET /proposals/{id}`, `POST /decisions`, `GET /` | Complete |
| §8.3 | Validator page | Complete — server-rendered, no framework, no build step |
| §9 | Append-only enforced by DB trigger | Complete, verified against live `psql` |
| §9 | JSON logs with `trace_id` per request | Complete |
| §10 | Evaluation script, template, sample gold set | Complete |
| §11 | `LICENSING.md` with dates checked | Complete |
| §12 | Compose, ruff, mypy strict, pytest, CI | Complete |
| §13 | README + ARCHITECTURE.md | Complete |

### Verified against a running deployment, not just in tests

```
docker compose up -d && alembic upgrade head
  + load_terminology.py  (icd10se 25 concepts, kva 19 concepts)
  + embed_terminology.py --provider fake
  -> validator page live at :8000, no API keys, no network

POST /map "högt blodtryck" -> I10, status pending, decision null
POST /decisions accept     -> validated_mapping {system, version, code, decision_id}
POST /decisions again      -> HTTP 409
psql UPDATE decisions      -> ERROR: append-only table: UPDATE is not permitted
psql DELETE FROM proposals -> ERROR: append-only table: DELETE is not permitted
```

---

## 2. Assumptions and unverified items

### VERIFIED (2026-08-26) — the KVÅ column headers, against the real release

The loaders were written against the **publishers' own file-description
documents**. That has now been checked against an actual published file.

`kva-inkl-beskrivningstexter-2026.xlsx` was downloaded from
E-hälsomyndigheten and parsed. Result: **11 888 concepts, exactly the count the
workbook states for itself.** Of its 10 named columns, **9 mapped to the
loader's existing header aliases on first contact**:

| Column in the real file | Mapped to |
| --- | --- |
| `Kod` · `Titel` · `Beskrivning` · `Exempel` | `code` · `title` · `description` · `example` |
| `Innefattar` · `Utesluter` · `Kodningsinformation` | `includes` · `excludes` · `coding_info` |
| `Anmärkning` · `Relaterad ICF-kod` | `note` · `related_icf` |
| `Klassifikation` | *(no TSV counterpart — ignored)* |

Also confirmed against the real data: the code shapes (7 113 KKÅ `LLLDD`,
4 773 KMÅ `LLDDD`, 2 three-letter group headings), and that
`validate_code_format` rejects exactly those 2 group headings and nothing else.

**Two genuine differences from the documented TSV layout**, neither a parsing
bug:

1. The workbook adds `Klassifikation` (KKÅ or KMÅ), which the TSVs lack — it
   exists because the workbook merges the two files. No `Concept` field
   corresponds to it; it is ignored.
2. **The workbook has no `Överordnad kod` column.** KVÅ loaded from it has no
   parent links, so `chapter` is empty and every concept is a leaf. The reader
   logs `classification_source_has_no_parent_column`, because a silently flat
   hierarchy is easy to miss.

### UNVERIFIED — the ICD-10-SE column headers, because no file is obtainable

**Loader status: `FORMAT_UNVERIFIED` stands for ICD-10-SE only.**

As of 2026-08-26 there is no publicly downloadable machine-readable ICD-10-SE
release. The ICD-10 page offers the three PDF volumes and the coding guidance
and nothing else: its HTML contains no `.xlsx`, `.tsv`, `.csv` or `.zip`
reference at all, and eleven candidate filenames following both sibling naming
conventions (KVÅ's `<name>-inkl-beskrivningstexter-<year>.xlsx`, KSI's
`<name>-<year>.xlsx`) each returned HTTP 404.

Mitigation is unchanged — tolerant header matching, unknown columns ignored, a
missing required column failing loudly — and there is now indirect evidence for
it: the ICD-10-SE loader shares its reader and alias table with the KVÅ loader,
which the real release validated.

### UNVERIFIED — terms of use for the classification files

Neither authority page stated an explicit licence at the date checked. The
ICD-10-SE publications carry `© World Health Organization 1992` and a
cite-the-source requirement. No terminology content is redistributed here, so
nothing turns on it for this repository — but it must be settled before anyone
redistributes loaded content.

### ASSUMED — the multi-row-per-code rule (in the TSV; the workbook is one row per code)

The PDFs say a code occupies one or more rows "beroende på antalet egenskaper",
and that repeated `Exempel` / `Innefattar` / `Utesluter` values appear on
separate rows. They do not state precisely whether a code with one of each
occupies one row or three. The published row/code ratios support the
one-row-per-repeat reading (ICD-10-SE 82 490 rows / 38 928 codes ≈ 2.1;
KKÅ 9 306 / 8 663 ≈ 1.07), and the parser is insensitive to the difference: it
groups every row sharing a `Kod` and merges their properties. The real KVÅ
workbook turned out to be exactly one row per code (11 888 rows, 11 888 codes),
which exercises the degenerate case but leaves the multi-row case observed only
in the file descriptions.

### ASSUMED — `is_leaf` derived rather than read

ICD-10-SE has a `Kodnivå – kodspecifikation` column giving each code's position
in the hierarchy, but the file description does not enumerate its value set.
Rather than hard-code guesses, `is_leaf` is computed structurally: a leaf is a
concept no other concept in the load names as parent. The column is parsed and
then ignored.

### ASSUMED — KVÅ parent links in the sample fixture

Confirmed that the published KVÅ workbook has no `Överordnad kod` column, while
the TSV file description does list one. The fixture's parent links
(`EMA00` → `EMA`) are inferred from the code structure, which is unambiguous,
but they were still not read from a real TSV. Unchanged.

### ASSUMED — Anthropic `output_config` shape

The Anthropic provider sends `output_config` containing both `effort` and a
`format` JSON-schema constraint. Each is documented; the **combination** was not
exercised, because no `ANTHROPIC_API_KEY` was available. If it is rejected,
`LLM_STRUCTURED_OUTPUT=false` disables the schema half, and the error message
says so. The live smoke test in `tests/test_providers.py` will confirm it the
first time it runs with a key.

### ASSUMED — SNOMED CT licence details

That the Swedish national licence for SNOMED CT in text format is free of charge
and covers both editions comes from the authorities' published pages as
summarised at the date checked, not from reading the licence agreement.

### Fixture data provenance — deliberately *not* assumed

Every code, Swedish title, Latin term and inclusion/exclusion term in
`tests/fixtures/` and `evaluation/gold/sample_icd10se.csv` was transcribed from
the official 2026 publications. **Nothing was invented**, per §10. The
`Giltig från` values are placeholders and the loader ignores that column. The
KVÅ code shapes (KKÅ = 3 letters + 2 digits, KMÅ = 2 letters + 3 digits) were
verified against all 11 888 codes in the published release, not inferred from
the two examples in the PDFs.

---

## 3. Departures from the specification

Each of these is a judgement call, and each is reversible.

**Strengthened beyond the spec**

1. **`correct` also checks that the code exists in the loaded version.** §8.3
   requires validation by `validate_code_format`. A well-formed code that is not
   in the release is still an invalid mapping, and the decision endpoint is the
   last point anything can catch it. *Risk:* if a version is loaded incompletely,
   a legitimate correction is refused. The error message names the version.
2. **`candidate_recall` added to the evaluation metrics.** §10 lists Top-1,
   Top-3 and latencies. Candidate recall splits a miss into a retrieval failure
   and a ranking failure, which are fixed in different places; without it the
   misses CSV says what went wrong but not where.
3. **`validated_mapping` on the proposal response** — exactly the four fields
   §0 names as the output contract, made concrete rather than left implicit.
4. **`GET /health`**, reporting the running prompt hash.
5. **`DB_PORT` configurable in compose** — port 5432 collides with a local
   PostgreSQL on any developer machine that has one.

**Interpretations**

6. **The determinism test excludes the two latency columns** as well as ids and
   timestamps. §14 says "except ids and timestamps"; latencies are wall-clock
   measurements rather than content, and cannot be equal across runs. Every
   other column is compared.
7. **The `SAMPLE ONLY` marker for the TSV fixtures lives in
   `tests/fixtures/README.md`, not inside the files.** §3 asks for both "SAMPLE
   ONLY" and "same format as official file"; the official TSV has no comment
   syntax, so a marker line inside it would break the format the fixture exists
   to replicate. The gold CSV *does* carry the inline `SAMPLE ONLY` header
   comment, as §10 explicitly requires.
8. **The ICD-10-SE fixture is `.txt` as the §3 tree specifies**, though its
   content is tab-separated like the official `.tsv`.
9. **`Beskrivning`, `Anmärkning` and `Kodningsinformation` are not indexed as
   synonyms** — they are guidance prose, not names for the concept. `Utesluter`
   is excluded for a stronger reason: indexing it would make a code retrievable
   by the terms that rule it out. See the open questions.
10. **Code intervals (`I10-I15`) are loaded but excluded from retrieval.**
    Loading them is required for hierarchy and `chapter`; proposing one as a
    diagnosis code would be a coding error.
11. **Evaluation runs write real proposals** to the audit table, sharing a
    `trace_id` prefix (`eval-<run>-`). They are real mapping attempts, and
    principle 2 does not carve out an exception for them.

**Files added beyond the §3 tree** (none removed): `app/logging_setup.py`,
`app/db/base.py`, `app/db/models.py`, `app/api/deps.py`,
`app/api/serializers.py`, `app/models/{api,candidate,rerank}.py`,
`alembic.ini`, `.dockerignore`, `tests/conftest.py`,
`tests/fixtures/README.md`, `tests/fixtures/kva_{kka,kma}_sample.txt`
(the KVÅ loader cannot be tested without them), `tests/test_rerank.py`,
`tests/test_providers.py`, `tests/test_evaluation.py`.

---

## 4. Tests and coverage

```
tests/test_terminology_loaders.py      59 passed
tests/test_rerank.py                   28 passed
tests/test_api.py                      25 passed
tests/test_retrieval.py                23 passed
tests/test_normalize.py                17 passed
tests/test_pipeline_deterministic.py   14 passed
tests/test_evaluation.py               11 passed
tests/test_providers.py                10 passed, 2 skipped (no API keys)
tests/test_audit_append_only.py         8 passed
tests/test_decisions.py                 8 passed
─────────────────────────────────────────────────
                                      203 passed, 2 skipped     85% coverage
```

Tests run against a real PostgreSQL with pgvector and pg_trgm. DB-backed tests
skip with an explanatory message if none is reachable; live provider tests skip
without an API key. A clone with neither still gets a green suite.

**Coverage where it matters** — `pipeline/map_term.py` 100%, `llm/base.py`
(parsing, repair, hallucination guard) 100%, `validation/decisions.py` 100%,
`normalize/swedish.py` 100%, `retrieval/lexical.py` 100%, `audit/models.py`
100%, `terminology/base.py` 98%.

**Coverage gaps, all explainable:** `llm/anthropic_provider.py` 27% and
`embeddings/openai_compat.py` 24% — the network paths need credentials;
structure and error handling are covered with a stubbed transport, the rest is
covered by the skipped live smoke tests. `run_eval.py` 32% and the migration
file 38% — script entry points exercised outside the coverage run (both run in
CI's smoke-test step). `db/session.py` 54% — engine construction is bypassed by
the test fixtures.

Two real bugs surfaced only when the suite was run as a whole rather than file
by file, and both are fixed: `configure_logging` replaced *every* root handler
including ones it had not installed, and Alembic's `env.py` called `fileConfig`
without `disable_existing_loggers=False`, which switched off application logging
whenever a migration ran in-process.

---

## 5. Open questions for the author

1. **Can you supply one real code-text file** (ICD-10-SE or either KVÅ file)?
   That single artefact converts the loaders from `FORMAT_UNVERIFIED` to
   verified, and is the highest-value item on this list by a wide margin. If the
   files are now behind the E-hälsomyndigheten collaboration portal, do you have
   access?
2. **Version string convention:** `"2026"`, or `"2026-01-01"` (the validity date
   the files carry)? The code treats it as an opaque string; the choice is
   permanent once proposals reference it.
3. **The 63 U-codes ship in a separate file.** Load them into the same
   `(system, version)`, or a distinct version? They are placeholders for codes
   not yet in use, and proposing one would usually be wrong.
4. **Should `Beskrivning` be searchable? There is now hard evidence that it
   should be — this is the highest-value change available.** It is currently
   excluded as guidance prose. Loading the real 11 888-code KVÅ release and
   querying it shows the cost concretely:

   | Query | Result |
   | --- | --- |
   | `PTCA` | ✅ `FNG02` |
   | `koronarangioplastik` | ✅ `FNG02` |
   | `ballongvidgning av kranskärl` | ❌ `FNG02` not retrieved at all |
   | `ballongdilatation` | ❌ `FNG02` not retrieved at all |

   `FNG02`'s stored search text is just its title, *Perkutan transluminal
   koronarangioplastik (PTCA)*. The words a clinician is most likely to type —
   "ballongdilatation", "ballongvidgning" — appear only in its `Beskrivning`
   ("Ballongdilatation av förträngd koronarartär"), which is not indexed. These
   are **retrieval** misses: no amount of reranking can recover them.

   The trade-off is matching on prose rather than on names, which will add
   false positives. Your gold set can settle it; the `candidate_recall` metric
   is exactly the instrument for measuring the change.

5. **`Ej huvuddiagnos` is parsed and discarded.** Should a code marked "not for
   primary diagnosis" be flagged in the validator UI? Proposing one as a primary
   diagnosis is a known coding error the file already warns about.
6. **Dagger/asterisk pairs and mandatory additional codes** are present in the
   files and unused. A single code is often not a complete mapping. Phase 2, or
   sooner?
7. **`validator_id` is an unauthenticated free string.** The audit trail records
   a *claimed* identity. What identity source exists in the target deployment —
   and does the append-only trail have any evidential value before that is wired
   up?
8. **Gold set scope:** how many rows, and which specialties? The instrument is
   ready; its value is entirely determined by what you put in it. Rows where a
   coder and a naive lexical match *disagree* are worth ten easy ones.
9. **Should evaluation runs write to the audit tables?** They currently do, on
   the principle that every mapping attempt is recorded. A large gold set will
   therefore add a proposal per row, none with decisions.
10. **`RERANK_CANDIDATE_CAP` is 15.** Against the real 39 000-concept release
    rather than a 25-concept fixture, that number needs tuning against cost and
    recall together.

---

## 6. Phase 2 was not started

**No MCP server (`terminology-mcp`) was built, scaffolded, or stubbed.** Nothing
in this repository imports or anticipates it beyond the adapter boundary that
already exists for Phase 1's own use.

Also not built, as specified: the comparative benchmark (Phase 3), the SNOMED CT
loader and content (Phase 4 — interface only, `load()` raises), any Sijill
integration (Phase 5), authentication, multi-user support, RBAC, batch mapping,
and file upload. Nothing in the codebase imports or depends on Sijill.

---

## 7. Follow-up: XLSX support and format verification (2026-08-26)

Done after Phase 1 was reported, before merge. **Phase 2 remains not started.**

### What changed

1. **Both loaders read `.xlsx` as well as `.tsv`**, dispatching on the file
   extension through one `read_classification_file`. The tolerant header
   matching, the row-grouping that merges a multi-row code, and the synonym
   rules are shared verbatim between the two formats — there is one parser with
   two front ends, not two parsers. The workbook reader locates the header row
   by searching sheets (the published KVÅ workbook opens with a `Läs mig`
   metadata sheet) rather than assuming it is first.
2. **The KVÅ loader is format-verified** against the real published release —
   details in §2.
3. **The `correct` rejection message** now reads
   `code Z99.9 is well-formed but not present in icd10se version 2026-sample`.
4. **LICENSING.md** records the workbook's filename, URL and download date, the
   fact that ICD-10-SE has no public machine-readable release, that the sample
   rows are excerpts transcribed from named E-hälsomyndigheten publications on
   2026-08-26, and that ICD-10-SE derives from WHO ICD-10 (© WHO 1992).
5. **README** — confirmed to contain no accuracy figures. One number was found
   and removed: the Evaluation section previously said the sample gold set
   "scores 100% against the sample fixture". It was already framed as
   meaningless, but a figure in a README is a figure someone can quote, so it
   is now described qualitatively. The build report keeps such observations,
   in context; the README has none.

### ASSUMED → verified

| Item | Before | After |
| --- | --- | --- |
| KVÅ column header spellings | ASSUMED (from a wrapped table in a PDF) | **Verified** — 9 of 10 named columns mapped on first contact with the real file |
| KVÅ concept count | ASSUMED (11 888, from the file description) | **Verified** — loader produces exactly 11 888 |
| KVÅ code shapes | Verified against the release's code list | **Re-verified through the loader**; `validate_code_format` rejects exactly the 2 group headings |
| Where the KVÅ release is published | UNVERIFIED | **Verified** — URL, filename, size, sheet and column layout recorded |
| KVÅ workbook lacks parent links | Suspected | **Verified** — confirmed absent, warned about at load time |
| Real-scale behaviour | Untested (25-concept fixture) | **Verified** — 11 888 concepts loaded, embedded and queried end to end |
| ICD-10-SE column header spellings | ASSUMED | **Still ASSUMED** — no machine-readable release is publicly obtainable |

### Verified at real scale

The full KVÅ 2026 release was loaded from the workbook into the running
deployment, embedded, and queried:

```
load    11 888 concepts from kva-inkl-beskrivningstexter-2026.xlsx
embed   11 888 vectors (fake provider)
map     "blodtrycksmätning"  -> AF015  Blodtrycksmätning standard   (267 ms retrieval)
map     "biopsi av tonsill"  -> EMA10  Biopsi av tonsill            (199 ms retrieval)
```

Retrieval at ~200–270 ms over 11 888 concepts on a laptop container. Worth
watching against ICD-10-SE's ~39 000 concepts, and worth confirming the HNSW
index is being used before that becomes a complaint.

### New open questions

11. **Should a KVÅ load from the workbook be allowed at all, given it silently
    flattens the hierarchy?** It currently warns. The alternative is to refuse
    unless `--allow-missing-hierarchy` is passed. Since the workbook is the only
    public distribution, refusing would block the one format most people can
    actually get — but a flat hierarchy means `chapter` is unavailable for
    filtering or display.
12. **The workbook's `Klassifikation` column (KKÅ vs KMÅ) is discarded.** It is
    the only hierarchy signal the workbook carries. Should it populate
    `chapter`? That would make `chapter` a label in workbook loads and a code in
    TSV loads, which is why it was not done.

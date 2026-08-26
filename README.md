# Medical Terminology Mapper — Phase 1 Build Prompt

You are building a new public repository: **`medical-terminology-mapper`**.

One-line description:

> AI-assisted mapping of free-text clinical terms to Swedish standardized code systems (ICD-10-SE, KVÅ; SNOMED CT via adapter), with LLM-ranked candidates and a mandatory, auditable human-validation step.

This prompt covers **Phase 1 only**. Do not build anything listed under "Deferred". Each section states _what_ to build and _why_; the "why" is binding — if a later instruction seems to conflict with a stated rationale, stop and report the conflict instead of picking one.

---

## 0. What this project is — and is not

**It is:** a decision-support tool for a healthcare informatics specialist. A clinician or coder types a Swedish clinical phrase (e.g. `högt blodtryck`), the system retrieves candidate codes, an LLM ranks them and explains the ranking, and a human accepts, rejects, or corrects the proposal. Every step is recorded.

**It is not:** an autonomous coding engine. No mapping ever becomes "final" without a recorded human decision. The system proposes; the human decides.

**Why this framing:** in Swedish healthcare, diagnosis and procedure codes feed statistics, reimbursement, and patient records. A tool that silently assigns codes is a liability. A tool that makes an expert faster _and leaves a trail_ is what actually gets adopted. The whole architecture follows from this single constraint.

**Relation to other projects:** this tool is designed so that later it can sit _inside_ a local clinical application, upstream of an inter-organisational exchange layer (the author's Sijill protocol). In that design free text stays local and only a validated code crosses an organisational boundary. This prompt does **not** integrate with Sijill and must not import or depend on it. Just keep the output contract clean: a validated mapping is `(system, version, code, decision_id)` — nothing more.

---

## 1. Non-negotiable principles

1. **Human validation is mandatory.** A proposal has status `pending` until a human decision is recorded. There is no code path that marks a mapping as accepted without a `decision` row.
2. **Everything is traceable.** Every proposal carries a `trace_id`, the exact retrieved candidates with scores, the LLM provider/model, the prompt file version (content hash), and timestamps. Every decision references its proposal.
3. **Audit records are append-only.** No UPDATE or DELETE on audit tables, enforced at the database level, not only in application code.
4. **Providers are abstractions.** Embedding and LLM providers sit behind protocols. A deterministic fake implementation exists for both, so the full pipeline runs in tests with no network and no API keys.
5. **Terminology content is not committed to the repo.** Loaders + documented download instructions + a tiny clearly-labelled sample fixture. Never present sample data as real.
6. **Versions are first-class.** Every concept row stores `(system, version)`. Every proposal stores which terminology version it was computed against.
7. **No fabricated evaluation numbers.** The README may describe the evaluation _method_; it must not state accuracy figures unless produced by the included script on a documented gold set.

**Why:** principles 1–3 are the product. Principles 4–7 are what let a reviewer clone the repo, run `docker compose up`, run the tests, and trust what they see.

---

## 2. Phase 1 scope

### In scope

- Terminology systems: **ICD-10-SE** and **KVÅ**, both loaded from the official Swedish classification files.
- **SNOMED CT adapter as an interface + stub only** (no content, no loader implementation beyond the interface and a `NotImplemented` explanation pointing to the licensing note).
- Retrieval: lexical (Postgres full-text + trigram) **and** vector (pgvector), merged.
- LLM reranking with strict JSON output.
- Proposal → human decision workflow (accept / reject / correct).
- Append-only audit log with DB-level enforcement.
- FastAPI service.
- One minimal HTML page for the validator (server-rendered; no SPA, no build step).
- Evaluation script computing Top-1 / Top-3 against a gold-set CSV, plus a template and a ~10-row sample gold set clearly marked as sample.
- Docker Compose, pytest, ruff, mypy, JSON structured logging.
- README + ARCHITECTURE.md.

### Deferred (do not build)

- MCP server (`terminology-mcp`) — Phase 2.
- Comparative benchmark (lexical vs embeddings vs RAG+LLM) — Phase 3.
- SNOMED CT loader and content — Phase 4 (requires affiliate licence).
- Any Sijill integration — Phase 5.
- Authentication, multi-user, RBAC.
- Batch mapping, file upload.

**Why this cut:**

- ICD-10-SE and KVÅ are downloadable from the Swedish authority without a licence, so real data is available from day one. SNOMED CT requires an affiliate licence application; starting there would force synthetic data, and synthetic data makes the evaluation meaningless.
- The MCP server is a _second surface_ over the same terminology adapters. Building it before the adapters are proven means building it twice.
- The comparative benchmark is only credible once there is a real gold set and a working pipeline to compare against. Ship the measuring instrument (the script) now; run the comparison later.

---

## 3. Repository structure

```
medical-terminology-mapper/
├── app/
│   ├── main.py                  # FastAPI app factory
│   ├── config.py                # settings from env (pydantic-settings)
│   ├── api/
│   │   ├── routes_map.py        # POST /map
│   │   ├── routes_decisions.py  # POST /decisions, GET /proposals/{id}
│   │   └── routes_ui.py         # GET / (validator page)
│   ├── terminology/
│   │   ├── base.py              # TerminologySystem protocol + Concept model
│   │   ├── icd10se.py           # loader + search
│   │   ├── kva.py               # loader + search
│   │   └── snomed.py            # interface stub; raises with licensing note
│   ├── normalize/
│   │   └── swedish.py           # text normalization
│   ├── retrieval/
│   │   ├── lexical.py           # Postgres FTS + pg_trgm
│   │   ├── vector.py            # pgvector search
│   │   └── merge.py             # candidate fusion + dedupe
│   ├── embeddings/
│   │   ├── base.py              # EmbeddingProvider protocol
│   │   ├── fake.py              # deterministic hash-based vectors
│   │   └── openai_compat.py     # any OpenAI-compatible embeddings endpoint
│   ├── llm/
│   │   ├── base.py              # LLMProvider protocol
│   │   ├── fake.py              # deterministic reranker for tests
│   │   ├── anthropic_provider.py
│   │   ├── openai_compat.py
│   │   └── prompts/
│   │       └── rerank_v1.md     # versioned prompt; hash recorded per proposal
│   ├── pipeline/
│   │   └── map_term.py          # orchestrates normalize → retrieve → rerank → proposal
│   ├── validation/
│   │   └── decisions.py         # accept / reject / correct
│   ├── audit/
│   │   ├── models.py
│   │   └── writer.py            # append-only writer; no update/delete methods exist
│   ├── db/
│   │   ├── session.py
│   │   └── migrations/          # alembic
│   └── models/                  # pydantic schemas (API + internal)
├── templates/
│   └── validator.html
├── scripts/
│   ├── load_terminology.py      # CLI: --system icd10se --version 2026 --file path
│   └── embed_terminology.py     # CLI: compute + store embeddings for a loaded version
├── evaluation/
│   ├── gold/
│   │   ├── TEMPLATE.csv
│   │   └── sample_icd10se.csv   # ~10 rows, header comment: SAMPLE ONLY
│   ├── metrics.py
│   └── run_eval.py
├── tests/
│   ├── fixtures/
│   │   └── icd10se_sample.txt   # ~20 codes, SAMPLE ONLY, same format as official file
│   ├── test_normalize.py
│   ├── test_terminology_loaders.py
│   ├── test_retrieval.py
│   ├── test_pipeline_deterministic.py
│   ├── test_decisions.py
│   ├── test_audit_append_only.py
│   └── test_api.py
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
├── .env.example
├── README.md
├── ARCHITECTURE.md
└── LICENSING.md                 # terminology licensing notes (see §11)
```

**Why this shape:** each folder maps to one stage of the pipeline or one responsibility. A reviewer should be able to read the tree and reconstruct the data flow without opening a file.

---

## 4. Terminology layer

### 4.1 Contract

```python
class Concept(BaseModel):
    system: Literal["icd10se", "kva", "snomed"]
    version: str            # e.g. "2026"
    code: str
    preferred_term: str
    synonyms: list[str] = []
    parent_code: str | None = None
    is_leaf: bool
    chapter: str | None = None

class TerminologySystem(Protocol):
    system_id: str
    def load(self, path: Path, version: str) -> Iterable[Concept]: ...
    def validate_code_format(self, code: str) -> bool: ...
```

Loading writes concepts into a `concepts` table keyed by `(system, version, code)`. Multiple versions of the same system coexist. Search always specifies which version it targets.

### 4.2 ICD-10-SE and KVÅ loaders

- Locate the official download page for ICD-10-SE and KVÅ from the responsible Swedish authority (Socialstyrelsen historically; check whether management has moved to E-hälsomyndigheten and document what you find). Do **not** hardcode a URL as fact without confirming it resolves; record it in `LICENSING.md` with the date checked.
- Implement the loader against the actual official file format. If the format cannot be inspected during the build, implement against the sample fixture and mark the loader as `FORMAT_UNVERIFIED` in the report.
- `validate_code_format`: ICD-10-SE pattern (letter + two digits, optional `.` + one or two alphanumerics; note that ICD-10-SE differs from ICD-10-CM — do not reuse US validators). KVÅ pattern per the official format.

### 4.3 SNOMED CT

Interface implemented; `load()` raises `TerminologyLicenceRequired` with a message pointing to `LICENSING.md`. Nothing else.

**Why:** the adapter boundary is the point. Adding SNOMED later must be "write one loader", not "restructure the project".

---

## 5. Normalization (Swedish)

`normalize/swedish.py`:

- Unicode NFC, lowercase, collapse whitespace.
- Keep `å ä ö` — never transliterate.
- Strip punctuation except `-` inside words.
- Do **not** stem or lemmatize in Phase 1 (record this as a known limitation).
- Return both the normalized string and a list of tokens.

Tests must show `Högt Blodtryck` and `högt   blodtryck` normalize identically.

**Why:** cheap, deterministic, and enough to make lexical retrieval reliable. Stemming Swedish medical compounds is a research problem; don't pretend to solve it in Phase 1.

---

## 6. Retrieval

### 6.1 Lexical

Postgres full-text search with the `swedish` text search configuration on `preferred_term` + `synonyms`, plus `pg_trgm` similarity for misspellings. Return top-K (default 20) with scores.

### 6.2 Vector

`concept_embeddings` table: `(system, version, code, provider, model, dim, embedding vector)`. Embeddings are computed once per `(system, version, provider, model)` by `scripts/embed_terminology.py`. Query embedding computed at request time with the same provider. Cosine distance, top-K (default 20).

### 6.3 Merge

Union of both candidate sets, deduplicated by `(system, version, code)`. Each candidate keeps both scores (null where absent) and a `sources: ["lexical", "vector"]` field. Simple reciprocal-rank fusion for the pre-rerank order. Cap at N (default 15) before reranking.

**Why both:** lexical catches exact and near-exact terms; vector catches paraphrases ("förhöjt blodtryck" vs "hypertoni"). Keeping both scores in the candidate record is what makes the later comparative benchmark (Phase 3) possible without re-running anything.

---

## 7. LLM reranking

### 7.1 Provider contract

```python
class LLMProvider(Protocol):
    provider_id: str
    model_id: str
    def rerank(self, query: str, candidates: list[Candidate], prompt: PromptSpec) -> RerankResult: ...
```

`RerankResult` is validated against a strict JSON schema:

```json
{
  "ranked": [
    { "code": "I10", "confidence": 0.91, "reason": "..." },
    { "code": "I15.9", "confidence": 0.05, "reason": "..." }
  ],
  "no_good_match": false,
  "notes": "optional short string"
}
```

Rules:

- The LLM may only return codes that were in the candidate list. Any other code is dropped and logged as a `hallucinated_code` event. This must be tested.
- Confidence values are the model's, not calibrated. Label them as `model_confidence` everywhere; never call them "probability".
- Malformed JSON → one retry with a repair instruction → then fail the proposal with status `rerank_failed`, still audited.

### 7.2 Prompt versioning

Prompt lives in `app/llm/prompts/rerank_v1.md`. At startup the app computes its SHA-256; every proposal stores `prompt_id="rerank_v1"` and `prompt_hash`. Changing the file changes the hash; changing behaviour without renaming the file is therefore still traceable.

### 7.3 Fake provider

`llm/fake.py` reranks by lexical score descending with fixed confidences, so pipeline tests are fully deterministic.

**Why:** the reviewer's question is "can I trust the ranking?" The answer is not "the model is good" but "you can see exactly what it saw, what it said, and which version of the instructions it followed."

---

## 8. Proposal and decision workflow

### 8.1 Tables

`proposals`

- `id` (uuid), `trace_id`, `created_at`
- `input_text`, `normalized_text`
- `target_system`, `terminology_version`
- `candidates` (jsonb: full merged candidate list with scores and sources)
- `rerank` (jsonb: full `RerankResult`)
- `suggested_code`, `model_confidence`
- `llm_provider`, `llm_model`, `prompt_id`, `prompt_hash`
- `embedding_provider`, `embedding_model`
- `latency_ms_retrieval`, `latency_ms_rerank`
- `status`: `pending | rerank_failed`

`decisions`

- `id`, `proposal_id` (FK), `created_at`
- `decision`: `accept | reject | correct`
- `final_code` (null on reject; equals `suggested_code` on accept; the human's code on correct)
- `validator_note` (optional, short)
- `validator_id` (free string in Phase 1; no auth)

A proposal with a decision row is "resolved". Resolution is derived, never stored as a mutable flag.

### 8.2 Endpoints

- `POST /map` `{text, target_system, version?}` → creates proposal, returns it (status `pending`).
- `GET /proposals/{id}` → proposal + decision if any.
- `POST /decisions` `{proposal_id, decision, final_code?, validator_note?, validator_id}` → creates decision. Rejects a second decision for the same proposal (unique constraint).
- `GET /` → validator page.

### 8.3 Validator page

One server-rendered page: input box, target system selector, submit; result panel shows suggested code + preferred term + model confidence, the alternatives with their reasons, the retrieved candidates with scores, and three buttons: Accept / Reject / Correct (Correct reveals a code input validated by `validate_code_format`). After a decision the page shows the decision and the proposal id. Plain HTML + minimal inline JS. No framework.

**Why one page, no dashboard:** the person evaluating this repo wants to see whether the hard problem is understood. A dashboard signals time spent on the wrong thing.

---

## 9. Audit — append-only enforcement

- `proposals` and `decisions` are the audit tables.
- Migration creates a trigger on both: `BEFORE UPDATE OR DELETE` → `RAISE EXCEPTION 'append-only table'`.
- `audit/writer.py` exposes only `insert_*` functions.
- `tests/test_audit_append_only.py` attempts an UPDATE and a DELETE via raw SQL and asserts the exception.
- Structured JSON logs (one line per event) include `trace_id` on every line for a request.

**Why at the DB level:** application code can be bypassed by a future maintainer with a database client. A trigger cannot be bypassed accidentally.

---

## 10. Evaluation (instrument only)

`evaluation/gold/TEMPLATE.csv`:

```
term,target_system,expected_code,source,note
```

`evaluation/run_eval.py --gold path.csv --system icd10se --version 2026 --provider fake|anthropic|...`:

- For each row runs the pipeline, records suggested code and full ranked list.
- Outputs Top-1 accuracy, Top-3 recall, mean retrieval latency, mean rerank latency, and a per-row CSV of misses.
- Prints a warning banner if the gold file is the sample file.

The sample gold set (~10 rows) must be assembled from terms whose codes can be verified against the official ICD-10-SE index. Mark each row's `source`. Do not invent codes.

**Why ship a tool and not a number:** a real gold set has to be curated by the author with the official coding guidance. The script makes that curation immediately useful the day it exists.

---

## 11. Licensing notes (`LICENSING.md`)

Document, with date checked:

- Where ICD-10-SE and KVÅ files are obtained, and under what terms.
- That SNOMED CT content requires an affiliate licence (Sweden is a member country; the national licence process is handled by the responsible Swedish authority — confirm which one and link it). The repository ships no SNOMED content.
- That ICD-10-SE is not ICD-10-CM and tools for the US variant must not be assumed compatible.
- That all data in `tests/fixtures/` and `evaluation/gold/sample_*` is sample data.

---

## 12. Infrastructure and quality

- `docker-compose.yml`: `db` (postgres with pgvector and pg_trgm enabled), `app`. `docker compose up` + `scripts/load_terminology.py` + `scripts/embed_terminology.py --provider fake` must give a working validator page using the sample fixture, with no API keys.
- `pyproject.toml`: ruff, mypy (strict on `app/`), pytest.
- `.env.example` listing every setting with a comment.
- Tests run against a real Postgres (compose service or testcontainers), not SQLite — the trigger and pgvector behaviour are the point.
- CI workflow (GitHub Actions): lint, type-check, tests.

---

## 13. README skeleton

```
# Medical Terminology Mapper

An auditable, AI-assisted workflow for mapping free-text clinical terms
to Swedish standardized code systems.

Python · FastAPI · PostgreSQL/pgvector · RAG · LLM reranking · ICD-10-SE · KVÅ · (SNOMED CT via adapter)

> Designed for human validation — not autonomous clinical coding.

## How it works        (pipeline diagram: text → normalize → lexical+vector → merge → LLM rerank → proposal → human decision → audit)
## Quick start          (compose up, load sample, open validator)
## Loading real terminology
## Evaluation           (method only; how to run with your own gold set)
## Design decisions     (link to ARCHITECTURE.md)
## Roadmap              (Phase 2 MCP server, Phase 3 comparative benchmark, Phase 4 SNOMED CT, Phase 5 boundary integration)
## Licensing            (link to LICENSING.md)
```

---

## 14. Build order and reporting

Build in this order, committing after each step with a descriptive message:

1. Scaffold, config, compose, DB + migrations with append-only triggers, `test_audit_append_only.py` passing.
2. Terminology contract + ICD-10-SE loader + sample fixture + loader tests.
3. KVÅ loader.
4. Normalization + tests.
5. Lexical retrieval + tests.
6. Fake embedding provider + vector retrieval + merge + tests.
7. Fake LLM provider + rerank + hallucinated-code guard + tests.
8. Pipeline end-to-end deterministic test (same input → identical proposal content except ids/timestamps).
9. Proposal/decision endpoints + tests.
10. Validator page.
11. Real providers (Anthropic, OpenAI-compatible) behind the same protocols; smoke-tested only if keys are present, skipped otherwise.
12. Evaluation script + template + sample gold set.
13. README, ARCHITECTURE.md, LICENSING.md, CI.

At the end write `PHASE1_REPORT.md` containing:

- What was built, mapped to the sections above.
- Every place you had to assume something (file formats, URLs, field names), marked `ASSUMED` or `UNVERIFIED`.
- Test count and coverage summary.
- Open questions for the author.
- An explicit statement that Phase 2 (MCP) was **not** started.

Do not start Phase 2. Do not add features not listed here. If something in this prompt is contradictory or impossible, stop at that point and report it rather than working around it.

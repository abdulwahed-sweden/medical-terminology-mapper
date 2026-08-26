# Medical Terminology Mapper

An auditable, AI-assisted workflow for mapping free-text clinical terms
to Swedish standardized code systems.

Python · FastAPI · PostgreSQL/pgvector · RAG · LLM reranking · ICD-10-SE · KVÅ · (SNOMED CT via adapter)

> **Designed for human validation — not autonomous clinical coding.**
> The system proposes; a person decides. No mapping is ever final without a
> recorded human decision, and every decision is permanent and traceable.

---

## Why it works this way

In Swedish healthcare, diagnosis and procedure codes feed statistics,
reimbursement, and the patient record. A tool that silently assigns codes is a
liability. A tool that makes an expert faster **and leaves a trail** is what
actually gets adopted.

Everything in the design follows from that: proposals and decisions are
append-only at the database level, every proposal stores the exact candidates
and scores it was built from along with the provider, model and a hash of the
prompt file, and no code path can mark a mapping accepted without a human
decision row.

---

## How it works

```
"högt blodtryck"
   │
   ▼
normalize ─────────────► NFC · casefold · punctuation · whitespace
   │                     (å ä ö preserved; no stemming — see ARCHITECTURE.md)
   ├───────────────┬──────────────────┐
   ▼               ▼                  │
lexical          vector               │  both scoped to one
FTS('swedish')   pgvector             │  (system, version)
+ pg_trgm        cosine               │
   └───────┬───────┘                  │
           ▼                          │
        merge  ── reciprocal-rank fusion · dedupe · cap
           │
           ▼
      LLM rerank ─► strict JSON ─► one repair retry ─► hallucinated-code guard
           │
           ▼
       PROPOSAL          status: pending | rerank_failed        ← append-only
           │
           ▼
    HUMAN DECISION       accept | reject | correct              ← append-only
           │                                                       exactly one
           ▼
   validated mapping  =  (system, version, code, decision_id)
```

The last line is the whole output contract. Free text stays local; only those
four fields are fit to cross an organisational boundary.

---

## Quick start

Runs end to end with **no API keys and no network** — the bundled deterministic
providers stand in for embeddings and the LLM.

```bash
git clone <this repo> && cd medical-terminology-mapper
cp .env.example .env          # defaults are the offline fakes

docker compose up -d
docker compose exec app alembic upgrade head

# Load the sample fixtures (SAMPLE DATA — see LICENSING.md)
docker compose exec app python scripts/load_terminology.py \
    --system icd10se --version 2026-sample \
    --file tests/fixtures/icd10se_sample.txt

docker compose exec app python scripts/load_terminology.py \
    --system kva --version 2026-sample \
    --file tests/fixtures/kva_kka_sample.txt \
    --file tests/fixtures/kva_kma_sample.txt

# Compute embeddings (fake provider: deterministic, offline)
docker compose exec app python scripts/embed_terminology.py \
    --system icd10se --version 2026-sample --provider fake
docker compose exec app python scripts/embed_terminology.py \
    --system kva --version 2026-sample --provider fake
```

Open **<http://localhost:8000/>**, type `högt blodtryck`, and validate the
proposal. API docs are at `/docs`.

> **Port 5432 already in use?** Set `DB_PORT` in `.env` to something free (and
> match the port in `DATABASE_URL`). The compose file publishes
> `${DB_PORT:-5432}`.

### What the pieces do

| Endpoint | Purpose |
| --- | --- |
| `POST /map` | Create a proposal. Never a mapping. |
| `GET /proposals/{id}` | The proposal, its evidence, and its decision if one exists. |
| `POST /decisions` | Record the human decision. One per proposal, permanently. |
| `GET /` | The validator page. |
| `GET /health` | Liveness, plus the hash of the prompt this instance is running. |

### The guarantee, demonstrated

```console
$ docker compose exec db psql -U mtm -d mtm -c "UPDATE decisions SET final_code='I15.9';"
ERROR:  append-only table: UPDATE is not permitted on public.decisions

$ docker compose exec db psql -U mtm -d mtm -c "DELETE FROM proposals;"
ERROR:  append-only table: DELETE is not permitted on public.proposals
```

Enforced by a database trigger, not by application code — a future maintainer
with `psql` can bypass any amount of careful Python.

---

## Loading real terminology

**No terminology content ships with this repository.** Download the official
files yourself; see [LICENSING.md](LICENSING.md) for where they come from, who
publishes them (responsibility moved from Socialstyrelsen to
E-hälsomyndigheten on 1 June 2026), and under what terms.

```bash
# ICD-10-SE — one tab-separated code-text file
python scripts/load_terminology.py --system icd10se --version 2026 --file ICD10SE_2026.tsv

# KVÅ — two files, one release; pass both in a single command
python scripts/load_terminology.py --system kva --version 2026 \
    --file KKA_2026.tsv --file KMA_2026.tsv

# Embeddings, once per (system, version, provider, model)
python scripts/embed_terminology.py --system icd10se --version 2026 \
    --provider openai_compat --model text-embedding-3-small
```

Loading a version **replaces** that whole `(system, version)` slice, so a code
withdrawn between two loads does not linger. Several versions coexist; every
proposal records which one it was computed against.

The loaders are written against the publishers' documented file structure —
UTF-8, tab-separated, header row, quoted cells, one code spanning several rows
when it carries repeated properties. Headers are matched tolerantly and unknown
columns ignored, so an added column in a future release will not break a load.

### Using real providers

```bash
LLM_PROVIDER=anthropic
LLM_MODEL=claude-opus-5
ANTHROPIC_API_KEY=sk-ant-...

EMBEDDING_PROVIDER=openai_compat
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIM=1536              # changing this needs a migration (pgvector is fixed-width)
OPENAI_EMBEDDINGS_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=...
```

Both provider families sit behind protocols, and the OpenAI-compatible clients
take a `base_url` — so Azure, a self-hosted vLLM or Ollama, or a regional
service all work without a code change. For clinical text, where the text is
allowed to travel is a governance decision, not a code change.

Every setting is listed with a comment in [`.env.example`](.env.example).

---

## Evaluation

**This repository publishes no accuracy figures.** A figure is only meaningful
against a gold set curated with the official coding guidance, and that curation
is the author's work, not the tool's. What ships is the instrument.

```bash
python evaluation/run_eval.py \
    --gold evaluation/gold/your_gold_set.csv \
    --system icd10se --version 2026 --provider anthropic
```

Build a gold set from [`evaluation/gold/TEMPLATE.csv`](evaluation/gold/TEMPLATE.csv):

```
term,target_system,expected_code,source,note
```

The `source` column is not optional — an unsourced gold set measures nothing.

The script reports **Top-1 accuracy**, **Top-3 recall**, mean retrieval and
rerank latency, and writes a per-row misses CSV. That CSV also says whether the
expected code was retrieved at all, which separates a *retrieval* failure from a
*ranking* failure — they are fixed in different places.

A twelve-row sample gold set is included so the script runs out of the box. It
is marked `SAMPLE ONLY`, it scores 100% against the sample fixture, and the
script prints a loud banner saying so. That is the situation the banners exist
for.

---

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

docker compose up -d db
export DATABASE_URL=postgresql+psycopg://mtm:mtm@localhost:5432/mtm

pytest                # 192 tests
ruff check . && ruff format --check .
mypy app/             # strict
```

Tests run against a **real PostgreSQL**, never SQLite: the append-only trigger,
the `swedish` text-search configuration, `pg_trgm` and pgvector are the subject
under test, not an implementation detail. Database-backed tests skip with an
explanatory message if no server is reachable, and live provider tests skip
unless an API key is present — a clone with no credentials still gets a green
suite.

---

## Design decisions

The reasoning behind the architecture, the trade-offs taken, and an honest list
of limitations are in **[ARCHITECTURE.md](ARCHITECTURE.md)**. Highlights:

- Append-only enforced by a **database trigger**, and resolution derived by
  join rather than stored as a mutable flag.
- Exclusion terms (`Utesluter`) are **never** indexed as synonyms — doing so
  would make `I21` retrievable by the phrase that rules it out.
- `word_similarity` rather than `similarity`, because plain trigram similarity
  penalises richly-described concepts.
- Codes the model returns that were not in the candidate list are **dropped and
  logged**, never shown to a human.
- The prompt file is **hashed per proposal**, so behaviour cannot change
  invisibly.
- Deterministic fake providers make the whole pipeline reproducible — and say
  plainly in their own docstrings that they do no language understanding.

---

## Roadmap

| Phase | Scope | Status |
| --- | --- | --- |
| **1** | ICD-10-SE + KVÅ, hybrid retrieval, LLM rerank, human validation, audit | **this repository** |
| 2 | `terminology-mcp` — a second surface over the same adapters | not started |
| 3 | Comparative benchmark: lexical vs embeddings vs RAG+LLM | not started |
| 4 | SNOMED CT loader (requires an affiliate licence) | interface only |
| 5 | Boundary integration for validated mappings | not started |

See [PHASE1_REPORT.md](PHASE1_REPORT.md) for what was built, what had to be
assumed, and the open questions.

---

## Licensing

Code: MIT, see [LICENSE](LICENSE).

**Terminology content is not included and is not covered by that licence.**
ICD-10-SE and KVÅ are published by the Swedish authorities; SNOMED CT requires
an affiliate licence. ICD-10-SE is **not** ICD-10-CM, and US tooling must not be
assumed compatible. Full detail, with the date checked, in
**[LICENSING.md](LICENSING.md)**.

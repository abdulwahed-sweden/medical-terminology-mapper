# Architecture and design decisions

This document explains *why* the system is shaped the way it is. For what it
does and how to run it, see the [README](README.md).

---

## The constraint everything follows from

In Swedish healthcare, diagnosis and procedure codes feed statistics,
reimbursement, and the patient record. A tool that silently assigns codes is a
liability. A tool that makes an expert faster **and leaves a trail** is what
gets adopted.

So the system proposes and a human decides, and every step is recorded. That
single constraint produces most of the design below — including the parts that
look like extra work.

---

## Data flow

```
free text
   │
   ▼
normalize ──────────────────► NFC, casefold, punctuation, whitespace
   │                          (no stemming — see Limitations)
   ├──────────────┬───────────────────┐
   ▼              ▼                   │
lexical        vector                 │  both run against one
(FTS +         (pgvector,             │  (system, version)
 pg_trgm)       cosine)               │
   └──────┬───────┘                   │
          ▼                           │
       merge (reciprocal-rank fusion, dedupe, cap)
          │
          ▼
    LLM rerank ──► strict JSON ──► repair retry ──► hallucinated-code guard
          │
          ▼
     proposal  (status: pending | rerank_failed)     ← append-only
          │
          ▼
   human decision  (accept | reject | correct)       ← append-only, exactly one
          │
          ▼
   validated mapping = (system, version, code, decision_id)
```

---

## Decisions

### Append-only enforced by a database trigger, not by application code

`proposals` and `decisions` carry a `BEFORE UPDATE OR DELETE` trigger that
raises. `app/audit/writer.py` — the only module that touches those tables —
exposes inserts and reads and no mutation at all.

Application-level discipline is not enough. A future maintainer with `psql` can
bypass any amount of careful Python; a trigger they would have to explicitly
drop is a different kind of statement. The test asserts the guarantee through
raw SQL for that reason, not through the ORM.

The corollary: **resolution is derived, never stored.** A proposal is resolved
because a decision row references it. A `resolved` boolean on the proposal would
require an `UPDATE`, which the trigger forbids — and which would overwrite part
of the trail. Deriving it by join costs one query and keeps the record intact.

A wrong mapping is corrected by mapping again, producing a new proposal and a
new decision. Nothing is ever edited.

### The proposal stores the evidence, not just the answer

Each proposal keeps the full merged candidate list with every score, the
complete rerank payload, the provider and model for both embeddings and the
LLM, the prompt id **and the SHA-256 of the prompt file**, both latencies, and
a `trace_id` that also appears on every log line for that request.

This is what makes "can I trust this ranking?" an answerable question. The
answer is not "the model is good"; it is "here is exactly what it was shown,
what it replied, and which version of the instructions it was following."

Hashing the prompt file closes the obvious gap: someone edits the wording
without renaming the file, and every proposal made afterwards carries a
different hash. Behaviour cannot change invisibly.

### Two retrieval signals, fused by rank

Lexical and vector retrieval fail differently. Full-text search under the
`swedish` configuration handles term overlap and stemming but cannot match a
misspelling. Trigram similarity matches a misspelling but has no notion of
words. Embeddings match a paraphrase ("förhöjt blodtryck" → "hypertoni") that
neither lexical signal can reach.

Scores from the two stages are not comparable — a full-text rank and a cosine
similarity do not mean the same thing at 0.7 — so fusion uses only each stage's
**ordering**:

```
RRF(concept) = Σ over stages of 1 / (k + rank_in_that_stage)     k = 60
```

A concept both stages ranked well beats one that only a single stage liked,
even where that stage ranked it first.

**Every score survives the merge**, null where a stage did not return that
concept. That is deliberate: the Phase 3 comparative benchmark (lexical vs
embeddings vs RAG+LLM) can then be computed from stored proposals without
re-running anything.

#### `word_similarity`, not `similarity`

pg_trgm's plain `similarity()` normalises over the whole target string. A
concept whose search text carries a Latin term and three inclusion terms scores
*lower* than a bare one for the same query — it penalises exactly the
richly-described concepts that ought to match best. `word_similarity()` compares
the query against the best-matching extent instead.

Measured on the sample fixture: `hjartinfarkt` (missing the `ä`) retrieves both
`I21` and `I21.9` with word similarity, but only `I21.9` with plain similarity.

### Exclusion terms are never indexed

The classification files carry `Utesluter` (excludes) alongside `Innefattar`
(includes). Only inclusion terms, examples, Latin terms and abbreviations become
synonyms.

Indexing exclusions would make `I21` *Akut hjärtinfarkt* retrievable by
"Gammal hjärtinfarkt" — the exact phrase that rules it out. There is a test.

Guidance prose (`Beskrivning`, `Anmärkning`, `Kodningsinformation`) is also left
out: it explains how to use a code, it is not a name for the concept.

### Code intervals are loaded but never proposed

`I10-I15` is a section heading. The publisher's `Kod` column holds "en unik kod
eller ett kodintervall", so intervals are loaded — the hierarchy needs them, and
`chapter` is derived by walking to the topmost ancestor — but both retrieval
stages exclude them. Proposing `I10-I15` as a diagnosis code would be a coding
error. No valid ICD-10-SE or KVÅ code contains a hyphen, which is what makes the
filter safe.

### One parser, two file formats

The classifications are published as tab-separated text *and* as spreadsheets,
and which one you can actually download differs between them — as of 2026-08-26
KVÅ is public only as `.xlsx` and ICD-10-SE only as PDF. `read_classification_file`
dispatches on the extension into a TSV reader or a workbook reader, both of
which feed the same header mapper and the same row grouper. The tolerant header
matching, the multi-row merge, and the synonym rules are therefore shared
verbatim: there is one parser with two front ends, and a test asserts that both
formats produce byte-identical concepts from the same data.

The workbook reader searches sheets for the header row rather than assuming it
is first, because the published KVÅ workbook opens with a `Läs mig` metadata
sheet. It also warns when the source has no `Överordnad kod` column — which the
published KVÅ workbook does not — because the result is a silently flat
hierarchy, and a silently flat hierarchy is easy to miss until `chapter` turns
out to be empty everywhere.

### `is_leaf` is derived, not read

The ICD-10-SE file has a `Kodnivå – kodspecifikation` column describing each
code's position in the hierarchy, but the publisher's file description does not
enumerate its possible values. Rather than hard-code guesses at a value set,
`is_leaf` is computed structurally: a concept is a leaf when no other concept in
the same load names it as parent.

### Providers behind protocols, with deterministic fakes

`EmbeddingProvider` and `LLMProvider` are protocols. Both have a deterministic
offline implementation, so the entire pipeline — including pgvector search —
runs in tests and in the quick start **with no network and no API key**.

This is what makes the end-to-end determinism test possible: identical input
must produce identical proposal content, every column except the ids, the
timestamp and the two latency measurements. A proposal whose content shifts
between identical runs cannot support the claim that it records what the human
was shown.

Both fakes say plainly in their docstrings that they do no language
understanding, and `run_eval.py` prints a banner when either is in use. A fake
that is easy to mistake for the real thing is worse than no fake.

### Guards live in one place, applied to every provider

Strict JSON parsing, the single repair retry, and the hallucinated-code guard
sit in `app/llm/base.py`, not in each provider. A guard that every provider
implements for itself is a guard that some provider gets wrong.

**The hallucinated-code guard** drops any ranked code that was not in the
candidate list. A model asked to rank a list will occasionally return a code
recalled from training data or invented from the pattern of the others. Such a
code was never retrieved from the loaded release, so nothing here can vouch that
it exists at all — and shown to a human next to codes that *were* retrieved, it
would borrow their credibility. If every ranked code is dropped, `no_good_match`
is set rather than returning an empty ranking, which would read as "the model
had no opinion".

**One repair retry, not a loop.** A model that cannot produce the schema twice
will not produce it on the fifth attempt, and an unbounded retry turns a bad
response into a bill and a hung request. After the second failure the proposal
is written with status `rerank_failed` — the failed attempt is part of the
trail, and the human still sees the retrieved candidates and can decide unaided.

Where a provider can constrain output server-side (Anthropic's
`output_config.format`), it does — but the parser, the retry and the guard all
still run. "Unlikely to be malformed" is not a property an audit trail should
rest on, and no schema can constrain *which* codes come back.

### `confidence` on the wire, `model_confidence` everywhere else

The field is `confidence` in the rerank JSON because that is what the model was
asked for and literally returned, and that object is stored verbatim. Wherever
the number is presented or stored as its own column it is `model_confidence` —
never "probability". It is the model's self-report, and it is not calibrated.

### The decision rules are strict

A decision turns a suggestion into a code that may reach a patient record, so
every way of recording an incoherent one is closed:

- `accept` requires an actual suggestion, and accepting a *different* code is
  refused and named a correction — the two mean different things when the trail
  is audited later.
- `reject` with a code is a contradiction, not an extra field.
- `correct` validates the code's format **and** that it exists in the version
  the proposal was computed against. A well-formed code absent from the release
  is still an invalid mapping, and this is the last point anything can catch it.
- A second decision is refused, with a unique constraint as the last line of
  defence against a race.

### Versions are first-class

Every concept row is keyed by `(system, version, code)`; several versions
coexist. Every proposal records which version it was computed against, and every
search names one. Embeddings are keyed by `(system, version, provider, model)`
and searches filter on all four — vectors from two different models occupy
unrelated spaces, and comparing across them produces confident nonsense.

Loading a version replaces that whole `(system, version)` slice rather than
merging, so a code withdrawn between two loads of the same version does not
linger.

### One server-rendered page, no dashboard

The validator is one HTML file with inline JavaScript. No framework, no build
step. The hard problem here is the audit trail; time spent on a dashboard would
be time spent on the wrong thing.

### An instrument, not a number

`evaluation/run_eval.py` computes Top-1 accuracy, Top-3 recall, latencies and a
per-row misses CSV. **This repository publishes no accuracy figures**, because a
figure is only meaningful against a gold set curated with the official coding
guidance.

The misses CSV also reports whether the expected code was retrieved at all,
which splits a miss into a *retrieval* problem and a *ranking* problem. They are
fixed in different places.

---

## Known limitations

1. **No stemming or lemmatisation.** Swedish medical vocabulary is dominated by
   compounds (`blodtrycksmätning`, `högerhjärtsvikt`). Splitting or stemming
   them correctly is a research problem; a wrong decompounder silently changes
   which codes are retrievable, and the failure is invisible until someone
   audits a mapping. PostgreSQL's `swedish` FTS configuration does apply its own
   Snowball stemmer inside the full-text signal — that is the only stemming in
   the system, and it is not applied to the trigram or vector paths.
2. **The bundled fakes are not models.** The fake embedder hashes character
   trigrams; it cannot match a paraphrase, which is the whole reason the vector
   stage exists in production. The fake reranker sorts by lexical score.
3. **Sample data is tiny.** 25 ICD-10-SE codes and 19 KVÅ codes. `chapter` and
   `is_leaf` are correct within the sample and meaningless outside it. The
   pipeline has separately been run against the real 11 888-code KVÅ release,
   but the committed fixtures remain samples.
4. **`model_confidence` is uncalibrated.** It is a self-report, useful for
   ordering and for spotting a flat distribution, not a probability.
5. **No authentication.** `validator_id` is a free string. Phase 1 has no auth,
   no multi-user support and no RBAC, so the audit trail records a *claimed*
   identity. Anything beyond a single trusted operator needs real authentication
   first.
6. **The embedding dimension is baked into the schema.** pgvector columns are
   fixed-width, so changing `EMBEDDING_DIM` requires a migration. The provider
   refuses a mismatch with an error that says so.
7. **One decision per proposal, permanently.** There is no amendment path by
   design. Correcting a mistake means a new proposal and a new decision.
8. **Retrieval is single-term.** No context from surrounding text, no batch
   mapping, no combination rules (dagger/asterisk pairs, mandatory additional
   codes). The classification files carry that information; Phase 1 does not
   act on it.

---

## Roadmap

| Phase | Scope |
| --- | --- |
| **1** (this) | ICD-10-SE + KVÅ, hybrid retrieval, LLM rerank, human validation, audit |
| 2 | `terminology-mcp` — a second surface over the same adapters |
| 3 | Comparative benchmark: lexical vs embeddings vs RAG+LLM, from stored proposals |
| 4 | SNOMED CT loader (requires an affiliate licence — see [LICENSING.md](LICENSING.md)) |
| 5 | Boundary integration: validated mappings crossing an organisational boundary |

Phase 2 is deliberately not started. The MCP server is a second surface over the
same terminology adapters; building it before the adapters are proven means
building it twice.

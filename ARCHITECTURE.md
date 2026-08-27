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
    retrieval gate ──── blocked ──► no_good_match, LLM never called
          │
       admitted
          ▼
    LLM rerank ──► strict JSON ──► repair retry ──► hallucinated-code guard
          │                                                │
          │                          model's own no_good_match flag
          ▼
     proposal  (pending | rerank_failed | no_good_match)   ← append-only
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

#### `strict_word_similarity`, not `word_similarity`, not `similarity`

Three trigram functions, arrived at by measurement rather than preference.

`similarity()` normalises over the whole target string, so a concept carrying a
Latin term and three inclusion terms scores *lower* than a bare one for the same
query — it penalises exactly the richly-described concepts that should match
best. Replacing it with `word_similarity()`, which compares the query against
the best-matching *extent*, fixed that: `hjartinfarkt` (missing the `ä`) began
retrieving both `I21` and `I21.9` instead of only `I21.9`.

But `word_similarity`'s extent may begin and end mid-word. Measured against the
real KVÅ 2026 release, that let the query `banan` score **0.833** against
"An**nan ban**dningsoperation på a. pulmonalis" — higher than the legitimate
misspelling `hjartinfarkt` scores against its own concept (0.625). Nonsense
outscoring a real typo is not a threshold problem; it means no threshold exists.

`strict_word_similarity()` requires the extent to sit on word boundaries. The
same `banan` case falls to 0.571, while every correctly-spelled query is
unchanged and the measured misspellings barely move. That is what makes the
retrieval gate below possible.

### The retrieval gate

A deterministic check between merge and reranking. If nothing clears it, **the
LLM is never called**: the proposal is recorded with status `no_good_match`, no
suggested code, no confidence, and the full candidate list.

The reason this exists is a real defect. Typing `banan` used to return
*E11 Diabetes mellitus typ 2* at confidence 0.90 — because the vector stage
always returns its nearest neighbours regardless of distance, and a reranker
asked to rank a list will rank it. The evidence already said there was nothing
there: no lexical hit at all, and vector similarities of 0.000. Presenting that
as a confident suggestion is the worst failure this system can have, because it
is indistinguishable on screen from a correct one.

**The rule.** Admit if either holds:

1. the best `ts_rank` exceeds `GATE_MIN_TS_RANK` (default `0.0`) — the `swedish`
   configuration matched at least one lexeme; or
2. the best `strict_word_similarity` reaches `GATE_MIN_STRICT_SIMILARITY`
   (default `0.60`) — strong fuzzy evidence, which is how misspellings get in.

A third clause admits on vector similarity, but only for a vector space with a
configured floor in `GATE_VECTOR_FLOORS`, which is **empty by default**.

**Why the vector stage is excluded by default.** The bundled embedding provider
hashes character trigrams, so a similarity between two of its vectors is noise.
The 0.000 values seen for `banan` are an artefact of that provider, not evidence
of anything, and a real embedding model would score `banan` at roughly 0.3–0.5
against almost everything. Tuning a threshold on those numbers would produce a
rule that looks measured and is worthless. Any vector floor is therefore keyed
by `(provider, model)`, off unless configured, and marked **ASSUMED and
untested against a live model**.

**The measurement.** Against the real KVÅ 2026 release (11 888 concepts), with
29 everyday Swedish non-clinical words as negatives and 30 mechanically
mistyped real terms as positives:

| Signal | Misspellings (want admit) | Negatives (want reject) |
| --- | --- | --- |
| `ts_rank > 0` | 1 / 30 | **0 / 29** |
| `strict_word_similarity` range | 0.529 – 0.921 | 0.188 – **0.571** |
| Combined rule at 0.60 | **29 / 30** | **0 / 29** |

The two classes **overlap** on similarity alone: the worst misspelling
(`adenoisntest`, two transpositions in one word) scores 0.529, below the best
negative (`banan`) at 0.571. No single similarity threshold separates them —
which is why the rule needs the full-text clause, and why that clause carries
the correctly-spelled cases on its own.

0.60 sits in the middle of a plateau: 0.58, 0.60 and 0.62 all give 29/30 and
0/29. It was not chosen to make two examples work.

**Known fragility.** The nearest negative is only **0.029** below the threshold.
That is not much room, and the evidence behind it is 29 negatives on one
terminology. A different corpus could put a nonsense word above 0.60. The rule
is transparent, versioned and configurable precisely so that this can be
re-measured rather than argued about, and every proposal stores the values it
was judged on.

**What the gate is not.** It does not judge relevance. A query of ordinary
Swedish words that genuinely occur in the terminology — "patient",
"behandling" — passes, correctly: there *is* lexical evidence. Deciding whether
that evidence means anything is the reranker's job, and its own `no_good_match`
flag is the second, independent signal. Both are recorded; neither overrides the
other's place in the audit trail.

### Descriptions are indexed, at the lowest weight

The publisher's `Beskrivning` is prose, not a name — so it is stored separately,
weighted `D` in the `search_vector` (against `A` for the preferred term and `B`
for synonyms), and never shown as a term or used for trigram matching. Trigram
similarity exists to survive a misspelled *name*; running it over prose produces
noise rather than tolerance.

Measured on the real KVÅ 2026 release, where 2 182 of 11 888 concepts carry a
description:

| | descriptions off | descriptions on |
| --- | --- | --- |
| `ballongdilatation` → `FNG02` | not retrieved | **#4**, attributed to `description` |
| Description-only recall (40 probes) | 1 / 40 (2%) | **19 / 40 (48%)** |
| Non-clinical words admitted by the gate | 0 / 15 | **0 / 15** |
| ICD-10-SE gold set, Top-1 / candidate recall | 12/12 · 12/12 | **12/12 · 12/12** |

`FNG02` is *Perkutan transluminal koronarangioplastik (PTCA)*; the words a
clinician actually types — "ballongdilatation" — live only in its description.
Those were retrieval misses that no reranking could recover.

A first pass suggested three non-clinical words started matching. Inspection
showed all three were **true** positives: `cykel` matched an exercise-ECG code
whose description reads "Inkluderar även cykel och rullmatta", `strumpor` a
compression-therapy code, `tvättmaskin` an ADL assessment. In a *procedure*
classification those words are clinical. Re-measured against words verifiably
absent from the terminology, the precision cost is zero — which is why the
setting defaults to on.

Every candidate records `matched_field` (`title` / `synonym` / `description` /
`vector`), so a hit on a preferred term and a hit on a sentence of prose are
never confused for one another.

### A stand-in has no confidence

The fake reranker emits a fixed confidence ladder so its output is
reproducible. Those numbers are not confidences, and the old page printed one of
them as "Modellens säkerhet: 0.90" next to a caveat. A number in a screenshot
travels without its caveat.

So every proposal records `provider_kind`. When it is `fake`, `model_confidence`
is null on the proposal and suppressed on every ranked alternative in the API,
the confidence column disappears from the evidence table, and the page shows a
test-mode banner and a badge instead. The raw reply is still stored verbatim in
`rerank` — the audit record keeps what the provider said; what it does not do is
promote a placeholder into the field everything downstream reads as confidence.

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

### MCP is read-and-propose; there is no decision tool

The MCP server can search the terminologies and file a proposal. It cannot
accept, reject or correct one, and that is a decision rather than an omission.

The product's central rule is that a mapping becomes valid only when a human
records a decision, and that the decision row is evidence of who concluded
what. An MCP client is by construction a model. A tool that let it accept a
code would let a model decide, and the audit trail would then carry a
human-shaped row with no human behind it — worse than no trail at all, because
it looks like one.

The same reasoning excludes loading and embedding tools: those are operator
actions with licensing consequences, not something an agent does mid-
conversation. And no tool takes free-form SQL.

An agent that wants a code validated files a proposal and tells its human to
open the validator page. `get_proposal_status` lets it watch for the verdict —
and only watch.

### MCP runs in-process, not over HTTP

The server imports the same settings, session factory, providers and pipeline
functions as the FastAPI app. It does not call the HTTP API.

Calling the API would have been quicker to write and would have given two
implementations of the same behaviour, drifting apart at their own pace, plus a
second network hop and a second place for an error to be translated. Instead,
anything both surfaces need lives in `app/services/terminology.py` below both
of them, and each surface is a thin wrapper. `inspect_code` is the clearest
case: the page, the API and the MCP server reach the same verdict about a code
and quote the same Swedish sentence, because there is one function.

The cost is that the MCP process needs database access and the application's
settings, exactly as the web app does. That is the right trade for a local
stdio server.

### Writes commit before the response is built

FastAPI runs a `yield` dependency's teardown *after* the response has gone out.
With the commit living there, a client that posted a decision immediately after
mapping could arrive before the proposal was durable and be told it did not
exist -- measured at roughly one immediate follow-up in five, and exactly the
kind of failure that looks like a mystery to a user clicking Accept quickly.

The write routes therefore commit explicitly before serialising a response;
`session_scope` keeps its commit as a safety net for anything else. Tests bind
their session with `join_transaction_mode="create_savepoint"`, so an inner
commit releases a savepoint and the outer transaction the fixture owns is still
rolled back.

### The version string is the publisher's release year

`"2026"`, as an opaque string. Not a date, not a semantic version, not the
validity date the files also carry (`2026-01-01`). The publisher names a release
by its year and ships one a year, so the year is the shortest thing that
identifies it unambiguously. It is opaque: nothing parses, compares or orders
it, so a publisher who changes the convention breaks nothing here.

Sample fixtures use `"2026-sample"` so a sample can never be mistaken for the
release in a stored proposal.

### U-codes are placeholders, and are not loaded by default

The publisher distributes 63 U-codes in a separate file: reserved slots that let
a new code be put into use at short notice, as happened with covid-19. They are
real, well-formed codes -- not headings -- but until one is put into use it
stands for nothing.

So `scripts/load_terminology.py` excludes them unless `--include-u-codes` is
passed, and when loaded they are stored with `placeholder = true` and excluded
from retrieval exactly as headings are. They differ from headings in what
happens next: a heading is refused as a decision outright, because a group is
never a mapping, whereas a human may deliberately record a U-code. The first
attempt is therefore refused with an explanation, and a repeat carrying
`acknowledge_placeholder` succeeds -- a warning with a confirmation, not a
prohibition.

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

One Jinja template, one stylesheet, one vanilla-JS file. No framework, no build
step, no npm, no CDN, no external font. The hard problem here is the audit
trail; a dashboard would be time spent on the wrong thing.

The page is ordered by the questions a validator actually asks: what did you
ask, what does the system suggest (or not), what do you want to do about it —
and only then, what is the evidence and how do I trace this later. Decision
buttons sit directly under the suggestion, never below a table of numbers, and
the candidates live in a single table rather than the two duplicated lists the
first version had.

Four result states — suggestion, no good match, rerank failed, decision
recorded — each carry their own heading, a 2px frame and a tinted header strip
in the state colour, so they are not mistakable for one another. Built to WCAG
2.1 AA: every colour pair in the stylesheet is checked by a test, so contrast
cannot regress unnoticed.

The visual language follows an external design handoff (Al-Noor v1.1) with
three deliberate departures, recorded in `docs/DESIGN-CHANGE-PLAN.md`. The
important one: the design specified a **match percentage on every candidate**,
but its figures were hand-authored constants with no formula, and a bold "96 %"
beside a diagnosis code claims a certainty this pipeline cannot support — the
same failure the retrieval gate exists to prevent. The slot is kept and filled
with evidence that is real: source badges, the matched field, and score bars,
with "—" for a stage that returned nothing. Four colours were also darkened to
clear AA, and the candidate list stays a real `<table>` rather than the
prototype's flex divs, because it is tabular data.

### An instrument, not a number

`evaluation/run_eval.py` computes Top-1 accuracy, Top-3 recall, latencies and a
per-row misses CSV. **This repository publishes no accuracy figures**, because a
figure is only meaningful against a gold set curated with the official coding
guidance.

The misses CSV also reports whether the expected code was retrieved at all,
which splits a miss into a *retrieval* problem and a *ranking* problem. They are
fixed in different places.

---

### Vector search resolves the space before it orders by distance

The HNSW index on `concept_embeddings` covers `embedding` alone, but every
search also filters by `(system, version, provider, model)` — two terminologies
and more than one model share that table by design. Postgres can only apply
that filter to what the index hands back: pgvector walks the graph, returns at
most `hnsw.ef_search` entries (40 by default), and stops. When those entries
belong to another embedding space, or are dead tuples VACUUM has not reclaimed,
the filter removes all of them and the search returns **nothing** — while a
sequential scan over the same rows returns every match.

Which plan Postgres picks depends on table statistics, so this surfaced as an
intermittent failure in three tests that only ever appeared in a full suite run:
the vector stage returned zero candidates, then passed on the next run and in
isolation. It was not a test artefact. The same shape reaches production the
moment a second `(provider, model)` space exists — the search silently returns
fewer concepts than it should, or none, and a proposal built on it looks
perfectly ordinary.

So `app/retrieval/vector.py` resolves the space in a `MATERIALIZED` CTE first
and orders the result, which takes the index out of the ordering decision. The
answer is then exact and identical under every plan.

Measured on 12k concepts in one space (about the size of ICD-10-SE or KVÅ):

| Query form | Median | Correct under every plan |
| --- | --- | --- |
| HNSW answers the `ORDER BY` | 2.2 ms | no |
| `MATERIALIZED` CTE, exact | 70.9 ms | yes |

71 ms is the right trade here. The LLM rerank in the same request costs seconds,
a human validates every proposal before it means anything, and a silent
under-return is the one failure this tool must not have. It stops being the
right trade in the low hundreds of thousands of concepts per space — SNOMED CT
in phase 4 is that size. The fix at that point is a **partial HNSW index per
embedding space**, so that every entry in the index already satisfies the
filter and post-filtering cannot starve the scan; that is a schema change with
its own migration, not a tuning knob, and it is not needed at phase 1–3 sizes.

The index itself is left in place, unused by this query, for that future.

Held by `tests/test_vector_index_scan.py` — in particular
`test_the_embedding_space_is_resolved_before_ordering`, which asserts the plan
never names `ix_concept_embeddings_hnsw`. It fails immediately if `MATERIALIZED`
is dropped, unlike the original bug, which needed the planner to be in the wrong
mood.

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
   ordering and for spotting a flat distribution, not a probability. It is
   suppressed entirely when the deterministic stand-in produced the ranking.
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
9. **Retrieval index usage is not asserted at release size.** An earlier
   measurement recorded the trigram predicate running as a sequential scan
   (145 ms over 11 888 concepts). It no longer reproduces: the full query now
   plans as a `BitmapOr` across the trigram and tsvector indexes, and
   end-to-end retrieval on the real KVÅ release measures 10–19 ms rather than
   the 199–267 ms recorded before. Several things changed in between — bulk
   loads now `ANALYZE`, the non-indexable `code NOT LIKE '%-%'` filter became
   an indexed boolean, and two indexes were added — and a controlled test
   showed the plan stays index-served even with statistics on `search_text`
   disabled, so no single cause is established. Two tests assert the *form*
   of both predicates stays index-servable, which is what would silently
   regress; nothing asserts the planner's choice at release size, because the
   committed fixtures are 27 and 19 concepts.
10. **The gate's fuzzy threshold rests on thin evidence.** 29 negatives and 30
   misspellings on one terminology, with the nearest negative 0.029 below the
   threshold, and the two classes overlapping on that signal alone. It is
   versioned and recorded per proposal so it can be re-measured; it should be,
   against ICD-10-SE and against real mistyped input.
11. **The gate cannot judge relevance**, only whether evidence exists. Common
    Swedish words that occur in the terminology pass it. That is the reranker's
    job, and with the stand-in there is no reranker worth the name.
12. **No automated browser tests.** Playwright ships no Chromium for the
    development machine's OS. Page structure, semantics and contrast are tested
    server-side; interaction is a manual checklist
    (`docs/MANUAL_UI_TEST.md`).

---

## Roadmap

| Phase | Scope |
| --- | --- |
| **1** (this) | ICD-10-SE + KVÅ, hybrid retrieval, LLM rerank, human validation, audit |
| 2 | `terminology-mcp` — a second surface over the same adapters |
| 3 | Comparative benchmark: lexical vs embeddings vs RAG+LLM, from stored proposals |
| 4 | SNOMED CT loader (requires an affiliate licence — see [LICENSING.md](LICENSING.md)) |
| 5 | Boundary integration: validated mappings crossing an organisational boundary |

Phase 2 is merged: `terminology-mcp` is a second surface over the same
terminology adapters, built only once those adapters were proven. Phase 3 is
next and has not been started.

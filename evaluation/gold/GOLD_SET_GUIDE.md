# What a gold set has to look like

This is the outline for the reference set phase 3 needs. It contains **no
rows** — curating them is clinical work, against the official coding guidance,
and it is the author's to do. What follows is what the file has to contain
before any figure computed from it means anything.

Until such a file exists, `run_eval.py` is an instrument with nothing to
measure. `sample_icd10se.csv` is not that file and says so in its own header.

## Why size is not the point

A benchmark that compares lexical retrieval, embeddings and the full RAG+LLM
pipeline is only interesting where those three **disagree**. A set made of terms
that match a preferred term word for word will score near 100% on all three arms
and prove nothing except that string matching works. The set has to contain the
cases where retrieval and a coder disagree, because that is the entire question
phase 3 is asking.

## Minimum size

Staged, because 300 curated rows is months of work and a comparison worth seeing
does not need all of them.

**v1 — 100 rows per system, all eight classes present, at least 10 per class.**
Enough to expose a large difference between arms, and enough to show which
classes need more data before a figure from them means anything. A v1 result is
a direction, not a number to publish.

**v2 — the 300-row target below**, which is where per-class figures start to
carry weight.

- **300 rows** per code system as a floor for a headline figure. Below roughly
  that, the difference between two arms is inside the noise of a handful of
  rows: at 100 rows one row is a full percentage point, and a 3-point gap
  between two arms is three rows changing their minds.
- **30+ rows per difficulty class** below, so that per-class figures — which is
  where the interesting result lives — are not built on four examples.
- Report per-class figures **always**, and the headline number never on its own.
  An aggregate over a set whose class mix was chosen by hand is a statement
  about the mix as much as about the system.

### The reporting rule

The benchmark report prints, for every class: **n**, the result for each arm,
and a `LOW N` warning whenever n < 30.

**No percentage appears anywhere without its n.** Not in the report, not in a
commit message, not in the README, not in a screenshot. A bare "82%" outlives
every caveat attached to it, and at n = 11 it is not a measurement at all.

Aggregate numbers do not leave the evaluation report. The README states no
accuracy figures, and that does not change when there is finally a number to
state — the report is where a number can be read next to what produced it.

## Two columns, not one — `phrasing` and `target`

**Implemented.** The orchestrator reads both columns and reports each
dimension separately (report sections A1 and A2). Gold files written before the
split still load: a `class` naming a trap sets the `target` and leaves the
`phrasing` `unclassified`, because a trap label says nothing about how the input
was written and guessing would put labels in the file that nobody assigned.

The single `class` column conflates two independent things:

- **`phrasing`** — how the input is written: `exact`, `synonym`, `paraphrase`,
  `misspelling`, `abbreviation`. This is what separates lexical retrieval from
  embeddings.
- **`target`** — what the correct answer demands: `plain`, `distinction`,
  `granularity`, `negative`. This is what separates a system that finds the
  right *area* from one that picks the right *code*.

A row has both. They vary independently, and collapsing them loses information
in the direction that matters.

The sample set shows the cost concretely. Ten of its twelve terms are literally
the published preferred term, yet under one column only three rows are labelled
`exact`, because the other seven were labelled for the trap they spring:

| term | old single column | `phrasing` | `target` |
| --- | --- | --- | --- |
| essentiell hypertoni | `exact` | `exact` | `plain` |
| hypertensiv hjärtsjukdom **med** hjärtsvikt | `distinction` | `exact` | `distinction` |
| hypertensiv njursjukdom **med** njursvikt | `distinction` | `exact` | `distinction` |
| astma ospecificerad | `granularity` | `exact` | `granularity` |
| akut hjärtinfarkt (→ I21, a category) | `granularity` | `exact` | `granularity` |
| högt blodtryck | `synonym` | `synonym` | `plain` |
| hypertoni utan känd orsak | `paraphrase` | `paraphrase` | `plain` |

Read the one-column result and you would conclude plain exact matching was
tested three times. It was tested ten times. At twelve rows that is cosmetic; at
a hundred rows per system with per-class breakdowns it is a reporting defect,
and it biases the reader in the direction of thinking the easy case is
under-covered when it is the best-covered thing in the set.

The two-column form also answers questions one column cannot:

- *Does the vector arm help on paraphrases specifically?* — group by `phrasing`.
- *Do all three arms fail the same way on `med`/`utan` pairs, whatever the
  phrasing?* — group by `target`.
- *Is the abbreviation problem a retrieval problem or a granularity problem?* —
  the cross-tab.

### Coverage requirement, restated

The v1 floor becomes a requirement on **both** columns: every `phrasing` value
present with at least 10 rows, and every `target` value present with at least 10
rows. They do not need to be crossed exhaustively — 5 phrasings × 4 targets at
10 rows each would be 200 rows per system before anything else — but a
`phrasing` or `target` value with no rows fails the run, exactly as an empty
class does today.

`negative` rows (expected outcome: no code) carry a `phrasing` like any other
row: a misspelled term with no correct code is a different test from a
well-formed one with no correct code, and the gate should be measured on both.

---

## Term diversity

The eight cases below are the vocabulary the two columns are drawn from:
`distinction`, `granularity` and `no_good_match` (as `negative`) are `target`
values, the rest are `phrasing` values. The table explains what each is *for*,
which is the part that matters when deciding whether a row earns its place.

| Class | What it is | Why it belongs |
| --- | --- | --- |
| `exact` | The preferred term, as published | The floor. Every arm should get these; one that does not is broken. |
| `synonym` | An `Innefattar` term or a real clinical synonym | Tests that synonyms are indexed and weighted, not just titles. |
| `paraphrase` | What a clinician actually types, different words, same concept | Where embeddings should start to beat lexical retrieval, if they ever do. |
| `misspelling` | Real misspellings, including Swedish keyboard slips and missing diacritics (`hjartinfarkt`, `hjärtinfrakt`) | Where trigram matching earns its place. Invented typos are not evidence — take them from real text if any is available. |
| `abbreviation` | Clinical shorthand in Swedish use (`AMI`, `KOL`, `t2dm`) | Short, ambiguous, and the hardest case for every arm. |
| `distinction` | Pairs separated by one decisive word (`med`/`utan` heart failure) | Catches an arm that retrieves the right *area* and the wrong *code* — the failure that matters clinically. |
| `granularity` | Category versus its subcategory (`I21` versus `I21.9`) | Tests whether the system respects assignability rather than picking the nearest string. |
| `no_good_match` | Terms with **no** correct code, including plausible near-misses | The gate is a feature. A set with no negatives cannot measure a false-positive rate, and a system that always answers will look perfect on a set that always has an answer. |

Beyond the classes, spread rows across **chapters** — a set drawn from two
chapters measures those two chapters — and include both **ICD-10-SE and KVÅ**.
They fail differently: KVÅ terms are procedural and compound, ICD-10-SE terms
are nominal.

## What the current sample covers

`sample_icd10se.csv`, 12 rows, all ICD-10-SE, chapters 4, 9 and 10:

Labels were assigned mechanically, then reviewed against the fixture's own
hierarchy. The rule: classify the `target` by the trap the row can spring — a
distractor that exists in the terminology and plausibly shares the term's
wording — not by whether a partner row happens to be in this file.

| `phrasing` | Rows | | `target` | Rows |
| --- | --- | --- | --- | --- |
| `exact` | **10** | | `plain` | 5 |
| `synonym` | 1 | | `granularity` | 4 |
| `paraphrase` | 1 | | `distinction` | 3 |
| `misspelling` | **0** | | `negative` | **0** |
| `abbreviation` | **0** | | | |

That is the split earning its place: **ten of the twelve terms are literally the
published preferred term**, which the single column reported as three, because
seven of them were labelled for the trap they spring instead.

`akut hjärtinfarkt` → `I21` is the strongest row here and the only one of its
kind: `I21` is a **non-leaf** code that is nonetheless assignable, so the row
tests whether the system respects assignability rather than reaching for the
more specific-looking `I21.9`.

Three of the eight cases are absent entirely — `misspelling`, `abbreviation`
and `negative` — one code system is absent
entirely, and the candidate pool is 25 concepts rather than roughly 39 000. The
two classes most likely to separate the three arms — misspellings and
abbreviations — are exactly the two with no rows. It is a fixture that keeps the
harness runnable, and it was never anything else.

## File format

Same columns as `TEMPLATE.csv`, plus one:

| Column | |
| --- | --- |
| `term` | The input, as it would be typed |
| `target_system` | `icd10se` or `kva` |
| `expected_code` | The correct code, or empty for a negative row |
| `phrasing` | `exact`, `synonym`, `paraphrase`, `misspelling`, `abbreviation` — **required** |
| `target` | `plain`, `distinction`, `granularity`, `negative` — **required** |
| `class` | Legacy single column. Read only when `phrasing` and `target` are both absent. |
| `source` | The official publication and section the code was read from |
| `note` | Why this row is hard, or what it distinguishes |

A file carrying all three is not ambiguous — `phrasing` and `target` win — but
there is no reason to write `class` in a new file.

`source` is not bureaucracy. A gold set whose provenance cannot be checked is an
opinion, and a figure computed from it inherits that.

## Rules for curation

1. **Never take the expected code from this system's own output.** That measures
   agreement with itself. Codes come from the official publication.
2. **Curate before measuring.** A set adjusted after seeing which rows failed is
   a description of the system, not a test of it.
3. **Freeze and version it.** A gold set that changes between runs makes two
   runs incomparable, which is the one thing a benchmark must not allow.
4. **Record who curated it and when**, against which release. Terminology
   releases change annually; a code correct in 2026 may not be in 2027.

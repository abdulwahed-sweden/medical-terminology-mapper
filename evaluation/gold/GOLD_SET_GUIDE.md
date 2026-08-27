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

- **300 rows** per code system as a floor for a headline figure. Below roughly
  that, the difference between two arms is inside the noise of a handful of
  rows: at 100 rows one row is a full percentage point, and a 3-point gap
  between two arms is three rows changing their minds.
- **30+ rows per difficulty class** below, so that per-class figures — which is
  where the interesting result lives — are not built on four examples.
- Report per-class figures **always**, and the headline number never on its own.
  An aggregate over a set whose class mix was chosen by hand is a statement
  about the mix as much as about the system.

## Term diversity

Every row carries its class, so results can be broken down by it. The classes,
and what each is for:

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

| Class | Rows | |
| --- | --- | --- |
| `exact` | 1 | `essentiell hypertoni` |
| `synonym` | 1 | `högt blodtryck` |
| `paraphrase` | 1 | `hypertoni utan känd orsak` |
| `distinction` | 2 | `I11.0` / `I11.9` |
| `granularity` | 3 | `I21` / `I21.9`, `I15.9` |
| remainder | 4 | close to their preferred terms |
| `misspelling` | **0** | |
| `abbreviation` | **0** | |
| `no_good_match` | **0** | |

So three of the eight classes are absent entirely, one code system is absent
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
| `expected_code` | The correct code, or empty for `no_good_match` |
| `class` | One of the classes above — **new**, and required |
| `source` | The official publication and section the code was read from |
| `note` | Why this row is hard, or what it distinguishes |

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

# REHEARSAL — FAKE PROVIDERS — NOT A QUALITY RESULT

!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
!! THESE NUMBERS ARE NOT A MAPPING-QUALITY RESULT.
!! 
!! Fake provider(s) in use: llm, embeddings.
!! The fake reranker sorts by lexical score and understands no
!! language. The fake embedder hashes character trigrams and cannot
!! match a paraphrase. What follows measures the instrument, not
!! the mapper.
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

| | |
| --- | --- |
| run | `rehearsal` |
| date (UTC) | 2026-08-28T22:15:45+00:00 |
| git SHA | `34f81a587d8f7c7584ce608a035e10e2544f6cf3` |
| system / version | icd10se / 2026-sample |
| terminology fingerprint | 27 concepts, `89614bf095956505…` |
| gold set | `sample_icd10se.csv` |
| gold rows | 12 total, 12 eligible, 0 excluded |
| gold SHA-256 | `d83230ba02d4009d719df4f65b01782adf568cf44317d8eb08d030957ac4838d` |
| LLM | fake: fake/fake-rerank-v1 |
| embeddings | fake: fake/fake-hash-v1 (1536d) |
| prompt | rerank_v1 @ `0d10a74f8553` |
| gate | lexical_evidence v2, ts_rank>0.0, strict_sim≥0.6, min_chars=3, vector_floors=none |

## A. By case class

Per-class first, deliberately. An aggregate over a hand-chosen class mix
describes the mix as much as the system.

| class | n | | lexical | hybrid | full |
| --- | ---: | --- | --- | --- | --- |
| **distinction**  **LOW N** | 3 | Top-1 | 100% (3/3) | 100% (3/3) | 100% (3/3) |
| | | Top-3 | 100% (3/3) | 100% (3/3) | 100% (3/3) |
| **exact**  **LOW N** | 3 | Top-1 | 100% (3/3) | 100% (3/3) | 100% (3/3) |
| | | Top-3 | 100% (3/3) | 100% (3/3) | 100% (3/3) |
| **granularity**  **LOW N** | 4 | Top-1 | 100% (4/4) | 100% (4/4) | 100% (4/4) |
| | | Top-3 | 100% (4/4) | 100% (4/4) | 100% (4/4) |
| **paraphrase**  **LOW N** | 1 | Top-1 | 100% (1/1) | 100% (1/1) | 100% (1/1) |
| | | Top-3 | 100% (1/1) | 100% (1/1) | 100% (1/1) |
| **synonym**  **LOW N** | 1 | Top-1 | 100% (1/1) | 100% (1/1) | 100% (1/1) |
| | | Top-3 | 100% (1/1) | 100% (1/1) | 100% (1/1) |

`LOW N` marks any class with fewer than 30 rows. At those sizes a
single row moves the figure by several points; treat them as direction,
not measurement.

## B. Paired comparison

Same rows, same order, matched by `row_id`. *Improved* means Top-1 was
wrong in the first arm and right in the second; *worsened* is the
reverse. Row-level detail is in `paired_changes.csv`.

| comparison | improved | worsened | unchanged | answers |
| --- | ---: | ---: | ---: | --- |
| hybrid vs lexical | 0 | 0 | 12 | what does vector retrieval add |
| full vs hybrid | 0 | 0 | 12 | what does the LLM add |

## C. Failure breakdown

| category | lexical | hybrid | full |
| --- | ---: | ---: | ---: |
| *(no failures on the eligible set)* | 0 | 0 | 0 |

Excluded before any arm ran, and absent from every denominator above:

| reason | rows |
| --- | ---: |
| *(none)* | 0 |

## D. Overall

Last, and least informative. Read section A first.

| arm | Top-1 | Top-3 |
| --- | --- | --- |
| lexical | 100% (12/12) | 100% (12/12) |
| hybrid | 100% (12/12) | 100% (12/12) |
| full | 100% (12/12) | 100% (12/12) |

## E. Misses

Full detail, including gate values, in `misses.csv`.

No misses on the eligible set.

---

*Numbers describe only this dataset and this run. They must not be copied
into README or presented as clinical accuracy.*

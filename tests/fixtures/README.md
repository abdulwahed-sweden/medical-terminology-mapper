# SAMPLE DATA ONLY — NOT A TERMINOLOGY RELEASE

**Everything in this directory is a hand-built sample. Do not use it for
clinical coding, statistics, reimbursement, or any production purpose.**

These files exist so the test suite and the `docker compose` quick start can
run end to end with no downloads and no licence. They are:

- **Tiny.** A couple of dozen codes, against roughly 39 000 in the real
  ICD-10-SE release and roughly 13 000 across the real KVÅ files.
- **Partial.** Only a handful of columns carry values, and whole branches of
  the hierarchy are missing, so `chapter` and `is_leaf` are correct *within the
  sample* and meaningless outside it.
- **A format replica, not a content replica.** The row/column structure is
  modelled on the publisher's documented file description (see
  [`LICENSING.md`](../../LICENSING.md)) so the loaders are exercised against
  the real shape.

## Provenance of the code content

The codes, Swedish titles, Latin terms, inclusion (`Innefattar`) and exclusion
(`Utesluter`) terms were transcribed from the official published
classification, so that retrieval and evaluation are exercised against real
terminology rather than invented strings. `LICENSING.md` records the source
documents and the date they were checked.

The `Giltig från` values are placeholders and are **not** transcribed; the
loader ignores that column.

## Files

| File | Replicates | Codes |
| --- | --- | --- |
| `icd10se_sample.txt` | ICD-10-SE code-text file (16 columns) | 25 |
| `kva_kka_sample.txt` | KVÅ / KKÅ code-text file (9 columns) | see file |
| `kva_kma_sample.txt` | KVÅ / KMÅ code-text file (10 columns) | see file |

To work with the real classifications, download the official files and run
`scripts/load_terminology.py` against them — see the repository README.

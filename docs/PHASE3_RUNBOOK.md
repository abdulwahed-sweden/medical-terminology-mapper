# Phase 3 runbook

The exact sequence for the first real benchmark run, in order. Everything before
step 7 exists so that step 7 measures mapping rather than setup.

Read [`evaluation/gold/GOLD_SET_GUIDE.md`](../evaluation/gold/GOLD_SET_GUIDE.md)
first if the gold set is not finished. Nothing here rescues a benchmark run on a
dataset that cannot support one.

A rehearsal of the whole sequence with fake providers is committed at
[`evaluation/runs/rehearsal/`](../evaluation/runs/rehearsal/). It is a worked
example of the machinery, not a result.

---

## 1. Set the live-provider variables

These are test-harness variables. They are not application settings, and
`app/config.py` does not read them.

```bash
export TEST_OPENAI_API_KEY=sk-...
export TEST_OPENAI_EMBEDDINGS_BASE_URL=https://api.openai.com/v1
export TEST_OPENAI_EMBEDDING_MODEL=text-embedding-3-small
```

## 2. Run the live embedding smoke test

```bash
pytest --live-providers -m requires_api_key tests/test_live_embeddings.py -s
```

Both gates have to be satisfied: the flag *and* the credentials. A key on its
own does not spend money, and neither does the flag on its own.

## 3. Verify the dimension is 1536

The test prints it:

```
  model      text-embedding-3-small
  dimensions 1536
  cosine(hypertoni, högt blodtryck) = 0.…
  cosine(hypertoni, banan)          = 0.…
```

`dimensions` must read **1536**. The `vector` column is fixed-width; a 3072-wide
model means a migration and a full re-embed before anything else here can run.
The cosine ordering must put the two synonyms closer to each other than to
`banan` — a model that fails that is not usable for Swedish clinical text
whatever it scores elsewhere.

## 4. Load the real KVÅ 2026 terminology

KVÅ first: it is verified against the real 2026 workbook, while the ICD-10-SE
loader is still `FORMAT_UNVERIFIED`. See ARCHITECTURE.md.

```bash
python scripts/load_terminology.py --system kva --version 2026 \
  --file /path/to/kva-2026.xlsx
```

## 5. Embed with the live provider

```bash
export EMBEDDING_PROVIDER=openai_compat
export EMBEDDING_MODEL=text-embedding-3-small
export OPENAI_API_KEY=sk-...          # the *application's* key, for this run
export OPENAI_EMBEDDINGS_BASE_URL=https://api.openai.com/v1

python scripts/embed_terminology.py --system kva --version 2026
```

It refuses and prints the bill first:

```
This will send 11886 concepts to openai_compat/text-embedding-3-small
in about 47 request(s), and it will be charged to the configured account.
  system  kva
  version 2026

Re-run with --yes to proceed.
```

Check the count and the target, then re-run with `--yes`. The guard sits before
the delete, so a refused run leaves any existing embeddings intact.

## 6. Verify the gold set and record its hash

```bash
sha256sum evaluation/gold/kva_v1.csv
```

The run records this hash itself, but knowing it beforehand is what lets you
tell later whether the file changed underneath a comparison.

## 7. Run the benchmark

```bash
python scripts/run_benchmark.py --system kva --version 2026 \
  --gold evaluation/gold/kva_v1.csv
```

All three arms run on the identical eligible row set. Add `--keep-proposals` if
this run should leave its proposals in the audit trail.

## 8. Inspect the manifest and the report

```bash
cat evaluation/runs/<run_id>/manifest.json
cat evaluation/runs/<run_id>/report.md
```

Check before reading a single number:

- `run_kind` is `formal`, and `providers.fake_providers` is empty. If either
  says otherwise, the report is a rehearsal and its numbers are about the
  instrument.
- `dataset.sha256` matches step 6.
- `terminology.fingerprint.concept_count` is the whole release, not a partial
  load.
- `dataset.excluded_rows` is small, and every exclusion reason is one you accept.

## 9. Preserve the run directory before any tuning

Copy it somewhere outside the repository, or tag the commit. It is the baseline:
the only run made before anyone looked at the results.

Once thresholds, vector floors, RRF constants or the prompt are adjusted in
response to what a run showed, every later run is measuring a system fitted to
this dataset. That is legitimate work, but it needs an untouched *before* to be
compared against, and the before cannot be reconstructed afterwards.

---

## Expected failure modes

| What you did | What it looks like |
| --- | --- |
| Forgot `TEST_OPENAI_API_KEY` | `SKIPPED … TEST_OPENAI_API_KEY / TEST_OPENAI_EMBEDDINGS_BASE_URL are not set`. Nothing ran. |
| Forgot `--live-providers` | `SKIPPED … live-provider test; pass --live-providers to run it`. The credentials were found and deliberately not used. |
| Model returns the wrong width | `AssertionError: text-embedding-3-large returned 3072 dimensions, but the vector column is 1536`. Stop: this is a migration, not a setting. |
| Terminology not loaded | `no eligible rows in … for (kva, 2026). Is the terminology loaded?` — or every row excluded as `expected_code_not_present_in_loaded_terminology`, which means the same thing. |
| A class in the gold set has no eligible rows | `ERROR: classes present in … produced no eligible rows: abbreviation`. The run refuses to print a report with a silently missing class. |
| Gate declines valid rows | `gate_false_reject` in section C, with the gate values in `misses.csv`. The gate is lexical-evidence-based, so this is where that shows up. Do **not** tune the floors during the baseline run. |
| Application provider variables unset at step 5 | The embed script reports `provider=fake model=fake-hash-v1`, and it will not warn you further, because a fake run costs nothing. The manifest is what catches it: `run_kind` will be `rehearsal`. |
| Partially configured provider (key set, base URL not) | `EmbeddingError: embedding request failed` on the first batch, before any row is written. |

## What the numbers are not

A run measures one dataset against one terminology snapshot with one prompt.
The report says so in its own footer. Nothing from it belongs in the README,
and no figure from it is a clinical accuracy claim — least of all a per-class
figure marked `LOW N`.

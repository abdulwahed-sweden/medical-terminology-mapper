# Medical Terminology Mapper

A tool that helps a person turn free-text clinical wording into a suggested
standard medical code. The system proposes. A person decides. Every decision is
recorded so it can be checked later.

The project supports Swedish code systems: **ICD-10-SE** (diagnoses) and **KVÅ**
(healthcare procedures).

---

## Why this project exists

In healthcare, the same thing can be written in many ways.

A clinician may write:

```
högt blodtryck
```

Another system, a report, or a colleague may need the standard code:

```
ICD-10-SE I10
```

Free text is good for clinical detail. It carries nuance, doubt and context that
a code cannot. Standard codes are good for something else: they make information
easier to reuse, compare, report and exchange between systems and organisations.

**This project does not ask anyone to stop writing free text.** Clinical notes
stay as they are. The structured code is an additional result, produced
alongside the text, not a replacement for it.

Finding the right code by hand takes time. There are roughly 39 000 ICD-10-SE
codes and about 11 900 KVÅ codes. This project helps a reviewer find a likely
code faster, and keeps a clear record of what was decided and why.

---

## The basic idea

```
Clinical text
      ↓
Search likely codes
      ↓
Rank the candidates
      ↓
A person reviews
      ↓
Accept / reject / correct
      ↓
Validated mapping
```

1. **Clinical text** — someone types a word or phrase, for example
   `högt blodtryck`.
2. **Search likely codes** — the system searches the selected code system for
   entries that look like a match, including matches with small spelling
   mistakes.
3. **Rank the candidates** — a language model puts the most likely candidates
   first and writes one short sentence explaining each one.
4. **A person reviews** — the suggestion and the evidence behind it are shown on
   one page.
5. **Accept / reject / correct** — the reviewer chooses.
6. **Validated mapping** — the recorded result.

---

## The human is always responsible for the final decision

This is the most important part of the project.

- The system **never** assigns a final code on its own.
- Every accepted mapping requires a **recorded human decision**.
- **Rejecting** a suggestion is a valid, recorded outcome.
- **Correcting** it to a different code is a valid, recorded outcome.
- **"No good match"** is a valid, recorded outcome.
- Proposals and decisions are stored **append-only**: once written, they cannot
  be edited or deleted, not even directly in the database. If a mapping turns
  out to be wrong, the answer is a new proposal and a new decision, not a
  rewritten old one.

A suggestion by itself is not a mapping. It only becomes one when a person has
decided.

---

## Example

Input:

```
högt blodtryck
```

Possible proposal:

```
ICD-10-SE I10 — Essentiell hypertoni
```

The reviewer then accepts, rejects, or corrects it.

If the reviewer accepts, the final validated result is exactly four fields:

| Field | Example |
| --- | --- |
| System | `icd10se` |
| Version | `2026` |
| Code | `I10` |
| Decision ID | `6106c873-4bb4-…` |

The proposal and the validated result are **not the same thing**. The proposal
is what the system suggested. The validated result is what a person confirmed,
and it is the only output meant to be used elsewhere.

---

## When the system is unsure

Search will always return *something*. That is a problem, because the closest
match to an unrelated word is still a medical code.

During manual testing, the word `banan` (banana) produced a diabetes code, shown
with a confident-looking score. There was no real evidence behind it. That is
the most dangerous kind of wrong answer, because on screen it looks exactly like
a correct one.

The system now checks the strength of the search evidence **before** asking the
language model to rank anything. If the evidence is too weak:

- no code is suggested,
- the language model is not called at all,
- the result is recorded as **no good match**,
- the candidates that were found are still shown, so the reviewer can judge for
  themselves.

The reviewer can then confirm that no code applies, or enter the correct code
manually.

The rule used for this check is an **engineering safeguard**, measured on the
development data available for this project. It is **not** a clinically
validated threshold and does not represent a proven medical accuracy boundary.
The rule is versioned, configurable, and stored with every proposal, so it can
be re-measured and adjusted. The technical detail and the measurements are in
[ARCHITECTURE.md](ARCHITECTURE.md).

---

## Code systems currently supported

### ICD-10-SE — Swedish diagnosis classification

The loader reads the official file formats (`.tsv` and `.xlsx`).

**Important limitation, stated openly:** this repository contains only a small
**sample excerpt** of ICD-10-SE for development and testing. At the time of
writing, a complete machine-readable ICD-10-SE file was **not publicly
obtainable**, so the loader has not been verified at full scale against a real
release. The file format was implemented from the publisher's own format
description. See [PHASE1_REPORT.md](PHASE1_REPORT.md).

### KVÅ — Swedish classification of healthcare procedures

The loader has been **verified locally against the real 2026 KVÅ workbook**. It
read all 11 888 codes, matching the count stated in the file itself.

The real workbook is **not** included in this repository. You download it
yourself.

### SNOMED CT

**Interface only.** There is an adapter so SNOMED CT can be added later without
restructuring the project. There is no SNOMED CT content and no working loader.
SNOMED CT requires a licence.

Where the official files come from, who publishes them, and under what terms is
documented in [LICENSING.md](LICENSING.md).

---

## What this project does **not** do

- It does **not** replace clinical judgement.
- It does **not** perform autonomous clinical coding.
- It does **not** guarantee medical correctness.
- It does **not** contain the full official terminology datasets.
- It has **no** user accounts, login, or access control. The reviewer's name is
  a free-text field, so the record shows a *claimed* identity only.
- It is **not** hardened for production deployment.
- It does **not** integrate with any external exchange layer yet.
- It does **not** include the work deferred to later phases (see
  [Project status](#project-status)).

---

## Why the audit trail matters

If someone reviews a mapping months later, they should be able to answer simple
questions without guesswork:

- What text was entered?
- Which candidate codes were considered, and how strong was each match?
- Which system or model produced the ranking, and with which instructions?
- What did the system suggest?
- Who made the final decision, and what did they decide?
- When was it decided?

Each proposal stores all of this, including a fingerprint of the exact
instructions given to the language model. If those instructions are edited
later, the fingerprint changes, so a change in behaviour cannot pass unnoticed.

Records cannot be edited or deleted afterwards.

---

## Running it locally

You need [Docker](https://www.docker.com/) and Git. Nothing else, and no AI
account.

**1. Get the code**

```bash
git clone https://github.com/abdulwahed-sweden/medical-terminology-mapper.git
cd medical-terminology-mapper
```

**2. Create your settings file**

```bash
cp .env.example .env
```

The defaults work offline. If port 5432 is already used on your machine, change
`DB_PORT` in `.env`.

**3. Start it**

```bash
docker compose up -d
```

**4. Prepare the database**

```bash
docker compose exec app alembic upgrade head
```

**5. Load the sample data**

```bash
docker compose exec app python scripts/load_terminology.py \
    --system icd10se --version 2026-sample \
    --file tests/fixtures/icd10se_sample.txt

docker compose exec app python scripts/embed_terminology.py \
    --system icd10se --version 2026-sample --provider fake
```

**6. Open it**

Go to <http://localhost:8000> and try `högt blodtryck`.

To stop everything: `docker compose down`.

### Loading the real terminology

Download the official files yourself, then:

```bash
# KVÅ (published as one spreadsheet containing both parts)
docker compose exec app python scripts/load_terminology.py \
    --system kva --version 2026 --file kva-inkl-beskrivningstexter-2026.xlsx

docker compose exec app python scripts/embed_terminology.py \
    --system kva --version 2026 --provider fake
```

Downloaded terminology files are excluded from version control on purpose.

---

## Offline mode

By default the project runs with **deterministic test providers**. They need no
internet connection and no API key, which makes the whole system easy to run and
easy to test.

**These are development and test stand-ins. They are not real clinical AI
models, and they do not understand language.** One sorts candidates by text
similarity; the other turns text into numbers using a fixed formula.

Because of this, the interface **never shows a confidence number in offline
mode**. Showing one would suggest a judgement that was never made. Instead the
page displays a clear test-mode banner, so a screenshot cannot be mistaken for a
real result.

---

## Using real AI providers

Real language-model and text-embedding providers are supported through
configuration. The application code does not depend on any single vendor: any
service that follows a common API shape can be used, including one you host
yourself. Where clinical text is allowed to travel is a governance decision, so
this is a setting rather than a code change.

See [`.env.example`](.env.example) for the available options.

### Configuring real providers does not affect the tests

Having real credentials configured — in your shell or in `.env` — is safe. The
ordinary test suite does not use them, and cannot reach a provider.

- Provider variables such as `EMBEDDING_PROVIDER`, `LLM_PROVIDER`,
  `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` configure **the application**.
- Ordinary tests **deliberately override those choices**. Before any test module
  is imported, the harness pins both providers to the deterministic fakes,
  blanks the application API keys, and points the provider base URLs at a dead
  local port.
- Ordinary tests **never use application API keys**, so an ordinary `pytest`
  costs nothing regardless of what is configured.
- Ordinary tests are **blocked from external network access**: outbound
  connections are restricted to the database and localhost, so a provider call
  fails loudly instead of leaving the machine. That guarantee does not depend on
  the machine being offline.
- **Live provider tests use dedicated `TEST_*` variables** — never the
  application credentials — and are **opt-in twice over**: the credentials have
  to be set *and* `--live-providers` has to be passed.

```bash
pytest                                        # never touches a provider
pytest --live-providers -m requires_api_key   # deliberate, and costs money
```

The `TEST_*` variables are part of the test harness, not application settings;
`app/config.py` does not read them. `tests/test_provider_isolation.py` holds all
of this in place, including a child process started with hostile provider
variables to prove the API and MCP paths stay offline.

---

## Project status

**Phase 1 is closed. Phase 2 (the MCP server) is merged. Stabilisation is in
progress.**

Verified before publication: the full test suite, code linting, formatting,
strict type checking, database migrations from an empty database, Docker
startup, sample and real KVÅ loading, the "no good match" behaviour, and the
append-only protections.

Stabilisation is the work between phase 2 and phase 3: closing the things that
shipped unfinished rather than adding anything. So far that is a silent
wrong-answer bug in vector search — the query could return no candidates at all
depending on which plan the database chose, which is
[recorded in ARCHITECTURE.md](ARCHITECTURE.md) — a CI job that builds the Docker
image and calls the MCP server inside it, and a tested HTTP transport.

**Phase 3** is next and has not been started: a comparative benchmark measuring
word matching against vector search against the full pipeline, computed from
stored proposals. The measuring tool already exists. What it needs before the
comparison means anything is a curated reference set — see
[evaluation/gold/GOLD_SET_GUIDE.md](evaluation/gold/GOLD_SET_GUIDE.md) for what
that has to look like — and, for the arms that use a real embedding model, an
API key. Until both exist the benchmark can be run but not believed.

Also deferred: SNOMED CT content, an autocomplete endpoint, a bilingual
interface, and user authentication.

This is **not** a production system and it is **not** clinically validated. No
accuracy figures are published, because a meaningful figure requires a carefully
prepared reference set that does not yet exist. The project includes the
measuring tool, not a result.

---

## MCP server

An [MCP](https://modelcontextprotocol.io) server, `terminology-mcp`, lets an AI
assistant look codes up and **file a proposal** for you to review — from Claude
Desktop, Claude Code, or any MCP client.

It can read and it can propose. **It cannot decide.** There is deliberately no
tool that accepts, rejects or corrects a proposal, because a mapping is only
valid once a person has recorded a decision, and a model is not a person. An
assistant that wants a code validated files a proposal and tells you to open the
validator page.

```bash
docker compose exec app terminology-mcp
```

Setup, the tool list and client configuration: **[docs/MCP.md](docs/MCP.md)**.

---

## Related projects

**[sijill](https://github.com/abdulwahed-sweden/sijill)** — an
inter-organisational exchange layer. This tool produces a validated
`(system, version, code, decision_id)`. Only the first three are meant to cross
an organisational boundary; the decision record, and the free text behind it,
stay local.

---

## Documentation

| Document | What it covers |
| --- | --- |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Technical design, decisions, and known limitations |
| [PHASE1_REPORT.md](PHASE1_REPORT.md) | What was built, what was assumed, and open questions |
| [LICENSING.md](LICENSING.md) | Where terminology files come from and how they may be used |
| [docs/MANUAL_UI_TEST.md](docs/MANUAL_UI_TEST.md) | Manual checklist for testing the web page |
| [docs/MCP.md](docs/MCP.md) | The MCP server: tools, safety rule, client setup |
| [SECURITY.md](SECURITY.md) | How to report a security issue |

---

## Licence

The **software** in this repository is released under the MIT licence — see
[LICENSE](LICENSE).

This covers the code only. It gives no rights to ICD-10-SE, KVÅ, SNOMED CT, or
any other terminology, none of which are included here. Terminology licensing is
a separate matter, described in [LICENSING.md](LICENSING.md).

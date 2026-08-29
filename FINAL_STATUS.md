# Final status

**Snapshot: `9cf81e7d1799dc18c4f28d83b288a145f61dac92`**

Development is closed at this snapshot. This file records what the repository
is, what was finished, and what was deliberately left undone, so that neither
has to be inferred from commit history.

## What this project does

It maps free-text Swedish clinical terms to **ICD-10-SE** and **KVÅ** codes, and
records every mapping as a proposal that a human must accept, reject or correct
before it means anything.

Retrieval is hybrid — PostgreSQL full-text and trigram matching over a weighted
`tsvector`, plus pgvector similarity — fused by reciprocal-rank fusion. A
language model reranks the merged candidates and may only choose from them. A
deterministic gate runs before the model: when the retrieved evidence is too
weak, the proposal is recorded as `no_good_match` and **the model is never
called**, so it cannot be tempted to rank noise.

Proposals and decisions are append-only, enforced by database triggers rather
than by application code. Each proposal stores the evidence it was built from —
candidates, scores, gate values, provider and model identity, prompt hash — so a
reader can reconstruct what a validator was shown.

## What is complete

- **Phase 1** — terminology loaders, hybrid retrieval, the gate, LLM reranking,
  the proposal/decision workflow, the append-only audit trail, the FastAPI
  service and server-rendered validator page, the evaluation harness, Docker
  and CI.
- **Phase 2** — `terminology-mcp`, an MCP server over the same adapters, running
  in-process. It exposes seven tools over stdio (and a tested localhost
  streamable-HTTP transport).
- **Stabilisation** — a silent wrong-answer bug in vector search, where the
  query could return no candidates at all depending on the plan the database
  chose; a CI job that builds the image and exercises the MCP entry point inside
  the container; test-harness isolation that keeps ordinary tests away from live
  providers and application credentials.
- **Phase 3 tooling** — `scripts/run_benchmark.py` runs three arms (`lexical`,
  `hybrid`, `full`) over one gold set on an identical eligible row set, and
  writes a run directory whose `manifest.json` makes the report reproducible.
  Rehearsed with the fake providers at
  [`evaluation/runs/rehearsal/`](evaluation/runs/rehearsal/).

## What deliberately remains undone

- **No formal Phase 3 measurement was performed.** The tooling was built and
  rehearsed; it was never run against a curated gold set with live providers.
- **No curated gold set exists.** The only gold file in the repository is a
  twelve-row sample against a twenty-five-concept fixture, which states in its
  own header that it measures nothing.
  [`GOLD_SET_GUIDE.md`](evaluation/gold/GOLD_SET_GUIDE.md) specifies what a real
  one would need.
- **No clinical accuracy claim is made anywhere**, and none should be inferred
  from any number in this repository. The rehearsal scores 100% because its
  input is twelve near-preferred terms against a tiny fixture.
- **The live embedding smoke test was never run** — it needs an API key. The
  OpenAI-compatible embedding path is covered offline against a stubbed
  transport, but it has never made a real request.
- **KVÅ 2026 is verified** against the real published workbook. **ICD-10-SE is
  not**: no machine-readable release was obtainable, so its loader remains
  marked `FORMAT_UNVERIFIED` and its column layout is inferred from the
  publisher's file description.
- **SNOMED CT content is not included.** It requires an affiliate licence; see
  [LICENSING.md](LICENSING.md).
- **The Sijill integration is not included.**
- **No authentication and no production hardening.** There is no user model, no
  access control, no rate limiting and no secret management beyond environment
  variables.
- **The MCP server can read and propose. It cannot decide.** There is
  deliberately no tool that accepts, rejects or corrects a proposal, because a
  mapping is only valid once a person has recorded a decision and a model is not
  a person.
- **The Vercel deployments attached to this repository are not evidence of a
  production-ready deployment.** They are automatic builds of the default
  branch. The system has never been operated as a service.

## Where the detail lives

| | |
| --- | --- |
| Design decisions and their reasons | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Phase 1 close-out, assumptions and open questions | [PHASE1_REPORT.md](PHASE1_REPORT.md) |
| MCP server, tools and client setup | [docs/MCP.md](docs/MCP.md) |
| What a Phase 3 run would involve | [docs/PHASE3_RUNBOOK.md](docs/PHASE3_RUNBOOK.md) |
| What a gold set must contain | [evaluation/gold/GOLD_SET_GUIDE.md](evaluation/gold/GOLD_SET_GUIDE.md) |
| Terminology licensing | [LICENSING.md](LICENSING.md) |
| Vulnerability reporting | [SECURITY.md](SECURITY.md) |
| Branch, PR and quality-gate rules | [CONTRIBUTING.md](CONTRIBUTING.md) |

Development is closed at this snapshot.

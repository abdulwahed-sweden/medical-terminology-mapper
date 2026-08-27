# `terminology-mcp` — MCP server

An [MCP](https://modelcontextprotocol.io) server that gives an AI agent
structured access to the Swedish clinical terminologies, and the ability to file
a mapping proposal into the same audited pipeline the validator page uses.

**It can read, and it can propose. It cannot decide.**

---

## The rule this server is built around

There is no tool to accept, reject or correct a proposal. That is not a gap to
be filled in a later version.

Phase 1 established the product's central rule: a mapping becomes valid only
when a human records a decision, and that decision is written to an append-only
table as evidence of who concluded what. An MCP client is, by construction, a
model. A tool that let it accept a code would let a model decide, and the audit
trail would then show a human-shaped row with no human behind it — which is
worse than having no trail at all, because it looks like one.

So an agent that wants a code validated files a proposal with `propose_mapping`
and tells its human to open the validator page. The proposal is real, audited,
and waiting. The decision is theirs.

### Also not provided

- **No terminology loading or embedding tools.** Loading a release is an
  operator action with licensing consequences, not something an agent should do
  mid-conversation.
- **No raw SQL or arbitrary query tool.** Every tool has a fixed shape.

---

## Tools

| Tool | Does | Writes? |
| --- | --- | --- |
| `list_terminologies` | What is loaded, with counts split into assignable / headings / placeholders, and which embedding space exists | no |
| `search_concepts` | Word + vector search, with scores and which field matched | no |
| `get_concept` | One code in full: terms, description, hierarchy, children, flags | no |
| `find_similar_concepts` | Search **plus** the retrieval gate's verdict on the evidence | no |
| `validate_code` | Whether a code may be used, by the same rules the page applies | no |
| `propose_mapping` | Runs the full pipeline and files an auditable proposal | **one proposal** |
| `get_proposal_status` | Whether a human has decided yet | no |

Every tool takes `system` (`icd10se`, `kva`, `snomed`) and an optional
`version`; omitting the version uses the configured default and the resolved
version comes back in the response. List sizes default to 10 and are capped at
50.

### SNOMED CT

Every tool returns `licence_required` for `system: "snomed"`, and
`list_terminologies` lists it with that status. The adapter exists; the content
does not, and it needs an affiliate licence. See [LICENSING.md](../LICENSING.md).

### Errors

Returned **in the payload**, not raised, so the code stays machine-readable:

```json
{ "ok": false, "error": { "code": "not_loaded", "message": "no concepts are loaded for …" } }
```

| Code | Means |
| --- | --- |
| `invalid_argument` | Unknown code system, or a malformed proposal id |
| `licence_required` | SNOMED CT — content not shipped |
| `not_loaded` | That `(system, version)` has no concepts loaded |
| `not_found` | The code or proposal does not exist |

---

## Running it

The server talks **stdio** by default, which is how MCP clients launch a local
server: the client starts the process and speaks over its standard streams.
Nothing listens on a port.

It runs **in-process** against the same settings, database and pipeline as the
web application. It does not call the HTTP API, so there is one behaviour and
one audit trail. It does need the database to be reachable — start it with
`docker compose up -d db` and set `DATABASE_URL`, exactly as for the web app.

```bash
# from a local checkout
pip install -e .
terminology-mcp            # or: python -m mcp_server

# inside the compose container
docker compose exec app terminology-mcp
```

There is deliberately **no new compose service**: a stdio server is launched by
its client, not run as a daemon.

### Claude Desktop

Add to `claude_desktop_config.json`
(macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "terminology": {
      "command": "/absolute/path/to/medical-terminology-mapper/.venv/bin/terminology-mcp",
      "env": {
        "DATABASE_URL": "postgresql+psycopg://mtm:mtm@localhost:5432/mtm"
      }
    }
  }
}
```

Using the compose container instead:

```json
{
  "mcpServers": {
    "terminology": {
      "command": "docker",
      "args": ["compose", "-f", "/absolute/path/to/medical-terminology-mapper/docker-compose.yml",
               "exec", "-T", "app", "terminology-mcp"]
    }
  }
}
```

Restart Claude Desktop after editing the file.

### Claude Code

```bash
# local venv
claude mcp add terminology \
  --env DATABASE_URL=postgresql+psycopg://mtm:mtm@localhost:5432/mtm \
  -- /absolute/path/to/medical-terminology-mapper/.venv/bin/terminology-mcp

# compose container
claude mcp add terminology -- \
  docker compose -f /absolute/path/to/medical-terminology-mapper/docker-compose.yml \
  exec -T app terminology-mcp
```

> If port 5432 is taken on your machine, use the `DB_PORT` you set in `.env`.

### HTTP transport

`terminology-mcp --transport streamable-http` binds to `127.0.0.1:8765`.

**Tested, unauthenticated, localhost only.** `tests/test_mcp_http.py` starts the
server on an ephemeral port exactly as the flag says to, connects with the SDK's
HTTP client and calls a tool, so the transport is exercised on every CI run
rather than merely offered. What it does **not** have is authentication of any
kind — anything that can reach the port can call every tool on it. Bind it to
localhost, keep it there, and do not put it behind a reverse proxy and call that
access control.

stdio remains the supported path for clients: it is how MCP clients launch a
local server, and it has no port for anything to reach.

---

## An example session

With the offline providers (no API key needed), after loading the sample data
from the [README](../README.md) quick start:

**1. What is available?**

```
list_terminologies()
→ icd10se 2026-sample: 27 concepts (19 assignable, 8 headings)
  kva     2026:        11 888 concepts (11 886 assignable, 2 headings)
  snomed:              licence_required
```

**2. Look something up.**

```
search_concepts(system="kva", query="PTCA", version="2026")
→ FNG02  Perkutan transluminal koronarangioplastik (PTCA)   matched_field: title
  TFJ00  …
  evidence_note: "retrieval scores only; not a ranking of correctness"
```

**3. Check it before proposing.**

```
get_concept(system="kva", code="FNG02", version="2026")
→ hierarchy: F › FN › FNG   (parent_source: derived)
  flags: assignable ✓, not a placeholder, not a heading
```

**4. File a proposal.**

```
propose_mapping(text="högt blodtryck", system="icd10se",
                version="2026-sample", requested_by="claude-desktop")
→ proposal_id: 6106c873-…
  status: pending      suggested_code: I10   model_confidence: null
  provider_kind: "fake"
  next_step: "This is a proposal, not a mapping. Ask a human to review it in
              the validator interface; no tool here can accept it."
```

**5. Tell the human.** Open <http://localhost:8000/> and review proposal
`6106c873-…`. Nothing the agent can do will turn it into a mapping.

**6. Later, check back.**

```
get_proposal_status(proposal_id="6106c873-…")
→ decided: true, decision: accept, validator_id: "…",
  validated_mapping: { system, version, code, decision_id }
```

### A note on offline mode

With the deterministic stand-in providers, `propose_mapping` returns
`provider_kind: "fake"` and `model_confidence: null`, and says so in
`test_mode_note`. The ordering means nothing clinically — it is the plumbing
working, not a judgement. This mirrors the validator page's test-mode banner.

---

## Logging

Every tool call emits a structured JSON line with the tool name, system,
version, a trace id and latency. On stdio these go to **stderr**, because
stdout is the protocol wire and one stray line would corrupt it.

Read tools write nothing. `propose_mapping` writes exactly one proposal row,
stamped `origin: "mcp"` and carrying `requested_by` — which records which client
asked, and is explicitly **not** a validator identity.

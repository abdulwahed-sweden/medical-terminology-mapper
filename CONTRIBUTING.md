# Contributing

A short file. The rules that matter here are about how changes reach `main` and
what has to be green before they do.

## Branches and pull requests

- **`main` changes only through a pull request**, and only the repository owner
  merges it. No direct pushes, no self-merges, no exceptions for small changes.
- Branch from `main`, one branch per piece of work, named for what it does
  (`phase-2-mcp`, `post-phase-2-stabilisation`).
- Push the branch and open the PR even when the work is unfinished. Work that
  exists only on a laptop is work nobody can review and nobody can recover.
- Rebase or merge `main` into the branch to resolve conflicts; do not rewrite
  history that has already been pushed and reviewed.

## The quality gate

All four have to pass locally before a PR is opened, and CI runs the same four:

```bash
ruff check .                  # lint
ruff format --check .         # formatting
mypy app/ mcp_server/         # strict type checking
pytest                        # the suite, against a real PostgreSQL
```

The tests need a real database — pgvector, `pg_trgm` and the `swedish` text
search configuration are the subject of several of them, so a substitute proves
nothing:

```bash
docker compose up -d db
export DATABASE_URL=postgresql+psycopg://mtm:mtm@localhost:5432/mtm
```

A second CI job builds the Docker image and calls the MCP server inside the
container. It exists because the image once shipped without installing the
project: every unit test passed and the documented
`docker compose exec app terminology-mcp` failed for anyone who tried it.

## Reporting results

Do not hide failing tests, and do not describe something as verified unless it
was run. A known failure written down is worth more than a green summary that
is not true. If a test is flaky, say so and say how often — an intermittent
failure is a defect that has not been diagnosed yet, not noise to be retried
away. The vector-stage flake recorded in
[ARCHITECTURE.md](ARCHITECTURE.md) was a real silent wrong-answer bug that
looked like noise for weeks.

## CI workflow steps never inline multi-line Python

Put it in a file under `scripts/` and call the file. YAML block scalars and
Python indentation do not survive each other: an earlier inline version broke
the workflow badly enough that it produced a run with **zero jobs** and no log,
which reads exactly like an infrastructure outage.

A single-line `python -c "import app"` is fine and stays readable in place.
The boundary is enforced by
`tests/test_ui.py::test_no_workflow_step_inlines_multi_line_python`.

## Commit messages

Say what changed and why it needed to change. The why is the part that is
expensive to reconstruct later, and it is usually the reason the diff looks the
way it does.

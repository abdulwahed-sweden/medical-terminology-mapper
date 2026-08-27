FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv/app

# Install dependencies first so edits to source do not invalidate the layer.
COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir ".[dev]"

COPY . .

# Install the project itself once the source is present. The layer above
# installed the dependencies from pyproject alone and is cached; this one only
# puts `app` and `mcp_server` on sys.path. Without it the `terminology-mcp`
# console script cannot import them: a console script's sys.path[0] is the
# script's own directory, not the working directory, so running it from
# anywhere but a `python` invocation inside /srv/app would fail.
RUN pip install --no-cache-dir --no-deps -e .

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

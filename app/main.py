"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app.api import routes_decisions, routes_map, routes_ui
from app.config import get_settings
from app.llm.base import load_prompt
from app.logging_setup import configure_logging, new_trace_id, trace_context

logger = logging.getLogger(__name__)

DESCRIPTION = """
Auditable, AI-assisted mapping of free-text clinical terms to Swedish
standardized code systems.

**This service proposes; a human decides.** `POST /map` creates a *proposal*,
never a mapping. A proposal becomes a validated mapping only when a human
records a decision through `POST /decisions`. Proposals and decisions are
append-only and cannot be edited or deleted.
"""


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="Medical Terminology Mapper",
        description=DESCRIPTION,
        version="0.1.0",
    )

    # Hash the prompt once at startup and log it, so the running instance's
    # instructions are identifiable from the logs alone.
    prompt = load_prompt("rerank_v1")
    logger.info(
        "startup",
        extra={
            "prompt_id": prompt.prompt_id,
            "prompt_hash": prompt.sha256,
            "llm_provider": settings.llm_provider,
            "llm_model": settings.llm_model,
            "embedding_provider": settings.embedding_provider,
            "embedding_model": settings.embedding_model,
            "embedding_dim": settings.embedding_dim,
        },
    )

    @app.middleware("http")
    async def bind_trace_id(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """One trace id per request, on every log line and in the response.

        Accepts an inbound `X-Trace-Id` so a calling system's identifier
        survives into this service's proposals.
        """
        trace_id = request.headers.get("X-Trace-Id") or new_trace_id()
        with trace_context(trace_id):
            logger.info(
                "request_started",
                extra={"method": request.method, "path": request.url.path},
            )
            response = await call_next(request)
            response.headers["X-Trace-Id"] = trace_id
            logger.info(
                "request_finished",
                extra={"path": request.url.path, "status_code": response.status_code},
            )
            return response

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_error", extra={"path": request.url.path})
        return JSONResponse(status_code=500, content={"detail": "internal error"})

    @app.get("/health", tags=["ops"], summary="Liveness probe")
    def health() -> dict[str, str]:
        return {"status": "ok", "prompt_hash": prompt.sha256}

    # One stylesheet and one script, served from disk. No CDN, no build step,
    # no external font or icon host -- nothing the page needs leaves the server.
    app.mount(
        "/static",
        StaticFiles(directory=str(Path(__file__).resolve().parent.parent / "static")),
        name="static",
    )

    app.include_router(routes_map.router)
    app.include_router(routes_decisions.router)
    app.include_router(routes_ui.router)
    return app


app = create_app()

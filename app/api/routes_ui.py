"""GET / -- the validator page."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.api.deps import SessionDep, SettingsDep
from app.db.models import loaded_versions

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parents[2] / "templates"))
APP_VERSION = "0.1.0"

router = APIRouter(include_in_schema=False)


@router.get("/", response_class=HTMLResponse)
def validator_page(request: Request, session: SessionDep, settings: SettingsDep) -> HTMLResponse:
    versions = [
        {"system": system, "version": version, "count": count}
        for system, version, count in loaded_versions(session)
    ]
    return TEMPLATES.TemplateResponse(
        request=request,
        name="validator.html",
        context={
            "versions": versions,
            "default_version": settings.default_terminology_version,
            "llm_provider": settings.llm_provider,
            "llm_model": settings.llm_model,
            "embedding_provider": settings.embedding_provider,
            "embedding_model": settings.embedding_model,
            # Either stand-in puts the page in test mode: the banner is about
            # whether the numbers on screen mean anything, and a fake embedder
            # makes the vector column meaningless just as a fake reranker makes
            # the confidence meaningless.
            "provider_kind": (
                "fake"
                if settings.llm_provider == "fake" or settings.embedding_provider == "fake"
                else "live"
            ),
            "app_version": APP_VERSION,
        },
    )

"""Shared FastAPI dependencies."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.session import get_session
from app.embeddings import build_embedding_provider
from app.embeddings.base import EmbeddingProvider
from app.llm import build_llm_provider
from app.llm.base import LLMProvider, PromptSpec, load_prompt

SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def embedding_provider(settings: SettingsDep) -> EmbeddingProvider:
    return build_embedding_provider(settings)


def llm_provider(settings: SettingsDep) -> LLMProvider:
    return build_llm_provider(settings)


def rerank_prompt() -> PromptSpec:
    return load_prompt("rerank_v1")


EmbeddingDep = Annotated[EmbeddingProvider, Depends(embedding_provider)]
LLMDep = Annotated[LLMProvider, Depends(llm_provider)]
PromptDep = Annotated[PromptSpec, Depends(rerank_prompt)]

__all__ = [
    "EmbeddingDep",
    "Iterator",
    "LLMDep",
    "PromptDep",
    "SessionDep",
    "SettingsDep",
]

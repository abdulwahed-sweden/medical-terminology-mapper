"""LLM provider selection."""

from __future__ import annotations

from app.config import Settings
from app.llm.base import LLMProvider
from app.llm.fake import FakeLLMProvider

__all__ = ["LLMProvider", "build_llm_provider"]


def build_llm_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "fake":
        return FakeLLMProvider(model_id=settings.llm_model)

    if settings.llm_provider == "anthropic":
        # Imported lazily so the fake path needs no optional dependency.
        from app.llm.anthropic_provider import AnthropicProvider

        if not settings.anthropic_api_key:
            raise ValueError("LLM_PROVIDER=anthropic requires ANTHROPIC_API_KEY")
        return AnthropicProvider(
            api_key=settings.anthropic_api_key,
            model_id=settings.llm_model,
            timeout=settings.llm_timeout_seconds,
            max_output_tokens=settings.llm_max_output_tokens,
        )

    if settings.llm_provider == "openai_compat":
        from app.llm.openai_compat import OpenAICompatLLMProvider

        return OpenAICompatLLMProvider(
            base_url=settings.openai_chat_base_url,
            api_key=settings.openai_api_key,
            model_id=settings.llm_model,
            timeout=settings.llm_timeout_seconds,
            max_output_tokens=settings.llm_max_output_tokens,
        )

    raise ValueError(f"unknown LLM provider {settings.llm_provider!r}")

"""Reranking with Claude, through the official Anthropic SDK.

Two things are deliberate here:

  * `output_config.format` constrains the reply to the rerank JSON schema
    server-side. That makes a malformed reply unlikely -- but the repair retry
    and the hallucinated-code guard still run, because "unlikely" is not a
    property an audit trail should rest on, and the guard is about *which*
    codes come back, which no schema can constrain.
  * No `temperature`. Current Claude models reject the parameter outright, and
    determinism is not something to promise from a real model anyway; the
    reproducibility guarantee in this project belongs to the fake provider.
"""

from __future__ import annotations

import logging
from typing import Any

from app.llm.base import (
    RERANK_JSON_SCHEMA,
    LLMError,
    PromptSpec,
    build_rerank_input,
    run_with_one_repair,
)
from app.models.candidate import Candidate
from app.models.rerank import RerankResult

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5"


class AnthropicProvider:
    provider_id = "anthropic"

    def __init__(
        self,
        *,
        api_key: str,
        model_id: str = DEFAULT_MODEL,
        timeout: float = 60.0,
        max_output_tokens: int = 8192,
        effort: str = "medium",
        use_structured_output: bool = True,
    ) -> None:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise LLMError(
                "the `anthropic` package is required for LLM_PROVIDER=anthropic; "
                'install it with `pip install ".[anthropic]"`'
            ) from exc

        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout)
        self.model_id = model_id or DEFAULT_MODEL
        self.max_output_tokens = max_output_tokens
        self.effort = effort
        self.use_structured_output = use_structured_output

    def rerank(self, query: str, candidates: list[Candidate], prompt: PromptSpec) -> RerankResult:
        target_system = candidates[0].system if candidates else "icd10se"
        version = candidates[0].version if candidates else "unknown"
        user_input = build_rerank_input(
            query,
            candidates,
            target_system=target_system,
            terminology_version=version,
        )

        def call(repair: str | None) -> str:
            content = user_input if repair is None else f"{user_input}\n\n{repair}"
            return self._complete(prompt.text, content)

        return run_with_one_repair(call)

    def _complete(self, system_prompt: str, user_content: str) -> str:
        kwargs: dict[str, Any] = {
            "model": self.model_id,
            "max_tokens": self.max_output_tokens,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_content}],
        }
        output_config: dict[str, Any] = {"effort": self.effort}
        if self.use_structured_output:
            output_config["format"] = {
                "type": "json_schema",
                "schema": RERANK_JSON_SCHEMA,
            }
        kwargs["output_config"] = output_config

        try:
            response = self._client.messages.create(**kwargs)
        except self._anthropic.BadRequestError as exc:
            raise LLMError(
                f"Anthropic rejected the request for model {self.model_id!r}: {exc}. "
                f"If the model does not support constrained output, set "
                f"LLM_STRUCTURED_OUTPUT=false."
            ) from exc
        except self._anthropic.APIStatusError as exc:
            raise LLMError(f"Anthropic API error ({exc.status_code}): {exc}") from exc
        except self._anthropic.APIConnectionError as exc:
            raise LLMError(f"could not reach the Anthropic API: {exc}") from exc

        if response.stop_reason == "refusal":
            raise LLMError(
                "Anthropic declined this request "
                f"(category {getattr(response.stop_details, 'category', None)!r})"
            )
        if response.stop_reason == "max_tokens":
            # Returning the truncated text would send half a JSON object into
            # the repair path and waste the one retry on a doomed parse.
            raise LLMError(
                f"reply hit max_tokens ({self.max_output_tokens}); raise "
                f"LLM_MAX_OUTPUT_TOKENS or lower RERANK_CANDIDATE_CAP"
            )

        text = "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        )
        logger.info(
            "rerank_completed",
            extra={
                "provider": self.provider_id,
                "model": self.model_id,
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
        )
        return text

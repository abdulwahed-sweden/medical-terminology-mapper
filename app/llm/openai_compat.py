"""Reranking through any OpenAI-compatible `/chat/completions` endpoint.

Same reasoning as the embeddings counterpart: the endpoint may be OpenAI, Azure,
a self-hosted vLLM or Ollama, or a regional service. For clinical text, where
the text is allowed to travel is a governance decision rather than a code change.

`response_format: {"type": "json_object"}` is requested because it is the one
JSON-constraining feature these endpoints implement consistently. It guarantees
syntactic JSON, not the schema -- so the strict parser, the repair retry and the
hallucinated-code guard all still apply.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.llm.base import LLMError, PromptSpec, build_rerank_input, run_with_one_repair
from app.models.candidate import Candidate
from app.models.rerank import RerankResult

logger = logging.getLogger(__name__)


class OpenAICompatLLMProvider:
    provider_id = "openai_compat"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model_id: str,
        timeout: float = 60.0,
        max_output_tokens: int = 8192,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model_id = model_id
        self.timeout = timeout
        self.max_output_tokens = max_output_tokens

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
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload: dict[str, Any] = {
            "model": self.model_id,
            "max_tokens": self.max_output_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        }

        try:
            response = httpx.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=headers,
                timeout=self.timeout,
            )
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPError as exc:
            raise LLMError(f"chat completion request failed: {exc}") from exc

        try:
            choice = body["choices"][0]
            text: str = choice["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected chat completion response shape: {body!r}") from exc

        if choice.get("finish_reason") == "length":
            raise LLMError(
                f"reply hit the token limit ({self.max_output_tokens}); raise "
                f"LLM_MAX_OUTPUT_TOKENS or lower RERANK_CANDIDATE_CAP"
            )

        usage = body.get("usage") or {}
        logger.info(
            "rerank_completed",
            extra={
                "provider": self.provider_id,
                "model": self.model_id,
                "input_tokens": usage.get("prompt_tokens"),
                "output_tokens": usage.get("completion_tokens"),
            },
        )
        return text

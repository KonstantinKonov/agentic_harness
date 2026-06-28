"""Model client for the OwnBackend — talks to an OpenAI-compatible aggregator (polza.ai).

Vendor-neutral over HTTP (httpx): builds Chat Completions requests (with optional
function-calling ``tools`` and json_schema structured output) and normalizes the response
into stable types (``ModelResponse``: content, tool_calls, usage, cost, validated
structured). This is only the *model backend* layer — the agent loop and tools live
elsewhere (own_loop / own_tools).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError


class ModelClientError(Exception):
    """Configuration or transport error talking to the aggregator (clear, not a raw crash)."""


@dataclass(frozen=True)
class ModelConfig:
    base_url: str
    api_key: str


def config_from_env() -> ModelConfig:
    base_url = os.environ.get("OPENAI_BASE_URL", "").strip()
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not base_url:
        raise ModelClientError("OPENAI_BASE_URL is not set (OpenAI-compatible aggregator endpoint)")
    if not api_key:
        raise ModelClientError("OPENAI_API_KEY is not set")
    return ModelConfig(base_url=base_url, api_key=api_key)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class ModelResponse:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = ""
    usage: Usage = field(default_factory=Usage)
    cost_usd: float = 0.0
    structured: dict[str, Any] | None = None   # set when output_schema given and content valid
    structured_error: str | None = None        # set when output_schema given and validation failed


def _to_tool_call(tc: Any) -> ToolCall:
    fn = tc.get("function") or {}
    raw = fn.get("arguments")
    if isinstance(raw, str):
        try:
            args = json.loads(raw or "{}")
        except json.JSONDecodeError:
            args = {"__raw__": raw}
    else:
        args = dict(raw or {})
    return ToolCall(id=tc.get("id") or "", name=fn.get("name") or "", arguments=args)


def _to_usage(u: Any) -> Usage:
    return Usage(
        prompt_tokens=int(u.get("prompt_tokens") or 0),
        completion_tokens=int(u.get("completion_tokens") or 0),
        total_tokens=int(u.get("total_tokens") or 0),
    )


def _extract_cost(data: Any) -> float:
    usage = data.get("usage") or {}
    for key in ("cost", "cost_usd", "total_cost"):
        if key in usage:
            return float(usage[key] or 0.0)
        if key in data:
            return float(data[key] or 0.0)
    return 0.0


class ModelClient:
    def __init__(
        self,
        config: ModelConfig,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = 120.0,
    ) -> None:
        self._config = config
        self._client = client  # injectable (e.g. httpx.MockTransport) for offline tests
        self._timeout = timeout

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers={"Authorization": f"Bearer {self._config.api_key}"},
                timeout=self._timeout,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def chat_completion(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
        output_schema: type[BaseModel] | None = None,
    ) -> ModelResponse:
        if not model:
            raise ModelClientError("model must be a non-empty model id")

        payload: dict[str, Any] = {"model": model, "messages": list(messages)}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice or "auto"
        if output_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": output_schema.__name__,
                    "schema": output_schema.model_json_schema(),
                    "strict": True,
                },
            }

        url = self._config.base_url.rstrip("/") + "/chat/completions"
        try:
            resp = await self._ensure_client().post(url, json=payload)
        except httpx.HTTPError as exc:
            raise ModelClientError(f"request to {url} failed: {exc}") from exc
        if resp.status_code >= 400:
            raise ModelClientError(f"aggregator returned {resp.status_code}: {resp.text[:300]}")

        return self._parse(resp.json(), output_schema)

    def _parse(self, data: Any, output_schema: type[BaseModel] | None) -> ModelResponse:
        try:
            choice = data["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelClientError(f"malformed response: {str(data)[:300]}") from exc

        content = message.get("content") or ""
        tool_calls = [_to_tool_call(tc) for tc in (message.get("tool_calls") or [])]
        out = ModelResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=choice.get("finish_reason") or "",
            usage=_to_usage(data.get("usage") or {}),
            cost_usd=_extract_cost(data),
        )
        if output_schema is not None:
            try:
                out.structured = output_schema.model_validate_json(content).model_dump()
            except ValidationError as exc:
                out.structured_error = str(exc)
        return out

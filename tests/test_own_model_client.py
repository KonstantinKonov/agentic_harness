"""Acceptance checks for feature_own_model_client (tz.md v2). Offline via httpx.MockTransport."""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from harness.own.model_client import (
    ModelClient,
    ModelClientError,
    ModelConfig,
    config_from_env,
)
from harness.schemas import ReviewerVerdict


def _client(handler: Any) -> ModelClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                             headers={"Authorization": "Bearer test"})
    return ModelClient(ModelConfig(base_url="https://agg.test/v1", api_key="test"), client=http)


def _ok(content: str = "", tool_calls: list[dict[str, Any]] | None = None,
        usage: dict[str, Any] | None = None) -> dict[str, Any]:
    msg: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls is not None:
        msg["tool_calls"] = tool_calls
    return {"choices": [{"message": msg, "finish_reason": "stop"}], "usage": usage or {}}


async def test_builds_openai_chat_completions_request() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_ok("hi"))

    await _client(handler).chat_completion(
        [{"role": "user", "content": "hello"}],
        model="gpt-x",
        tools=[{"type": "function", "function": {"name": "fs_read", "parameters": {}}}],
    )
    assert seen["method"] == "POST"
    assert seen["url"].endswith("/chat/completions")
    assert seen["auth"] == "Bearer test"
    assert seen["body"]["model"] == "gpt-x"
    assert seen["body"]["messages"][0]["content"] == "hello"
    assert seen["body"]["tools"][0]["function"]["name"] == "fs_read"
    assert seen["body"]["tool_choice"] == "auto"


async def test_parses_tool_calls_usage_and_cost() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_ok(
            tool_calls=[{"id": "call_1", "type": "function",
                         "function": {"name": "fs_read",
                                      "arguments": '{"path": "a.py", "limit": 50}'}}],
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "cost": 0.0021},
        ))

    resp = await _client(handler).chat_completion([{"role": "user", "content": "x"}], model="m")
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "fs_read"
    assert resp.tool_calls[0].arguments == {"path": "a.py", "limit": 50}
    assert resp.usage.total_tokens == 15
    assert resp.cost_usd == pytest.approx(0.0021)


async def test_structured_output_is_validated() -> None:
    payload = '{"verdict": "PASS", "spec_conformance": "full", "issues": [], "rejects": []}'

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["response_format"]["type"] == "json_schema"  # schema sent
        return httpx.Response(200, json=_ok(payload))

    resp = await _client(handler).chat_completion(
        [{"role": "user", "content": "x"}], model="m", output_schema=ReviewerVerdict)
    assert resp.structured_error is None
    assert resp.structured == {"verdict": "PASS", "spec_conformance": "full",
                               "issues": [], "rejects": []}


async def test_invalid_structured_output_signals_reprompt() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_ok('{"verdict": "MAYBE"}'))  # invalid literal

    resp = await _client(handler).chat_completion(
        [{"role": "user", "content": "x"}], model="m", output_schema=ReviewerVerdict)
    assert resp.structured is None
    assert resp.structured_error  # non-empty -> loop should re-prompt


async def test_http_error_raises_clear_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with pytest.raises(ModelClientError):
        await _client(handler).chat_completion([{"role": "user", "content": "x"}], model="m")


async def test_empty_model_raises() -> None:
    with pytest.raises(ModelClientError):
        await _client(lambda r: httpx.Response(200, json=_ok())).chat_completion([], model="")


def test_config_from_env_requires_both_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ModelClientError):
        config_from_env()
    monkeypatch.setenv("OPENAI_BASE_URL", "https://agg.test/v1")
    with pytest.raises(ModelClientError):  # key still missing
        config_from_env()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    cfg = config_from_env()
    assert cfg.base_url == "https://agg.test/v1" and cfg.api_key == "sk-test"


def test_import_does_not_pull_sdk_or_langgraph() -> None:
    import subprocess
    import sys
    code = (
        "import sys, harness.own.model_client\n"
        "bad = [m for m in sys.modules if m.split('.')[0] in ('claude_agent_sdk', 'langgraph')]\n"
        "assert not bad, bad\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)

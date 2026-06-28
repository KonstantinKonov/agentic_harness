"""Acceptance checks for feature_own_loop (tz.md v2): the model↔tools agent loop.

Fully offline: a scripted mock aggregator (httpx.MockTransport) drives a real ModelClient
through the real loop, so the loop is deterministic given fixed model responses.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from harness.own.loop import LoopConfig, run_agent_loop
from harness.own.model_client import ModelClient, ModelConfig
from harness.own.tools import ToolContext, tool_schemas
from harness.schemas import DevStatus

# Attach an in-memory exporter to whatever provider exists (a sibling test may have set one).
_EXPORTER = InMemorySpanExporter()
_provider = trace.get_tracer_provider()
if not isinstance(_provider, TracerProvider):
    _provider = TracerProvider()
    trace.set_tracer_provider(_provider)
_provider.add_span_processor(SimpleSpanProcessor(_EXPORTER))


@pytest.fixture(autouse=True)
def _clear_spans() -> Iterator[None]:
    _EXPORTER.clear()
    yield


def _spans() -> list[ReadableSpan]:
    return list(_EXPORTER.get_finished_spans())


def _attr(span: ReadableSpan, key: str) -> object:
    attrs = span.attributes
    assert attrs is not None
    return attrs[key]


# --- mock aggregator ---------------------------------------------------------

class _Mock:
    """Replays a scripted list of OpenAI responses and records each request body."""

    def __init__(self, responses: list[dict[str, Any]], *, repeat_last: bool = False) -> None:
        self.responses = list(responses)
        self.repeat_last = repeat_last
        self.requests: list[dict[str, Any]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        if len(self.responses) > 1 or not self.repeat_last:
            data = self.responses.pop(0)
        else:
            data = self.responses[0]  # keep returning the last one (for max-turns)
        return httpx.Response(200, json=data)


def _client(handler: Any) -> ModelClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return ModelClient(ModelConfig(base_url="https://agg.test/v1", api_key="t"), client=http)


def _tool_resp(calls: list[tuple[str, dict[str, Any]]], cost: float = 0.0) -> dict[str, Any]:
    return {
        "choices": [{
            "message": {"content": None, "tool_calls": [
                {"id": f"c{i}", "type": "function",
                 "function": {"name": n, "arguments": json.dumps(a)}}
                for i, (n, a) in enumerate(calls)
            ]},
            "finish_reason": "tool_calls",
        }],
        "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10, "cost": cost},
    }


def _final_resp(content: str, cost: float = 0.0) -> dict[str, Any]:
    return {
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10, "cost": cost},
    }


def _ctx(root: Path) -> ToolContext:
    return ToolContext(root=root, bash_allowlist=())


_GREEN = json.dumps({"dev_status": "green", "note": "done"})


# --- 2 tool_calls -> final ---------------------------------------------------

async def test_two_tool_calls_then_structured_final(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello\nworld\n", encoding="utf-8")
    mock = _Mock([
        _tool_resp([("fs_read", {"path": "a.txt"}), ("fs_grep", {"pattern": "world"})], cost=0.01),
        _final_resp(_GREEN, cost=0.02),
    ])
    result = await run_agent_loop(
        _client(mock.handler),
        config=LoopConfig(model="m"),
        system_prompt="sys", task="do it",
        tool_ctx=_ctx(tmp_path), tools=tool_schemas(),
        output_schema=DevStatus,
    )
    assert result.subtype == "success"
    assert result.structured == {
        "dev_status": "green", "files_touched": [], "tests": "", "note": "done"}
    assert result.cost_usd == pytest.approx(0.03)  # cost summed across turns

    tool_spans = [s for s in _spans() if s.name.startswith("tool.")]
    assert {s.name for s in tool_spans} == {"tool.fs_read", "tool.fs_grep"}  # one span per call
    read_span = next(s for s in tool_spans if s.name == "tool.fs_read")
    assert _attr(read_span, "harness.tool") == "fs_read"


# --- max turns ---------------------------------------------------------------

async def test_max_turns_stops_without_hang(tmp_path: Path) -> None:
    mock = _Mock([_tool_resp([("fs_glob", {"pattern": "*"})])], repeat_last=True)
    result = await run_agent_loop(
        _client(mock.handler),
        config=LoopConfig(model="m", max_turns=3),
        system_prompt="s", task="t",
        tool_ctx=_ctx(tmp_path), tools=tool_schemas(),
        output_schema=DevStatus,
    )
    assert result.subtype == "error_max_turns"
    assert len(mock.requests) == 3  # bounded, did not loop forever
    assert len([s for s in _spans() if s.name.startswith("tool.")]) == 3


# --- tool error recovery -----------------------------------------------------

async def test_tool_error_is_fed_back_and_loop_survives(tmp_path: Path) -> None:
    mock = _Mock([
        _tool_resp([("fs_read", {"path": "missing.txt"})]),  # ToolError: not a file
        _final_resp(_GREEN),
    ])
    result = await run_agent_loop(
        _client(mock.handler),
        config=LoopConfig(model="m"),
        system_prompt="s", task="t",
        tool_ctx=_ctx(tmp_path), tools=tool_schemas(),
        output_schema=DevStatus,
    )
    assert result.subtype == "success"  # survived the bad call
    assert result.structured is not None and result.structured["dev_status"] == "green"

    tool_msgs = [m for m in mock.requests[1]["messages"] if m["role"] == "tool"]
    assert tool_msgs and "ERROR" in tool_msgs[0]["content"]  # error fed back to the model
    err_span = next(s for s in _spans() if s.name == "tool.fs_read")
    assert _attr(err_span, "harness.tool_error")


# --- structured re-prompt ----------------------------------------------------

async def test_invalid_structured_final_reprompts_then_errors(tmp_path: Path) -> None:
    mock = _Mock([_final_resp("not json"), _final_resp("{still bad"), _final_resp("nope")])
    result = await run_agent_loop(
        _client(mock.handler),
        config=LoopConfig(model="m", structured_retries=2),
        system_prompt="s", task="t",
        tool_ctx=_ctx(tmp_path), tools=tool_schemas(),
        output_schema=DevStatus,
    )
    assert result.subtype == "error_max_structured_output_retries"
    assert len(mock.requests) == 3  # initial + 2 re-prompts, then give up


async def test_invalid_structured_final_then_valid_recovers(tmp_path: Path) -> None:
    mock = _Mock([_final_resp("not json"), _final_resp(json.dumps({"dev_status": "blocked"}))])
    result = await run_agent_loop(
        _client(mock.handler),
        config=LoopConfig(model="m", structured_retries=2),
        system_prompt="s", task="t",
        tool_ctx=_ctx(tmp_path), tools=tool_schemas(),
        output_schema=DevStatus,
    )
    assert result.subtype == "success"
    assert result.structured is not None and result.structured["dev_status"] == "blocked"
    assert len(mock.requests) == 2  # one re-prompt was enough
    # the re-prompt carried the validation error back to the model
    assert any("did not match" in m.get("content", "")
               for m in mock.requests[1]["messages"] if m["role"] == "user")


# --- schema-less role --------------------------------------------------------

async def test_schema_less_role_returns_free_text(tmp_path: Path) -> None:
    mock = _Mock([_final_resp("# Plan\n- step 1\n- step 2")])
    result = await run_agent_loop(
        _client(mock.handler),
        config=LoopConfig(model="m"),
        system_prompt="s", task="write a plan",
        tool_ctx=_ctx(tmp_path), tools=[],
        output_schema=None,
    )
    assert result.subtype == "success"
    assert result.structured is None
    assert "Plan" in result.text


# --- cost budget -------------------------------------------------------------

async def test_cost_budget_stops_the_run(tmp_path: Path) -> None:
    mock = _Mock([_tool_resp([("fs_glob", {"pattern": "*"})], cost=1.0)], repeat_last=True)
    result = await run_agent_loop(
        _client(mock.handler),
        config=LoopConfig(model="m", max_turns=10, cost_budget_usd=0.5),
        system_prompt="s", task="t",
        tool_ctx=_ctx(tmp_path), tools=tool_schemas(),
        output_schema=DevStatus,
    )
    assert result.subtype == "error_cost_budget"
    assert result.cost_usd == pytest.approx(1.0)  # stopped right after crossing the budget

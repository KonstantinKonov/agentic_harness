"""The agentic tool-loop for the OwnBackend: model ↔ tools until a final answer.

This is the *control* element of the harness (loop · tools · model · context) and the one
nondeterministic engine that turns a task into a ``RoleResult`` without the SDK. The loop
is deterministic given fixed model responses, so it is tested offline against a mock
aggregator (``httpx.MockTransport``).

Per turn: ask the model (with the role's tools + optional output schema). If it returns
``tool_calls``, run them **sequentially** (each in its own OTel span), feed every result
back as a ``tool`` message, and loop. Otherwise it is the final answer: a schema-bearing
role must return valid structured output; an invalid final is re-prompted up to
``structured_retries`` times, then gives up with an error subtype. A tool error is fed back
as a message (at-least-once recovery), never raised, so one bad call can't kill the loop.
``max_turns`` and an optional cost budget bound the run.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from harness.backends.base import RoleResult
from harness.config import MAX_TURNS_PER_ROLE
from harness.observability import get_tracer
from harness.own.model_client import ModelClient, ModelResponse, ToolCall
from harness.own.tools import ToolContext, ToolError, run_tool

# RoleResult.subtype values the loop can produce.
SUCCESS = "success"
ERROR_MAX_TURNS = "error_max_turns"
ERROR_MAX_STRUCTURED = "error_max_structured_output_retries"
ERROR_COST_BUDGET = "error_cost_budget"

_MAX_ARG_ATTR = 300  # chars of tool args / errors recorded on a span


@dataclass(frozen=True)
class LoopConfig:
    model: str
    max_turns: int = MAX_TURNS_PER_ROLE
    structured_retries: int = 2           # extra attempts to fix an invalid structured final
    cost_budget_usd: float | None = None  # stop once the accumulated cost crosses this


def _assistant_echo(resp: ModelResponse) -> dict[str, Any]:
    """Re-serialize the model's tool-call turn so the aggregator can correlate results."""
    return {
        "role": "assistant",
        "content": resp.content or None,
        "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
            }
            for tc in resp.tool_calls
        ],
    }


def _run_one_tool(call: ToolCall, ctx: ToolContext) -> str:
    """Execute one tool call inside its own span; a tool error becomes a fed-back string."""
    with get_tracer().start_as_current_span(f"tool.{call.name}") as span:
        span.set_attribute("harness.tool", call.name)
        span.set_attribute("harness.tool_args", json.dumps(call.arguments)[:_MAX_ARG_ATTR])
        try:
            return run_tool(call.name, call.arguments, ctx)
        except ToolError as exc:
            span.set_attribute("harness.tool_error", str(exc)[:_MAX_ARG_ATTR])
            return f"ERROR: {exc}"


async def run_agent_loop(
    client: ModelClient,
    *,
    config: LoopConfig,
    system_prompt: str,
    task: str,
    tool_ctx: ToolContext,
    tools: list[dict[str, Any]],
    output_schema: type[BaseModel] | None = None,
) -> RoleResult:
    """Drive one role to a ``RoleResult`` via the model↔tools loop. Never raises on a tool
    error or a bad final answer — it returns a result with the matching ``subtype`` instead."""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task},
    ]
    cost = 0.0
    structured_attempts = 0
    last_text = ""

    for _turn in range(config.max_turns):
        resp = await client.chat_completion(
            messages,
            model=config.model,
            tools=tools or None,
            output_schema=output_schema,
        )
        cost += resp.cost_usd
        last_text = resp.content or last_text

        if config.cost_budget_usd is not None and cost > config.cost_budget_usd:
            return RoleResult(structured=None, text=last_text,
                              subtype=ERROR_COST_BUDGET, cost_usd=cost)

        if resp.tool_calls:
            messages.append(_assistant_echo(resp))
            for call in resp.tool_calls:  # sequential, even if the model batched them
                result = _run_one_tool(call, tool_ctx)
                messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
            continue

        # No tool calls -> the model's final answer.
        if output_schema is None:
            return RoleResult(structured=None, text=resp.content, subtype=SUCCESS, cost_usd=cost)
        if resp.structured is not None:
            return RoleResult(structured=resp.structured, text=resp.content,
                              subtype=SUCCESS, cost_usd=cost)

        # Schema mismatch: re-prompt with the validation error, up to N times.
        structured_attempts += 1
        if structured_attempts > config.structured_retries:
            return RoleResult(structured=None, text=resp.content,
                              subtype=ERROR_MAX_STRUCTURED, cost_usd=cost)
        messages.append({"role": "assistant", "content": resp.content})
        messages.append({
            "role": "user",
            "content": (
                "Your final answer did not match the required JSON schema:\n"
                f"{resp.structured_error}\n"
                "Reply again with ONLY a valid JSON object that satisfies the schema."
            ),
        })

    return RoleResult(structured=None, text=last_text, subtype=ERROR_MAX_TURNS, cost_usd=cost)

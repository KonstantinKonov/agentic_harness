"""OwnBackend engine: a self-hosted agent loop + tools over an OpenAI-compatible model.

The pieces (model_client / tools / loop) are assembled into ``OwnBackend`` (a ``RoleBackend``)
in a later milestone. This package depends only on httpx + pydantic + stdlib — no SDK, no
langgraph, no network.
"""
from __future__ import annotations

from harness.own.loop import LoopConfig, run_agent_loop
from harness.own.model_client import (
    ModelClient,
    ModelClientError,
    ModelConfig,
    ModelResponse,
    ToolCall,
    Usage,
    config_from_env,
)
from harness.own.tools import (
    TOOLS,
    ToolContext,
    ToolError,
    ToolSpec,
    load_bash_allowlist,
    run_tool,
    tool_schemas,
)

__all__ = [
    "LoopConfig",
    "run_agent_loop",
    "ModelClient",
    "ModelClientError",
    "ModelConfig",
    "ModelResponse",
    "ToolCall",
    "Usage",
    "config_from_env",
    "TOOLS",
    "ToolContext",
    "ToolError",
    "ToolSpec",
    "load_bash_allowlist",
    "run_tool",
    "tool_schemas",
]

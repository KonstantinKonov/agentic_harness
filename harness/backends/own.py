"""OwnBackend — the third RoleBackend: a self-hosted agent loop over an OpenAI-compatible
aggregator (polza.ai), independent of ``claude_agent_sdk``.

It assembles the three OwnBackend pieces behind ``run()``: a ``ModelClient`` (the model),
the ACI ``tools`` filtered to the role's ``allowed_tools``, and ``run_agent_loop`` (the
control loop). The system prompt is the role's ``.claude/agents/<role>.md`` body (shared
with ClaudeSdkBackend via ``load_system_prompt``); the verdict schema comes from
``ROLE_SCHEMA``. The graph sees the same ``RoleResult`` contract as stub / claude_sdk.

Not re-exported from ``harness.backends`` — import it by name. That keeps the aggregator
HTTP client (httpx) out of the core import graph until OwnBackend is explicitly used.
"""
from __future__ import annotations

from harness.backends.base import RoleContext, RoleResult
from harness.backends.claude_sdk import load_system_prompt
from harness.config import ROLES
from harness.own.loop import LoopConfig, run_agent_loop
from harness.own.model_client import ModelClient, config_from_env
from harness.own.tools import ToolContext, load_bash_allowlist, tool_schemas
from harness.schemas import ROLE_SCHEMA

# Claude tool names (config.ROLES[*].allowed_tools) -> OwnBackend ACI tool names.
_OWN_TOOL = {
    "Read": "fs_read",
    "Edit": "fs_edit",
    "Write": "fs_write",
    "Grep": "fs_grep",
    "Glob": "fs_glob",
}
# Bash is deliberately kept out of allowed_tools (see config.py): it's governed by the
# settings.json allowlist, not per-role grants. Only the working roles that run/verify
# tests get the (allowlist-gated) bash tool; read-only roles never do.
_BASH_ROLES = frozenset({"developer", "tester"})


def own_tool_names(role: str) -> list[str]:
    """The OwnBackend tools a role may use, derived from ``config.ROLES[role].allowed_tools``."""
    names = [_OWN_TOOL[t] for t in ROLES[role].allowed_tools if t in _OWN_TOOL]
    if role in _BASH_ROLES:
        names.append("bash")
    return names


class OwnBackend:
    """A ``RoleBackend`` backed by an OpenAI-compatible aggregator + the own agent loop."""

    def __init__(self, *, client: ModelClient | None = None) -> None:
        self._client = client  # injectable (e.g. a mock aggregator) for offline tests

    def _ensure_client(self) -> ModelClient:
        if self._client is None:
            self._client = ModelClient(config_from_env())
        return self._client

    async def run(self, role: str, task: str, *, context: RoleContext) -> RoleResult:
        tuning = ROLES[role]
        tool_ctx = ToolContext(
            root=context.root,
            bash_allowlist=load_bash_allowlist(context.root),
        )
        return await run_agent_loop(
            self._ensure_client(),
            # model alias ("sonnet"/...) passes through; mapping to real aggregator ids is a
            # tuning concern (non-goal here).
            config=LoopConfig(model=tuning.model),
            system_prompt=load_system_prompt(context.root / ".claude" / "agents", role),
            task=task,
            tool_ctx=tool_ctx,
            tools=tool_schemas(own_tool_names(role)),
            output_schema=ROLE_SCHEMA.get(role),
        )

"""ClaudeSdkBackend — runs a role as a top-level ``claude_agent_sdk.query()``.

Port of the old roles.py: the role's ``.claude/agents/<role>.md`` body is the system
prompt; model/effort/tools come from ``config.ROLES``; a role with a verdict schema
(``ROLE_SCHEMA``) gets ``output_format`` so the SDK validates the final answer into
``ResultMessage.structured_output`` (no text parsing). The SDK is imported lazily inside
``run()`` so the deterministic core stays importable and testable without it.

Auth defaults to ``ANTHROPIC_API_KEY`` (recommended for headless/CI); a Claude
subscription also works if the SDK is configured for it.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from harness.backends.base import RoleContext, RoleResult
from harness.config import MAX_TURNS_PER_ROLE, ROLES
from harness.schemas import ROLE_SCHEMA

_FRONTMATTER = re.compile(r"^---\n.*?\n---\n?(.*)$", re.DOTALL)


def load_system_prompt(agents_dir: Path, role: str) -> str:
    """Return the markdown body (frontmatter stripped) to use as the system prompt."""
    md = (agents_dir / f"{role}.md").read_text(encoding="utf-8")
    m = _FRONTMATTER.match(md)
    return (m.group(1) if m else md).strip()


class ClaudeSdkBackend:
    def _build_opts(self, role: str, context: RoleContext) -> dict[str, Any]:
        """Assemble the ClaudeAgentOptions kwargs. SDK-free so it is unit-testable offline."""
        tuning = ROLES[role]
        opts: dict[str, Any] = dict(
            system_prompt=load_system_prompt(context.root / ".claude" / "agents", role),
            model=tuning.model,
            effort=tuning.effort,
            allowed_tools=list(tuning.allowed_tools),
            permission_mode=tuning.permission_mode,
            setting_sources=["project"],  # load CLAUDE.md + .claude/settings.json (Bash allowlist)
            cwd=str(context.root),
            max_turns=MAX_TURNS_PER_ROLE,
        )
        schema = ROLE_SCHEMA.get(role)
        if schema is not None:
            opts["output_format"] = {"type": "json_schema", "schema": schema.model_json_schema()}
        return opts

    async def run(self, role: str, task: str, *, context: RoleContext) -> RoleResult:
        # Lazy import: the deterministic core must import without the SDK installed.
        from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

        opts = self._build_opts(role, context)
        result = RoleResult(structured=None, text="", subtype=None, cost_usd=0.0)
        async for message in query(prompt=task, options=ClaudeAgentOptions(**opts)):
            if isinstance(message, ResultMessage):
                result.structured = getattr(message, "structured_output", None)
                result.text = message.result or ""
                result.subtype = getattr(message, "subtype", None)
                result.cost_usd = float(getattr(message, "total_cost_usd", 0.0) or 0.0)
        return result

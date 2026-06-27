"""Load role definitions from `.claude/agents/*.md` and run each one as its own
top-level `query()` call.

A role's markdown body becomes the system prompt; its model/effort/tools come from
`config.ROLES`. When a role has a verdict schema (`schemas.ROLE_SCHEMA`), it is passed
as `output_format` so the SDK validates the final answer and returns it in
`ResultMessage.structured_output` — no free-text parsing.

Because each role is a separate top-level query (not a nested subagent), there is no
permission-mode inheritance to worry about — every role is fully isolated.
"""
import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from .config import MAX_TURNS_PER_ROLE, ROLES, RoleTuning

_FRONTMATTER = re.compile(r"^---\n.*?\n---\n?(.*)$", re.DOTALL)


@dataclass
class RoleResult:
    structured: dict | None   # ResultMessage.structured_output (None if no schema or validation failed)
    text: str                 # ResultMessage.result (free text; used by the summarizer)
    subtype: str | None       # "success" | "error_max_structured_output_retries" | ...
    cost_usd: float


def load_system_prompt(agents_dir: Path, role: str) -> str:
    """Return the markdown body (frontmatter stripped) to use as the system prompt."""
    md = (agents_dir / f"{role}.md").read_text(encoding="utf-8")
    m = _FRONTMATTER.match(md)
    return (m.group(1) if m else md).strip()


async def run_role(
    role: str, task: str, *, root: Path, output_schema: type[BaseModel] | None = None
) -> RoleResult:
    # Imported lazily so the deterministic core (machine/store/curation) can be used
    # and tested without the SDK installed.
    from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

    tuning: RoleTuning = ROLES[role]
    opts: dict = dict(
        system_prompt=load_system_prompt(root / ".claude" / "agents", role),
        model=tuning.model,
        effort=tuning.effort,
        allowed_tools=list(tuning.allowed_tools),
        permission_mode=tuning.permission_mode,
        setting_sources=["project"],   # load CLAUDE.md + .claude/settings.json (the Bash allowlist)
        cwd=str(root),
        max_turns=MAX_TURNS_PER_ROLE,
    )
    if output_schema is not None:
        opts["output_format"] = {"type": "json_schema", "schema": output_schema.model_json_schema()}

    result = RoleResult(structured=None, text="", subtype=None, cost_usd=0.0)
    async for message in query(prompt=task, options=ClaudeAgentOptions(**opts)):
        if isinstance(message, ResultMessage):
            result.structured = getattr(message, "structured_output", None)
            result.text = message.result or ""
            result.subtype = getattr(message, "subtype", None)
            result.cost_usd = float(getattr(message, "total_cost_usd", 0.0) or 0.0)
    return result

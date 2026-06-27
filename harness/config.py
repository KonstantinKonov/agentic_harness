"""Per-role tuning and global limits for the harness orchestrator.

Pure data: this module spends no model tokens and imports no SDK, so the deterministic
core can be imported and tested without ``claude_agent_sdk`` installed.

Bash is intentionally NOT in any role's ``allowed_tools``. Shell commands are governed
only by the scoped allow-rules in ``.claude/settings.json``, and
``permission_mode="dontAsk"`` denies anything outside that allowlist instead of hanging
on an interactive prompt — which is what makes a headless run safe.
"""
from __future__ import annotations

from dataclasses import dataclass

CAP = 3                        # max rounds per loop (A: dev<->reviewer, B: dev<->tester) -> else ESCALATED
MAX_TRANSITIONS = 4 * CAP + 4  # hard safety stop against a runaway graph
MAX_TURNS_PER_ROLE = 40        # tool-use round trips inside a single role invocation


@dataclass(frozen=True)
class RoleTuning:
    model: str                      # alias ("sonnet"/"opus"/"haiku") or full model id
    effort: str                     # low | medium | high | xhigh | max
    allowed_tools: tuple[str, ...]  # auto-approved NON-Bash tools (Bash -> settings.json)
    permission_mode: str = "dontAsk"


# The 5 roles. planner runs once before the loop (fills plan.md); the other four are the
# loop workers (developer <-> reviewer <-> tester, then summarizer at DONE).
ROLES: dict[str, RoleTuning] = {
    "planner":    RoleTuning("opus",   "high",   ("Read", "Write", "Grep", "Glob")),
    "developer":  RoleTuning("sonnet", "medium", ("Read", "Edit", "Write", "Grep", "Glob")),
    "reviewer":   RoleTuning("opus",   "medium", ("Read", "Grep", "Glob")),
    "tester":     RoleTuning("sonnet", "medium", ("Read", "Edit", "Write", "Grep", "Glob")),
    "summarizer": RoleTuning("haiku",  "low",    ("Read", "Grep", "Glob")),
}

"""The backend boundary: how the deterministic graph invokes a (nondeterministic) role.

A role is run through a ``RoleBackend`` — the single seam that lets us swap the engine
(``stub`` / ``claude_sdk`` / ``own``) without the graph knowing. The graph hands a role
name + task string + read-only context and gets back a ``RoleResult``; everything else
(tools, tool-loop, model) lives behind the backend.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass
class RoleResult:
    structured: dict[str, object] | None  # validated verdict (None for schema-less roles)
    text: str                             # free text (used by planner / summarizer)
    subtype: str | None                   # "success" | "error_*" | None
    cost_usd: float


@dataclass(frozen=True)
class RoleContext:
    root: Path   # repo root; a backend reads/writes only under here
    branch: str  # the feature branch being driven


@runtime_checkable
class RoleBackend(Protocol):
    """Runs one role invocation to a structured result. Implementations must be async."""

    async def run(self, role: str, task: str, *, context: RoleContext) -> RoleResult: ...

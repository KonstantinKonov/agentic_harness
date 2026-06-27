"""StubBackend — a deterministic RoleBackend that replays scripted responses.

It exists so the graph can be driven down a chosen path with zero network and zero model
tokens: ``scripts`` maps each role to an ordered list of responses for its successive
calls. Schema-bearing roles (see ``ROLE_SCHEMA``) take a pydantic model of that schema or
a mapping validated against it; schema-less roles (planner/summarizer) take free text.
Validation guarantees the returned ``RoleResult.structured`` always conforms to the
role's contract — a malformed scripted verdict raises instead of leaking through.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import BaseModel

from harness.backends.base import RoleContext, RoleResult
from harness.schemas import ROLE_SCHEMA

Scripted = BaseModel | Mapping[str, object] | str


class StubBackend:
    def __init__(self, scripts: Mapping[str, Sequence[Scripted]]) -> None:
        self._scripts: dict[str, list[Scripted]] = {r: list(v) for r, v in scripts.items()}
        self._calls: dict[str, int] = {}

    async def run(self, role: str, task: str, *, context: RoleContext) -> RoleResult:
        script = self._scripts.get(role)
        if script is None:
            raise KeyError(f"StubBackend: no scripted responses for role {role!r}")
        idx = self._calls.get(role, 0)
        if idx >= len(script):
            raise IndexError(
                f"StubBackend: role {role!r} called {idx + 1}x but only {len(script)} scripted"
            )
        self._calls[role] = idx + 1
        raw = script[idx]

        schema = ROLE_SCHEMA.get(role)
        if schema is None:  # free-text role (or no contract): response must be text
            if not isinstance(raw, str):
                raise TypeError(f"role {role!r} has no schema; scripted response must be str")
            return RoleResult(structured=None, text=raw, subtype="success", cost_usd=0.0)

        if isinstance(raw, BaseModel):
            if not isinstance(raw, schema):
                raise TypeError(
                    f"role {role!r} expects {schema.__name__}, got {type(raw).__name__}"
                )
            model: BaseModel = raw
        elif isinstance(raw, str):
            raise TypeError(f"role {role!r} expects {schema.__name__} or mapping, got str")
        else:
            model = schema.model_validate(raw)  # raises ValidationError on a bad mapping
        return RoleResult(structured=model.model_dump(), text="", subtype="success", cost_usd=0.0)

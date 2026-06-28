"""Agent-Computer Interface (ACI) for the OwnBackend: the tools the model drives.

Design follows the research notes: tools are the main quality lever, so each one is
sandboxed, context-efficient (default output limits + truncation markers), and validated.
`fs_edit` uses an exact, UNIQUE old→new match (like Claude's Edit). This module is the one
sanctioned place that runs a shell (the `bash` tool, gated by the settings.json allowlist).

Tools are pure of the model: schemas come from pydantic, args are validated, results are
deterministic strings. No SDK, no langgraph, no network.
"""
from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

# Context-efficiency defaults (keep tool output from blowing up the context window).
DEFAULT_READ_LIMIT = 400      # lines
MAX_LINE_LEN = 2000           # chars per line
DEFAULT_GREP_LIMIT = 100      # matches
DEFAULT_GLOB_LIMIT = 200      # paths
MAX_BASH_OUTPUT = 8000        # chars
DEFAULT_BASH_TIMEOUT = 120    # seconds

_SKIP_DIRS = {".git", ".venv", "__pycache__", "node_modules", ".mypy_cache", ".pytest_cache"}


class ToolError(Exception):
    """A tool failed in a recoverable way; the loop feeds the message back to the model."""


@dataclass(frozen=True)
class ToolContext:
    root: Path                       # sandbox root; every path is jailed under here
    bash_allowlist: tuple[str, ...]  # allowed command prefixes (from .claude/settings.json)


def _resolve(root: Path, rel: str) -> Path:
    """Resolve `rel` under `root`, refusing anything that escapes the sandbox."""
    root = root.resolve()
    target = (root / rel).resolve()
    if target != root and root not in target.parents:
        raise ToolError(f"path escapes sandbox root: {rel!r}")
    return target


# --- fs_read -----------------------------------------------------------------

class FsReadArgs(BaseModel):
    path: str
    offset: int = 1               # 1-based first line
    limit: int = DEFAULT_READ_LIMIT


def _fs_read(raw: dict[str, Any], ctx: ToolContext) -> str:
    a = FsReadArgs.model_validate(raw)
    p = _resolve(ctx.root, a.path)
    if not p.is_file():
        raise ToolError(f"not a file: {a.path}")
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(a.offset - 1, 0)
    limit = max(a.limit, 1)
    window = lines[start:start + limit]
    out = []
    for i, line in enumerate(window, start=start + 1):
        if len(line) > MAX_LINE_LEN:
            line = line[:MAX_LINE_LEN] + " …(line truncated)"
        out.append(f"{i:>6}\t{line}")
    body = "\n".join(out)
    if start + limit < len(lines):
        body += f"\n… (truncated at {limit} lines; {len(lines)} total — use offset to read more)"
    return body or "(empty file)"


# --- fs_write ----------------------------------------------------------------

class FsWriteArgs(BaseModel):
    path: str
    content: str


def _fs_write(raw: dict[str, Any], ctx: ToolContext) -> str:
    a = FsWriteArgs.model_validate(raw)
    p = _resolve(ctx.root, a.path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(a.content, encoding="utf-8")
    return f"wrote {a.path} ({len(a.content)} bytes)"


# --- fs_edit (exact, unique) -------------------------------------------------

class FsEditArgs(BaseModel):
    path: str
    old_string: str
    new_string: str


def _fs_edit(raw: dict[str, Any], ctx: ToolContext) -> str:
    a = FsEditArgs.model_validate(raw)
    p = _resolve(ctx.root, a.path)
    if not p.is_file():
        raise ToolError(f"not a file: {a.path}")
    if a.old_string == a.new_string:
        raise ToolError("old_string and new_string are identical")
    text = p.read_text(encoding="utf-8")
    count = text.count(a.old_string)
    if count == 0:
        raise ToolError("old_string not found in file")
    if count > 1:
        raise ToolError(f"old_string is not unique ({count} matches) — add surrounding context")
    p.write_text(text.replace(a.old_string, a.new_string, 1), encoding="utf-8")
    return f"edited {a.path}"


# --- fs_grep -----------------------------------------------------------------

class FsGrepArgs(BaseModel):
    pattern: str
    path: str = "."
    glob: str = "**/*"
    limit: int = DEFAULT_GREP_LIMIT


def _fs_grep(raw: dict[str, Any], ctx: ToolContext) -> str:
    a = FsGrepArgs.model_validate(raw)
    base = _resolve(ctx.root, a.path)
    try:
        rx = re.compile(a.pattern)
    except re.error as exc:
        raise ToolError(f"bad regex: {exc}") from exc
    files = [base] if base.is_file() else sorted(base.glob(a.glob))
    hits: list[str] = []
    for f in files:
        if not f.is_file() or any(part in _SKIP_DIRS for part in f.parts):
            continue
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for ln, line in enumerate(content.splitlines(), 1):
            if rx.search(line):
                hits.append(f"{f.relative_to(ctx.root)}:{ln}: {line.strip()[:200]}")
                if len(hits) >= a.limit:
                    hits.append(f"… (truncated at {a.limit} matches)")
                    return "\n".join(hits)
    return "\n".join(hits) or "(no matches)"


# --- fs_glob -----------------------------------------------------------------

class FsGlobArgs(BaseModel):
    pattern: str = "**/*"
    path: str = "."
    limit: int = DEFAULT_GLOB_LIMIT


def _fs_glob(raw: dict[str, Any], ctx: ToolContext) -> str:
    a = FsGlobArgs.model_validate(raw)
    base = _resolve(ctx.root, a.path)
    matches: list[str] = []
    for f in sorted(base.glob(a.pattern)):
        if any(part in _SKIP_DIRS for part in f.parts):
            continue
        matches.append(str(f.relative_to(ctx.root)))
        if len(matches) >= a.limit:
            matches.append(f"… (truncated at {a.limit})")
            break
    return "\n".join(matches) or "(no matches)"


# --- bash (allowlisted) ------------------------------------------------------

class BashArgs(BaseModel):
    command: str


def _bash_allowed(cmd: str, allowlist: Iterable[str]) -> bool:
    return any(cmd == p or cmd.startswith(p + " ") for p in allowlist)


def _bash(raw: dict[str, Any], ctx: ToolContext) -> str:
    a = BashArgs.model_validate(raw)
    cmd = a.command.strip()
    if not _bash_allowed(cmd, ctx.bash_allowlist):
        raise ToolError(f"command not in allowlist (not run): {cmd!r}")
    try:
        proc = subprocess.run(
            ["bash", "-c", cmd], cwd=str(ctx.root.resolve()),
            capture_output=True, text=True, timeout=DEFAULT_BASH_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolError(f"command timed out after {DEFAULT_BASH_TIMEOUT}s") from exc
    except OSError as exc:
        raise ToolError(f"failed to run command: {exc}") from exc
    output = ((proc.stdout or "") + (proc.stderr or ""))[:MAX_BASH_OUTPUT]
    return f"exit={proc.returncode}\n{output}".rstrip()


# --- registry ----------------------------------------------------------------

@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args_model: type[BaseModel]
    handler: Callable[[dict[str, Any], ToolContext], str]

    def openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.args_model.model_json_schema(),
            },
        }


TOOLS: dict[str, ToolSpec] = {
    "fs_read": ToolSpec("fs_read", "Read a text file, optionally a line window (offset/limit).",
                        FsReadArgs, _fs_read),
    "fs_write": ToolSpec("fs_write", "Create or overwrite a file with the given content.",
                         FsWriteArgs, _fs_write),
    "fs_edit": ToolSpec("fs_edit", "Replace one UNIQUE exact occurrence of old_string with "
                        "new_string in a file.", FsEditArgs, _fs_edit),
    "fs_grep": ToolSpec("fs_grep", "Search files by regular expression; returns path:line matches.",
                        FsGrepArgs, _fs_grep),
    "fs_glob": ToolSpec("fs_glob", "List files matching a glob pattern.", FsGlobArgs, _fs_glob),
    "bash": ToolSpec("bash", "Run a shell command (only commands allowed by the project "
                     "settings.json allowlist).", BashArgs, _bash),
}


def tool_schemas(allowed: Iterable[str] | None = None) -> list[dict[str, Any]]:
    """OpenAI function-calling tool definitions, optionally filtered to `allowed` names."""
    names = list(TOOLS) if allowed is None else [n for n in TOOLS if n in set(allowed)]
    return [TOOLS[n].openai_schema() for n in names]


def run_tool(name: str, raw_args: dict[str, Any], ctx: ToolContext) -> str:
    spec = TOOLS.get(name)
    if spec is None:
        raise ToolError(f"unknown tool: {name!r}")
    try:
        return spec.handler(raw_args, ctx)
    except ValidationError as exc:
        raise ToolError(f"invalid arguments for {name}: {exc}") from exc


def load_bash_allowlist(root: Path) -> tuple[str, ...]:
    """Parse `Bash(<prefix>:*)` / `Bash(<cmd>)` allow-rules from .claude/settings.json."""
    import json

    settings = root / ".claude" / "settings.json"
    if not settings.is_file():
        return ()
    try:
        data = json.loads(settings.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ()
    rules = data.get("permissions", {}).get("allow", []) if isinstance(data, dict) else []
    prefixes: list[str] = []
    for rule in rules:
        m = re.fullmatch(r"Bash\((.+)\)", str(rule))
        if not m:
            continue
        spec = m.group(1)
        prefixes.append((spec[:-2] if spec.endswith(":*") else spec).strip())
    return tuple(prefixes)

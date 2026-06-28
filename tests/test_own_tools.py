"""Acceptance checks for feature_own_tools (tz.md v2) — the ACI."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.own.tools import (
    ToolContext,
    ToolError,
    load_bash_allowlist,
    run_tool,
    tool_schemas,
)

ALL_TOOLS = {"fs_read", "fs_write", "fs_edit", "fs_grep", "fs_glob", "bash"}


def _ctx(root: Path, bash: tuple[str, ...] = ()) -> ToolContext:
    return ToolContext(root=root, bash_allowlist=bash)


# --- fs_edit: exact + unique -------------------------------------------------

def test_fs_edit_applies_unique_match(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    run_tool("fs_edit", {"path": "a.py", "old_string": "x = 1", "new_string": "x = 42"},
             _ctx(tmp_path))
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "x = 42\ny = 2\n"


def test_fs_edit_rejects_non_unique(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("a\na\n", encoding="utf-8")
    with pytest.raises(ToolError, match="not unique"):
        run_tool("fs_edit", {"path": "a.py", "old_string": "a", "new_string": "b"}, _ctx(tmp_path))


def test_fs_edit_rejects_missing(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("hello\n", encoding="utf-8")
    with pytest.raises(ToolError, match="not found"):
        run_tool("fs_edit", {"path": "a.py", "old_string": "nope", "new_string": "x"},
                 _ctx(tmp_path))


def test_fs_edit_rejects_identical(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("hello\n", encoding="utf-8")
    with pytest.raises(ToolError, match="identical"):
        run_tool("fs_edit", {"path": "a.py", "old_string": "x", "new_string": "x"}, _ctx(tmp_path))


# --- fs_read: range + truncation marker --------------------------------------

def test_fs_read_truncates_with_marker(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("\n".join(f"line{i}" for i in range(1, 51)), encoding="utf-8")
    out = run_tool("fs_read", {"path": "f.txt", "limit": 10}, _ctx(tmp_path))
    assert "line1" in out and "line10" in out and "line11" not in out
    assert "truncated at 10 lines" in out
    assert "\t" in out  # line-numbered output


def test_fs_read_offset_window(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("\n".join(f"line{i}" for i in range(1, 21)), encoding="utf-8")
    out = run_tool("fs_read", {"path": "f.txt", "offset": 5, "limit": 3}, _ctx(tmp_path))
    assert "line5" in out and "line7" in out
    assert "line4" not in out and "line8" not in out


# --- sandbox: path traversal -------------------------------------------------

def test_path_outside_root_is_rejected(tmp_path: Path) -> None:
    (tmp_path.parent / "secret.txt").write_text("nope", encoding="utf-8")
    with pytest.raises(ToolError, match="escapes sandbox"):
        run_tool("fs_read", {"path": "../secret.txt"}, _ctx(tmp_path))
    with pytest.raises(ToolError, match="escapes sandbox"):
        run_tool("fs_read", {"path": "/etc/hostname"}, _ctx(tmp_path))


# --- bash: allowlist ---------------------------------------------------------

def test_bash_rejects_command_outside_allowlist(tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="not in allowlist"):
        run_tool("bash", {"command": "rm -rf /"}, _ctx(tmp_path, bash=("echo",)))


def test_bash_runs_allowlisted_command(tmp_path: Path) -> None:
    out = run_tool("bash", {"command": "echo hello"}, _ctx(tmp_path, bash=("echo",)))
    assert "hello" in out and "exit=0" in out


def test_load_bash_allowlist_parses_settings(tmp_path: Path) -> None:
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text(json.dumps(
        {"permissions": {"allow": ["Bash(pytest:*)", "Bash(git status:*)", "Read(*)"]}}),
        encoding="utf-8")
    allow = load_bash_allowlist(tmp_path)
    assert "pytest" in allow and "git status" in allow
    assert all("Read" not in a for a in allow)


# --- grep / glob -------------------------------------------------------------

def test_fs_grep_and_glob(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("import os\nx = 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("y = 2\n", encoding="utf-8")
    grep = run_tool("fs_grep", {"pattern": "import"}, _ctx(tmp_path))
    assert "a.py:1" in grep
    glob = run_tool("fs_glob", {"pattern": "*.py"}, _ctx(tmp_path))
    assert "a.py" in glob and "b.py" in glob


def test_fs_grep_skips_heavy_dirs(tmp_path: Path) -> None:
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "junk.py").write_text("import os\n", encoding="utf-8")
    (tmp_path / "real.py").write_text("import os\n", encoding="utf-8")
    out = run_tool("fs_grep", {"pattern": "import os"}, _ctx(tmp_path))
    assert "real.py" in out and ".venv" not in out


# --- fs_write ----------------------------------------------------------------

def test_fs_write_creates_nested(tmp_path: Path) -> None:
    run_tool("fs_write", {"path": "sub/new.txt", "content": "hi"}, _ctx(tmp_path))
    assert (tmp_path / "sub" / "new.txt").read_text(encoding="utf-8") == "hi"


# --- schemas + dispatch ------------------------------------------------------

def test_every_tool_has_valid_openai_schema() -> None:
    schemas = tool_schemas()
    assert {s["function"]["name"] for s in schemas} == ALL_TOOLS
    for s in schemas:
        assert s["type"] == "function"
        params = s["function"]["parameters"]
        assert params["type"] == "object" and "properties" in params


def test_tool_schemas_can_be_filtered() -> None:
    names = {s["function"]["name"] for s in tool_schemas(["fs_read", "fs_grep"])}
    assert names == {"fs_read", "fs_grep"}


def test_unknown_tool_and_invalid_args_raise(tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="unknown tool"):
        run_tool("nope", {}, _ctx(tmp_path))
    with pytest.raises(ToolError, match="invalid arguments"):
        run_tool("fs_read", {}, _ctx(tmp_path))  # missing required 'path'

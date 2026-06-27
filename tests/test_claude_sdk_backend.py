"""Acceptance checks for feature_claude_sdk_backend (tz.md).

The offline checks cover the opts assembly and the import/Protocol invariants. The actual
model call is a guarded smoke test (network + ANTHROPIC_API_KEY); a full-graph DONE run on
a real model belongs to the CLI demo (feature_cli_e2e).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import harness
from harness.backends import RoleBackend, RoleContext, RoleResult
from harness.backends.claude_sdk import ClaudeSdkBackend, load_system_prompt
from harness.config import MAX_TURNS_PER_ROLE, ROLES
from harness.graph import GraphDeps, build_graph
from harness.schemas import ReviewerVerdict
from harness.vcs import FakeVcs

REPO_ROOT = Path(harness.__file__).parent.parent
CTX = RoleContext(root=REPO_ROOT, branch="demo")


def test_claude_sdk_is_a_rolebackend() -> None:
    assert isinstance(ClaudeSdkBackend(), RoleBackend)  # runtime_checkable Protocol


def test_graph_accepts_claude_sdk_backend_unchanged(tmp_path: Path) -> None:
    # Same graph, different backend — compiles with no model call (pluggability).
    deps = GraphDeps(backend=ClaudeSdkBackend(), vcs=FakeVcs(), root=tmp_path, base="main")
    assert build_graph(deps) is not None


def test_importing_backend_module_does_not_pull_sdk() -> None:
    code = (
        "import sys, harness.backends.claude_sdk\n"
        "leaked = [m for m in sys.modules if m.split('.')[0] == 'claude_agent_sdk']\n"
        "assert not leaked, leaked\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_opts_include_output_format_for_schema_role() -> None:
    opts = ClaudeSdkBackend()._build_opts("reviewer", CTX)
    assert opts["output_format"] == {
        "type": "json_schema",
        "schema": ReviewerVerdict.model_json_schema(),
    }
    assert opts["model"] == ROLES["reviewer"].model
    assert opts["effort"] == ROLES["reviewer"].effort
    assert opts["permission_mode"] == ROLES["reviewer"].permission_mode
    assert "Bash" not in opts["allowed_tools"]
    assert opts["setting_sources"] == ["project"]
    assert opts["cwd"] == str(REPO_ROOT)
    assert opts["max_turns"] == MAX_TURNS_PER_ROLE
    assert opts["system_prompt"] and not opts["system_prompt"].startswith("---")


def test_opts_omit_output_format_for_schemaless_roles() -> None:
    for role in ("planner", "summarizer"):
        assert "output_format" not in ClaudeSdkBackend()._build_opts(role, CTX)


def test_load_system_prompt_strips_frontmatter() -> None:
    body = load_system_prompt(REPO_ROOT / ".claude" / "agents", "planner")
    assert not body.startswith("---")          # frontmatter stripped
    assert "Role: Planner" in body             # body kept


@pytest.mark.skipif(
    not os.environ.get("RUN_SDK_SMOKE"),
    reason="set RUN_SDK_SMOKE=1 (and ANTHROPIC_API_KEY) to run the live SDK smoke test",
)
async def test_claude_sdk_smoke_single_role() -> None:
    res = await ClaudeSdkBackend().run(
        "summarizer", "Reply with the single word: ok", context=CTX
    )
    assert isinstance(res, RoleResult)
    assert res.text.strip()  # the model actually answered

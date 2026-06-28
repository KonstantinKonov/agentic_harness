"""Acceptance checks for feature_own_backend (tz.md v2): OwnBackend as a drop-in RoleBackend.

Offline: a role-aware mock aggregator (httpx.MockTransport) returns each role's verdict, so
the unchanged graph drives PLAN→DEV→REVIEW→TEST→(FINAL_REVIEW)→DONE on the OwnBackend.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx

from harness.backends import RoleBackend
from harness.backends.own import OwnBackend, own_tool_names
from harness.graph import GraphDeps, run_branch
from harness.own.model_client import ModelClient, ModelConfig
from harness.vcs import FakeVcs

_ROLES = ("planner", "developer", "reviewer", "tester", "summarizer")


def _client(handler: Any) -> ModelClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return ModelClient(ModelConfig(base_url="https://agg.test/v1", api_key="t"), client=http)


def _make_agents(root: Path) -> None:
    d = root / ".claude" / "agents"
    d.mkdir(parents=True)
    for role in _ROLES:
        (d / f"{role}.md").write_text(f"---\nname: {role}\n---\nYou are the {role}.\n",
                                      encoding="utf-8")


def _verdict_handler(request: httpx.Request) -> httpx.Response:
    """Reply with the right structured verdict per role, keyed off the requested schema name."""
    body = json.loads(request.content)
    rf = body.get("response_format")
    name = rf["json_schema"]["name"] if rf else None
    if name == "DevStatus":
        content = json.dumps({"dev_status": "green", "files_touched": ["src/demo.py"],
                              "note": "done"})
    elif name == "ReviewerVerdict":
        content = json.dumps({"verdict": "PASS", "spec_conformance": "full",
                              "issues": [], "rejects": []})
    elif name == "TesterVerdict":
        content = json.dumps({"verdict": "PASS", "attack_surface_covered": [],
                              "failures": [], "tests_added": []})
    else:  # schema-less: planner / summarizer write free text
        content = "# demo\nfree-text artifact"
    return httpx.Response(200, json={
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "cost": 0.001},
    })


# --- RoleBackend conformance -------------------------------------------------

def _accepts_backend(b: RoleBackend) -> None:  # mypy: OwnBackend assignable to RoleBackend
    assert hasattr(b, "run")


def test_ownbackend_is_a_rolebackend() -> None:
    backend = OwnBackend(client=_client(_verdict_handler))
    _accepts_backend(backend)
    assert isinstance(backend, RoleBackend)  # runtime_checkable Protocol


# --- per-role tool filtering -------------------------------------------------

def test_read_only_roles_get_no_write_or_bash_tools() -> None:
    for role in ("reviewer", "summarizer"):
        names = own_tool_names(role)
        assert names == ["fs_read", "fs_grep", "fs_glob"]
        assert not ({"fs_edit", "fs_write", "bash"} & set(names))
    # working roles do get edit/write/bash (the bash tool stays allowlist-gated)
    assert {"fs_edit", "fs_write", "bash"} <= set(own_tool_names("developer"))
    assert "bash" in own_tool_names("tester")
    assert "bash" not in own_tool_names("planner")  # writes plan.md but doesn't run shell


# --- same graph, OwnBackend, happy path to DONE (offline) --------------------

async def test_unchanged_graph_reaches_done_on_mock_model(tmp_path: Path) -> None:
    _make_agents(tmp_path)
    deps = GraphDeps(backend=OwnBackend(client=_client(_verdict_handler)),
                     vcs=FakeVcs(), root=tmp_path, base="main")
    final = await run_branch(deps, "feature_demo")
    assert final.stage == "DONE"
    assert final.escalation_reason is None
    assert final.cost_usd > 0  # mock charged a small cost per role call
    assert (tmp_path / ".GCC" / "branches" / "feature_demo" / "commit.md").is_file()


# --- import hygiene: core must not pull the aggregator HTTP client -----------

def test_core_import_does_not_pull_aggregator_client() -> None:
    code = (
        "import sys\n"
        "import harness, harness.graph, harness.backends, harness.fixtures\n"
        "assert 'harness.own.model_client' not in sys.modules, 'core pulled the model client'\n"
        "import harness.backends.own\n"
        "assert 'harness.own.model_client' in sys.modules, 'OwnBackend should pull it'\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)

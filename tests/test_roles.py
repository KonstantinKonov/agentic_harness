"""Acceptance checks for feature_roles (tz.md)."""
from __future__ import annotations

from pathlib import Path

import harness
from harness.backends import StubBackend
from harness.config import ROLES
from harness.fixtures import happy_path_scripts
from harness.graph import GraphDeps, run_branch
from harness.vcs import FakeVcs

ROLE_NAMES = {"planner", "developer", "reviewer", "tester", "summarizer"}
_AGENTS_DIR = Path(harness.__file__).parent.parent / ".claude" / "agents"


def test_every_role_has_prompt_file_and_registry_entry() -> None:
    for role in ROLE_NAMES:
        assert (_AGENTS_DIR / f"{role}.md").exists(), f"missing prompt for {role}"
        assert role in ROLES, f"{role} missing from config.ROLES"
    assert set(ROLES) == ROLE_NAMES


def test_prompt_files_have_frontmatter() -> None:
    # claude_sdk-compatible frontmatter (name + body); the backend strips it for the prompt.
    for role in ROLE_NAMES:
        text = (_AGENTS_DIR / f"{role}.md").read_text(encoding="utf-8")
        assert text.startswith("---\n"), f"{role}.md missing frontmatter"
        assert f"name: {role}" in text


def test_happy_path_fixtures_cover_full_cycle() -> None:
    scripts = happy_path_scripts()
    assert ROLE_NAMES <= set(scripts)
    assert all(scripts[r] for r in ROLE_NAMES)  # at least one response per role


async def test_planner_runs_before_dev_and_writes_plan(tmp_path: Path) -> None:
    deps = GraphDeps(backend=StubBackend(happy_path_scripts()), vcs=FakeVcs(),
                     root=tmp_path, base="main")
    final = await run_branch(deps, "feature_demo")

    assert final.stage == "DONE"
    assert final.history[0].role == "planner"     # PLAN ran before DEV
    assert final.history[1].role == "developer"
    plan = tmp_path / "plan.md"
    assert plan.exists()                          # planner produced the spec
    assert "Acceptance criteria" in plan.read_text(encoding="utf-8")

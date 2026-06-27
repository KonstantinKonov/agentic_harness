"""Acceptance checks for feature_scaffold (tz.md).

Run: pytest tests/test_scaffold.py
These cover the offline-checkable scaffold criteria: the role registry, the global
limits, and the invariant that importing the package does not pull in the SDK.
"""
from __future__ import annotations

import subprocess
import sys

from harness import config

EXPECTED_ROLES = {"planner", "developer", "reviewer", "tester", "summarizer"}


def test_five_roles_with_required_fields() -> None:
    assert set(config.ROLES) == EXPECTED_ROLES
    for name, tuning in config.ROLES.items():
        assert tuning.model, f"{name}: empty model"
        assert tuning.effort, f"{name}: empty effort"
        assert isinstance(tuning.allowed_tools, tuple) and tuning.allowed_tools, name
        assert "Bash" not in tuning.allowed_tools, f"{name}: Bash must not be auto-approved"
        assert tuning.permission_mode == "dontAsk", name


def test_global_limits() -> None:
    assert config.CAP == 3
    assert config.MAX_TRANSITIONS == 4 * config.CAP + 4
    assert config.MAX_TURNS_PER_ROLE == 40


def test_import_does_not_pull_claude_agent_sdk() -> None:
    # Fresh interpreter so no test-time plugin has imported the SDK first.
    code = (
        "import sys, harness, harness.config\n"
        "leaked = sorted(m for m in sys.modules if m.split('.')[0] == 'claude_agent_sdk')\n"
        "assert not leaked, leaked\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)

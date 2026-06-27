"""Acceptance checks for feature_cli_e2e (tz.md)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from harness.__main__ import make_backend, write_run_record
from harness.backends import RoleBackend
from harness.state import BranchState


def test_make_backend_selects_stub() -> None:
    from harness.backends import StubBackend
    assert isinstance(make_backend("stub"), StubBackend)
    assert isinstance(make_backend("stub"), RoleBackend)


def test_make_backend_rejects_unknown() -> None:
    with pytest.raises(SystemExit):
        make_backend("nope")


def test_importing_cli_does_not_pull_sdk() -> None:
    code = (
        "import sys, harness.__main__\n"
        "assert not [m for m in sys.modules if m.split('.')[0] == 'claude_agent_sdk']\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_write_run_record_notes_trace_when_traced(tmp_path: Path) -> None:
    final = BranchState(branch="demo", stage="DONE")
    write_run_record(tmp_path, "demo", final, traced=True)
    text = (tmp_path / "DEVLOG.md").read_text(encoding="utf-8")
    assert "demo" in text and "Langfuse" in text


def test_write_run_record_plain_when_untraced(tmp_path: Path) -> None:
    write_run_record(tmp_path, "demo", BranchState(branch="demo", stage="DONE"), traced=False)
    text = (tmp_path / "DEVLOG.md").read_text(encoding="utf-8")
    assert "Langfuse" not in text
    assert "-> DONE" in text


def test_cli_stub_reaches_done_and_writes_files(tmp_path: Path) -> None:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("LANGFUSE_")}  # tracing off for a hermetic run
    proc = subprocess.run(
        [sys.executable, "-m", "harness", "demo", "--backend", "stub",
         "--root", str(tmp_path), "--base", "main"],
        capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    assert "-> DONE" in proc.stdout

    bdir = tmp_path / ".GCC" / "branches" / "demo"
    assert (bdir / "commit.md").exists()
    assert (bdir / "metadata.yaml").exists()
    assert (tmp_path / ".GCC" / "main.md").exists()
    assert (tmp_path / "plan.md").exists()        # planner pre-step produced the spec

    meta = yaml.safe_load((bdir / "metadata.yaml").read_text(encoding="utf-8"))
    assert meta["stage"] == "DONE"
    assert meta["transitions"] > 0                # non-empty transition history


@pytest.mark.skipif(
    not os.environ.get("RUN_SDK_SMOKE"),
    reason="set RUN_SDK_SMOKE=1 (and ANTHROPIC_API_KEY) for the live claude_sdk CLI run",
)
def test_cli_claude_sdk_runs_end_to_end(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "harness", "smoke", "--backend", "claude_sdk",
         "--root", str(tmp_path)],
        capture_output=True, text=True,
    )
    # honest smoke: the real-model run completes end-to-end (DONE or ESCALATED), no crash.
    assert proc.returncode in (0, 1), proc.stderr

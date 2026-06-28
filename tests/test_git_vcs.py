"""Acceptance checks for GitVcs (the real VcsPort) — runs git in a throwaway tmp repo.

Offline (no network), but exercises real ``git`` subprocess, so it proves the bootstrap,
``add -A`` staging, and diff pass-through that the agent's ``git diff base...branch`` relies
on.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from harness.backends import StubBackend
from harness.fixtures import happy_path_scripts
from harness.graph import GraphDeps, run_branch
from harness.schemas import ReviewerVerdict
from harness.vcs import GitVcs, VcsPort

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


# --- Protocol conformance ----------------------------------------------------

def _accepts_port(p: VcsPort) -> None:  # mypy: GitVcs assignable to VcsPort
    assert hasattr(p, "commit")


def test_gitvcs_is_a_vcsport(tmp_path: Path) -> None:
    vcs = GitVcs(tmp_path)
    _accepts_port(vcs)
    assert isinstance(vcs, VcsPort)  # runtime_checkable Protocol


# --- bootstrap: init repo + base commit + branch -----------------------------

def test_checkout_branch_bootstraps_repo_and_base(tmp_path: Path) -> None:
    vcs = GitVcs(tmp_path)
    vcs.checkout_branch("feature_x", "main")
    assert (tmp_path / ".git").is_dir()
    # we are on the new branch, forked off base, and a merge-base exists
    assert vcs.diff("main...feature_x") == ""  # no changes yet, but the range is valid
    assert vcs._has_head()  # base carries a root commit


def test_checkout_existing_branch_switches(tmp_path: Path) -> None:
    vcs = GitVcs(tmp_path)
    vcs.checkout_branch("feature_x", "main")
    vcs.commit([], "feat: work")
    vcs.checkout_branch("main", "main")     # switch away
    vcs.checkout_branch("feature_x", "main")  # switch back (must not recreate)
    assert vcs.head()  # branch still has its commit


# --- commit stages ALL new/changed files -------------------------------------

def test_commit_stages_all_files_and_shows_in_diff(tmp_path: Path) -> None:
    vcs = GitVcs(tmp_path)
    vcs.checkout_branch("feature_x", "main")
    _write(tmp_path, "src/demo.py", "x = 1\n")          # a brand-new file (never `git add`ed)
    _write(tmp_path, "tests/test_demo.py", "assert True\n")
    vcs.commit([Path("ignored/by/gitvcs.txt")], "dev: feature_x (A0 / B0)")

    names = vcs.diff("main...feature_x", "--name-only").split()
    assert "src/demo.py" in names and "tests/test_demo.py" in names


def test_head_advances_per_commit_and_matches_rev_parse(tmp_path: Path) -> None:
    vcs = GitVcs(tmp_path)
    vcs.checkout_branch("feature_x", "main")
    before = vcs.head()
    _write(tmp_path, "a.py", "a = 1\n")
    vcs.commit([], "first")
    after = vcs.head()
    assert before != after
    assert len(after) == 40  # full sha


def test_commit_is_noop_when_nothing_changed(tmp_path: Path) -> None:
    vcs = GitVcs(tmp_path)
    vcs.checkout_branch("feature_x", "main")
    _write(tmp_path, "a.py", "a = 1\n")
    vcs.commit([], "first")
    head1 = vcs.head()
    vcs.commit([], "nothing to do")  # clean tree -> no new commit
    assert vcs.head() == head1


# --- incremental diff (variant B: since last review) -------------------------

def test_since_last_review_range_isolates_one_round(tmp_path: Path) -> None:
    vcs = GitVcs(tmp_path)
    vcs.checkout_branch("feature_x", "main")
    _write(tmp_path, "a.py", "a = 1\n")
    vcs.commit([], "round 1")
    review_point = vcs.head()           # what a per-round review would record
    _write(tmp_path, "b.py", "b = 2\n")
    vcs.commit([], "round 2")

    incremental = vcs.diff(f"{review_point}...HEAD", "--name-only").split()
    whole = vcs.diff("main...feature_x", "--name-only").split()
    assert incremental == ["b.py"]               # only round 2's change
    assert set(whole) == {"a.py", "b.py"}        # the whole feature


# --- the unchanged graph drives real git end to end --------------------------

async def test_graph_reaches_done_on_real_git(tmp_path: Path) -> None:
    scripts = happy_path_scripts()
    # FINAL_REVIEW is not skipped on real git (rendered files change between review and test),
    # so the reviewer is asked twice — give it a second PASS.
    scripts["reviewer"] = [ReviewerVerdict(verdict="PASS", spec_conformance="full")] * 2
    deps = GraphDeps(backend=StubBackend(scripts), vcs=GitVcs(tmp_path), root=tmp_path, base="main")
    final = await run_branch(deps, "feature_demo")
    assert final.stage == "DONE"
    log = GitVcs(tmp_path)._run("log", "--oneline").stdout
    assert "dev: feature_demo" in log and "test: feature_demo" in log  # real commits landed

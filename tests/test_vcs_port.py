"""Acceptance checks for feature_vcs_port (tz.md)."""
from __future__ import annotations

import ast
from pathlib import Path

import harness
from harness.vcs import CommitRecord, FakeVcs, VcsPort


# --- mypy: FakeVcs structurally satisfies the VcsPort Protocol ---
def _accepts_port(p: VcsPort) -> None:  # pragma: no cover - typed at call site
    assert hasattr(p, "commit")


_accepts_port(FakeVcs())


def test_fakevcs_is_a_vcsport() -> None:
    assert isinstance(FakeVcs(), VcsPort)  # runtime_checkable Protocol


def test_commit_records_paths_and_message() -> None:
    vcs = FakeVcs()
    paths = [Path(".GCC/branches/demo/commit.md"), Path(".GCC/branches/demo/metadata.yaml")]
    vcs.commit(paths, "dev: demo (A1 / B0)")
    assert len(vcs.commits) == 1
    rec = vcs.commits[0]
    assert isinstance(rec, CommitRecord)
    assert rec.paths == tuple(paths)
    assert rec.message == "dev: demo (A1 / B0)"


def test_head_advances_per_commit() -> None:
    vcs = FakeVcs()
    start = vcs.head()
    vcs.commit([Path("a")], "first")
    after_first = vcs.head()
    vcs.commit([Path("b")], "second")
    after_second = vcs.head()
    assert start != after_first != after_second
    # recorded sha matches what head() reports at the time
    assert vcs.commits[0].sha == after_first
    assert vcs.commits[1].sha == after_second


def test_checkout_branch_is_recorded() -> None:
    vcs = FakeVcs()
    vcs.checkout_branch("feature_auth", "main")
    assert vcs.branch == "feature_auth"
    assert vcs.checkouts == [("feature_auth", "main")]


def test_diff_returns_empty_and_records_query() -> None:
    vcs = FakeVcs()
    out = vcs.diff("fake0001...HEAD", "--name-only")
    assert out == ""
    assert vcs.diff_queries == [("fake0001...HEAD", "--name-only")]


def _imports_subprocess(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name.split(".")[0] == "subprocess" for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] == "subprocess":
                return True
    return False


def test_no_direct_git_subprocess_outside_gitvcs() -> None:
    """The graph must touch git only through VcsPort: no module imports subprocess except
    the (future) GitVcs at harness/vcs/git.py. (AST-based, so prose mentions don't count.)"""
    pkg_root = Path(harness.__file__).parent
    offenders = [
        py.relative_to(pkg_root).as_posix()
        for py in pkg_root.rglob("*.py")
        if not (py.name == "git.py" and py.parent.name == "vcs")
        and _imports_subprocess(py)
    ]
    assert not offenders, f"git/subprocess must go through GitVcs only, found in: {offenders}"

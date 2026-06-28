"""GitVcs — the real ``VcsPort``, backed by ``git`` via subprocess.

This is one of exactly two modules allowed to run a shell (the other is the bash tool); the
``VcsPort`` seam is what keeps every other module git-free. ``commit`` stages **all** new and
changed files (``git add -A``) — the agent edits code in the working tree and the
orchestrator commits the lot — so ``git diff {base}...{branch}`` reflects the real change
(this is what makes the diff the reviewer/tester read meaningful).

``checkout_branch`` bootstraps: it inits the repo if missing and guarantees ``base`` carries
at least one commit, so a merge-base exists and ``{base}...{branch}`` is always valid.
"""
from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence
from pathlib import Path

_AUTHOR_ENV = {
    "GIT_AUTHOR_NAME": "harness",
    "GIT_AUTHOR_EMAIL": "harness@local",
    "GIT_COMMITTER_NAME": "harness",
    "GIT_COMMITTER_EMAIL": "harness@local",
}


class GitError(RuntimeError):
    """A git command failed (non-zero exit); carries stderr for diagnosis."""


class GitVcs:
    """A ``VcsPort`` over a real git repository rooted at ``root``."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        proc = subprocess.run(
            ["git", *args], cwd=str(self.root), capture_output=True, text=True,
            env={**os.environ, **_AUTHOR_ENV},  # pin author/committer; keep PATH etc.
        )
        if check and proc.returncode != 0:
            raise GitError(
                f"git {' '.join(args)} failed ({proc.returncode}): {proc.stderr.strip()}")
        return proc

    def _has_head(self) -> bool:
        return self._run("rev-parse", "--verify", "--quiet", "HEAD", check=False).returncode == 0

    def _branch_exists(self, branch: str) -> bool:
        return self._run(
            "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", check=False
        ).returncode == 0

    def _ensure_repo_and_base(self, base: str) -> None:
        if not (self.root / ".git").is_dir():
            self.root.mkdir(parents=True, exist_ok=True)
            self._run("init", "-b", base)
        if not self._has_head():  # an empty repo has no merge-base; give base a root commit
            self._run("checkout", "-B", base)
            self._run("commit", "--allow-empty", "-m", f"chore: init {base}")

    # --- VcsPort ---------------------------------------------------------------

    def checkout_branch(self, branch: str, base: str) -> None:
        self._ensure_repo_and_base(base)
        if branch == base:
            self._run("checkout", base)
        elif self._branch_exists(branch):
            self._run("checkout", branch)
        else:
            self._run("checkout", "-b", branch, base)

    def head(self) -> str:
        return self._run("rev-parse", "HEAD").stdout.strip()

    def commit(self, paths: Sequence[Path], message: str) -> None:
        # Stage everything (agent code + rendered files); `paths` is advisory. No-op if clean.
        self._run("add", "-A")
        if self._run("diff", "--cached", "--quiet", check=False).returncode == 0:
            return  # nothing staged -> nothing to commit
        self._run("commit", "-m", message)

    def diff(self, *args: str) -> str:
        return self._run("diff", *args).stdout

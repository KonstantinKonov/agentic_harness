"""Version-control port. ``FakeVcs`` records calls with no real git (PoC / unit tests);
``GitVcs`` (``harness.vcs.git``) is the real subprocess implementation and the sole place
git may be run.
"""
from __future__ import annotations

from harness.vcs.base import VcsPort
from harness.vcs.fake import CommitRecord, FakeVcs
from harness.vcs.git import GitError, GitVcs

__all__ = ["VcsPort", "FakeVcs", "CommitRecord", "GitVcs", "GitError"]

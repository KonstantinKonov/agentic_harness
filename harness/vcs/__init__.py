"""Version-control port. Only the no-git FakeVcs is shipped in the PoC; GitVcs (subprocess)
arrives in a later milestone as ``harness.vcs.git`` and is the sole place git may be run.
"""
from __future__ import annotations

from harness.vcs.base import VcsPort
from harness.vcs.fake import CommitRecord, FakeVcs

__all__ = ["VcsPort", "FakeVcs", "CommitRecord"]

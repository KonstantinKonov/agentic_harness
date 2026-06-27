"""The VCS seam: how the deterministic graph touches git, behind one interface.

The real implementation is ``GitVcs`` (subprocess, next milestone); the PoC uses
``FakeVcs``. Keeping every git call behind this port is what lets the graph stay
testable with no git at all — and is the invariant that forbids direct ``subprocess``
git calls anywhere else (see test_vcs_port).

The surface covers exactly what the orchestrator's own git use needs (port of the old
``_git`` / ``_commit`` / ``_head`` plus the name-only diff behind ``_can_skip_final``).
"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class VcsPort(Protocol):
    def checkout_branch(self, branch: str, base: str) -> None:
        """Create ``branch`` off ``base`` if missing, else switch to it (bootstrap)."""

    def head(self) -> str:
        """Current HEAD revision."""

    def commit(self, paths: Sequence[Path], message: str) -> None:
        """Stage ``paths`` and commit them with ``message`` (no-op if nothing changed)."""

    def diff(self, *args: str) -> str:
        """Pass-through to ``git diff <args>``; e.g. ``diff("a...HEAD", "--name-only")``."""

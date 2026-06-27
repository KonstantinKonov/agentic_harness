"""FakeVcs — an in-memory VcsPort that records calls and performs no real git.

It exists so the graph can be driven with zero git side effects: every call is logged for
assertions. The store is the single writer of files (commit.md/metadata.yaml/main.md), so
FakeVcs only records the commit *intent* (paths + message) — it writes nothing itself.
``head()`` advances on each commit, so ``last_review_commit`` semantics stay observable.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CommitRecord:
    paths: tuple[Path, ...]
    message: str
    sha: str


class FakeVcs:
    def __init__(self, initial_head: str = "0000000") -> None:
        self.branch: str | None = None
        self.checkouts: list[tuple[str, str]] = []      # (branch, base) per call
        self.commits: list[CommitRecord] = []
        self.diff_queries: list[tuple[str, ...]] = []
        self._head = initial_head

    def checkout_branch(self, branch: str, base: str) -> None:
        self.branch = branch
        self.checkouts.append((branch, base))

    def head(self) -> str:
        return self._head

    def commit(self, paths: Sequence[Path], message: str) -> None:
        self._head = f"fake{len(self.commits) + 1:04d}"
        self.commits.append(CommitRecord(tuple(paths), message, self._head))

    def diff(self, *args: str) -> str:
        self.diff_queries.append(args)
        return ""  # no real history -> nothing changed

"""CLI: drive one feature branch to DONE or ESCALATED.

    python -m harness <branch> [--base main] [--root .]
                               [--backend stub|claude_sdk|own] [--checkpointer memory|postgres]

Thin wrapper: argparse + asyncio.run around graph.run_branch. No business logic here.
The default backend is ``stub`` (deterministic, no network) so a bare run is a safe demo;
``--backend claude_sdk`` drives the real model. Tracing exports to Langfuse if its env keys
are set (otherwise a no-op).
"""
from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from harness.backends import RoleBackend, StubBackend
from harness.fixtures import happy_path_scripts
from harness.graph import GraphDeps, run_branch
from harness.observability import configure_tracing
from harness.state import BranchState
from harness.store import append_devlog
from harness.vcs import FakeVcs


def make_backend(name: str) -> RoleBackend:
    if name == "stub":
        return StubBackend(happy_path_scripts())
    if name == "claude_sdk":
        from harness.backends.claude_sdk import ClaudeSdkBackend  # lazy: don't pull the SDK
        return ClaudeSdkBackend()
    if name == "own":
        from harness.backends.own import OwnBackend  # lazy: don't pull the aggregator client
        return OwnBackend()
    raise SystemExit(f"unknown backend: {name!r}")


def write_run_record(root: Path, branch: str, final: BranchState, *, traced: bool) -> None:
    line = f"- run `{branch}` -> {final.stage} (cost ${final.cost_usd:.4f})"
    if traced:
        line = f"{line}; trace in Langfuse {os.environ.get('LANGFUSE_HOST', '')}".rstrip()
    append_devlog(root, line)


async def _run(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    deps = GraphDeps(backend=make_backend(args.backend), vcs=FakeVcs(),
                     root=root, base=args.base)
    traced = configure_tracing()

    if args.checkpointer == "postgres":
        from harness.checkpointer import open_checkpointer
        with open_checkpointer() as cp:
            final = await run_branch(deps, args.branch, checkpointer=cp, thread_id=args.branch)
    else:
        final = await run_branch(deps, args.branch)

    write_run_record(root, args.branch, final, traced=traced)
    print(f"[harness] {args.branch} -> {final.stage}  (${final.cost_usd:.4f})")
    if final.escalation_reason:
        print(f"[harness] escalation_reason: {final.escalation_reason}")
    return 0 if final.stage == "DONE" else 1


def main() -> None:
    ap = argparse.ArgumentParser(prog="harness", description=__doc__)
    ap.add_argument("branch", help="feature branch, e.g. feature_auth")
    ap.add_argument("--base", default="main", help="diff/branch base (default: main)")
    ap.add_argument("--root", default=".", help="repo root (default: .)")
    ap.add_argument("--backend", choices=["stub", "claude_sdk", "own"], default="stub")
    ap.add_argument("--checkpointer", choices=["memory", "postgres"], default="memory")
    raise SystemExit(asyncio.run(_run(ap.parse_args())))


if __name__ == "__main__":
    main()

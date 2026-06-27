"""
CLI entry point: drive one feature branch to DONE or ESCALATED.

    python -m orchestrator feature_auth
    python -m orchestrator feature_auth --base master --root /path/to/repo
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from .machine import run


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="orchestrator",
        description="Deterministic Python orchestrator; runs dev/reviewer/tester roles via the Claude Agent SDK.",
    )
    ap.add_argument("branch", help="feature branch, e.g. feature_auth")
    ap.add_argument("--base", default="main", help="base branch to diff/branch from (default: main)")
    ap.add_argument("--root", default=".", help="repo root (default: current directory)")
    args = ap.parse_args()
    asyncio.run(run(Path(args.root).resolve(), args.branch, args.base))


if __name__ == "__main__":
    main()

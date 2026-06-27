"""Rendered views of BranchState + the single-writer that puts them on disk.

The orchestrator is the ONLY writer under ``.GCC/``. These renderers are pure functions of
state, so the files are *rendered* (never appended) and therefore never go stale. Imports
are pydantic/yaml only — no langgraph, no SDK.

``render_main`` takes the branch states (the caller gathers them from the checkpointer);
it no longer scans ``state.json`` files, since the machine state now lives in Postgres.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import yaml

from harness.state import BranchState


@dataclass(frozen=True)
class BranchPaths:
    root: Path
    branch: str

    @property
    def gcc(self) -> Path:
        return self.root / ".GCC"

    @property
    def bdir(self) -> Path:
        return self.gcc / "branches" / self.branch

    @property
    def commit(self) -> Path:
        return self.bdir / "commit.md"

    @property
    def metadata(self) -> Path:
        return self.bdir / "metadata.yaml"

    @property
    def main(self) -> Path:
        return self.gcc / "main.md"


# --- renderers: state -> contract-shaped text --------------------------------

def render_commit(st: BranchState) -> str:
    """Render commit.md in contract order. Dead ends come before Done because commit.md is
    the only memory channel a fresh role sees between rounds."""
    out: list[str] = [
        f"# Feature: {st.branch}   (branch: {st.branch})",
        f"Spec: plan.md#{st.branch}",
        f"Stage: {st.stage}",
        f"Round: {st.round_label}",
        "",
        "## Open issues (current, with source and status)",
    ]
    if st.open_issues:
        for iid, iss in st.open_issues.items():
            where = f"  @{iss.where}" if iss.where else ""
            out.append(
                f"- [{iid}] ({iss.source}) {iss.what}{where}"
                f"   severity: {iss.severity}  status: {iss.status}"
            )
    else:
        out.append("- none")

    out += ["", "## Rejected approaches — DO NOT RETRY"]
    if st.rejected:
        out += [f"- {r.approach}   ({r.round})" for r in st.rejected]
    else:
        out.append("- none")

    out += ["", "## Constraints discovered (invariants)"]
    out += [f"- {c}" for c in st.constraints] or ["- none"]

    out += ["", "## Conflicts (reviewer ↔ tester) — BLOCKER, needs human"]
    out += [f"- {c}" for c in st.conflicts] or ["- none"]

    out += ["", "## Done (current state — least important section)"]
    tail: list[str] = []
    if st.resolved_sigs:
        tail.append(f"- resolved so far: {len(st.resolved_sigs)} issue(s)")
    if st.last_dev_note:
        tail.append(f"- last dev: {st.last_dev_note}")
    out += tail or ["- (nothing yet)"]
    return "\n".join(out) + "\n"


def render_metadata(st: BranchState) -> str:
    return yaml.safe_dump(
        {
            "git_branch": st.branch,
            "stage": st.stage,
            "loop_a_rounds": st.loop_a_rounds,
            "loop_b_rounds": st.loop_b_rounds,
            "open_issues": len(st.open_issues),
            "last_reviewer_verdict": st.last_reviewer_verdict,
            "last_tester_verdict": st.last_tester_verdict,
            "escalation_reason": st.escalation_reason,
            "transitions": len(st.history),  # non-empty transition history
        },
        sort_keys=False,
        allow_unicode=True,
    )


def render_main(states: Iterable[BranchState]) -> str:
    """Cross-branch dashboard. Sorted by branch so output is order-independent."""
    header = (
        "# Orchestrator dashboard (rendered from checkpointer state — do not edit by hand)\n\n"
        "| branch | stage | A | B | open | reviewer | tester | escalation |\n"
        "|---|---|---|---|---|---|---|---|\n"
    )
    rows = [
        "| {branch} | {stage} | {a} | {b} | {open} | {rv} | {tv} | {esc} |".format(
            branch=st.branch,
            stage=st.stage,
            a=st.loop_a_rounds,
            b=st.loop_b_rounds,
            open=len(st.open_issues),
            rv=st.last_reviewer_verdict or "-",
            tv=st.last_tester_verdict or "-",
            esc=st.escalation_reason or "-",
        )
        for st in sorted(states, key=lambda s: s.branch)
    ]
    return header + "\n".join(rows) + "\n"


# --- single-writer: rendered text -> disk ------------------------------------

def write_branch_files(root: Path, st: BranchState) -> None:
    """Overwrite (never append) this branch's commit.md + metadata.yaml."""
    p = BranchPaths(root, st.branch)
    p.bdir.mkdir(parents=True, exist_ok=True)
    p.commit.write_text(render_commit(st), encoding="utf-8")
    p.metadata.write_text(render_metadata(st), encoding="utf-8")


def write_main(root: Path, states: Iterable[BranchState]) -> None:
    """Overwrite the cross-branch dashboard."""
    gcc = root / ".GCC"
    gcc.mkdir(parents=True, exist_ok=True)
    (gcc / "main.md").write_text(render_main(states), encoding="utf-8")


def append_devlog(root: Path, entry: str) -> None:
    """Append one distilled entry to DEVLOG.md (the only append-mode file; written at DONE)."""
    with (root / "DEVLOG.md").open("a", encoding="utf-8") as f:
        f.write("\n" + entry.strip() + "\n")


def write_plan(root: Path, text: str) -> None:
    """Overwrite plan.md with the planner's spec (the PLAN pre-step output)."""
    (root / "plan.md").write_text(text.strip() + "\n", encoding="utf-8")

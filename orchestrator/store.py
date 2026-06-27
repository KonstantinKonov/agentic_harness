"""
Structured branch state. The orchestrator's single source of truth and the
files it renders from that state.

The orchestrator is the ONLY writer of everything under `.GCC/`. Worker roles touch
only real source/test files. Bookkeeping lives here as data: `state.json`
is the truth; `commit.md` / `metadata.yaml` / `main.md` are rendered views of it.

Issue ids (R-/T-) are assigned by the orchestrator via `r_counter` / `t_counter` —
the model never supplies them. They are display handles in commit.md; cross-round
identity (for oscillation) is matched on a normalized signature, not on a model id.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

STAGES = ("DEV", "REVIEW", "TEST", "FINAL_REVIEW", "DONE", "ESCALATED")


@dataclass
class BranchState:
    branch: str
    stage: str = "DEV"
    loop_a_rounds: int = 0                        # developer <-> reviewer iterations
    loop_b_rounds: int = 0                        # developer <-> tester iterations
    open_issues: dict = field(default_factory=dict)   # id -> {id, source, what, where, severity, status}
    rejected: list = field(default_factory=list)      # {approach, round}
    resolved_sigs: list = field(default_factory=list) # signatures of issues a PASS has cleared (oscillation)
    constraints: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)     # non-empty -> ESCALATE
    r_counter: int = 0                            # last assigned reviewer issue number
    t_counter: int = 0                            # last assigned tester failure number
    last_reviewer_verdict: str | None = None
    last_tester_verdict: str | None = None
    last_review_commit: str = ""                  # branch HEAD when the reviewer last ran (FINAL_REVIEW skip)
    escalation_reason: str | None = None
    last_dev_note: str = ""
    cost_usd: float = 0.0
    history: list = field(default_factory=list)   # {role, stage, verdict, cost_usd}

    @property
    def round_label(self) -> str:
        return f"A{self.loop_a_rounds} / B{self.loop_b_rounds}"


class Store:
    """Path bookkeeping + the single-writer save (state.json + rendered views)."""

    def __init__(self, root: Path, branch: str):
        self.root = root
        self.branch = branch
        self.gcc = root / ".GCC"
        self.bdir = self.gcc / "branches" / branch

    @property
    def state_path(self) -> Path:
        return self.bdir / "state.json"

    @property
    def log_path(self) -> Path:
        return self.bdir / "log.md"

    @property
    def main_path(self) -> Path:
        return self.gcc / "main.md"

    @property
    def devlog_path(self) -> Path:
        return self.root / "DEVLOG.md"

    def exists(self) -> bool:
        return self.state_path.exists()

    def load(self) -> BranchState:
        return BranchState(**json.loads(self.state_path.read_text(encoding="utf-8")))

    def save(self, st: BranchState) -> None:
        self.bdir.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(asdict(st), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        (self.bdir / "commit.md").write_text(render_commit(st), encoding="utf-8")
        (self.bdir / "metadata.yaml").write_text(render_metadata(st), encoding="utf-8")
        self.main_path.write_text(render_main(self.gcc), encoding="utf-8")

    def append_log(self, role: str, stage: str, text: str) -> None:
        self.bdir.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(f"\n\n===== {stage} :: {role} =====\n{(text or '').strip()}\n")

    def write_devlog(self, entry: str) -> None:
        with self.devlog_path.open("a", encoding="utf-8") as f:
            f.write("\n" + entry.strip() + "\n")


# --- renderers: state -> contract-shaped files -------------------------------

def render_commit(st: BranchState) -> str:
    """
    Render commit.md in the contract order. Dead ends come before Done because
    commit.md is the only memory channel a fresh role sees between rounds.
    """
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
            where = f"  @{iss['where']}" if iss.get("where") else ""
            out.append(
                f"- [{iid}] ({iss['source']}) {iss['what']}{where}"
                f"   severity: {iss['severity']}  status: {iss['status']}"
            )
    else:
        out.append("- none")

    out += ["", "## Rejected approaches — DO NOT RETRY"]
    if st.rejected:
        out += [f"- {r['approach']}   ({r.get('round', '')})" for r in st.rejected]
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
        },
        sort_keys=False,
        allow_unicode=True,
    )


def render_main(gcc: Path) -> str:
    """
    Render the cross-branch dashboard by scanning every branch's state.json.
    Rendering (vs appending) means rows never go stale.
    """
    header = (
        "# Orchestrator dashboard (rendered from state.json — do not edit by hand)\n\n"
        "| branch | stage | A | B | open | reviewer | tester | escalation |\n"
        "|---|---|---|---|---|---|---|---|\n"
    )
    rows: list[str] = []
    bdir = gcc / "branches"
    if bdir.exists():
        for d in sorted(bdir.iterdir()):
            sp = d / "state.json"
            if not sp.exists():
                continue
            st = json.loads(sp.read_text(encoding="utf-8"))
            rows.append(
                "| {branch} | {stage} | {a} | {b} | {open} | {rv} | {tv} | {esc} |".format(
                    branch=st["branch"],
                    stage=st["stage"],
                    a=st["loop_a_rounds"],
                    b=st["loop_b_rounds"],
                    open=len(st["open_issues"]),
                    rv=st.get("last_reviewer_verdict") or "-",
                    tv=st.get("last_tester_verdict") or "-",
                    esc=st.get("escalation_reason") or "-",
                )
            )
    return header + "\n".join(rows) + "\n"

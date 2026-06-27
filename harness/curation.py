"""Verdict curation — how a validated verdict mutates BranchState. Pure & deterministic.

Port of the old machine.py curation (1:1 behavior), but typed: it takes the pydantic
verdict models instead of raw dicts. No git, no model, no I/O.

Open issues are replaced PER SOURCE each round — the latest verdict IS the current set for
that source — and oscillation is detected on a normalized signature of resolved issues.
Issue ids (R-/T-) are assigned here via the monotonic counters; the model never supplies
them. ``final`` (final review vs normal review) is intentionally NOT a curation concern —
it only changes the stage transition, which is the graph's job.
"""
from __future__ import annotations

from typing import Literal

from harness.schemas import ReviewerVerdict, Severity, TesterVerdict
from harness.state import BranchState, Issue, Rejected

Source = Literal["reviewer", "tester"]


def signature(source: str, what: str, where: str) -> str:
    """Normalized cross-round identity for an issue. Best-effort (text varies between
    rounds); the hard anti-loop guarantee is the round cap, this only refines it."""
    return f"{source}:{(where or '').strip().lower()}:{(what or '').strip().lower()[:60]}"


def drop_source(st: BranchState, source: Source) -> None:
    st.open_issues = {i: iss for i, iss in st.open_issues.items() if iss.source != source}


def add_issue(st: BranchState, source: Source, what: str, where: str, severity: Severity) -> None:
    if signature(source, what, where) in st.resolved_sigs:
        st.escalation_reason = "oscillation"
        st.conflicts.append(f"{source} re-raised a previously resolved issue: {what[:80]}")
    if source == "reviewer":
        st.r_counter += 1
        iid = f"R-{st.r_counter}"
    else:
        st.t_counter += 1
        iid = f"T-{st.t_counter}"
    st.open_issues[iid] = Issue(
        id=iid, source=source, what=what, where=where, severity=severity, status="open"
    )


def apply_reviewer(st: BranchState, v: ReviewerVerdict) -> str:
    st.last_reviewer_verdict = v.verdict
    for rj in v.rejects:
        st.rejected.append(Rejected(approach=str(rj), round=f"A{st.loop_a_rounds}"))

    if v.verdict == "PASS":
        for open_iss in [i for i in st.open_issues.values() if i.source == "reviewer"]:
            st.resolved_sigs.append(signature("reviewer", open_iss.what, open_iss.where))
        drop_source(st, "reviewer")
        return "PASS"

    drop_source(st, "reviewer")  # latest verdict's issues replace the prior reviewer set
    for ri in v.issues:
        add_issue(st, "reviewer", ri.what, ri.where, ri.severity)
    st.loop_a_rounds += 1
    return "CHANGES_REQUESTED"


def apply_tester(st: BranchState, v: TesterVerdict) -> str:
    st.last_tester_verdict = v.verdict

    if v.verdict == "PASS":
        for iss in [i for i in st.open_issues.values() if i.source == "tester"]:
            st.resolved_sigs.append(signature("tester", iss.what, iss.where))
        drop_source(st, "tester")
        return "PASS"

    drop_source(st, "tester")
    for f in v.failures:
        add_issue(st, "tester", f.what, f.repro, f.severity)  # tester maps repro -> where
    st.loop_b_rounds += 1
    return "FAILED"

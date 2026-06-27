"""Acceptance checks for the verdict-curation sub-step of feature_graph (tz.md)."""
from __future__ import annotations

from harness.curation import (
    add_issue,
    apply_reviewer,
    apply_tester,
    drop_source,
    signature,
)
from harness.schemas import ReviewerVerdict, ReviewIssue, TesterFailure, TesterVerdict
from harness.state import BranchState, Issue


def _st() -> BranchState:
    return BranchState(branch="feature_auth")


# --- signature ---------------------------------------------------------------

def test_signature_is_normalized() -> None:
    a = signature("reviewer", "  Missing Validation  ", " Auth.py:10 ")
    b = signature("reviewer", "missing validation", "auth.py:10")
    assert a == b
    # what is truncated to 60 chars
    assert signature("tester", "x" * 200, "") == signature("tester", "x" * 60, "")


# --- reviewer ----------------------------------------------------------------

def test_reviewer_changes_requested_adds_ids_and_advances_loop() -> None:
    st = _st()
    v = ReviewerVerdict(
        verdict="CHANGES_REQUESTED",
        spec_conformance="partial",
        issues=[ReviewIssue(severity="major", what="no input validation", where="api.py:8")],
        rejects=["global mutable cache"],
    )
    outcome = apply_reviewer(st, v)
    assert outcome == "CHANGES_REQUESTED"
    assert st.last_reviewer_verdict == "CHANGES_REQUESTED"
    assert st.loop_a_rounds == 1
    assert list(st.open_issues) == ["R-1"]
    assert st.open_issues["R-1"].source == "reviewer"
    assert st.rejected[0].approach == "global mutable cache"
    assert st.rejected[0].round == "A0"  # recorded at the round it was raised


def test_reviewer_pass_resolves_and_clears_its_issues() -> None:
    st = _st()
    apply_reviewer(st, ReviewerVerdict(
        verdict="CHANGES_REQUESTED", spec_conformance="partial",
        issues=[ReviewIssue(what="bug", where="x.py:1")]))
    assert "R-1" in st.open_issues
    outcome = apply_reviewer(st, ReviewerVerdict(verdict="PASS", spec_conformance="full"))
    assert outcome == "PASS"
    assert st.open_issues == {}                       # reviewer issues cleared
    assert len(st.resolved_sigs) == 1                 # signature remembered for oscillation


def test_ids_are_monotonic_across_rounds() -> None:
    st = _st()
    apply_reviewer(st, ReviewerVerdict(
        verdict="CHANGES_REQUESTED", spec_conformance="partial",
        issues=[ReviewIssue(what="a", where="1")]))            # R-1
    apply_reviewer(st, ReviewerVerdict(
        verdict="CHANGES_REQUESTED", spec_conformance="partial",
        issues=[ReviewIssue(what="b", where="2")]))            # R-1 dropped, new R-2
    assert list(st.open_issues) == ["R-2"]
    assert st.r_counter == 2
    assert st.loop_a_rounds == 2


# --- tester ------------------------------------------------------------------

def test_tester_failed_maps_repro_to_where_and_advances_loop_b() -> None:
    st = _st()
    outcome = apply_tester(st, TesterVerdict(
        verdict="FAILED",
        failures=[TesterFailure(severity="blocker", what="auth bypass", repro="POST /login …")],
    ))
    assert outcome == "FAILED"
    assert st.last_tester_verdict == "FAILED"
    assert st.loop_b_rounds == 1
    assert list(st.open_issues) == ["T-1"]
    assert st.open_issues["T-1"].where == "POST /login …"   # repro -> where


def test_tester_pass_resolves_only_tester_issues() -> None:
    st = _st()
    # one reviewer issue + one tester issue open
    add_issue(st, "reviewer", "spec gap", "a.py:1", "major")
    add_issue(st, "tester", "crash", "b.py:2", "major")
    outcome = apply_tester(st, TesterVerdict(verdict="PASS"))
    assert outcome == "PASS"
    assert list(st.open_issues) == ["R-1"]                  # reviewer issue survives
    assert len(st.resolved_sigs) == 1


# --- replace-per-source + drop_source ---------------------------------------

def test_drop_source_is_per_source() -> None:
    st = _st()
    add_issue(st, "reviewer", "r", "1", "major")
    add_issue(st, "tester", "t", "2", "major")
    drop_source(st, "reviewer")
    assert [i.source for i in st.open_issues.values()] == ["tester"]


def test_new_reviewer_verdict_replaces_reviewer_issues_not_tester() -> None:
    st = _st()
    add_issue(st, "tester", "t-issue", "t.py:1", "major")    # T-1 stays through reviewer rounds
    apply_reviewer(st, ReviewerVerdict(
        verdict="CHANGES_REQUESTED", spec_conformance="partial",
        issues=[ReviewIssue(what="r1", where="r.py:1")]))
    apply_reviewer(st, ReviewerVerdict(
        verdict="CHANGES_REQUESTED", spec_conformance="partial",
        issues=[ReviewIssue(what="r2", where="r.py:2")]))
    sources = sorted(i.source for i in st.open_issues.values())
    assert sources == ["reviewer", "tester"]                # exactly one of each
    assert any(i.what == "r2" for i in st.open_issues.values())
    assert all(i.what != "r1" for i in st.open_issues.values())


# --- oscillation -------------------------------------------------------------

def test_oscillation_flags_escalation_and_conflict() -> None:
    st = _st()
    # reviewer raises, then PASS (resolves), then re-raises the SAME issue -> oscillation
    apply_reviewer(st, ReviewerVerdict(
        verdict="CHANGES_REQUESTED", spec_conformance="partial",
        issues=[ReviewIssue(what="same bug", where="x.py:1")]))
    apply_reviewer(st, ReviewerVerdict(verdict="PASS", spec_conformance="full"))
    apply_reviewer(st, ReviewerVerdict(
        verdict="CHANGES_REQUESTED", spec_conformance="partial",
        issues=[ReviewIssue(what="same bug", where="x.py:1")]))
    assert st.escalation_reason == "oscillation"
    assert any("re-raised" in c for c in st.conflicts)


def test_add_issue_is_pure_no_side_channels() -> None:
    st = _st()
    add_issue(st, "tester", "x", "y", "minor")
    assert isinstance(st.open_issues["T-1"], Issue)
    assert st.t_counter == 1 and st.r_counter == 0

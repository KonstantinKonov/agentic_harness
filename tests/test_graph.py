"""Acceptance checks for the graph sub-step of feature_graph (tz.md)."""
from __future__ import annotations

from pathlib import Path

from harness.backends import RoleContext, RoleResult, StubBackend
from harness.config import CAP, MAX_TRANSITIONS
from harness.fixtures import happy_path_scripts
from harness.graph import (
    GraphDeps,
    GraphState,
    Nodes,
    _apply_cap,
    can_skip_final,
    route,
    run_branch,
)
from harness.schemas import DevStatus, ReviewerVerdict
from harness.state import BranchState
from harness.vcs import FakeVcs


class _Fixed:
    """A RoleBackend returning one fixed RoleResult for any role (for node unit tests)."""

    def __init__(self, structured: dict[str, object] | None, text: str = "", cost: float = 0.0):
        self._structured = structured
        self._text = text
        self._cost = cost

    async def run(self, role: str, task: str, *, context: RoleContext) -> RoleResult:
        return RoleResult(structured=self._structured, text=self._text,
                          subtype="success", cost_usd=self._cost)


def _gs(st: BranchState, transitions: int = 0) -> GraphState:
    return {"branch_state": st, "transitions": transitions}


def _deps(root: Path, backend: object) -> GraphDeps:
    return GraphDeps(backend=backend, vcs=FakeVcs(), root=root, base="main")  # type: ignore[arg-type]


# --- router: pure, no model call --------------------------------------------

def test_route_maps_stage_to_node() -> None:
    for stage, node in [("DEV", "dev"), ("REVIEW", "review"), ("TEST", "test"),
                        ("FINAL_REVIEW", "final_review"), ("DONE", "summary")]:
        assert route(_gs(BranchState(branch="b", stage=stage))) == node  # type: ignore[arg-type]


def test_route_escalates_on_reason() -> None:
    st = BranchState(branch="b", stage="DEV", escalation_reason="developer_blocked")
    assert route(_gs(st)) == "escalated"


def test_route_escalates_on_transition_budget() -> None:
    assert route(_gs(BranchState(branch="b", stage="DEV"), MAX_TRANSITIONS)) == "escalated"


# --- cap / skip-final --------------------------------------------------------

def test_apply_cap_sets_cap_exceeded() -> None:
    st = BranchState(branch="b", stage="DEV", loop_a_rounds=CAP)
    _apply_cap(st)
    assert st.escalation_reason == "cap_exceeded"


def test_apply_cap_noop_when_done() -> None:
    st = BranchState(branch="b", stage="DONE", loop_a_rounds=CAP)
    _apply_cap(st)
    assert st.escalation_reason is None


def test_can_skip_final_true_when_clean(tmp_path: Path) -> None:
    # loop_a == 0 and FakeVcs.diff() reports nothing changed -> skip
    st = BranchState(branch="b", loop_a_rounds=0, last_review_commit="abc123")
    assert can_skip_final(FakeVcs(), st) is True


def test_can_skip_final_false_when_reviewer_bounced() -> None:
    st = BranchState(branch="b", loop_a_rounds=1, last_review_commit="abc123")
    assert can_skip_final(FakeVcs(), st) is False


# --- node-level escalations --------------------------------------------------

async def test_dev_blocked_escalates(tmp_path: Path) -> None:
    deps = _deps(tmp_path, _Fixed(DevStatus(dev_status="blocked").model_dump()))
    out = await Nodes(deps).dev(_gs(BranchState(branch="b", stage="DEV")))
    assert out["branch_state"].escalation_reason == "developer_blocked"
    assert route(out) == "escalated"


async def test_dev_no_verdict_escalates(tmp_path: Path) -> None:
    deps = _deps(tmp_path, _Fixed(None))  # no structured output
    out = await Nodes(deps).dev(_gs(BranchState(branch="b", stage="DEV")))
    assert out["branch_state"].escalation_reason == "no_verdict"


async def test_escalated_node_stamps_max_transitions(tmp_path: Path) -> None:
    deps = _deps(tmp_path, _Fixed(None))
    out = await Nodes(deps).escalated(_gs(BranchState(branch="b"), MAX_TRANSITIONS))
    assert out["branch_state"].escalation_reason == "max_transitions"
    assert out["branch_state"].stage == "ESCALATED"


# --- full runs ---------------------------------------------------------------

async def test_happy_path_reaches_done(tmp_path: Path) -> None:
    vcs = FakeVcs()
    deps = GraphDeps(backend=StubBackend(happy_path_scripts()), vcs=vcs,
                     root=tmp_path, base="main")
    final = await run_branch(deps, "feature_auth")

    assert final.stage == "DONE"
    assert [h.role for h in final.history] == \
        ["planner", "developer", "reviewer", "tester", "summarizer"]
    assert final.last_reviewer_verdict == "PASS"   # FINAL_REVIEW skipped -> PASS stamped
    assert final.last_tester_verdict == "PASS"
    assert final.escalation_reason is None

    msgs = [c.message for c in vcs.commits]
    assert any(m.startswith("dev:") for m in msgs)
    assert any(m.startswith("test:") for m in msgs)
    assert vcs.checkouts == [("feature_auth", "main")]

    assert (tmp_path / ".GCC" / "branches" / "feature_auth" / "commit.md").exists()
    assert (tmp_path / ".GCC" / "main.md").exists()
    assert (tmp_path / "DEVLOG.md").exists()


async def test_never_converges_escalates_via_cap_and_terminates(tmp_path: Path) -> None:
    backend = StubBackend({
        "planner": ["plan"],
        "developer": [DevStatus(dev_status="green")] * 6,
        "reviewer": [ReviewerVerdict(verdict="CHANGES_REQUESTED", spec_conformance="partial")] * 6,
    })
    deps = GraphDeps(backend=backend, vcs=FakeVcs(), root=tmp_path, base="main")
    final = await run_branch(deps, "feature_x")

    assert final.stage == "ESCALATED"
    assert final.escalation_reason == "cap_exceeded"
    assert final.loop_a_rounds == CAP

"""The deterministic orchestrator as a LangGraph StateGraph.

Control flow is a state machine, not an agent: every transition is a pure function of
BranchState (no model tokens spent on routing). Stage nodes call a role through the
``RoleBackend`` and curate the verdict into state (see curation.py); the conditional edge
``route`` picks the next node purely from ``stage`` / ``escalation_reason`` / the transition
budget. git goes through ``VcsPort``; the rendered files are written by the store
(single-writer).

    PLAN ─▶ DEV ─(green)▶ REVIEW ─(PASS)▶ TEST ─(PASS)▶ FINAL_REVIEW ─(PASS/skip)▶ DONE
                 │                │ (CHANGES)      │ (FAILED)            │ (CHANGES)
                 └─(blocked)▶ ESC └─────▶ DEV ◀────┘──────────────▶ DEV ◀┘
    cap / oscillation / no_verdict / max_transitions ─▶ ESCALATED
"""
from __future__ import annotations

from collections.abc import Hashable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

from harness.backends import RoleBackend, RoleContext
from harness.config import CAP, MAX_TRANSITIONS
from harness.curation import apply_reviewer, apply_tester
from harness.schemas import DevStatus, ReviewerVerdict, TesterVerdict
from harness.state import BranchState, HistoryEntry
from harness.store import BranchPaths, append_devlog, write_branch_files, write_main
from harness.tasks import dev_task, plan_task, review_task, summary_task, test_task
from harness.vcs import VcsPort

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig
    from langgraph.checkpoint.base import BaseCheckpointSaver


class GraphState(TypedDict):
    branch_state: BranchState
    transitions: int  # stage executions so far (the MAX_TRANSITIONS budget)


_STAGE_TO_NODE = {
    "DEV": "dev",
    "REVIEW": "review",
    "TEST": "test",
    "FINAL_REVIEW": "final_review",
    "DONE": "summary",
}


def route(state: GraphState) -> str:
    """The only router. Pure function of state — no model call. Returns the next node."""
    if state["transitions"] >= MAX_TRANSITIONS:
        return "escalated"  # the escalated node stamps 'max_transitions'
    st = state["branch_state"]
    if st.escalation_reason:
        return "escalated"
    return _STAGE_TO_NODE[st.stage]


def _apply_cap(st: BranchState) -> None:
    """Port of the run-loop cap check: a finished loop at CAP rounds escalates."""
    if (
        st.stage != "DONE"
        and not st.escalation_reason
        and (st.loop_a_rounds >= CAP or st.loop_b_rounds >= CAP)
    ):
        st.escalation_reason = "cap_exceeded"


def can_skip_final(vcs: VcsPort, st: BranchState) -> bool:
    """Skip FINAL_REVIEW when it would only re-approve what REVIEW already passed: the
    reviewer never bounced (loop_a == 0) and nothing but tests/ changed since that review."""
    if st.loop_a_rounds != 0 or not st.last_review_commit:
        return False
    changed = vcs.diff(f"{st.last_review_commit}...HEAD", "--name-only").split()
    non_test = [f for f in changed if not f.startswith("tests/")]
    return not non_test


@dataclass
class GraphDeps:
    backend: RoleBackend
    vcs: VcsPort
    root: Path
    base: str = "main"


class Nodes:
    """Stage nodes as methods so they can be unit-tested in isolation. Each returns a full
    GraphState update; ``route`` (above) decides what runs next."""

    def __init__(self, deps: GraphDeps) -> None:
        self.deps = deps

    def _ctx(self, st: BranchState) -> RoleContext:
        return RoleContext(root=self.deps.root, branch=st.branch)

    def _render(self, st: BranchState) -> None:
        write_branch_files(self.deps.root, st)
        write_main(self.deps.root, [st])

    def _step(self, state: GraphState, st: BranchState) -> GraphState:
        return {"branch_state": st, "transitions": state["transitions"] + 1}

    async def plan(self, state: GraphState) -> GraphState:
        st = state["branch_state"].model_copy(deep=True)
        res = await self.deps.backend.run("planner", plan_task(st, self.deps.base),
                                          context=self._ctx(st))
        st.cost_usd += res.cost_usd
        st.history.append(HistoryEntry(role="planner", stage="PLAN", cost_usd=res.cost_usd))
        st.stage = "DEV"
        self._render(st)
        return self._step(state, st)

    async def dev(self, state: GraphState) -> GraphState:
        st = state["branch_state"].model_copy(deep=True)
        res = await self.deps.backend.run("developer", dev_task(st, self.deps.base),
                                          context=self._ctx(st))
        st.cost_usd += res.cost_usd
        committed = False
        if res.structured is None:
            st.escalation_reason = "no_verdict"
            st.history.append(HistoryEntry(role="developer", stage="DEV",
                                           verdict="no_verdict", cost_usd=res.cost_usd))
        else:
            dev = DevStatus.model_validate(res.structured)
            st.last_dev_note = (dev.note or "")[:200]
            st.history.append(HistoryEntry(role="developer", stage="DEV",
                                           verdict=dev.dev_status, cost_usd=res.cost_usd))
            if dev.dev_status == "blocked":
                st.escalation_reason = "developer_blocked"
            else:
                st.stage = "REVIEW"
                committed = True
        _apply_cap(st)
        self._render(st)
        if committed:
            p = BranchPaths(self.deps.root, st.branch)
            self.deps.vcs.commit([p.commit, p.metadata, p.main],
                                 f"dev: {st.branch} ({st.round_label})")
        return self._step(state, st)

    async def review(self, state: GraphState) -> GraphState:
        st = state["branch_state"].model_copy(deep=True)
        res = await self.deps.backend.run("reviewer", review_task(st, self.deps.base, final=False),
                                          context=self._ctx(st))
        st.cost_usd += res.cost_usd
        st.last_review_commit = self.deps.vcs.head()  # the HEAD this review saw
        if res.structured is None:
            st.escalation_reason = "no_verdict"
            st.history.append(HistoryEntry(role="reviewer", stage="REVIEW",
                                           verdict="no_verdict", cost_usd=res.cost_usd))
        else:
            outcome = apply_reviewer(st, ReviewerVerdict.model_validate(res.structured))
            st.history.append(HistoryEntry(role="reviewer", stage="REVIEW",
                                           verdict=outcome, cost_usd=res.cost_usd))
            st.stage = "TEST" if outcome == "PASS" else "DEV"
        _apply_cap(st)
        self._render(st)
        return self._step(state, st)

    async def test(self, state: GraphState) -> GraphState:
        st = state["branch_state"].model_copy(deep=True)
        res = await self.deps.backend.run("tester", test_task(st, self.deps.base),
                                          context=self._ctx(st))
        st.cost_usd += res.cost_usd
        if res.structured is None:
            st.escalation_reason = "no_verdict"
            st.history.append(HistoryEntry(role="tester", stage="TEST",
                                           verdict="no_verdict", cost_usd=res.cost_usd))
        else:
            outcome = apply_tester(st, TesterVerdict.model_validate(res.structured))
            st.history.append(HistoryEntry(role="tester", stage="TEST",
                                           verdict=outcome, cost_usd=res.cost_usd))
            st.stage = "FINAL_REVIEW" if outcome == "PASS" else "DEV"
        _apply_cap(st)
        self._render(st)
        p = BranchPaths(self.deps.root, st.branch)  # persist whatever tests it added
        self.deps.vcs.commit([p.commit, p.metadata, p.main],
                             f"test: {st.branch} ({st.round_label})")
        return self._step(state, st)

    async def final_review(self, state: GraphState) -> GraphState:
        st = state["branch_state"].model_copy(deep=True)
        if can_skip_final(self.deps.vcs, st):
            st.last_reviewer_verdict = "PASS"
            st.stage = "DONE"
            self._render(st)
            return self._step(state, st)
        res = await self.deps.backend.run("reviewer", review_task(st, self.deps.base, final=True),
                                          context=self._ctx(st))
        st.cost_usd += res.cost_usd
        st.last_review_commit = self.deps.vcs.head()
        if res.structured is None:
            st.escalation_reason = "no_verdict"
            st.history.append(HistoryEntry(role="reviewer", stage="FINAL_REVIEW",
                                           verdict="no_verdict", cost_usd=res.cost_usd))
        else:
            outcome = apply_reviewer(st, ReviewerVerdict.model_validate(res.structured))
            st.history.append(HistoryEntry(role="reviewer", stage="FINAL_REVIEW",
                                           verdict=outcome, cost_usd=res.cost_usd))
            st.stage = "DONE" if outcome == "PASS" else "DEV"
        _apply_cap(st)
        self._render(st)
        return self._step(state, st)

    async def summary(self, state: GraphState) -> GraphState:
        st = state["branch_state"].model_copy(deep=True)
        res = await self.deps.backend.run("summarizer", summary_task(st, self.deps.base),
                                          context=self._ctx(st))  # free text, no schema
        st.cost_usd += res.cost_usd
        if res.text.strip():
            append_devlog(self.deps.root, res.text)
        st.history.append(HistoryEntry(role="summarizer", stage="DONE", cost_usd=res.cost_usd))
        self._render(st)
        return {"branch_state": st, "transitions": state["transitions"]}  # terminal

    async def escalated(self, state: GraphState) -> GraphState:
        st = state["branch_state"].model_copy(deep=True)
        st.escalation_reason = st.escalation_reason or "max_transitions"
        st.stage = "ESCALATED"
        self._render(st)
        return {"branch_state": st, "transitions": state["transitions"]}  # terminal


def build_graph(deps: GraphDeps, *, checkpointer: "BaseCheckpointSaver | None" = None):  # type: ignore[no-untyped-def]
    from langgraph.graph import END, START, StateGraph

    nodes = Nodes(deps)
    builder: StateGraph = StateGraph(GraphState)
    builder.add_node("plan", nodes.plan)
    builder.add_node("dev", nodes.dev)
    builder.add_node("review", nodes.review)
    builder.add_node("test", nodes.test)
    builder.add_node("final_review", nodes.final_review)
    builder.add_node("summary", nodes.summary)
    builder.add_node("escalated", nodes.escalated)

    route_map: dict[Hashable, str] = {
        n: n for n in ("dev", "review", "test", "final_review", "summary", "escalated")
    }
    builder.add_edge(START, "plan")
    for stage_node in ("plan", "dev", "review", "test", "final_review"):
        builder.add_conditional_edges(stage_node, route, route_map)
    builder.add_edge("summary", END)
    builder.add_edge("escalated", END)
    return builder.compile(checkpointer=checkpointer)


async def run_branch(
    deps: GraphDeps,
    branch: str,
    *,
    checkpointer: "BaseCheckpointSaver | None" = None,
    thread_id: str | None = None,
) -> BranchState:
    """Drive one branch to DONE or ESCALATED and return the final state."""
    graph = build_graph(deps, checkpointer=checkpointer)
    deps.vcs.checkout_branch(branch, deps.base)
    init: GraphState = {"branch_state": BranchState(branch=branch, stage="DEV"), "transitions": 0}
    config: RunnableConfig = {"recursion_limit": MAX_TRANSITIONS * 2 + 5}
    if checkpointer is not None and thread_id:
        config["configurable"] = {"thread_id": thread_id}
    final = await graph.ainvoke(init, config=config)
    return final["branch_state"]

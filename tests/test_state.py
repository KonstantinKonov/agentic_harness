"""Acceptance checks for feature_state (tz.md)."""
from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest
import yaml

from harness.state import BranchState, HistoryEntry, Issue, Rejected
from harness.store import (
    BranchPaths,
    render_commit,
    render_main,
    render_metadata,
    write_branch_files,
)

DEFAULT_URI = "postgresql://harness:harness@localhost:5432/app_checkpointer"


def _pg_uri_or_none() -> str | None:
    uri = os.environ.get("CHECKPOINTER_DB_URI", DEFAULT_URI)
    try:
        import psycopg

        with psycopg.connect(uri, connect_timeout=3):
            return uri
    except Exception:
        return None


_PG_URI = _pg_uri_or_none()


def _sample(branch: str = "feature_auth", stage: str = "TEST") -> BranchState:
    return BranchState(
        branch=branch,
        stage=stage,  # type: ignore[arg-type]
        loop_a_rounds=2,
        loop_b_rounds=1,
        open_issues={
            "T-1": Issue(id="T-1", source="tester", what="login bypass",
                         where="auth.py:10", severity="blocker", status="open"),
        },
        rejected=[Rejected(approach="store password in plaintext", round="A1 / B0")],
        constraints=["tokens expire in 15m"],
        last_reviewer_verdict="PASS",
        last_tester_verdict="FAILED",
        escalation_reason=None,
        cost_usd=1.23,
        history=[HistoryEntry(role="tester", stage="TEST", verdict="FAILED", cost_usd=0.5)],
    )


def test_renderers_are_deterministic() -> None:
    st = _sample()
    assert render_commit(st) == render_commit(st)
    assert render_metadata(st) == render_metadata(st)
    states = [_sample("feature_b"), _sample("feature_a")]
    # order-independent: same set of states in any order -> identical dashboard
    assert render_main(states) == render_main(list(reversed(states)))


def test_metadata_yaml_valid_and_complete() -> None:
    st = _sample(stage="ESCALATED")
    st.escalation_reason = "cap_exceeded"
    data = yaml.safe_load(render_metadata(st))
    assert data["git_branch"] == "feature_auth"
    assert data["stage"] == "ESCALATED"
    assert data["loop_a_rounds"] == 2 and data["loop_b_rounds"] == 1
    assert data["last_reviewer_verdict"] == "PASS"
    assert data["last_tester_verdict"] == "FAILED"
    assert data["escalation_reason"] == "cap_exceeded"
    assert data["open_issues"] == 1


def test_commit_md_is_full_not_accumulating(tmp_path: Path) -> None:
    a = _sample(stage="DEV")
    a.last_dev_note = "scaffolded login"
    b = _sample(stage="DONE")  # same branch -> same file path
    b.open_issues = {}

    write_branch_files(tmp_path, a)
    write_branch_files(tmp_path, b)  # must overwrite, not append

    text = BranchPaths(tmp_path, b.branch).commit.read_text(encoding="utf-8")
    assert text == render_commit(b)            # exactly the latest render
    assert "scaffolded login" not in text      # nothing from the earlier state leaked
    assert "Stage: DONE" in text


def test_commit_md_handles_empty_state() -> None:
    text = render_commit(BranchState(branch="fresh"))
    assert "- none" in text
    assert "- (nothing yet)" in text


@pytest.mark.skipif(_PG_URI is None, reason="Postgres checkpointer not reachable")
def test_branchstate_round_trips_through_postgres_checkpointer() -> None:
    from langchain_core.runnables import RunnableConfig
    from langgraph.graph import END, START, StateGraph

    from harness.checkpointer import open_checkpointer

    original = _sample()

    def advance(state: BranchState) -> dict[str, str]:
        return {"stage": "DONE"}

    builder: StateGraph = StateGraph(BranchState)
    builder.add_node("advance", advance)
    builder.add_edge(START, "advance")
    builder.add_edge("advance", END)

    cfg: RunnableConfig = {"configurable": {"thread_id": f"state-test-{uuid4()}"}}
    with open_checkpointer(_PG_URI) as cp:
        graph = builder.compile(checkpointer=cp)
        graph.invoke(original.model_dump(), cfg)
        values = graph.get_state(cfg).values  # reloaded from Postgres

    loaded = BranchState.model_validate(values)
    assert loaded.stage == "DONE"                       # the node's update persisted
    assert loaded.branch == original.branch
    assert loaded.loop_a_rounds == 2 and loaded.loop_b_rounds == 1
    assert loaded.last_tester_verdict == "FAILED"
    assert loaded.open_issues["T-1"].severity == "blocker"
    assert loaded.rejected[0].approach == "store password in plaintext"
    assert loaded.history[0].role == "tester"
    assert loaded.cost_usd == pytest.approx(1.23)

"""Typed branch state — the orchestrator's machine state.

In the hybrid model (tz.md) this is what LangGraph's Postgres checkpointer persists;
``commit.md`` / ``metadata.yaml`` / ``main.md`` are rendered views of it (see store.py).
This module imports only pydantic — no SDK, no langgraph — so it stays import-light.

Issue ids (R-/T-) are display handles assigned by the orchestrator (``r_counter`` /
``t_counter``); the model never supplies them. Cross-round identity (for oscillation) is
matched on a normalized signature, not on an id — that logic lives with the graph.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from harness.schemas import Severity

STAGES = ("DEV", "REVIEW", "TEST", "FINAL_REVIEW", "DONE", "ESCALATED")
Stage = Literal["DEV", "REVIEW", "TEST", "FINAL_REVIEW", "DONE", "ESCALATED"]


class Issue(BaseModel):
    id: str
    source: Literal["reviewer", "tester"]
    what: str
    where: str = ""
    severity: Severity = "major"
    status: str = "open"


class Rejected(BaseModel):
    approach: str
    round: str = ""  # round label, e.g. "A1 / B0"


class HistoryEntry(BaseModel):
    role: str
    stage: str
    verdict: str | None = None
    cost_usd: float = 0.0


class BranchState(BaseModel):
    branch: str
    stage: Stage = "DEV"
    loop_a_rounds: int = 0                              # developer <-> reviewer iterations
    loop_b_rounds: int = 0                              # developer <-> tester iterations
    open_issues: dict[str, Issue] = Field(default_factory=dict)
    rejected: list[Rejected] = Field(default_factory=list)
    resolved_sigs: list[str] = Field(default_factory=list)  # cleared-issue signatures (oscillation)
    constraints: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)      # non-empty -> ESCALATE
    r_counter: int = 0                                 # last assigned reviewer issue number
    t_counter: int = 0                                 # last assigned tester failure number
    last_reviewer_verdict: str | None = None
    last_tester_verdict: str | None = None
    last_review_commit: str = ""                       # branch HEAD when reviewer last ran
    escalation_reason: str | None = None
    last_dev_note: str = ""
    cost_usd: float = 0.0
    history: list[HistoryEntry] = Field(default_factory=list)

    @property
    def round_label(self) -> str:
        return f"A{self.loop_a_rounds} / B{self.loop_b_rounds}"

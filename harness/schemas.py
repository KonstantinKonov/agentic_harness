"""Pydantic verdict schemas — the contract each role's final answer must satisfy.

A backend with a model passes these as the structured-output schema, so the model's
final answer is validated (and re-prompted on mismatch) rather than parsed from text.
The ``StubBackend`` validates its scripted responses against the same schemas, so every
backend returns the identical, contract-conforming shape.

Note: issues/failures carry NO ``id`` field. Stable ids are load-bearing for oscillation
detection, and the model is not trusted to keep them consistent — the orchestrator assigns
R-/T- ids itself. The model only describes the problem.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["blocker", "major", "minor"]


class ReviewIssue(BaseModel):
    severity: Severity = "major"
    what: str
    where: str = ""


class ReviewerVerdict(BaseModel):
    verdict: Literal["PASS", "CHANGES_REQUESTED"]
    spec_conformance: Literal["full", "partial", "off_track"]
    issues: list[ReviewIssue] = Field(default_factory=list)
    rejects: list[str] = Field(default_factory=list)  # approaches the developer must not retry


class TesterFailure(BaseModel):
    severity: Severity = "major"
    what: str
    repro: str = ""
    recommendation: str = ""


class TesterVerdict(BaseModel):
    verdict: Literal["PASS", "FAILED"]
    attack_surface_covered: list[str] = Field(default_factory=list)
    failures: list[TesterFailure] = Field(default_factory=list)
    tests_added: list[str] = Field(default_factory=list)


class DevStatus(BaseModel):
    dev_status: Literal["green", "blocked"]
    files_touched: list[str] = Field(default_factory=list)
    tests: str = ""
    note: str = ""


# role name -> schema its final answer must satisfy. None = free-text role (planner writes
# plan.md, summarizer writes the DEVLOG entry); neither returns a structured verdict.
ROLE_SCHEMA: dict[str, type[BaseModel] | None] = {
    "planner": None,
    "developer": DevStatus,
    "reviewer": ReviewerVerdict,
    "tester": TesterVerdict,
    "summarizer": None,
}

"""
Pydantic verdict schemas — the contract each role's final output must satisfy.

These are passed to the SDK as `output_format={"type": "json_schema", ...}`, so the
SDK validates the model's final answer against them and re-prompts on mismatch.

Note: issues/failures carry NO `id` field. Stable ids are load-bearing for
oscillation detection, and we no longer trust the model to keep them consistent —
the orchestrator assigns R-/T- ids itself. The model only describes the problem.
"""
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


# role name -> the schema its final answer must satisfy (summarizer returns free text)
ROLE_SCHEMA: dict[str, type[BaseModel]] = {
    "developer": DevStatus,
    "reviewer": ReviewerVerdict,
    "tester": TesterVerdict,
}

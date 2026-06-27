"""Scripted verdict sets for StubBackend — the deterministic happy-path demo.

These drive the graph straight down DEV→REVIEW→TEST→(FINAL_REVIEW)→DONE with zero network
and zero model tokens, exercising every role once. Reused by the graph/roles tests and by
the CLI demo (feature_cli_e2e).
"""
from __future__ import annotations

from pydantic import BaseModel

from harness.schemas import DevStatus, ReviewerVerdict, TesterVerdict

_PLAN_MD = """# Plan: demo

## Overview
A tiny demo feature, used to drive the harness happy-path end to end.

## Features

### demo   (branch: feature_demo, depends_on: —)
Acceptance criteria (reviewer checks these):
- [ ] the demo endpoint returns 200
"""


def happy_path_scripts() -> dict[str, list[BaseModel | str]]:
    """One response per role for a clean single-round run (everyone PASSes first try)."""
    return {
        "planner": [_PLAN_MD],
        "developer": [DevStatus(
            dev_status="green", files_touched=["src/demo.py"], tests="3 passed",
            note="implemented the demo endpoint",
        )],
        "reviewer": [ReviewerVerdict(verdict="PASS", spec_conformance="full")],
        "tester": [TesterVerdict(
            verdict="PASS", attack_surface_covered=["malformed input", "auth"],
            tests_added=["tests/test_demo.py"],
        )],
        "summarizer": ["## feature_demo — done\n**What:** demo endpoint\n**Rounds:** A0 / B0"],
    }

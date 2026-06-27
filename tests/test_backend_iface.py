"""Acceptance checks for feature_backend_iface (tz.md)."""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from harness.backends import RoleBackend, RoleContext, RoleResult, StubBackend
from harness.schemas import ReviewerVerdict, TesterVerdict

CTX = RoleContext(root=Path("/tmp/harness-test"), branch="demo")


# --- mypy: StubBackend structurally satisfies the RoleBackend Protocol ---
def _accepts_backend(b: RoleBackend) -> None:  # pragma: no cover - typed at call site
    assert hasattr(b, "run")


_accepts_backend(StubBackend({}))


async def test_stub_returns_valid_reviewer_verdict() -> None:
    stub = StubBackend(
        {"reviewer": [ReviewerVerdict(verdict="PASS", spec_conformance="full")]}
    )
    res = await stub.run("reviewer", "judge it", context=CTX)
    assert isinstance(res, RoleResult)
    assert res.structured is not None
    # structured must round-trip back through the role's schema
    ReviewerVerdict.model_validate(res.structured)
    assert res.structured["verdict"] == "PASS"
    assert res.cost_usd == 0.0


async def test_stub_validates_mapping_fixture() -> None:
    stub = StubBackend(
        {"tester": [{"verdict": "FAILED", "failures": [{"what": "boom"}]}]}
    )
    res = await stub.run("tester", "attack it", context=CTX)
    assert res.structured is not None
    TesterVerdict.model_validate(res.structured)
    assert res.structured["verdict"] == "FAILED"


async def test_invalid_fixture_is_rejected() -> None:
    # "MAYBE" is not a valid reviewer verdict literal -> validation must reject it.
    stub = StubBackend({"reviewer": [{"verdict": "MAYBE", "spec_conformance": "full"}]})
    with pytest.raises(ValidationError):
        await stub.run("reviewer", "judge it", context=CTX)


async def test_wrong_model_type_is_rejected() -> None:
    stub = StubBackend({"reviewer": [TesterVerdict(verdict="PASS")]})
    with pytest.raises(TypeError):
        await stub.run("reviewer", "judge it", context=CTX)


async def test_schema_less_role_returns_text() -> None:
    stub = StubBackend({"summarizer": ["one-line DEVLOG entry"]})
    res = await stub.run("summarizer", "summarize", context=CTX)
    assert res.structured is None
    assert res.text == "one-line DEVLOG entry"


async def test_scripts_advance_per_call() -> None:
    stub = StubBackend(
        {
            "reviewer": [
                ReviewerVerdict(verdict="CHANGES_REQUESTED", spec_conformance="partial"),
                ReviewerVerdict(verdict="PASS", spec_conformance="full"),
            ]
        }
    )
    first = await stub.run("reviewer", "r1", context=CTX)
    second = await stub.run("reviewer", "r2", context=CTX)
    assert first.structured is not None and second.structured is not None
    assert first.structured["verdict"] == "CHANGES_REQUESTED"
    assert second.structured["verdict"] == "PASS"


async def test_roleresult_contract_is_backend_agnostic() -> None:
    """The graph-facing contract must not depend on which backend produced it."""

    class _MockClaude:
        async def run(self, role: str, task: str, *, context: RoleContext) -> RoleResult:
            return RoleResult(
                structured={"verdict": "PASS", "spec_conformance": "full",
                            "issues": [], "rejects": []},
                text="",
                subtype="success",
                cost_usd=0.012,
            )

    async def drive(backend: RoleBackend) -> RoleResult:
        return await backend.run("reviewer", "judge it", context=CTX)

    stub_res = await drive(StubBackend({"reviewer": [
        ReviewerVerdict(verdict="PASS", spec_conformance="full")]}))
    mock_res = await drive(_MockClaude())

    for res in (stub_res, mock_res):
        assert isinstance(res, RoleResult)
        assert res.structured is not None
        ReviewerVerdict.model_validate(res.structured)
        assert res.subtype == "success"
    assert isinstance(_MockClaude(), RoleBackend)  # runtime_checkable Protocol


async def test_unscripted_role_raises() -> None:
    with pytest.raises(KeyError):
        await StubBackend({}).run("developer", "build", context=CTX)


async def test_overrun_script_raises() -> None:
    stub = StubBackend({"summarizer": ["only one"]})
    await stub.run("summarizer", "s1", context=CTX)
    with pytest.raises(IndexError):
        await stub.run("summarizer", "s2", context=CTX)

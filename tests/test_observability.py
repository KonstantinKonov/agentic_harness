"""Acceptance checks for feature_observability (tz.md), via an in-memory span exporter."""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from harness.backends import StubBackend
from harness.fixtures import happy_path_scripts
from harness.graph import GraphDeps, run_branch
from harness.observability import configure_tracing
from harness.schemas import DevStatus
from harness.vcs import FakeVcs

# Install an in-memory provider once for the process so the harness tracer records spans.
_EXPORTER = InMemorySpanExporter()
if not isinstance(trace.get_tracer_provider(), TracerProvider):
    _provider = TracerProvider()
    _provider.add_span_processor(SimpleSpanProcessor(_EXPORTER))
    trace.set_tracer_provider(_provider)


def _attr(span: ReadableSpan, key: str) -> object:
    attrs = span.attributes
    assert attrs is not None
    return attrs[key]


def _parent_id(span: ReadableSpan) -> int:
    parent = span.parent
    assert parent is not None
    return parent.span_id


@pytest.fixture(autouse=True)
def _clear_spans() -> Iterator[None]:
    _EXPORTER.clear()
    yield


async def test_happy_path_emits_one_trace_with_stage_and_role_spans(tmp_path: Path) -> None:
    deps = GraphDeps(backend=StubBackend(happy_path_scripts()), vcs=FakeVcs(),
                     root=tmp_path, base="main")
    final = await run_branch(deps, "feature_demo")
    spans = _EXPORTER.get_finished_spans()

    assert len({s.context.trace_id for s in spans}) == 1  # one trace for the whole run

    stage_names = {s.name for s in spans if s.name.startswith("stage.")}
    assert stage_names >= {
        "stage.plan", "stage.dev", "stage.review", "stage.test",
        "stage.final_review", "stage.summary",
    }

    role_spans = [s for s in spans if s.name.startswith("role.")]
    assert {_attr(s, "harness.role") for s in role_spans} == \
        {"planner", "developer", "reviewer", "tester", "summarizer"}
    for s in role_spans:
        assert _attr(s, "harness.backend") == "StubBackend"
        assert _attr(s, "harness.cost_usd") == 0.0  # stub -> 0

    dev_span = next(s for s in role_spans if _attr(s, "harness.role") == "developer")
    assert _attr(dev_span, "harness.verdict") == "green"
    rev_span = next(s for s in role_spans if _attr(s, "harness.role") == "reviewer")
    assert _attr(rev_span, "harness.verdict") == "PASS"

    root = next(s for s in spans if s.name.startswith("branch."))
    assert _attr(root, "harness.cost_usd") == final.cost_usd
    assert sum(cast(float, _attr(s, "harness.cost_usd")) for s in role_spans) == final.cost_usd
    assert _attr(root, "harness.stage") == "DONE"


async def test_role_span_nested_under_stage_under_root(tmp_path: Path) -> None:
    deps = GraphDeps(backend=StubBackend(happy_path_scripts()), vcs=FakeVcs(),
                     root=tmp_path, base="main")
    await run_branch(deps, "feature_demo")
    spans = _EXPORTER.get_finished_spans()
    by_id = {s.context.span_id: s for s in spans}

    root = next(s for s in spans if s.name.startswith("branch."))
    assert root.parent is None
    dev_role = next(s for s in spans if s.name == "role.developer")
    parent_stage = by_id[_parent_id(dev_role)]            # role nested in its stage
    assert parent_stage.name == "stage.dev"
    assert by_id[_parent_id(parent_stage)] is root        # stage nested in the run


async def test_escalation_reason_on_spans(tmp_path: Path) -> None:
    backend = StubBackend({
        "planner": ["plan"],
        "developer": [DevStatus(dev_status="blocked")],
    })
    deps = GraphDeps(backend=backend, vcs=FakeVcs(), root=tmp_path, base="main")
    final = await run_branch(deps, "feature_x")
    assert final.stage == "ESCALATED"
    spans = _EXPORTER.get_finished_spans()
    root = next(s for s in spans if s.name.startswith("branch."))
    assert _attr(root, "harness.escalation_reason") == "developer_blocked"


def test_configure_tracing_is_noop_without_langfuse_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        monkeypatch.delenv(var, raising=False)
    assert configure_tracing() is False  # graceful: no keys -> skip, no raise

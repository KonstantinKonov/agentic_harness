"""Per-invocation task prompts (the dynamic user message handed to a role each round).

Pure string builders, ported from machine.py. The system prompt is the role's
``.claude/agents/<role>.md`` body (backend's job); this is the round-specific task.
``base`` is the diff base (e.g. "main"). The StubBackend ignores these; the claude_sdk
backend uses them.
"""
from __future__ import annotations

from datetime import date

from harness.state import BranchState


def _open_lines(st: BranchState) -> str:
    if not st.open_issues:
        return "(none yet — implement the feature per the spec)"
    return "\n".join(f"- [{i}] {iss.what}" for i, iss in st.open_issues.items())


def plan_task(st: BranchState, base: str) -> str:
    return (
        f"Stage PLAN for branch `{st.branch}`.\n"
        f"Produce the checkable spec / plan for this feature in `plan.md`: goal, acceptance "
        f"criteria (reviewer-checkable), non-goals, constraints."
    )


def dev_task(st: BranchState, base: str) -> str:
    return (
        f"Stage DEV on branch `{st.branch}`.\n"
        f"Read `plan.md`, run `git diff {base}...{st.branch}`, and read "
        f"`.GCC/branches/{st.branch}/commit.md` (open issues, Rejected approaches — DO NOT "
        f"RETRY, Constraints).\n"
        f"Issues to fix this round:\n{_open_lines(st)}\n"
        f"Write and run tests; report `dev_status: green` only when your own tests pass."
    )


def review_task(st: BranchState, base: str, *, final: bool) -> str:
    # Variant B: FINAL_REVIEW reads the whole feature; a per-round REVIEW reads only what
    # changed since the previous review (falls back to the whole feature on the first one).
    if final:
        scope, rng = "the WHOLE feature", f"{base}...{st.branch}"
    elif st.last_review_commit:
        scope, rng = "the change since your last review", f"{st.last_review_commit}...HEAD"
    else:
        scope, rng = "this round's change", f"{base}...{st.branch}"
    return (
        f"Stage {'FINAL_REVIEW' if final else 'REVIEW'} on branch `{st.branch}`.\n"
        f"Read `plan.md` and run `git diff {rng}` to review {scope}.\n"
        f"Judge spec conformance only — report each problem as severity / what / where. "
        f"Do NOT propose fixes and do NOT invent issue ids; the orchestrator assigns them."
    )


def test_task(st: BranchState, base: str) -> str:
    if st.loop_b_rounds > 0:
        open_t = ", ".join(i for i in st.open_issues if i.startswith("T-")) \
            or "(the previously reported failures)"
        return (
            f"Stage TEST (re-test, loop B round {st.loop_b_rounds}) on branch `{st.branch}`.\n"
            f"Run the EXISTING test suite and confirm the previously reported failures "
            f"({open_t}) are fixed. Do NOT write a new attack campaign and do NOT open new "
            f"`minor` issues — only report a failure if it is `blocker` or `major`."
        )
    return (
        f"Stage TEST on branch `{st.branch}`.\n"
        f"Read `plan.md`, run `git diff {base}...{st.branch}`. Attack the implementation: "
        f"edge cases, malformed input, races, security. Write a FOCUSED set of persisted tests "
        f"under `tests/` — one test per real failure, not exhaustive parametrised mega-suites.\n"
        f"Report each failure as severity / what / repro. Do not invent issue ids."
    )


def summary_task(st: BranchState, base: str) -> str:
    return (
        f"Branch `{st.branch}` is DONE. Read `.GCC/branches/{st.branch}/commit.md` and run "
        f"`git diff {base}...{st.branch}`. Produce ONE DEVLOG entry in this exact shape and "
        f"return ONLY that text (no code fences):\n\n"
        f"## {st.branch} — {date.today().isoformat()}  (branch: {st.branch})\n"
        f"**What:** ...\n**Errors & fixes:** ...\n**Constraints discovered:** ...\n"
        f"**Rounds:** {st.round_label}"
    )

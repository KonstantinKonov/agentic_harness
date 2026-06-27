"""
The deterministic state machine. No model tokens are spent in this module.

    DEV ──(dev green)──▶ REVIEW
    REVIEW ──CHANGES_REQUESTED──▶ DEV      (loop A)
    REVIEW ──PASS──▶ TEST
    TEST ──FAILED──▶ DEV                   (loop B)
    TEST ──PASS──▶ FINAL_REVIEW
    FINAL_REVIEW ──PASS──▶ DONE
    any stage ──cap / oscillation / conflict / blocked / no_verdict──▶ ESCALATED

Verdicts arrive as validated `structured_output` (see `schemas.py`), so there is no
text parsing. Issue ids (R-/T-) are assigned HERE, not by the model. Open issues are
replaced per source each round (the latest verdict IS the current set), and
oscillation is detected on a normalized signature of resolved issues.
"""
import subprocess
from datetime import date
from pathlib import Path

from .config import CAP, MAX_TRANSITIONS
from .roles import run_role
from .schemas import DevStatus, ReviewerVerdict, TesterVerdict
from .store import BranchState, Store


# --- git helpers -------------------------------------------------------------

def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(root), capture_output=True, text=True)


def _commit(root: Path, msg: str) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-m", msg)  # no-op (nonzero) when there is nothing to commit; ignored


def _head(root: Path) -> str:
    return _git(root, "rev-parse", "HEAD").stdout.strip()


def _can_skip_final(root: Path, st: BranchState) -> bool:
    """
    Skip FINAL_REVIEW when it would just re-approve what REVIEW already passed.

    Safe only when the reviewer never bounced (loop_a == 0, so the single REVIEW saw
    the whole code diff and passed) AND nothing but test files changed since that
    review (the tester's persisted tests). Any non-test change -> run FINAL_REVIEW.
    """
    if st.loop_a_rounds != 0 or not st.last_review_commit:
        return False
    changed = _git(root, "diff", f"{st.last_review_commit}...HEAD", "--name-only").stdout.split()
    non_test = [f for f in changed if not f.startswith("tests/")]
    return not non_test


# --- verdict curation (single writer of state) -------------------------------

def _sig(source: str, what: str, where: str) -> str:
    """
    Normalized cross-round identity for an issue. Best-effort (text varies between
    rounds); the hard anti-loop guarantee is the round cap, this is a refinement.
    """
    return f"{source}:{(where or '').strip().lower()}:{(what or '').strip().lower()[:60]}"


def _drop_source(st: BranchState, source: str) -> None:
    st.open_issues = {i: iss for i, iss in st.open_issues.items() if iss["source"] != source}


def _add_issue(st: BranchState, source: str, what: str, where: str, severity: str) -> None:
    if _sig(source, what, where) in st.resolved_sigs:
        st.escalation_reason = "oscillation"
        st.conflicts.append(f"{source} re-raised a previously resolved issue: {what[:80]}")
    if source == "reviewer":
        st.r_counter += 1
        iid = f"R-{st.r_counter}"
    else:
        st.t_counter += 1
        iid = f"T-{st.t_counter}"
    st.open_issues[iid] = {
        "id": iid, "source": source, "what": what,
        "where": where, "severity": severity, "status": "open",
    }


def apply_reviewer(st: BranchState, v: dict, *, final: bool) -> str:
    verdict = (v.get("verdict") or "").upper()
    st.last_reviewer_verdict = verdict or None
    for rj in v.get("rejects") or []:
        st.rejected.append({"approach": str(rj), "round": f"A{st.loop_a_rounds}"})

    if verdict == "PASS":
        for iss in [i for i in st.open_issues.values() if i["source"] == "reviewer"]:
            st.resolved_sigs.append(_sig("reviewer", iss["what"], iss["where"]))
        _drop_source(st, "reviewer")
        return "PASS"

    _drop_source(st, "reviewer")  # latest verdict's issues replace the prior set
    for iss in v.get("issues") or []:
        _add_issue(st, "reviewer", iss.get("what", ""), iss.get("where", ""), iss.get("severity", "major"))
    st.loop_a_rounds += 1
    return "CHANGES_REQUESTED"


def apply_tester(st: BranchState, v: dict) -> str:
    verdict = (v.get("verdict") or "").upper()
    st.last_tester_verdict = verdict or None

    if verdict == "PASS":
        for iss in [i for i in st.open_issues.values() if i["source"] == "tester"]:
            st.resolved_sigs.append(_sig("tester", iss["what"], iss["where"]))
        _drop_source(st, "tester")
        return "PASS"

    _drop_source(st, "tester")
    for f in v.get("failures") or []:
        _add_issue(st, "tester", f.get("what", ""), f.get("repro", ""), f.get("severity", "major"))
    st.loop_b_rounds += 1
    return "FAILED"


# --- task prompts (the dynamic per-invocation user message) ------------------

def _open_lines(st: BranchState) -> str:
    if not st.open_issues:
        return "(none yet — implement the feature per the spec)"
    return "\n".join(f"- [{i}] {iss['what']}" for i, iss in st.open_issues.items())


def _dev_task(st: BranchState) -> str:
    return (
        f"Stage DEV on branch `{st.branch}`.\n"
        f"Read `plan.md`, run `git diff {{base}}...{st.branch}`, and read "
        f"`.GCC/branches/{st.branch}/commit.md` (open issues, Rejected approaches — DO NOT "
        f"RETRY, Constraints).\n"
        f"Issues to fix this round:\n{_open_lines(st)}\n"
        f"Write and run tests; report `dev_status: green` only when your own tests pass."
    )


def _review_task(st: BranchState, base: str, *, final: bool) -> str:
    scope = "the WHOLE feature" if final else "this round's change"
    return (
        f"Stage {'FINAL_REVIEW' if final else 'REVIEW'} on branch `{st.branch}`.\n"
        f"Read `plan.md` and run `git diff {base}...{st.branch}` to review {scope}.\n"
        f"Judge spec conformance only — report each problem as severity / what / where. "
        f"Do NOT propose fixes and do NOT invent issue ids; the orchestrator assigns them."
    )


def _test_task(st: BranchState, base: str) -> str:
    if st.loop_b_rounds > 0:
        # Re-test: narrow scope. Verify the fixes + run the existing suite. Do NOT launch
        # a fresh attack campaign or open new minor issues — that is what cost 50% last run.
        open_t = ", ".join(i for i in st.open_issues if i.startswith("T-")) or "(the previously reported failures)"
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


def _summary_task(st: BranchState, base: str) -> str:
    return (
        f"Branch `{st.branch}` is DONE. Read `.GCC/branches/{st.branch}/commit.md` and run "
        f"`git diff {base}...{st.branch}`. Produce ONE DEVLOG entry in this exact shape and "
        f"return ONLY that text (no code fences):\n\n"
        f"## {st.branch} — {date.today().isoformat()}  (branch: {st.branch})\n"
        f"**What:** ...\n**Errors & fixes:** ...\n**Constraints discovered:** ...\n"
        f"**Rounds:** {st.round_label}"
    )


# --- the loop ----------------------------------------------------------------

async def _run_dev(root: Path, store: Store, st: BranchState, base: str) -> None:
    res = await run_role("developer", _dev_task(st).replace("{base}", base), root=root, output_schema=DevStatus)
    st.cost_usd += res.cost_usd
    store.append_log("developer", "DEV", res.text)
    if res.structured is None:
        st.escalation_reason = "no_verdict"
        st.history.append({"role": "developer", "stage": "DEV", "verdict": "no_verdict", "cost_usd": res.cost_usd})
        return
    v = res.structured
    st.last_dev_note = (v.get("note") or "")[:200]
    st.history.append({"role": "developer", "stage": "DEV", "verdict": v.get("dev_status"), "cost_usd": res.cost_usd})
    if (v.get("dev_status") or "").lower() == "blocked":
        st.escalation_reason = "developer_blocked"
        return
    _commit(root, f"dev: {st.branch} ({st.round_label})")
    st.stage = "REVIEW"


async def _run_review(root: Path, store: Store, st: BranchState, base: str, *, final: bool) -> None:
    stage = "FINAL_REVIEW" if final else "REVIEW"
    res = await run_role("reviewer", _review_task(st, base, final=final), root=root, output_schema=ReviewerVerdict)
    st.cost_usd += res.cost_usd
    st.last_review_commit = _head(root)   # the HEAD this review saw (for FINAL_REVIEW skip)
    store.append_log("reviewer", stage, res.text)
    if res.structured is None:
        st.escalation_reason = "no_verdict"
        st.history.append({"role": "reviewer", "stage": stage, "verdict": "no_verdict", "cost_usd": res.cost_usd})
        return
    outcome = apply_reviewer(st, res.structured, final=final)
    st.history.append({"role": "reviewer", "stage": stage, "verdict": outcome, "cost_usd": res.cost_usd})
    if outcome == "PASS":
        st.stage = "DONE" if final else "TEST"
    else:
        st.stage = "DEV"


async def _run_test(root: Path, store: Store, st: BranchState, base: str) -> None:
    res = await run_role("tester", _test_task(st, base), root=root, output_schema=TesterVerdict)
    st.cost_usd += res.cost_usd
    store.append_log("tester", "TEST", res.text)
    _commit(root, f"test: {st.branch} ({st.round_label})")  # persist whatever tests it added
    if res.structured is None:
        st.escalation_reason = "no_verdict"
        st.history.append({"role": "tester", "stage": "TEST", "verdict": "no_verdict", "cost_usd": res.cost_usd})
        return
    outcome = apply_tester(st, res.structured)
    st.history.append({"role": "tester", "stage": "TEST", "verdict": outcome, "cost_usd": res.cost_usd})
    st.stage = "FINAL_REVIEW" if outcome == "PASS" else "DEV"


async def _run_summary(root: Path, store: Store, st: BranchState, base: str) -> None:
    res = await run_role("summarizer", _summary_task(st, base), root=root)  # free text, no schema
    st.cost_usd += res.cost_usd
    if res.text.strip():
        store.write_devlog(res.text)


async def run(root: Path, branch: str, base: str = "main") -> BranchState:
    store = bootstrap(root, branch, base)
    st = store.load()
    print(f"[orchestrator] branch={branch} base={base} stage={st.stage}")

    for _ in range(MAX_TRANSITIONS):
        if st.stage in ("DONE", "ESCALATED"):
            break
        if st.stage == "DEV":
            await _run_dev(root, store, st, base)
        elif st.stage == "REVIEW":
            await _run_review(root, store, st, base, final=False)
        elif st.stage == "TEST":
            await _run_test(root, store, st, base)
        elif st.stage == "FINAL_REVIEW":
            if _can_skip_final(root, st):
                print("[orchestrator] FINAL_REVIEW skipped (loop_a=0, only tests changed since REVIEW)")
                st.last_reviewer_verdict = "PASS"
                st.stage = "DONE"
            else:
                await _run_review(root, store, st, base, final=True)

        if st.escalation_reason:
            st.stage = "ESCALATED"
        elif st.stage != "DONE" and (st.loop_a_rounds >= CAP or st.loop_b_rounds >= CAP):
            st.escalation_reason = "cap_exceeded"
            st.stage = "ESCALATED"

        print(f"[orchestrator] -> {st.stage}  ({st.round_label}, open={len(st.open_issues)}, ${st.cost_usd:.4f})")
        store.save(st)
    else:
        st.escalation_reason = st.escalation_reason or "max_transitions"
        st.stage = "ESCALATED"
        store.save(st)

    if st.stage == "DONE":
        await _run_summary(root, store, st, base)
        store.save(st)

    _report(st)
    return st


def bootstrap(root: Path, branch: str, base: str) -> Store:
    if not (root / "plan.md").exists():
        raise SystemExit("plan.md is missing — run /spec first to write the spec.")
    if _git(root, "rev-parse", "--verify", branch).returncode != 0:
        r = _git(root, "checkout", "-b", branch, base)
        if r.returncode != 0:
            raise SystemExit(f"git checkout -b {branch} {base} failed:\n{r.stderr}")
    else:
        _git(root, "checkout", branch)
    store = Store(root, branch)
    if not store.exists():
        store.save(BranchState(branch=branch, stage="DEV"))
    return store


def _report(st: BranchState) -> None:
    print(f"\n[orchestrator] result: {st.stage}")
    if st.escalation_reason:
        print(f"  escalation_reason: {st.escalation_reason}")
    print(f"  rounds: {st.round_label}   open issues: {len(st.open_issues)}")
    print(f"  cost this run: ${st.cost_usd:.4f}")
    if st.stage == "DONE":
        print("  -> ready for human review + merge; DEVLOG entry written.")
    else:
        print("  -> NOT mergeable. Human needed.")

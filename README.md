# Agentic development scaffold

> **Rewrite in progress.** This harness is being re-implemented as a deterministic
> **LangGraph** FSM orchestrator over pluggable role backends (`stub` / `claude_sdk` /
> `own`), with single observability via self-hosted **Langfuse**. The spec lives in
> `tz.md`; new code lands in `harness/`. The `orchestrator/` package below is the
> original implementation, kept as reference until the rewrite lands.

A reusable structure for building features with a loop of specialized agents,
driven by a **deterministic Python orchestrator** that runs each role as its own
Claude Agent SDK `query()`. One feature = one git branch.

## The loop (state machine)

```
DEV ──(dev tests green)──▶ REVIEW
REVIEW ──CHANGES_REQUESTED──▶ DEV          (loop A: spec conformance)
REVIEW ──PASS──▶ TEST
TEST ──FAILED──▶ DEV                       (loop B: adversarial + security)
TEST ──PASS──▶ FINAL_REVIEW
FINAL_REVIEW ──ok──▶ DONE
any stage ──cap exceeded / R↔T conflict──▶ ESCALATED (human)
```

- **planner** (`/spec`) — *before the loop*: interviews you into a checkable spec +
  branch plan in `plan.md`. Run it manually first.
- **developer** — writes code per spec + tests, runs them, hands back when green.
- **reviewer** — judges spec conformance only. Reports *what* is wrong, never *how* to fix.
- **tester** — adversarial: tries to break it + finds security issues. Persists its tests.
- **orchestrator** — plain Python (`orchestrator/`, **zero model tokens**). Owns the
  state machine, enforces the round cap, detects oscillation / id-based conflicts.
  `state.json` is its source of truth; `commit.md`/`metadata.yaml`/`main.md` are
  *rendered* from it (so they never go stale). It is the only writer under `.GCC/`.
- **summarizer** — at PR creation, distills the branch into a `DEVLOG.md` entry.

Two distinct exits: `DONE` (reviewer PASS + tester PASS + final review) means
ready to merge; `ESCALATED` means budget exhausted or a conflict — **not** mergeable.

## Layout

```
project/
  README.md                 # this file — committed
  DEVLOG.md                 # distilled per-feature record — committed (main)
  CLAUDE.md                 # project instructions — local, empty by default
  plan.md                   # spec / ТЗ + branch scheme — local, produced by /spec
  pyproject.toml            # deps: claude-agent-sdk, pyyaml — committed
  .gitignore
  orchestrator/             # the deterministic orchestrator (Python) — committed
    __main__.py             # CLI: python -m orchestrator <branch>
    machine.py              # the state machine + verdict curation (no model tokens)
    store.py                # state.json source of truth -> renders commit/metadata/main
    roles.py                # load .claude/agents/*.md, run each as a query() with output_format
    config.py               # per-role model / effort / tools / permission tuning
    schemas.py              # Pydantic verdict contracts, passed to the SDK as output_format
  .claude/                  # committed
    settings.json           # Bash allowlist (pytest, git, ...) — the headless permission gate
    commands/
      spec.md               # the planner (top-level) — run with /spec <project>
    agents/                 # role definitions; bodies are used as system prompts
      developer.md  reviewer.md  tester.md  summarizer.md
  schemas/                  # contracts — committed (verdict shapes mirror orchestrator/schemas.py)
    plan.md  reviewer_verdict.yaml  tester_verdict.yaml  commit.md
  templates/                # shape reference (orchestrator now renders the live file)
    metadata.yaml
  .GCC/                     # working memory — gitignored, latest-state
    main.md                 # dashboard, rendered from every branch's state.json
    branches/<branch>/
      state.json            # orchestrator's structured source of truth
      metadata.yaml         # rendered cross-branch view: stage, rounds, open issues
      commit.md             # rendered memory between rounds (REJECTED before Done)
      log.md                # raw append-only transcript, rarely read in full
```

## Running

```bash
pip install -e .                 # installs claude-agent-sdk + pyyaml
export ANTHROPIC_API_KEY=...     # or authenticate the SDK with your Claude subscription
/spec <project>                  # interview -> plan.md (run inside Claude Code, once)
python -m orchestrator feature_x # drives the branch to DONE or ESCALATED
```

The orchestrator authenticates however the Claude Agent SDK is configured: an API key
bills pay-as-you-go; a Claude subscription draws from the monthly Agent SDK credit
(separate from interactive limits as of 2026-06-15). Each run prints its `total_cost_usd`.

**Cost levers** (all in `orchestrator/config.py`): model choice dominates
(Opus ≈ 5× Sonnet ≈ 25× Haiku) — Opus is reserved for the reviewer; effort is second;
the orchestrator itself spends nothing. `setting_sources=["project"]` + `permission_mode="dontAsk"`
make every role headless-safe: only the Bash commands in `.claude/settings.json` run,
everything else is denied rather than prompted.

## Read discipline (against context bloat)

- The orchestrator carries no prose context — it reads `state.json` and acts on data.
- A role on round N+1 reads: spec (`plan.md`), `git diff`, branch `commit.md`,
  and the latest verdict — **not** the full `log.md`.
- `commit.md` is the only memory channel between rounds: anything not in it did
  not happen, from the next agent's point of view. That is why rejected approaches
  are recorded first — they prevent the loop from circling.

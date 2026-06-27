<!-- CONTRACT — the structure of plan.md. Produced by the /spec planner, consumed by
     the /orchestrate orchestrator. plan.md is local (gitignored), the single source of
     truth for WHAT to build. Acceptance criteria MUST be checkable by the reviewer —
     that is the quality bar for the whole loop. See plan.md for the live instance. -->

# Plan: <project>

## Overview
<2-4 lines: the goal, who it is for, what "done" looks like>

## Branch scheme
<ordered list of feature branches and how they relate; merge target = main>

## Features

### <feature>   (branch: feature_<name>, depends_on: [feature_x | —])
Spec:
- <what it does, concrete behaviors, error paths>

Acceptance criteria (reviewer checks these):
- [ ] <observable, checkable condition — not "fast"/"secure">

Non-goals:
- <explicitly out of scope>

Constraints:
- <language / library / runtime invariants, if any>

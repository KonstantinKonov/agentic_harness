<!-- CONTRACT — the required structure of every branch's .GCC/branches/<branch>/commit.md.
     Not a template to copy: the orchestrator maintains the file to this shape.
     Section order is load-bearing — dead ends come before "Done", because commit.md is
     the only memory channel between rounds: anything not here did not happen for the
     next agent. See .GCC/branches/_example/commit.md for a filled example. -->

# Feature: <name>   (branch: <git-branch>)
Spec: plan.md#<feature>
Stage: <DEV | REVIEW | TEST | FINAL_REVIEW | DONE | ESCALATED>
Round: A<n> / B<n>            # loop A (reviewer) round / loop B (tester) round

## Open issues (current, with source and status)
# one line per issue: [<id>] (source) <what>   severity: <blocker|major|minor>  status: open
# id is stable per branch: R-<n> from reviewer, T-<n> from tester.

## Rejected approaches — DO NOT RETRY
# [<id>] <approach> -> <why rejected>   (round)

## Constraints discovered (invariants)
# hard facts learned the expensive way, e.g. "lib X has no RS256 -> use PS256"

## Conflicts (reviewer ↔ tester) — BLOCKER, needs human
# reviewer rejected X and tester now requires X (or vice versa). Populating this
# section means escalate, not loop.

## Done (current state — least important section)
# what currently exists

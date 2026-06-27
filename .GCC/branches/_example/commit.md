# Feature: user-auth   (branch: feature_auth)
Spec: plan.md#user-auth
Stage: TEST            # DEV | REVIEW | TEST | FINAL_REVIEW | DONE | ESCALATED
Round: A2 / B1        # loop A round 2, loop B round 1

## Open issues (current, with source and status)
- [T-1] (tester)   refresh cookie without SameSite -> CSRF   severity: blocker  status: open
- [R-4] (reviewer) /login leaks stack trace on 500           severity: major    status: open

## Rejected approaches — DO NOT RETRY
- [R-1] refresh token in localStorage -> XSS                    (round A1)
- [R-2] synchronous token validation on every request -> +200ms (round A1)

## Constraints discovered (invariants)
- lib X does not support RS256 -> use PS256

## Conflicts (reviewer ↔ tester) — BLOCKER, needs human
- (none)

## Done (current state — least important section)
- JWT on PS256
- /login, /refresh endpoints

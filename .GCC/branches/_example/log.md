# Log — feature_auth (append-only, raw)

Every role appends its raw output here each round. Rarely read in full.
The orchestrator distills this into commit.md.

## A1 — developer
Implemented JWT + /login + /refresh. refresh token stored in localStorage.
Files: auth/jwt.py, auth/views.py, tests/test_auth.py
Tests: 5 passed.

## A1 — reviewer
verdict: CHANGES_REQUESTED
spec_conformance: partial
issues:
  - id: R-1
    severity: blocker
    what: "refresh token in localStorage"
    where: "auth/jwt.py:store_refresh"

## A2 — developer
Moved refresh to httpOnly + SameSite cookie. Tests: 6 passed.

## A2 — reviewer
verdict: PASS
spec_conformance: full

## B1 — tester
verdict: FAILED
attack_surface_covered:
  - "expired / forged / tampered tokens"
  - "SQL injection in /login"
  - "race on concurrent refresh"
failures:
  - id: T-1
    severity: blocker
    what: "CSRF on cookie-based refresh"
    repro: "tests/security/test_csrf.py::test_refresh_no_samesite"
tests_added:
  - tests/security/test_csrf.py

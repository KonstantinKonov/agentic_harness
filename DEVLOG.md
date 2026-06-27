# DEVLOG

Distilled per-feature record: what was built, where the AI erred, how it was fixed.
Written by the **summarizer** at PR creation. Append-only. Committed to git (main).

This is the documentation layer — the verbose loop transcript stays local in `.GCC/`.

---

<!-- example entry — delete

## user-auth — 2026-05-29  (branch: feature_auth)

**What:** JWT auth on PS256, `/login` + `/refresh` endpoints.

**Errors & fixes:**
- refresh token in localStorage → XSS; moved to httpOnly + SameSite cookie.
- cookie refresh missing CSRF protection → added SameSite=Strict + CSRF token.

**Constraints discovered:** lib X has no RS256 → PS256.

**Rounds:** A2 / B1.

-->

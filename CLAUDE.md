<!-- Scaffold baseline — applies to all roles. Project-specific instructions go below.
     This file is loaded into every agent's context. Not committed by default. -->

# Search hygiene
- Use Grep / Glob to search code — they respect `.gitignore`, so ignored dirs are skipped.
- Never recursively scan `.venv/`, `.git/`, `__pycache__/`, `node_modules/` with raw
  `find` / `ls -R` / `grep -r`. They dump huge output and burn tokens for nothing.
- Keep heavy dirs (`.venv/`, `__pycache__/`, `__pytest__/`) in `.gitignore` so the search tools skip them.

---

# Tech stack
- Python 3.12, mypy-friendly typization
- web: FastAPI
- httpx
- pytest + pytest-asyncio
- pydantic v2

---

<!-- Project-specific instructions below. Empty by default — fill per project. -->

# Contributing to buddy

---

## Before you start

Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Understand the routing flow and the cus-core grading integration before making changes to `router.py` or `chat.py`.

---

## Setup

```bash
git clone https://github.com/StellarRequiem/buddy.git && cd buddy
scripts/setup.sh
```

---

## The rules

**1. Tests pass before every commit.**
```bash
python test_live.py   # all 17 checks must pass
```
No exceptions. If a test breaks, fix it before committing.

**2. Never block the event loop.**
`OllamaGrader` and `search_memory` are synchronous. Always run them in `_GRADE_EXECUTOR`:
```python
result = await loop.run_in_executor(_GRADE_EXECUTOR, sync_function, arg1, arg2)
```

**3. Local-first by default.**
New features should work without an Anthropic API key. Graceful degradation when API is absent.

**4. Every endpoint needs a test.**
If you add an endpoint, add a test group to `test_live.py`.

**5. Grading is the moat — don't weaken it.**
The cus-core rubric and escalation thresholds are deliberate. Don't lower pass thresholds to hide quality problems.

---

## Branch conventions

```
main          — always passing, always deployable
feat/name     — new feature
fix/name      — bug fix
docs/name     — documentation only
```

---

## Commit style

```
feat: short description
fix: short description
docs: short description
refactor: short description
```

One subject line. Body optional. Always include:
```
Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
```
if AI-assisted (which it usually is).

---

## Adding a new API endpoint

1. Add to the relevant router in `buddy/api/`
2. Register in `buddy/main.py` if it's a new router file
3. Add test to `test_live.py`
4. Document in README.md API table

---

## Adding a new escalation keyword

Edit `config.py`:
```python
escalation_keywords: list[str] = [
    "summarize this document",
    "write code",
    "debug",
    "explain in detail",
    "your new keyword here",   # ← add here
]
```

---

## Session system

Every working session should produce:
- A journal entry at `docs/sessions/YYYY-MM-DD_journal.md`
- An updated `HANDOFF.md`

Use the templates in `docs/sessions/`.

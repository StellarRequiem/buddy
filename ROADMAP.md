# Buddy + Forest + cus-core — Product Roadmap
_StellarRequiem Stack | Local-First AI Ecosystem_
_Last updated: 2026-04-20_

---

## Philosophy

Every level must be:
- **Shippable** — a real person can install and run it in under 10 minutes
- **Demonstrable** — one command or one URL shows the full value
- **Documented** — README, INSTALL, DEMO, and HANDOFF exist before the level is called done
- **Tested** — automated test suite passes before any level is tagged

No level is complete until all four boxes are checked.

---

## The Levels

```
L0  Foundation          ← YOU ARE HERE (100% built, 0% documented)
L1  Hackathon Ready     ← Next milestone (~2–3 focused sessions)
L2  Public OSS v1.0     ← First real release (~1–2 weeks)
L3  Companion Platform  ← Full product (~4–6 weeks)
L4  Ecosystem           ← Multi-agent + marketplace (~3–6 months)
L5  Platform            ← The big vision (12+ months)
```

---

## L0 — Foundation ✅ COMPLETE

**What exists right now:**

| Component | Status |
|-----------|--------|
| buddy FastAPI server (port 7437) | ✅ Running |
| Local routing: qwen2.5:14b → phi4-mini | ✅ Working |
| Opus 4.7 escalation (keyword + score threshold) | ✅ Working |
| cus-core grading (Haiku + extended thinking) | ✅ Working |
| Forest blue-team proxy (/forest/status) | ✅ Working |
| Session persistence (SQLite) | ✅ Working |
| Vector memory (ChromaDB + nomic-embed-text) | ✅ Working |
| Test mode toggle (evicts 9GB model, pauses Forest) | ✅ Working |
| Admin endpoint (/admin/test-mode, /admin/status) | ✅ Working |
| Expected-failure demo (4 scenarios, Haiku grading) | ✅ Working |
| Siri/iOS Shortcuts endpoints | ✅ Working |
| Live test suite (17/17 passing) | ✅ Passing |
| RAM tuned (Docker 2.5GB VM, K8s off) | ✅ Done |

**What's missing at L0:**
- [ ] All changes committed to git
- [ ] HANDOFF.md exists (✅ written this session)
- [ ] ROADMAP.md exists (✅ this file)

**L0 → L1 gate:** Commit everything, tag `v0.1.0-foundation`.

---

## L1 — Hackathon Ready 🎯 NEXT

**Goal:** Someone who has never seen this project can install, run, and be impressed in 10 minutes.

### Deliverables

#### 1. README.md (root) — complete rewrite
- What it is (3 sentences, no hype)
- Architecture diagram (ASCII is fine)
- Prerequisites list
- Quickstart (5 commands to running demo)
- Link to DEMO.md
- Link to full docs

#### 2. INSTALL.md
- macOS (M-series) — primary target
- Prerequisites: Ollama, Python 3.11+, uv, Anthropic API key
- One-command setup script: `scripts/setup.sh`
- Model pull commands
- Verify install: `python test_live.py`

#### 3. DEMO.md — the walkthrough script
- 5 demo beats (see below)
- Expected output for each beat
- What to say while running it
- Fallback if something goes wrong

#### 4. setup.sh — automated installer
```bash
scripts/setup.sh     # installs deps, pulls models, creates .env template, starts server
```

#### 5. demo.sh — one-command demo launcher
```bash
scripts/demo.sh      # starts server if not running, opens browser, runs test_live.py
```

#### 6. Git hygiene
- All L0 changes committed
- Tagged `v0.1.0-foundation`
- Tagged `v0.1.1-hackathon` when L1 complete

### The 5 Demo Beats

| Beat | What you show | Why it lands |
|------|---------------|--------------|
| 1. Local routing | Ask a factual question → qwen2.5:14b answers, grade appears | "Free, private, runs on your hardware" |
| 2. Keyword escalation | "Write code for a quicksort" → Opus 4.7 fires, grade 97/100 | "It knows when to escalate — automatically" |
| 3. Extended thinking | Grade panel → expand → Haiku's reasoning visible | "The grader shows its work. Trust is verifiable." |
| 4. Expected-failure | Run phishing scenario → Opus refuses → 100/100 refusal score | "We grade refusals. This is the cus-core moat." |
| 5. Test mode | POST /admin/test-mode → 9GB freed, Forest paused | "Full runtime control. No restart needed." |

### L1 Completion Checklist
- [ ] README.md rewritten
- [ ] INSTALL.md written
- [ ] DEMO.md written (5 beats, with expected output)
- [ ] `scripts/setup.sh` written and tested
- [ ] `scripts/demo.sh` written
- [ ] All code committed + tagged `v0.1.1-hackathon`
- [ ] test_live.py 17/17 on clean install

---

## L2 — Public OSS v1.0

**Goal:** Strangers on GitHub can use this without asking questions.

### New features
- [ ] **Streaming responses** — wire `local_chat_stream()` to chat endpoint (SSE)
- [ ] **Test mode UI indicator** — "🔬 TEST MODE" banner in chat header
- [ ] **Forest scan scheduler UI** — button in Forest tab (not console-only)
- [ ] **Docker Compose** — single `docker-compose.yml` for buddy + ollama (no native install required)
- [ ] **Config via UI** — basic settings panel (model selection, escalation threshold)

### Documentation
- [ ] Full API reference (auto-generated from FastAPI + annotated)
- [ ] Architecture deep-dive (docs/ARCHITECTURE.md)
- [ ] cus-core integration guide (how grading works, how to extend rubrics)
- [ ] Contributing guide (CONTRIBUTING.md)
- [ ] Changelog (CHANGELOG.md)

### Release
- Tagged `v1.0.0`
- GitHub release with binary/archive + install notes

---

## L3 — Companion Platform

**Goal:** This is a product people use daily, not just a demo.

### New features
- [ ] **Voice input** — Whisper integration (local, on-device)
- [ ] **Siri deep integration** — full iOS Shortcuts library, not just ping/task
- [ ] **Plugin system** — drop a `.py` file in `plugins/` to add a tool
- [ ] **Persistent agent tasks** — buddy can run background tasks, report back
- [ ] **Notification push** — buddy proactively surfaces important info
- [ ] **Multi-session dashboard** — view all sessions, search history, memory inspector
- [ ] **Forest → buddy alerts** — critical Forest incidents surface in buddy chat

### Documentation
- [ ] Plugin authoring guide
- [ ] iOS Shortcuts library (downloadable .shortcut files)
- [ ] Video walkthrough (screen recording, < 5 min)

---

## L4 — Ecosystem

**Goal:** Multiple users, multiple agents, marketplace.

### New features
- [ ] **Multi-user** — isolated vaults per user
- [ ] **Agent task marketplace** — publish and subscribe to agent capabilities
- [ ] **Forest public dashboard** — shareable security posture view
- [ ] **cus-core public rubric library** — community-contributed grading rubrics
- [ ] **API for external integrations** — webhooks, third-party agent connections

---

## L5 — Platform

The metaverse / virtual world / enterprise vision. Not started. Requires L4 complete + funding.
See long-term vision notes in docs/VISION.md when ready.

---

## Session System

Every working session follows this structure. See `docs/sessions/` for templates.

```
SESSION START
  1. Read last HANDOFF.md
  2. Fill in SESSION_AGENDA.md (what we're doing today)
  3. Work

SESSION END
  1. Run test_live.py — confirm green
  2. Commit all changes
  3. Update HANDOFF.md
  4. Write SESSION_JOURNAL.md entry (what happened, decisions made, blockers)
  5. Update ROADMAP.md level checklist if items completed
```

Templates: `docs/sessions/AGENDA_TEMPLATE.md`, `docs/sessions/JOURNAL_TEMPLATE.md`

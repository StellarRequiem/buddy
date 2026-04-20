# buddy

**Local-first personal AI assistant with verifiable trust.**

buddy runs entirely on your hardware. Every response is automatically graded for quality using [cus-core](https://github.com/StellarRequiem/cus-core). When local quality isn't enough, it escalates to Claude Opus 4.7 — transparently, automatically, and only when it's worth it.

Part of the [StellarRequiem Stack](https://github.com/StellarRequiem) alongside [forest](https://github.com/StellarRequiem/forest) (blue-team security swarm) and [cus-core](https://github.com/StellarRequiem/cus-core) (grading engine).

---

## What it does

- **Local-first routing** — qwen2.5:14b runs on your machine, free, private, no API call
- **Smart escalation** — scores responses with phi4-mini; escalates to Opus 4.7 when quality isn't enough
- **Verifiable grading** — every response gets a score breakdown; Haiku shows its reasoning via extended thinking
- **Expected-failure demos** — proves the model refused correctly, with a graded rubric on the refusal quality
- **Persistent memory** — SQLite session history + ChromaDB vector store, survives restarts
- **Forest integration** — blue-team security swarm proxied through the UI
- **Runtime control** — test mode frees 9GB RAM in one API call, no restart needed

---

## Architecture

```
User → POST /chat
         │
         ▼
    route() in router.py
         │
         ├─ force_frontier=True ──────────────────┐
         ├─ keyword match ("write code", etc.) ───┤
         │                                        ▼
         ├─ try qwen2.5:14b (local, 9GB)    Opus 4.7 (API)
         │   └─ fallback: phi4-mini (2.5GB)       │
         │         │                              │
         │         ▼                              ▼
         │   phi4-mini grades          Haiku grades (extended thinking)
         │   score < 60? ─── yes ──► escalate to Opus
         │         │ no
         │         ▼
         └─ return local response + grade

Memory:  SQLite (history/facts) + ChromaDB (vectors, quality-gated at score ≥ 70)
Forest:  GET /forest/status proxies to blue-team API at :7438
```

---

## Quickstart

**Prerequisites:** macOS Apple Silicon, [Ollama](https://ollama.com/download), Python 3.11+, [uv](https://docs.astral.sh/uv/), Anthropic API key

```bash
# 1. Clone
git clone https://github.com/StellarRequiem/buddy.git && cd buddy

# 2. Install (automated)
scripts/setup.sh

# 3. Start
python -m buddy.main

# 4. Open
open http://localhost:7437

# 5. Verify (in a second terminal)
python test_live.py
```

Full install guide: [docs/install/INSTALL.md](docs/install/INSTALL.md)

---

## Demo

Five demo beats that show the full system in ~10 minutes:

| Beat | What it shows |
|------|--------------|
| 1. Local routing | qwen2.5:14b answers locally, grade appears automatically |
| 2. Keyword escalation | "write code" → Opus 4.7 fires, grade 95–100/100 |
| 3. Extended thinking | Expand grade panel → Haiku's full reasoning visible |
| 4. Expected-failure | Phishing prompt → Opus refuses → 100/100 refusal score |
| 5. Test mode | One API call frees 9GB RAM, no restart |

Full script with expected output: [docs/demos/DEMO.md](docs/demos/DEMO.md)

```bash
scripts/demo.sh    # starts server + opens browser + runs test suite
```

---

## API

| Endpoint | Purpose |
|----------|---------|
| `POST /chat` | `{message, session_id?, force_frontier?}` → response + grade |
| `GET /admin/status` | `{test_mode, local_model, ollama_loaded[]}` |
| `POST /admin/test-mode` | `{"enabled": true}` — evict 9GB model, warm phi4-mini |
| `GET /forest/status` | Blue-team swarm status (or "paused" in test mode) |
| `POST /demo/run` | `{"scenario_id": "phishing"}` — expected-failure demo |
| `GET /health` | `{"status": "ok"}` |

Full docs at `/api/docs` when server is running.

---

## RAM requirements

| Mode | RAM |
|------|-----|
| Full (qwen2.5:14b loaded) | 12–13 GB |
| Test mode (phi4-mini only) | 4–5 GB |
| API-only (no local models) | 2 GB |

Recommended: **16 GB unified memory** (M4 Mac Mini or equivalent)

---

## Repo structure

```
buddy/
├── buddy/
│   ├── api/          # FastAPI routers (chat, admin, forest, demo, siri, tasks, memory)
│   ├── llm/          # router.py (routing + grading), prompts.py
│   ├── memory/       # store.py (SQLite), vectors.py (ChromaDB)
│   └── ui/           # static/app.js, templates/index.html
├── docs/
│   ├── demos/        # DEMO.md — live demo script
│   ├── install/      # INSTALL.md — step-by-step setup
│   └── sessions/     # daily agenda + journal system
├── scripts/          # setup.sh, demo.sh, buddy-start.sh
├── test_live.py      # 9-group test suite, 17 checks
├── ROADMAP.md        # L0–L5 levelled roadmap
└── HANDOFF.md        # session state for context handoff
```

---

## Related projects

- **[forest](https://github.com/StellarRequiem/forest)** — LangGraph blue-team security swarm, powers `/forest/status`
- **[cus-core](https://github.com/StellarRequiem/cus-core)** — YAML-driven grading engine used for all response scoring

---

## License

MIT

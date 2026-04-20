# Buddy — Session Handoff Document
_Last updated: 2026-04-19_

---

## 1. What buddy is

**buddy** is a local-first personal assistant running on Alexander's Mac Mini M4 (16GB).  
FastAPI server on `http://localhost:7437`. Chat UI at that URL.

Core stack:
- **Local LLM**: qwen2.5:14b (primary, 9GB) → phi4-mini fallback (2.5GB)
- **Frontier LLM**: Claude Opus 4.7 (API, escalation only)
- **Grader**: Claude Haiku 4.5 with extended thinking (scores every Opus response)
- **Local grader**: phi4-mini via cus-core OllamaGrader (scores local responses, drives escalation)
- **Memory**: SQLite (`~/BuddyVault/buddy.db`) + ChromaDB vectors (`~/BuddyVault/chroma/`) + nomic-embed-text
- **Forest monitoring**: Forest blue-team swarm at port 7438, proxied through buddy at `/forest/status`

---

## 2. Project layout

```
~/Projects/buddy/
├── buddy/
│   ├── main.py                  # FastAPI app, all routers registered here
│   ├── config.py                # Settings (pydantic-settings, reads .env)
│   ├── api/
│   │   ├── chat.py              # POST /chat, GET /sessions, GET /history/:id
│   │   ├── admin.py             # POST /admin/test-mode, GET /admin/status  ← NEW
│   │   ├── forest.py            # GET /forest/status (proxy to :7438, test-mode aware)
│   │   ├── demo.py              # GET /demo/tasks, POST /demo/run (expected-failure demo)
│   │   ├── memory.py            # GET /memory/search, GET /memory/stats, GET /memory/facts
│   │   ├── tasks.py             # GET/POST/PUT /tasks
│   │   └── siri.py              # GET /siri/ping, GET /siri/status, POST /siri/task
│   ├── llm/
│   │   ├── router.py            # route(), opus_chat(), local_chat(), grading, _GRADE_EXECUTOR
│   │   └── prompts.py           # BUDDY_SYSTEM_PROMPT, build_chat_prompt()
│   ├── memory/
│   │   ├── store.py             # SQLite: append_message, get_history, upsert_fact, list_sessions
│   │   └── vectors.py           # ChromaDB: upsert_memory, search_memory (graceful degradation)
│   └── ui/
│       ├── static/app.js        # Frontend JS — chat, forest, demo, tasks, memory tabs
│       └── templates/index.html # Single-page UI
├── .env                         # ANTHROPIC_API_KEY, LOCAL_MODEL, etc.
├── test_live.py                 # 9-group observable test suite (17 checks)
└── HANDOFF.md                   # This file
```

---

## 3. How to start / stop buddy

```bash
cd ~/Projects/buddy

# Start (foreground, recommended for dev)
.venv/bin/python -m buddy.main

# Start (background)
nohup .venv/bin/python -m buddy.main &>/tmp/buddy.log &

# Stop
pkill -f "buddy.main"

# Run tests (takes ~3-5 min — qwen2.5:14b cold start)
.venv/bin/python test_live.py
```

buddy is NOT managed by launchd right now (launchd plist exists but was unloaded during this session to allow manual dev). If you want launchd auto-start back:
```bash
launchctl load ~/Library/LaunchAgents/com.stellarrequiem.buddy.plist
```

---

## 4. Current git state

**All changes are uncommitted.** The following files are modified vs last commit (`839e8fa`):

| File | What changed |
|------|-------------|
| `buddy/api/chat.py` | Added asyncio executor for search_memory, SEARCH regex stripper |
| `buddy/api/forest.py` | Added test-mode short-circuit (returns "paused" status) |
| `buddy/config.py` | Added `test_mode: bool = False` field |
| `buddy/llm/prompts.py` | Removed SEARCH directive, fixed phi4-mini prompt pollution |
| `buddy/llm/router.py` | Added ThreadPoolExecutor, keyword short-circuit, model availability check, 60s grade timeout |
| `buddy/llm/vectors.py` | Added graceful degradation, 10s embed timeout |
| `buddy/main.py` | Registered admin_router |
| `buddy/ui/static/app.js` | Removed 30s auto-poll, added scheduleForestScan(), added "paused" state |
| `buddy/api/admin.py` | **NEW FILE** — test-mode toggle, model eviction |

**Next commit should include all of these.** Suggested message:
```
feat: test-mode toggle, RAM management, Forest on-demand scan, async fixes
```

---

## 5. Key endpoints

| Endpoint | Purpose |
|----------|---------|
| `GET /health` | `{"status":"ok","vault":"..."}` |
| `POST /chat` | `{message, session_id?, force_frontier?}` → response + grade |
| `GET /chat/sessions` | List all session IDs |
| `GET /chat/history/:id` | Last N messages in a session |
| `GET /admin/status` | `{test_mode, local_model, ollama_loaded[]}` |
| `POST /admin/test-mode` | `{"enabled": true/false}` → evicts qwen2.5:14b or reloads it |
| `GET /forest/status` | Proxy to :7438; returns `{"status":"paused"}` in test mode |
| `GET /demo/tasks` | List 4 expected-failure scenarios |
| `POST /demo/run` | `{"scenario_id": "phishing"}` → runs scenario + Haiku grading |
| `GET /siri/ping` | `buddy online` |
| `POST /siri/task` | `{"title":"..."}` → adds task |

---

## 6. Routing logic (router.py)

```
route(messages, force_frontier, session_id)
  │
  ├─ force_frontier=True AND api_key → opus_chat() directly
  │
  ├─ message matches escalation_keywords AND api_key → opus_chat(), escalated=True
  │   (keywords: "summarize this document", "write code", "debug", "explain in detail")
  │
  ├─ try qwen2.5:14b (local, 9GB, ~30-150s depending on RAM state)
  │   └─ if unavailable → try phi4-mini (local, 2.5GB, ~5s)
  │       └─ if unavailable → opus_chat() escalation
  │
  ├─ grade local response with phi4-mini via cus-core (async, 60s timeout)
  │
  └─ score < 60 OR keywords match → opus_chat(), escalated=True
     else → return local result with grade
```

**GradeDetail** returned on every response:
- `composite_score` (0-100), `passed` (≥65), `rubrics[]`, `thinking_trace` (Haiku only), `escalated`

---

## 7. Test mode

```bash
# Enable — evicts qwen2.5:14b (frees 9GB), warms phi4-mini, pauses Forest scan
curl -X POST http://localhost:7437/admin/test-mode \
  -H "Content-Type: application/json" -d '{"enabled": true}'

# Disable — reloads qwen2.5:14b
curl -X POST http://localhost:7437/admin/test-mode \
  -H "Content-Type: application/json" -d '{"enabled": false}'

# Status
curl http://localhost:7437/admin/status
```

When test mode is on:
- qwen2.5:14b evicted from RAM (via `keep_alive: 0`)
- phi4-mini warmed in RAM
- `/forest/status` returns `{"status":"paused"}` without hitting :7438
- Forest tab UI shows "🔬 Test mode active — monitoring paused"

---

## 8. Forest scanning

The 30s auto-poll **has been removed**. Forest now scans:
1. **On-demand**: clicking the Forest tab triggers one scan
2. **Manual**: clicking the Refresh button in the Forest tab
3. **Automated**: call `scheduleForestScan(ms)` in browser console to set an interval, `stopForestScan()` to cancel

The Forest blue-team API runs at `http://localhost:7438` (forest-blue-team-guardian project).
Start it with: `cd ~/forest-blue-team-guardian && scripts/forest-api-start.sh`

---

## 9. Memory system

**SQLite** (`~/BuddyVault/buddy.db`):
- `messages` table: full chat history by session_id
- `facts` table: key-value pairs the model infers (via `REMEMBER: key=value` directive)
- `grades` table: grade log per response

**ChromaDB** (`~/BuddyVault/chroma/`):
- Vector embeddings via nomic-embed-text (274MB Ollama model)
- Only embeds exchanges where grade ≥ 70 (quality gate) and message length > 20 chars
- Searched at chat start: top-3 relevant past exchanges injected into system prompt
- Gracefully degrades to empty list if embed model is unavailable

**Memory eviction trigger**: `keep_alive: 0` sent to Ollama API. Works on both models.

---

## 10. Ollama models installed (native, not Docker)

| Model | Size | Role |
|-------|------|------|
| qwen2.5:14b | 9.0 GB | Primary local LLM |
| phi4-mini:latest | 2.5 GB | Fallback LLM + local grader |
| nomic-embed-text:latest | 274 MB | Vector embeddings |
| qwen2.5:3b | 1.9 GB | Available, not used by buddy |

---

## 11. RAM situation (M4 Mac Mini, 16GB)

**After this session's fixes:**

| Component | RAM |
|-----------|-----|
| Docker VM | 2.5 GB cap (1.5 GB actual at idle) |
| qwen2.5:14b (when loaded) | ~9 GB |
| phi4-mini (when loaded) | ~2.5 GB |
| macOS + system | ~3–4 GB |
| **Budget at idle** | ~360 MB free |

**Docker Desktop settings (via API — persists across restarts):**
- Memory: 2560 MiB
- Swap: 512 MiB
- Kubernetes: **disabled**

**Running Docker containers (auto-start on Docker restart):**
- forest-swarm, forest-decepticon, forest-postgres, forest-redis

**NOT auto-starting (restart=no):**
- decepticon-* (neo4j, litellm, langgraph, target, sandbox, postgres)
- dify-* (all)
- open-webui
- forest-ollama (duplicate — we use native Ollama)
- lucid_kalam (MouseMates test container, stale)

To start decepticon/dify when needed:
```bash
# Decepticon red-team
docker start decepticon-neo4j decepticon-litellm decepticon-langgraph decepticon-target decepticon-postgres

# Dify
docker start dify-api-1 dify-worker-1 dify-worker_beat-1 dify-web-1 dify-db_postgres-1 dify-redis-1 dify-weaviate-1 dify-nginx-1 dify-plugin_daemon-1 dify-sandbox-1 dify-ssrf_proxy-1

# Open-WebUI
docker start open-webui
```

---

## 12. cus-core integration

**cus-core** is the grading framework at `~/Projects/cus-core`.  
buddy imports:
```python
from cus_core.models import Rubric, Stage, StageName, Task
from cus_core.grader import Grader, OllamaGrader
```

The `OllamaGrader` is synchronous and blocks. It runs in a `ThreadPoolExecutor` (`_GRADE_EXECUTOR`, 2 workers) to avoid blocking the uvicorn event loop. Always use `_local_grade_async()` or `loop.run_in_executor()` for any cus-core calls.

---

## 13. .env file

```
ANTHROPIC_API_KEY=sk-ant-api03-...   # Opus + Haiku access
LOCAL_MODEL=qwen2.5:14b
FALLBACK_LOCAL_MODEL=phi4-mini
OLLAMA_HOST=http://127.0.0.1:11434
PORT=7437
DEBUG=false
```

---

## 14. Test suite results (last run: 2026-04-19)

17/17 checks passing. Key timings with qwen2.5:14b warm:
- Test 2 (local routing, qwen2.5:14b): **33s**, grade 86.8/100
- Test 3 (keyword escalation → Opus): **9s** (keyword short-circuit, no local call)
- Test 4 (force_frontier → Opus): **5.5s**, grade 100/100
- Test 5 (phishing expected-failure): **15s**, grade 100/100

---

## 15. Known issues / next steps

See `ROADMAP.md` for the full levelled roadmap and `docs/sessions/2026-04-20_agenda.md` for tomorrow's session plan.

### Immediate (L0 → L1, next session)
- [ ] **Commit all uncommitted changes** — first action next session (see section 4)
- [ ] **Tag v0.1.0-foundation** after commit
- [ ] **README.md rewrite** — root README needs to reflect current state
- [ ] **scripts/setup.sh** — one-command installer
- [ ] **DEMO.md** — written at `docs/demos/DEMO.md` ✅
- [ ] **INSTALL.md** — written at `docs/install/INSTALL.md` ✅
- [ ] **Test mode UI banner** — "🔬 TEST MODE" in chat header (app.js + CSS)

### L2 and beyond
- See `ROADMAP.md` for full breakdown

---

## 16. Quick reference: common commands

```bash
# Start buddy
cd ~/Projects/buddy && .venv/bin/python -m buddy.main

# Run tests
cd ~/Projects/buddy && .venv/bin/python test_live.py

# Enable test mode (free 9GB RAM)
curl -X POST http://localhost:7437/admin/test-mode -H "Content-Type: application/json" -d '{"enabled":true}'

# Check what's loaded in Ollama
curl http://localhost:11434/api/ps | python3 -m json.tool

# Evict a model from RAM immediately
curl -X POST http://localhost:11434/api/generate -d '{"model":"qwen2.5:14b","keep_alive":0}'

# Check Docker VM memory setting
curl -s --unix-socket ~/Library/Containers/com.docker.docker/Data/backend.sock \
  http://localhost/app/settings/flat | python3 -c "import sys,json; d=json.load(sys.stdin); print('VM:',d.get('memoryMiB'),'MiB | K8s:',d.get('kubernetesEnabled'))"

# Update Docker VM memory (no restart required for settings; restart VM to apply)
curl -X POST --unix-socket ~/Library/Containers/com.docker.docker/Data/backend.sock \
  http://localhost/app/settings -H "Content-Type: application/json" \
  -d '{"memoryMiB": 4096, "kubernetesEnabled": false}'

# Forest API status
curl http://localhost:7438/forest/status | python3 -m json.tool

# Check memory pressure
memory_pressure | grep -E "Pages free|pressure"
```

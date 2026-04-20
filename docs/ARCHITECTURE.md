# buddy — Architecture

## Overview

```
┌─────────────────────────────────────────────────────────┐
│                        Browser UI                        │
│          http://localhost:7437  (app.js + htmx)          │
└──────────────────────┬──────────────────────────────────┘
                       │ HTTP / SSE
┌──────────────────────▼──────────────────────────────────┐
│                    FastAPI Server                         │
│                    buddy/main.py                          │
│                                                           │
│  /chat        /chat/stream    /admin      /forest/status  │
│  /demo/run    /siri/*         /tasks      /memory/*       │
└──────┬────────────┬───────────────────────────────┬──────┘
       │            │                               │
┌──────▼──────┐ ┌───▼───────────────┐  ┌───────────▼──────┐
│  router.py  │ │   memory/store.py  │  │  Forest API :7438 │
│             │ │   (SQLite)         │  │  (blue-team swarm)│
│  route()    │ │                    │  └──────────────────┘
│     │       │ │  memory/vectors.py │
│     ├─ local│ │  (ChromaDB)        │
│     │  LLM  │ └───────────────────┘
│     │       │
│     └─ Opus │
│       4.7   │
└──────┬──────┘
       │
┌──────▼──────────────────────────────────────────────────┐
│                    Ollama (local)                         │
│                 http://127.0.0.1:11434                   │
│                                                           │
│  qwen2.5:14b  (9 GB)   — primary local LLM               │
│  phi4-mini    (2.5 GB) — fallback + local grader         │
│  nomic-embed-text (274MB) — vector embeddings            │
└─────────────────────────────────────────────────────────┘
```

---

## Request lifecycle — POST /chat/stream

```
1. Client sends:  POST /chat/stream {message, session_id, force_frontier}

2. chat.py
   ├── get_history(session_id)         → last 20 turns from SQLite
   ├── search_memory(message, n=3)     → top-3 relevant chunks from ChromaDB
   └── build_chat_prompt(...)          → assembles [system, history, user] messages

3. router.route()
   │
   ├─ force_frontier=True  ──────────────────────┐
   ├─ keyword match?  ───────────────────────────┤
   │                                             ▼
   │                                      opus_chat()
   │                                      Anthropic SDK
   │                                      → response text
   │                                      → _grade_with_thinking()
   │                                        (Haiku + extended thinking)
   │                                        → GradeDetail + thinking trace
   │
   ├─ _is_model_available(qwen2.5:14b)?
   │   yes → local_chat_stream()        (httpx POST /api/chat, stream=True)
   │   no  → local_chat_stream(phi4-mini)
   │
   └─ (non-streaming path: grade local response)
       _local_grade_async()             → OllamaGrader in ThreadPoolExecutor
       score < 60? → escalate to Opus

4. SSE response stream
   data: {"token": "Hello"}
   data: {"token": " world"}
   ...
   data: {"done": true, "session_id": "...", "model": "qwen2.5:14b"}

5. Persistence (after stream completes)
   ├── append_message(session_id, "user", ...)
   ├── append_message(session_id, "assistant", ..., model=...)
   └── upsert_memory(...)  if grade ≥ 70 and message not trivial
```

---

## Routing decision tree

```
route(messages, force_frontier, session_id)
  │
  ├─ force_frontier=True AND api_key
  │    → opus_chat()  [Anthropic SDK, ~5s]
  │
  ├─ message contains escalation keyword AND api_key
  │    keywords: "write code", "debug", "summarize this document", "explain in detail"
  │    → opus_chat(), escalated=True  [skips local entirely — saves ~30s]
  │
  ├─ qwen2.5:14b available?
  │    → local_chat(qwen2.5:14b)  [Ollama, ~30s warm / ~120s cold]
  │    else → local_chat(phi4-mini)  [Ollama, ~5s warm]
  │
  ├─ grade local response
  │    _local_grade_async(phi4-mini, timeout=60s)
  │    runs in ThreadPoolExecutor to avoid blocking event loop
  │
  └─ composite_score < 60?
       → opus_chat(), escalated=True
       else → return local result + GradeDetail
```

---

## Memory system

```
SQLite  ~/BuddyVault/buddy.db
  ├── messages     (session_id, role, content, model, timestamp)
  ├── facts        (key, value, source)   ← REMEMBER: key=value directives
  └── grades       (session_id, call_type, model, score, passed, detail)

ChromaDB  ~/BuddyVault/chroma/
  └── collection: "buddy_memory"
        ├── embedding: nomic-embed-text (274MB, runs via Ollama)
        ├── upserted: only when grade ≥ 70 AND message length > 20 chars
        └── searched: top-3 at chat start, injected into system prompt
```

---

## Grading

Two graders, used in different contexts:

### Local grader (phi4-mini + cus-core)
- Used for: local model responses
- Purpose: decide whether to escalate to Opus
- Cost: free (local)
- Speed: ~5–20s (runs in ThreadPoolExecutor)
- Rubric: relevance (40%), accuracy (35%), conciseness (15%), safety (10%)
- Pass threshold: 65/100

### Frontier grader (Haiku + extended thinking)
- Used for: Opus 4.7 responses + expected-failure demos
- Purpose: quality assurance + demo showcase
- Cost: ~$0.001/response (Haiku is cheap)
- Speed: ~5–10s
- Same rubric as above + thinking trace exposed in UI
- Extended thinking budget: 1024 tokens (configurable via `grader_thinking_budget`)

---

## Test mode

`POST /admin/test-mode {"enabled": true}` triggers:
1. Ollama `keep_alive: 0` on qwen2.5:14b → evicts 9GB from RAM immediately
2. Ollama pre-warm on phi4-mini → loads 2.5GB, ready for next request
3. `_test_mode = True` in admin.py runtime state
4. `/forest/status` returns `{"status": "paused"}` without hitting :7438
5. UI shows amber TEST MODE banner

`{"enabled": false}` reverses: reloads qwen2.5:14b, clears flag, resumes Forest.

---

## cus-core integration

buddy uses [cus-core](https://github.com/StellarRequiem/cus-core) for all grading.

```python
from cus_core.models import Rubric, Stage, StageName, Task
from cus_core.grader import Grader, OllamaGrader

# OllamaGrader is synchronous — always run in ThreadPoolExecutor
result = await loop.run_in_executor(
    _GRADE_EXECUTOR,
    _local_grade,
    response_text,
    user_message,
)
```

The `_GRADE_EXECUTOR` is a `ThreadPoolExecutor(max_workers=2)` shared across the app.
**Never call OllamaGrader directly from an async context** — it blocks the event loop.

---

## Forest integration

Forest blue-team swarm runs as a separate process at `http://127.0.0.1:7438`.
buddy proxies it through `/forest/status` with:
- 2s timeout (Forest is local, should be fast)
- Graceful offline state (returns `{"status": "offline"}` if unreachable)
- Test-mode short-circuit (returns `{"status": "paused"}` when test mode active)

Start Forest: `cd ~/forest-blue-team-guardian && scripts/forest-api-start.sh`

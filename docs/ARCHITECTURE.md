# buddy — Architecture

This document is for engineers who want to understand the internals well enough to contribute, extend, or audit buddy. It covers every major subsystem, the design decisions behind each, and the exact contracts between them.

---

## 1. System overview

buddy is a FastAPI application that wraps an agentic loop around locally-running Ollama models. On each request it builds a message list, streams it to the model with tool schemas attached, executes whatever tools the model requests (in parallel), injects the results, and repeats until the model responds with plain text. The final response is graded by cus-core, persisted to SQLite, and optionally embedded into ChromaDB for semantic recall.

**Components:**

- `buddy/main.py` — FastAPI app, lifespan hooks, API key middleware, shell execute endpoint
- `buddy/config.py` — Pydantic Settings, all env vars, path defaults
- `buddy/llm/agent.py` — the agentic loop (streaming tool calls, think-tag parser, parallel execution, context pruning)
- `buddy/llm/router.py` — model routing (local vs Opus 4.7 escalation), grading wrappers
- `buddy/llm/prompts.py` — system prompt builder, memory injection
- `buddy/tools/tool_registry.py` — ToolDef dataclass, all 25 tool definitions and executors, `execute_tool` dispatch
- `buddy/tools/filesystem.py` — path resolution and allowed-path enforcement
- `buddy/tools/shell.py` — CSRF token gate for shell commands
- `buddy/tools/plugin_loader.py` — runtime plugin discovery
- `buddy/memory/db.py` — SQLite schema, versioned migration runner
- `buddy/memory/store.py` — all SQLite read/write helpers
- `buddy/memory/vectors.py` — ChromaDB wrapper (embed + upsert + query)
- `buddy/api/chat.py` — `/chat` and `/chat/stream` endpoints
- `buddy/api/admin.py` — `/admin/*` endpoints, test-mode state, tool toggle
- `buddy/api/alerts.py` — Forest SSE alert poller and `/alerts/stream`
- `buddy/api/forest.py` — `/forest/status` proxy
- `buddy/api/memory.py` — `/memory/*` endpoints
- `buddy/api/demo.py` — `/demo/*` expected-failure demo endpoints

---

## 2. The agentic loop

**File:** `buddy/llm/agent.py`

The core loop in `run_agent_loop()` is an async generator that yields SSE-ready dicts. Here is the full lifecycle of a single iteration:

### Step 1: Stream the model with tools

`_ollama_stream_with_tools()` POSTs to `POST /api/chat` with `"stream": true` and the full `TOOL_SCHEMAS` list. It yields `("thinking", content)` tuples for every intermediate content token, and a single `("tool_calls", list)` tuple from the final `done` chunk if the model chose to call tools, or nothing if it responded with text.

Streaming is used here even though tool-call decisions arrive at the `done` chunk, because qwen3 emits reasoning tokens (`<think>…</think>`) before making tool decisions. Streaming those tokens live gives the UI real-time visibility into model reasoning.

### Step 2: Think-tag parser

While content tokens arrive, they are accumulated in `_think_buf` and passed through `_emit_think_chunk()`. This function is a two-state machine:

- **State: outside `<think>`** — emit content as `("token", text)` events up to 6 characters before the end of the buffer (lookahead for a split `<think>` tag boundary).
- **State: inside `<think>`** — emit content as `("thinking_trace", text)` events, keeping 8 characters in the buffer as lookahead for `</think>`.

The 6/8-char lookaheads exist because network chunks can split across tag boundaries. Without them, a chunk ending with `<thi` followed by `nk>` would be incorrectly emitted as plain token text. The function returns `(remaining_buf, new_in_think, events)` so state threads through successive calls.

`token` events contribute to the final response text. `thinking_trace` events are forwarded to the UI as collapsible reasoning but are not part of the stored answer.

### Step 3: Parallel tool execution

When the model emits `tool_calls[]`, all calls in the batch are dispatched simultaneously via `asyncio.gather()`. Each call goes through `_execute_tool_call()`, which:

1. Parses the `arguments` field (handles dict, JSON string, or empty)
2. Calls `execute_tool(name, args)` from the tool registry
3. Records the call in `tool_calls` SQLite table (name, success, latency_ms, args summary, result preview)
4. Returns `(name, args, result_text)`

Tool results are capped at `_MAX_TOOL_RESULT` (2,000 chars) before being injected into context as `role: tool` messages. This prevents large file reads or web responses from bloating the context window.

### Step 4: Shell gate

If any tool result starts with `[SHELL_GATE_PENDING]`, the loop halts immediately and yields a `shell_gate` event. The payload is a dict containing the command, a one-time CSRF token, and a human-readable confirmation message. The loop does not continue until the user explicitly approves via `POST /shell/execute` with the correct token.

### Step 5: Context pruning

After each iteration, `_prune_tool_messages()` checks whether tool-role messages have exceeded `_MAX_TOOL_MESSAGES` (12). If so, the oldest half are dropped. System and user/assistant messages are never pruned. This keeps the context window bounded across long sessions.

### Step 6: Hard timeout and max iterations

A `time.monotonic()` deadline is set at loop entry (`agent_timeout_seconds` from config). If the deadline passes at the top of any iteration, a timeout token is yielded and the loop breaks. If `max_agent_iterations` is reached, a final `_ollama_stream_final()` call is made with no tool schemas to synthesize a summary response.

### Non-streaming collect

`run_agent_collect()` wraps `run_agent_loop()` and accumulates all `token` events into a string. It is used by the non-streaming `POST /chat` endpoint. Thinking traces are discarded in the collected output.

---

## 3. Tool registry design

**File:** `buddy/tools/tool_registry.py`

### ToolDef

```python
@dataclass
class ToolDef:
    schema: dict            # OpenAI-compatible tool object
    execute: Callable[..., Awaitable[str]]
    human_gate: bool = False
```

Every tool is a `ToolDef`. The `schema` is a standard OpenAI function-calling object with `type`, `function.name`, `function.description`, and `function.parameters`. This schema is passed verbatim in the `tools` array to Ollama, so the model sees exactly the same structure as it would against the OpenAI API.

### TOOL_SCHEMAS and _TOOL_MAP

```python
_TOOL_MAP: dict[str, ToolDef] = {t.schema["function"]["name"]: t for t in TOOLS}
TOOL_SCHEMAS: list[dict] = [t.schema for t in TOOLS]
```

`TOOL_SCHEMAS` is the list sent to Ollama on every request. `_TOOL_MAP` is the dispatch table used by `execute_tool`.

### execute_tool

```python
async def execute_tool(name: str, args: dict) -> str:
    if name in cfg.disabled_tools:
        return f"[Tool '{name}' is disabled by configuration (DISABLED_TOOLS).]"
    tool = _TOOL_MAP.get(name)
    if not tool:
        available = ", ".join(k for k in _TOOL_MAP.keys() if k not in cfg.disabled_tools)
        return f"[Unknown tool '{name}'. Available: {available}]"
    return await tool.execute(args)
```

Dispatch is a single dict lookup. The disabled-tools check happens here, making runtime toggles take effect immediately without any restart or reload.

### human_gate concept

`human_gate=True` on `shell_execute` is a marker that tells the consuming code (the agent loop) that this tool's result should pause execution. The actual gating is implemented in the agent loop via the `[SHELL_GATE_PENDING]` sentinel prefix: `_exec_shell_execute` returns the sentinel rather than executing anything. The registry flag is surfaced in the `GET /memory/tools` endpoint so the UI can mark human-gated tools visually.

---

## 4. qwen3 thinking tokens

qwen3 wraps chain-of-thought reasoning in `<think>…</think>` before either calling tools or producing a final answer. This is valuable: the user sees the model reason through a problem live, before any tool is called.

The challenge is that HTTP/1.1 chunked streaming splits at arbitrary byte boundaries. A `<think>` tag spanning two chunks arrives as `<thi` + `nk>`. The state machine in `_emit_think_chunk()` handles this with lookahead buffers:

- When outside a think block: keep the last 6 bytes in the buffer (length of `<think>` minus 1), emit everything before that as `token` events.
- When inside a think block: keep the last 8 bytes in the buffer (length of `</think>` minus 1), emit everything before that as `thinking_trace` events.

The function is called on every incoming content token. State (`_in_think`, `_think_buf`) persists across calls within a single agent iteration. After the stream ends, any remaining buffer is flushed with the appropriate event type.

qwen2.5 emits no think tags, so `_emit_think_chunk` is a transparent pass-through for that model — every byte is emitted as `token` events.

---

## 5. cus-core grading

**File:** `buddy/llm/router.py`

Every response is graded against a four-rubric schema:

| Rubric | Weight | Scoring | Question |
|---|---|---|---|
| relevance | 40% | numeric 0-100 | How directly does the response address what the user asked? |
| accuracy | 35% | numeric 0-100 | How factually correct and well-reasoned is the response? |
| conciseness | 15% | numeric 0-100 | Is the response appropriately concise without losing clarity? |
| safety | 10% | binary (0 or 100) | Does the response avoid harmful, deceptive, or inappropriate content? |

The composite score is the weighted sum. The pass threshold is **65.0**.

### For agent (local) responses

`_local_grade()` uses `cus_core.grader.OllamaGrader` backed by `phi4-mini`. This is synchronous and runs in `_GRADE_EXECUTOR` (a 2-thread `ThreadPoolExecutor`) to avoid blocking the uvicorn event loop. The async wrapper `_local_grade_async()` adds a 45s timeout and returns `None` on failure (treated as pass — avoids spurious escalation on grader outages).

### For Opus 4.7 responses

`_grade_with_thinking()` calls the Anthropic API with `claude-haiku-4-5` and `thinking={"type": "enabled", "budget_tokens": 1024}`. The response contains two content blocks: a `thinking` block with Haiku's full reasoning trace, and a `text` block with the JSON scores. The thinking trace is stored in `GradeDetail.thinking_trace` and forwarded to the UI so the user sees Haiku reason through each rubric criterion in real time.

Falls back to Haiku without extended thinking if the budget is set to 0 or if the thinking API call fails.

### Memory quality gate

The score is also used to filter what gets stored in ChromaDB. Only exchanges where the grade is 70.0 or above (slightly higher than the pass threshold) are written to vector memory. Trivial exchanges (message under 20 chars, or response under 50 chars) skip grading and memory storage entirely.

---

## 6. Forest integration

Forest is a separate multi-agent blue-team security swarm running on port 7438. buddy connects to it via three mechanisms:

### Agent tools

`forest_status`, `forest_incidents`, and `forest_scan` in the tool registry call `_fetch_forest_status()`, which GETs `{FOREST_HOST}/forest/status` with a 4-second timeout. All three return empty or graceful strings when Forest is offline or in test mode — the agent loop continues normally.

`forest_scan` additionally tries `POST {FOREST_HOST}/forest/scan` before reading status. If that endpoint doesn't exist (Forest may not expose it), the POST is silently swallowed.

### Passive alert SSE stream

`buddy/api/alerts.py` runs `start_alert_poller()` as a background asyncio task (started in the `lifespan` hook in `main.py`). It polls Forest every `forest_alert_interval` seconds (default 30). New incidents whose severity is in `forest_alert_severities` (default `CRITICAL`, `ATTACK`) are deduped by `(timestamp, threat_type)` key and broadcast to all connected SSE clients via per-client `asyncio.Queue` objects.

The `GET /alerts/stream` endpoint returns an SSE `StreamingResponse`. Each browser tab connects once on load and keeps the connection open. A keepalive comment (`: keepalive`) is sent every 20 seconds to prevent proxy timeouts. `EventSource` on the client auto-reconnects on server restart.

### /forest/status proxy

`buddy/api/forest.py` proxies `GET /forest/status` to Forest with a 2-second timeout. It returns HTTP 200 with `status: offline` or `status: paused` on failures rather than a 5xx, so buddy's UI can show a degraded state without alarming uptime monitors.

---

## 7. Routing logic

**File:** `buddy/llm/router.py` and `buddy/api/chat.py`

The routing decision happens in `chat.py` before the agent loop fires:

```
use_frontier = (force_frontier AND api_key set)
               OR (api_key set AND message matches escalation_keywords)
```

If `use_frontier` is true, `opus_chat()` is called directly (bypassing the agent loop). This is the legacy non-agentic Opus path — useful for pure Q&A escalation without tool use.

If `use_frontier` is false and `cfg.use_agent_loop` is true (the default), `run_agent_loop()` fires with `conductor_model` (qwen2.5 or qwen3). The agent loop handles all tool execution and streams results.

If `cfg.use_agent_loop` is false, the legacy `route()` function in `router.py` is called. This tries local models first, grades the result with phi4-mini, and escalates to Opus if the score falls below `escalation_confidence_threshold * 100` or if the message matches escalation keywords.

The agent loop path does not use `route()` — it goes directly to the conductor model and grades the final collected response after the loop completes.

---

## 8. Memory architecture

### SQLite (buddy/memory/db.py, buddy/memory/store.py)

The schema is managed by a versioned migration system. Three migrations have been applied:

| Version | Description |
|---|---|
| 1 | Initial schema: `conversations`, `user_facts`, `tasks`, `grading_log`, indexes |
| 2 | `tool_calls` table: per-call metrics (name, success, latency_ms, args_summary, result_preview) |
| 3 | `audit_log` table: immutable action trail (ts, action, session_id, detail, source_ip) |

**conversations** — one row per message turn. Fields: `session_id`, `role`, `content`, `model`, `ts`. Indexed on `session_id`.

**user_facts** — key/value store for persistent facts. `key` is unique. Written by `remember_fact` tool and read by `get_facts()`. Also used for persisting test-mode state (`_test_mode` key).

**tasks** — task queue. `id` is a UUID. `status` is one of `queued`, `running`, `done`, `failed`.

**tool_calls** — every tool invocation logged here by `_execute_tool_call()`. Powers the `GET /admin/tool-metrics` endpoint.

**audit_log** — written on: API auth failures, shell command executions, admin mutations, tool toggles. Powers `GET /admin/audit`.

All connections use WAL journal mode (`PRAGMA journal_mode=WAL`) for concurrent read performance.

### ChromaDB (buddy/memory/vectors.py)

ChromaDB is stored at `~/BuddyVault/chroma/` with `anonymized_telemetry=False`. Embeddings are produced by `nomic-embed-text` via `POST {OLLAMA_HOST}/api/embed` with a 10-second timeout.

`upsert_memory(text)` dedupes by SHA-256 hash of the text (first 16 hex chars as the doc ID). It silently skips on embedding failure — the embed model may not be installed.

`search_memory(query, n)` embeds the query and calls `_collection().query()` with cosine distance. Returns empty list if embedding fails — graceful degradation with no crash.

Memory is searched at the start of every chat request (for messages >= 20 chars) and the top-3 results are injected into the system prompt via `build_chat_prompt()`.

### Session isolation

Each conversation has a `session_id` (UUID). History is fetched by session ID. Vector memory is shared across sessions (global semantic store). Facts are also global. The session boundary only applies to conversation history.

---

## 9. Security model

### API key middleware

`APIKeyMiddleware` in `main.py` is a `BaseHTTPMiddleware` that runs on every request. It accepts the key from `X-API-Key` header or `Authorization: Bearer <key>`. Public paths bypass it: `GET /`, `/health`, `/static/*`, `/api/docs`, `/api/openapi`. Failed auth attempts are logged to `audit_log` with the source IP.

When `API_KEY` is empty (the default), the middleware is a no-op — no key is checked. Appropriate for local-only installs.

### Admin token

`_verify_admin_token()` is a FastAPI `Depends` injected on every `/admin/*` route. It reads `X-Admin-Token` and compares it to `cfg.admin_token`. When `admin_token` is empty, the check is skipped entirely.

### Shell CSRF tokens

Shell execution is the highest-risk operation. The flow:

1. Model calls `shell_execute(command)`.
2. `_exec_shell_execute()` returns `[SHELL_GATE_PENDING] <command>` — no execution yet.
3. The agent loop detects the sentinel and calls `requires_confirmation(command)`.
4. `requires_confirmation()` checks the command against `shell_banned_patterns`, then generates a `secrets.token_hex(16)` CSRF token and stores it in `_pending_tokens: dict[str, str]`.
5. A `shell_gate` event is yielded to the client with the command and token.
6. The user sees the command and clicks Approve.
7. The UI POSTs to `/shell/execute` with the command and token.
8. `consume_pending_token()` validates the token matches the stored command and deletes it (single-use).
9. `shell.execute()` runs the command via `subprocess.run(shell=True)`.

Replaying the same token is rejected (the token is deleted on first use). A cross-origin request cannot forge a new token because tokens are generated server-side. Shell attempts with banned patterns (`rm -rf`, `sudo`, `chmod 777`, `curl | sh`, etc.) are blocked before a token is issued.

### Disabled tools

`cfg.disabled_tools` is checked at the top of `execute_tool()`. The `/admin/tools/{name}/toggle` endpoint mutates the in-process list; changes take effect on the next tool call without a restart. Changes do not persist across restarts (by design — use `DISABLED_TOOLS` env var for persistent disabling).

### allowed_read_paths

`buddy/tools/filesystem.py` resolves all read paths through `_resolve_allowed()`. Reads are permitted only within `~/BuddyVault` and the explicit allow-list in `cfg.allowed_read_paths` (defaults include `~/ForestVault` and `~/Projects/cus-core`). Write operations are always restricted to `~/BuddyVault`.

---

## 10. Database migrations

`init_db()` in `buddy/memory/db.py` is idempotent and called on every server startup. It works as follows:

1. Creates `schema_migrations(version, description, applied_at)` if it does not exist.
2. Reads all applied version numbers from that table.
3. Iterates `_MIGRATIONS` in order; skips versions already in `applied`.
4. For each pending migration, runs `conn.executescript(sql)` and inserts the version record.

**Rule for contributors:** always append to `_MIGRATIONS`. Never edit or reorder existing entries. Version numbers must be monotonically increasing integers. A migration that fails will prevent server startup — test migrations locally with `make dev` before committing.

Current migrations:

- **v1** (initial): `conversations`, `user_facts`, `tasks`, `grading_log`, `schema_migrations`, indexes
- **v2**: `tool_calls` metrics table and indexes
- **v3**: `audit_log` table and indexes

---

## 11. Plugin system

**File:** `buddy/tools/plugin_loader.py`

Plugins are Python files dropped in the `plugins/` directory at the project root. They are loaded once during the `lifespan` startup hook via `load_plugins()`. Files starting with `_` are ignored.

A valid plugin must define three module-level attributes:

```python
PLUGIN_NAME: str          # short identifier
PLUGIN_DESCRIPTION: str   # one-line description shown to the LLM in the system prompt
execute(args: str) -> str # called when the LLM uses the plugin
```

Example plugin at `plugins/weather.py`:

```python
PLUGIN_NAME = "weather"
PLUGIN_DESCRIPTION = "Get current weather for a city"

def execute(args: str) -> str:
    # args is the raw string the LLM passed after the plugin name
    city = args.strip() or "London"
    # ... fetch weather ...
    return f"Weather in {city}: 18°C, partly cloudy"
```

Loaded plugins are registered in `_plugins: dict[str, dict]`. The registry is accessible via `get_plugins()` and `call_plugin(name, args)`.

`plugin_system_prompt_section()` returns a formatted block listing all loaded plugins, which is injected into the system prompt so the model knows they exist. The model invokes them via `PLUGIN: <name> <args>` directives in its response text (parsed by the legacy text-routing path; native function-calling uses the tool registry instead).

To add a new native tool (one that the model calls via structured function-calling), add a `ToolDef` to `TOOLS` in `tool_registry.py` instead of using the plugin system.

---

## 12. Performance

### Concurrency

`_AGENT_SEMAPHORE = asyncio.Semaphore(3)` in `chat.py` limits concurrent agent runs to 3. Local LLMs are single-threaded — additional concurrency just queues tokens in Ollama's request queue, increasing apparent latency for all users. The semaphore keeps the queue shallow.

### Async everywhere

All I/O (Ollama HTTP calls, Forest HTTP calls, web search, http_get) uses `httpx.AsyncClient`. All tool executors are async. SQLite calls are synchronous but fast (sub-millisecond for typical operations). The ChromaDB client is synchronous and runs in `_GRADE_EXECUTOR` when called from async contexts.

### Tool parallelism

Within a single agent iteration, all tools in a `tool_calls[]` batch execute concurrently via `asyncio.gather()`. If the model calls three tools at once, all three start immediately rather than sequentially. Results are processed in the order they complete.

### Model warm-up at startup

`_warm_up_model()` POSTs an empty prompt to Ollama with `keep_alive: "10m"` during the `lifespan` startup hook. This loads the model into VRAM before the first user request arrives. It runs as a background task — the server becomes available immediately; the warm-up completes asynchronously within ~10-30 seconds depending on model size.

### qwen3 auto-upgrade

`_detect_and_upgrade_conductor()` runs at startup. It queries Ollama's `/api/tags`, and if `qwen3` is installed and the current conductor is still `qwen2.5:14b` (the default), it upgrades `cfg.conductor_model` in place to the installed qwen3 tag (preferring `qwen3:14b`). No restart required. The upgrade is logged at INFO level: `Conductor auto-upgraded: qwen2.5 → qwen3:14b`. Confirm it fired via `GET /admin/config`.

### Test mode

`POST /admin/test-mode {"enabled": true}` unloads `qwen2.5:14b` from VRAM (freeing ~9 GB of RAM on Apple Silicon) and warm-loads `phi4-mini` instead. In test mode: the agent loop uses phi4-mini, all grading is skipped, Forest tools return immediately, and all three Forest tools return a "paused" message. Toggle off to reload the full model. Test mode state is persisted in `user_facts` under the `_test_mode` key so it survives server restarts.

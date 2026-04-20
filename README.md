# buddy

[![CI](https://github.com/StellarRequiem/buddy/actions/workflows/ci.yml/badge.svg)](https://github.com/StellarRequiem/buddy/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)

**Local-first agentic AI assistant. 25-tool native function-calling loop. Forest security integration. Every response graded.**

---

buddy is a self-hosted AI assistant that runs entirely on your machine. Unlike ChatGPT (cloud-only, no real tools), Ollama WebUI (chat interface, no agent loop), or LangChain (abstraction framework, not a product), buddy is a complete agentic system: it connects to a locally-running Ollama instance, invokes 25 tools natively via the model's function-calling API (not prompt-stuffed pseudo-tools), integrates with the Forest blue-team security swarm for live threat awareness, and grades every response it produces using cus-core rubrics evaluated by Claude Haiku with extended thinking. The result is a private, auditable, genuinely agentic assistant that costs nothing to run and exposes nothing to external services unless you explicitly enable the Anthropic API for escalation.

---

## Feature matrix

| Feature | What it does | Why it matters |
|---|---|---|
| Native tool-calling loop | qwen2.5/qwen3 invoke tools via Ollama's `tools` parameter — the model emits structured `tool_calls[]`, not parsed text | Tools execute reliably; the model can chain them across iterations |
| 25-tool suite | Filesystem, Git, shell (human-gated), Python sandbox, web search, HTTP fetch, notes, tasks, memory read/write, Forest security | Covers the full daily-driver workload without external dependencies |
| qwen3 thinking tokens | `<think>…</think>` blocks are parsed at stream time and forwarded as `thinking_trace` events for the UI's collapsible Reasoning panel | Live visibility into model reasoning before tool calls |
| Forest security integration | Three tools (forest_status / forest_incidents / forest_scan) query the Forest blue-team swarm; a background SSE poller broadcasts CRITICAL and ATTACK alerts | Security incidents surface in chat in real time without polling |
| cus-core response grading | Every response is scored on relevance (40%), accuracy (35%), conciseness (15%), and safety (10%) with a 65.0 pass threshold | Objective quality signal; drives escalation and memory filtering |
| Expected-failure demos | `/demo/run` sends harmful prompts, grades the refusal as a high-quality outcome, and displays Haiku's reasoning through the rubric | Demonstrates that cus-core understands safety refusals |
| Persistent memory | SQLite stores conversations, facts, tasks, tool metrics, and audit log; ChromaDB stores vector embeddings for semantic recall | Context survives restarts; relevant past exchanges are injected into each prompt |
| API key auth | `API_KEY` protects all endpoints; `ADMIN_TOKEN` additionally gates `/admin/*`; both are optional for local installs | Safe to expose on a LAN or behind a reverse proxy |
| Audit trail | Every auth failure, shell execution, admin mutation, and tool toggle is written to `audit_log` in SQLite | Immutable record of what buddy did and who asked |
| Runtime tool toggle | `POST /admin/tools/{name}/toggle` enables or disables any tool without a server restart | Disable risky tools for demos or shared installs instantly |
| Session export | `GET /chat/export/{id}` returns a markdown document of the full conversation | Share or archive sessions in a readable format |
| Docker Compose | `docker-compose.yml` bundles buddy and Ollama; `make docker-models` pulls required models | One-command deployment on any platform |

---

## Architecture

```
User / Siri / API
       │
  POST /chat/stream
       │
  ┌────▼────────────────────────────────────────┐
  │            Agent Loop (agent.py)             │
  │  ┌─────────────────────────────────────────┐ │
  │  │  qwen3:14b / qwen2.5:14b (Ollama)       │ │
  │  │  ← streaming tool calls + think tokens  │ │
  │  └──────────┬──────────────────────────────┘ │
  │             │ tool_calls[]                    │
  │  ┌──────────▼──────────────────────────────┐ │
  │  │     Tool Registry  (25 tools)            │ │
  │  │  filesystem · git · web · memory        │ │
  │  │  notes · system · tasks · Forest        │ │
  │  └──────────┬──────────────────────────────┘ │
  └─────────────┼───────────────────────────────-┘
                │ results injected back
  ┌─────────────▼──────────────────────────────-┐
  │           cus-core grading                   │
  │  Haiku (extended thinking) grades response   │
  │  rubrics: relevance·accuracy·concise·safety  │
  └─────────────┬───────────────────────────────-┘
                │ score + pass/fail
         SQLite + ChromaDB
         (session history, vector memory, audit log)
```

---

## Quickstart

### Path A: Native (recommended for Apple Silicon)

```bash
git clone https://github.com/StellarRequiem/buddy.git && cd buddy
scripts/setup.sh          # installs deps, pulls models, creates .env
make dev                  # starts server
open http://localhost:7437
make test                 # verify everything works
```

### Path B: Docker Compose (any platform with GPU/CPU Ollama)

```bash
git clone https://github.com/StellarRequiem/buddy.git && cd buddy
cp .env.example .env      # edit ANTHROPIC_API_KEY
docker compose up -d
make docker-models        # pull qwen2.5:14b, phi4-mini, nomic-embed-text
open http://localhost:7437
```

### Path C: API only

```bash
# If you already have Ollama running, just point buddy at it:
OLLAMA_HOST=http://localhost:11434 make dev
```

---

## Configuration

All settings can be set in `.env` or as environment variables. The server reads `.env` at startup via `python-dotenv`.

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | _(empty)_ | Enables Claude Opus 4.7 escalation and Haiku grading. Leave empty for fully local operation. |
| `API_KEY` | _(empty)_ | Protects all non-public endpoints. Pass as `X-API-Key` header or `Authorization: Bearer`. Empty = no auth. |
| `ADMIN_TOKEN` | _(empty)_ | Additional guard for `/admin/*` endpoints. Pass as `X-Admin-Token`. Empty = no admin auth. |
| `CONDUCTOR_MODEL` | `qwen2.5:14b` | The tool-calling agent model. Auto-upgrades to `qwen3:14b` at startup if installed. |
| `LOCAL_MODEL` | `qwen2.5:14b` | General-purpose local model for non-agent routing path. |
| `FALLBACK_LOCAL_MODEL` | `phi4-mini` | Used when the primary local model is unavailable. |
| `OLLAMA_HOST` | `http://127.0.0.1:11434` | Ollama API base URL. |
| `FOREST_HOST` | `http://127.0.0.1:7438` | Forest blue-team swarm API base URL. |
| `MAX_AGENT_ITERATIONS` | `6` | Hard cap on tool-calling loops per request. |
| `AGENT_TIMEOUT_SECONDS` | `300` | Wall-clock timeout for a single agent run. |
| `USE_AGENT_LOOP` | `true` | `false` disables the native tool-calling loop and uses the legacy text-routing path. |
| `DISABLED_TOOLS` | _(empty)_ | Comma-separated list of tool names to disable. Example: `shell_execute,run_python` |
| `BRAVE_SEARCH_API_KEY` | _(empty)_ | Enables Brave Search for `web_search`. Falls back to DuckDuckGo Instant Answers when empty. |
| `VAULT_PATH` | `~/BuddyVault` | Root directory for all persistent data (SQLite, ChromaDB, notes). |
| `CHAT_HISTORY_LIMIT` | `20` | Number of previous turns injected into each prompt. |

---

## Tool catalogue

| Category | Tools | Notes |
|---|---|---|
| Filesystem | `read_file`, `write_file`, `append_file`, `list_directory`, `search_files` | Scoped to `~/BuddyVault` and configured `allowed_read_paths`. Writes restricted to `~/BuddyVault`. |
| Version Control | `git_status`, `git_log`, `code_search` | Read-only. `code_search` uses ripgrep if available, falls back to `grep`. All operations within allowed paths. |
| System | `shell_execute`, `run_python`, `get_datetime`, `get_sysinfo` | `shell_execute` requires human approval via the shell gate. `run_python` blocks dangerous imports and has a 10s timeout. |
| Web | `web_search`, `http_get` | `web_search` uses Brave Search API when key is set, else DuckDuckGo. `http_get` returns the first 4 KB of a URL. |
| Memory & Notes | `memory_search`, `remember_fact`, `note_write`, `note_read`, `note_list` | `memory_search` queries ChromaDB semantic store. `remember_fact` writes to `user_facts` SQLite table. Notes are markdown files in `~/BuddyVault/notes/`. |
| Tasks | `list_tasks`, `create_task` | Task queue backed by SQLite `tasks` table. Statuses: `queued`, `running`, `done`, `failed`. |
| Forest Security | `forest_status`, `forest_incidents`, `forest_scan` | Queries the Forest blue-team swarm API at `FOREST_HOST`. All three no-op gracefully in test mode. |

---

## API reference

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/chat` | API key | Send a message, get a full response. Returns `session_id`, `response`, `grade`, `model_used`, `tools_called`. |
| `POST` | `/chat/stream` | API key | SSE stream. Events: `token`, `tool_call`, `tool_result`, `thinking_trace`, `shell_gate`, `done`, `error`. |
| `GET` | `/chat/history/{id}` | API key | Conversation history for a session (default last 40 turns). |
| `GET` | `/chat/export/{id}` | API key | Export session as a markdown document (`Content-Disposition: attachment`). |
| `GET` | `/chat/sessions` | API key | List all known session IDs. |
| `GET` | `/health` | None | Rich health check: DB, Ollama model availability, Forest ping. Always HTTP 200; `status` is `ok`/`degraded`/`error`. |
| `GET` | `/admin/status` | Admin token | Current test mode, local model name, Ollama loaded-models list. |
| `GET` | `/admin/config` | Admin token | Full live config (sensitive fields redacted). Useful to confirm qwen3 auto-upgrade fired. |
| `POST` | `/admin/test-mode` | Admin token | Toggle test mode on/off (`{"enabled": true}`). Unloads large model, warms phi4-mini, persists across restarts. |
| `POST` | `/admin/tools/{name}/toggle` | Admin token | Disable or re-enable a tool at runtime (`{"disabled": true}`). No restart required. |
| `POST` | `/admin/tools/test` | Admin token | Execute a tool directly with specified args. Returns `ok`, `result`, `elapsed_ms`. |
| `GET` | `/admin/tool-metrics` | Admin token | Aggregate call counts, success rates, and avg latency per tool. |
| `GET` | `/admin/audit` | Admin token | Immutable audit log. Filter by action with `?action=shell_execute`. |
| `GET` | `/memory/tools` | API key | Full tool catalogue: name, description, parameters, `human_gate`, `disabled`. |
| `GET` | `/memory/facts` | API key | All facts in the `user_facts` table. |
| `GET` | `/forest/status` | API key | Proxy to Forest swarm. Returns `status: offline` gracefully when Forest is not running. |
| `POST` | `/demo/run` | API key | Run an expected-failure demo: harmful prompt graded for quality of refusal. |

---

## Authentication

buddy has two independent auth layers:

**API_KEY** protects all endpoints except `GET /`, `/health`, `/static/*`, and `/api/docs`. Pass it one of two ways:

```bash
# X-API-Key header
curl -H "X-API-Key: mysecret" http://localhost:7437/chat/sessions

# Authorization: Bearer
curl -H "Authorization: Bearer mysecret" http://localhost:7437/chat/sessions
```

**ADMIN_TOKEN** is additionally required for all `/admin/*` endpoints. Pass as `X-Admin-Token`:

```bash
curl -H "X-API-Key: mysecret" \
     -H "X-Admin-Token: myadminsecret" \
     http://localhost:7437/admin/status
```

Both values default to empty strings. An empty `API_KEY` disables auth entirely — appropriate for local-only installs. Auth failures are written to the audit log.

---

## The StellarRequiem Stack

buddy is one node in a three-project ecosystem:

```
┌─────────────────────────────────────────────────────┐
│                     buddy                            │
│  Local agentic assistant — this repo                 │
│  Talks to the user, calls tools, surfaces alerts     │
└────────────────┬────────────────────────────────────┘
                 │ reads /forest/status (port 7438)
                 │ three native tools: status/incidents/scan
┌────────────────▼────────────────────────────────────┐
│              Forest blue-team-guardian               │
│  Multi-agent security swarm                          │
│  Watchers → Analyst → Responder → Logger             │
│  Produces incident log + severity breakdown          │
└─────────────────────────────────────────────────────┘

        Both use ↓

┌─────────────────────────────────────────────────────┐
│                    cus-core                          │
│  Universal rubric grading library                    │
│  Rubric → Stage → Task → Grader → GradeResult        │
│  Haiku with extended thinking grades Opus responses  │
│  phi4-mini (local, free) grades agent responses      │
└─────────────────────────────────────────────────────┘
```

buddy surfaces Forest's threat intelligence to the user conversationally. Forest runs independently — buddy degrades gracefully to "Forest offline" if it is not started. cus-core provides the rubric grammar and grading engine used by both projects for quality assurance.

---

## Development

### Make targets

| Target | Description |
|---|---|
| `make install` | Install all dependencies with `uv sync --extra dev` |
| `make dev` | Start server with auto-reload on port 7437 |
| `make run` | Start server in production mode |
| `make test` | Run the full test suite (66 tests, ~45s) |
| `make test-fast` | Run without integration tests (no app boot, under 1s) |
| `make coverage` | Run tests with HTML coverage report at `htmlcov/index.html` |
| `make lint` | Lint with ruff |
| `make format` | Auto-format with ruff |
| `make typecheck` | Type-check with pyright |
| `make docker-up` | Start buddy + Ollama via Docker Compose |
| `make docker-down` | Stop Docker Compose stack |
| `make docker-models` | Pull qwen2.5:14b, phi4-mini, nomic-embed-text into the Docker Ollama container |
| `make docker-logs` | Tail buddy container logs |
| `make demo` | Run the interactive demo script |
| `make clean` | Remove build artifacts, caches, and coverage reports |

### Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The test suite has 66 tests covering the agent loop, tool registry, filesystem operations, memory layer, and full integration paths. Coverage threshold is 60% (enforced in CI).

---

## Related projects

- [forest-blue-team-guardian](https://github.com/StellarRequiem/forest-blue-team-guardian) — Multi-agent blue-team security monitoring swarm
- [cus-core](https://github.com/StellarRequiem/cus-core) — Universal rubric grading library for LLM outputs

---

## License

MIT — see [LICENSE](LICENSE).

# Changelog

All notable changes to buddy are documented here.
Format: [Semantic Versioning](https://semver.org/)

---

## [Unreleased] — L2 in progress

### Added
- `POST /chat/stream` — SSE streaming endpoint, tokens arrive as they generate
- Streaming cursor animation in chat UI (`▌` blink while tokens arrive)
- `docker-compose.yml` + `Dockerfile` — full containerised stack (buddy + Ollama)
- `docs/ARCHITECTURE.md` — routing flow, memory system, grading details
- `CONTRIBUTING.md` — setup, rules, branch conventions
- `CHANGELOG.md` — this file

---

## [0.1.1-hackathon] — 2026-04-20

### Added
- `README.md` — full rewrite with architecture diagram, quickstart, API reference
- `.env.example` — template for new installs
- `scripts/setup.sh` — automated installer (venv, deps, .env template, model pull prompts)
- `scripts/demo.sh` — one-command demo launcher (server check, browser open, test run)
- Test mode UI banner — amber `🔬 TEST MODE` header bar with inline Disable button
- `checkTestMode()` / `disableTestMode()` wired to `/admin/status` on page load
- `docs/demos/DEMO.md` — 5-beat demo script with expected output and fallbacks
- `docs/install/INSTALL.md` — step-by-step install guide with troubleshooting

---

## [0.1.0-foundation] — 2026-04-19

### Added
- `POST /admin/test-mode` — toggle test mode; evicts qwen2.5:14b (9GB), warms phi4-mini
- `GET /admin/status` — shows test_mode flag + Ollama models currently in RAM
- Forest monitoring pauses when test mode is active (`{"status": "paused"}`)
- `scheduleForestScan(ms)` / `stopForestScan()` — browser console API for automating Forest scans
- Forest 30s auto-poll removed — scan is manual (tab click or Refresh button)
- `_GRADE_EXECUTOR` ThreadPoolExecutor — OllamaGrader no longer blocks event loop
- `_local_grade_async()` — async wrapper with 60s timeout for local grading
- `_is_model_available()` — pre-check prevents Ollama auto-pulling missing models
- Keyword short-circuit — escalation keywords bypass local model entirely (saves ~30s)
- `search_memory` moved to executor — embed calls no longer block event loop
- `_clean_response()` now strips `SEARCH:` directives that polluted phi4-mini output
- Graceful degradation in `vectors.py` — embed failures return empty list silently
- `HANDOFF.md` — session state document for context continuity
- `ROADMAP.md` — L0–L5 levelled roadmap with gates, demo beats, session system
- `docs/sessions/` — agenda + journal templates, daily session tracking
- `test_live.py` — 9-group observable test suite, 17 checks

### Fixed
- ReadTimeout on `/chat`: synchronous `search_memory` was blocking uvicorn event loop
- OllamaGrader blocking event loop during grading
- phi4-mini outputting `SEARCH: ...` literally (removed directive from system prompt)
- Ollama auto-pulling qwen2.5:14b on startup (10-minute hang)
- Docker Desktop VM at 12GB + Kubernetes enabled (reduced to 2.5GB, K8s disabled)

---

## [0.0.3] — 2026-04-18

### Added
- Expected-failure demo mode — `/demo/tasks`, `/demo/run`
- Demo tab in UI with 4 scenarios: phishing, malware, manipulation, pii_harvest
- Haiku grader with extended thinking for Opus responses
- Thinking trace visible in UI grade panel (collapsible)
- `escalated` flag on RouteResult, displayed as `↑ escalated` badge in UI

---

## [0.0.2] — 2026-04-17

### Added
- Local-first routing: qwen2.5:14b → phi4-mini → Opus 4.7 escalation
- cus-core grading integration (OllamaGrader + Grader)
- Grade panel in chat UI (rubric bars, composite score, pass/fail badge)
- Force frontier toggle (🌐 button)
- Escalation confidence threshold (configurable, default 60%)
- Escalation keywords (configurable list in config.py)

---

## [0.0.1] — 2026-04-16

### Added
- Forest blue-team integration — `/forest/status` proxy to :7438
- Session persistence — SQLite message history, localStorage session ID
- Vector memory — ChromaDB + nomic-embed-text, quality-gated at score ≥ 70
- Memory filter — trivial exchanges (< 20 chars) skip embedding
- Siri/iOS Shortcuts endpoints — `/siri/ping`, `/siri/status`, `/siri/task`
- Tasks CRUD — `/tasks` GET/POST/PUT
- Memory inspection — `/memory/search`, `/memory/stats`, `/memory/facts`

---

## [0.0.0] — 2026-04-15

- Initial release: buddy v0.1.0
- FastAPI server on port 7437
- Basic chat endpoint with Opus 4.7
- SQLite persistence
- Single-page UI

# Buddy — Installation Guide
_macOS (Apple Silicon, M1/M2/M3/M4) — Primary Target_

**Time to install: ~10 minutes** (model downloads excluded — ~12GB total)

---

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| macOS | 13+ (Ventura or later) | — |
| Ollama | Latest | https://ollama.com/download |
| Python | 3.11+ | `brew install python@3.11` |
| uv | Latest | `brew install uv` |
| Anthropic API key | Required for Opus + Haiku | https://console.anthropic.com |

---

## Step 1 — Clone the repo

```bash
git clone https://github.com/StellarRequiem/buddy.git
cd buddy
```

---

## Step 2 — Create virtual environment and install dependencies

```bash
uv venv .venv
source .venv/bin/activate
uv pip install -e .
```

---

## Step 3 — Pull Ollama models

These models are required. Download time depends on your connection.

```bash
ollama pull qwen2.5:14b          # 9.0 GB — primary local LLM
ollama pull phi4-mini            # 2.5 GB — fallback LLM + local grader
ollama pull nomic-embed-text     # 274 MB — vector memory embeddings
```

Verify all three are installed:
```bash
ollama list
```

Expected output (sizes may vary slightly):
```
NAME                       SIZE
qwen2.5:14b                9.0 GB
phi4-mini:latest           2.5 GB
nomic-embed-text:latest    274 MB
```

---

## Step 4 — Create .env file

```bash
cp .env.example .env
```

Edit `.env` and fill in your Anthropic API key:
```
ANTHROPIC_API_KEY=sk-ant-api03-...
LOCAL_MODEL=qwen2.5:14b
FALLBACK_LOCAL_MODEL=phi4-mini
OLLAMA_HOST=http://127.0.0.1:11434
PORT=7437
DEBUG=false
```

---

## Step 5 — Start the server

```bash
python -m buddy.main
```

You should see:
```
INFO:     Uvicorn running on http://127.0.0.1:7437 (Press CTRL+C to quit)
```

---

## Step 6 — Verify installation

Open a second terminal:
```bash
python test_live.py
```

All 17 checks should pass. The first run will be slow (~2 min for Test 2) while qwen2.5:14b cold-starts.

Expected final output:
```
  17 passed  0 failed  out of 17 checks

  All checks passed. Stack is ready for the demo.
```

---

## Step 7 — Open the UI

Navigate to: **http://localhost:7437**

---

## Automated install (alternative to steps 2–5)

```bash
scripts/setup.sh
```

This script handles venv creation, dependency install, .env templating, and model verification.

---

## RAM requirements

| Configuration | RAM needed |
|--------------|-----------|
| Full stack (qwen2.5:14b loaded) | 12–13 GB |
| Test mode (phi4-mini only) | 4–5 GB |
| Minimum (no local models, API only) | 2 GB |

**Recommended: 16 GB unified memory** (Mac Mini M4, MacBook Pro M3/M4)

On 8GB machines: use test mode by default, enable qwen2.5:14b only when needed.

---

## Troubleshooting

**"Model not installed" error:**
Run `ollama list` and confirm model names match exactly.

**Slow first response (2+ minutes):**
Normal — qwen2.5:14b cold-starts from disk. Subsequent responses are ~30s.

**Grade shows "no grade":**
Grader timed out under memory pressure. This is graceful degradation — the response is still returned correctly. Free up RAM (close other apps) and retry.

**Port 7437 already in use:**
```bash
lsof -i :7437          # find the process
kill <PID>             # kill it
```

**Ollama not responding:**
```bash
ollama serve           # start Ollama if it's not running
```

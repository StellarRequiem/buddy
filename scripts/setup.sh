#!/usr/bin/env bash
# buddy setup — installs deps, pulls models, creates .env, verifies install
# Usage: scripts/setup.sh
set -e

BOLD="\033[1m"
GREEN="\033[92m"
YELLOW="\033[93m"
RED="\033[91m"
RESET="\033[0m"

ok()   { echo -e "  ${GREEN}✓${RESET}  $1"; }
warn() { echo -e "  ${YELLOW}⚠${RESET}  $1"; }
fail() { echo -e "  ${RED}✗${RESET}  $1"; exit 1; }
hdr()  { echo -e "\n${BOLD}$1${RESET}"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

echo -e "\n${BOLD}════════════════════════════════════════${RESET}"
echo -e "${BOLD}  buddy — setup${RESET}"
echo -e "${BOLD}════════════════════════════════════════${RESET}"

# ── 1. Check prerequisites ─────────────────────────────────────────────────────
hdr "1/5  Checking prerequisites"

command -v python3 &>/dev/null || fail "Python 3 not found. Install: brew install python@3.11"
PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
ok "Python $PY_VERSION"

command -v uv &>/dev/null || fail "uv not found. Install: brew install uv"
ok "uv $(uv --version 2>&1 | head -1)"

command -v ollama &>/dev/null || fail "Ollama not found. Install from: https://ollama.com/download"
ok "Ollama $(ollama --version 2>&1 | head -1)"

# ── 2. Virtual environment ─────────────────────────────────────────────────────
hdr "2/5  Python environment"

if [ ! -d ".venv" ]; then
  uv venv .venv
  ok "Created .venv"
else
  ok ".venv already exists"
fi

source .venv/bin/activate
uv pip install -e . -q
ok "Dependencies installed"

# ── 3. .env file ───────────────────────────────────────────────────────────────
hdr "3/5  Configuration"

if [ ! -f ".env" ]; then
  cat > .env << 'ENVEOF'
ANTHROPIC_API_KEY=your-key-here
LOCAL_MODEL=qwen2.5:14b
FALLBACK_LOCAL_MODEL=phi4-mini
OLLAMA_HOST=http://127.0.0.1:11434
PORT=7437
DEBUG=false
ENVEOF
  warn ".env created — edit it and add your ANTHROPIC_API_KEY before starting"
else
  ok ".env already exists"
  if grep -q "your-key-here" .env; then
    warn "ANTHROPIC_API_KEY is still the placeholder — edit .env before starting"
  fi
fi

# ── 4. Ollama models ───────────────────────────────────────────────────────────
hdr "4/5  Ollama models"

pull_if_missing() {
  local model="$1"
  local label="$2"
  if ollama list 2>/dev/null | grep -q "^${model%%:*}"; then
    ok "$label already installed"
  else
    echo -e "  Pulling $label (this may take a while)..."
    ollama pull "$model"
    ok "$label installed"
  fi
}

# Start ollama if not running
ollama list &>/dev/null || (ollama serve &>/dev/null & sleep 3)

pull_if_missing "phi4-mini"           "phi4-mini (2.5 GB — fast fallback + grader)"
pull_if_missing "nomic-embed-text"    "nomic-embed-text (274 MB — vector memory)"

echo ""
warn "qwen2.5:14b (9 GB) is the primary model. Pull now? [y/N]"
read -r PULL_14B
if [[ "$PULL_14B" =~ ^[Yy]$ ]]; then
  pull_if_missing "qwen2.5:14b" "qwen2.5:14b (9 GB — primary local LLM)"
else
  warn "Skipping qwen2.5:14b — buddy will use phi4-mini as primary until it's installed"
  warn "To install later: ollama pull qwen2.5:14b"
fi

# ── 5. Done ────────────────────────────────────────────────────────────────────
hdr "5/5  Ready"

echo ""
echo -e "${BOLD}  Next steps:${RESET}"
if grep -q "your-key-here" .env 2>/dev/null; then
  echo "    1. Edit .env — add your ANTHROPIC_API_KEY"
  echo "    2. python -m buddy.main"
else
  echo "    1. python -m buddy.main"
fi
echo "    2. Open http://localhost:7437"
echo "    3. python test_live.py  ← verify everything works"
echo ""
echo -e "${GREEN}  Setup complete.${RESET}"
echo ""

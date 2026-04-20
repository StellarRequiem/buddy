#!/usr/bin/env bash
# buddy demo — starts server if needed, opens browser, runs test suite
# Usage: scripts/demo.sh
set -e

BOLD="\033[1m"
GREEN="\033[92m"
YELLOW="\033[93m"
CYAN="\033[96m"
RESET="\033[0m"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

PORT=7437
URL="http://localhost:$PORT"

echo -e "\n${BOLD}════════════════════════════════════════${RESET}"
echo -e "${BOLD}  buddy — demo launcher${RESET}"
echo -e "${BOLD}════════════════════════════════════════${RESET}"

# ── Check server ───────────────────────────────────────────────────────────────
if curl -s "$URL/health" | grep -q '"ok"'; then
  echo -e "  ${GREEN}✓${RESET}  Server already running at $URL"
else
  echo -e "  ${YELLOW}▸${RESET}  Starting buddy server..."
  source .venv/bin/activate 2>/dev/null || true
  nohup python -m buddy.main &>/tmp/buddy-demo.log &
  SERVER_PID=$!

  # Wait for server
  for i in $(seq 1 20); do
    sleep 1
    curl -s "$URL/health" | grep -q '"ok"' && break
    if [ "$i" -eq 20 ]; then
      echo -e "  ${BOLD}Server failed to start. Check /tmp/buddy-demo.log${RESET}"
      exit 1
    fi
  done
  echo -e "  ${GREEN}✓${RESET}  Server started (PID $SERVER_PID)"
fi

# ── Open browser ───────────────────────────────────────────────────────────────
echo -e "  ${CYAN}▸${RESET}  Opening $URL..."
open "$URL" 2>/dev/null || xdg-open "$URL" 2>/dev/null || echo "  Open $URL in your browser"

# ── Show demo guide ────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}  5-beat demo (see docs/demos/DEMO.md for full script)${RESET}"
echo ""
echo -e "  ${CYAN}Beat 1${RESET}  Type: \"What is the speed of light? One sentence.\""
echo -e "          Expect: qwen2.5:14b, grade ~80-90/100"
echo ""
echo -e "  ${CYAN}Beat 2${RESET}  Type: \"Write code for a Python quicksort with inline comments.\""
echo -e "          Expect: opus 4.7, grade ~95-100/100, ↑ escalated badge"
echo ""
echo -e "  ${CYAN}Beat 3${RESET}  Click ▸ on the grade panel to expand"
echo -e "          Expect: rubric bars + 🧠 Haiku reasoning trace"
echo ""
echo -e "  ${CYAN}Beat 4${RESET}  Click Demo tab → Phishing → Run"
echo -e "          Expect: Opus refuses, 100/100 refusal grade"
echo ""
echo -e "  ${CYAN}Beat 5${RESET}  In a terminal:"
echo -e "          ${BOLD}curl -X POST $URL/admin/test-mode -H 'Content-Type: application/json' -d '{\"enabled\":true}'${RESET}"
echo -e "          Expect: qwen2.5:14b freed, phi4-mini warmed"
echo ""

# ── Run test suite ─────────────────────────────────────────────────────────────
echo -e "${BOLD}  Running test suite...${RESET}"
echo -e "  ${YELLOW}(first run takes 3-5 min — qwen2.5:14b cold start)${RESET}"
echo ""

source .venv/bin/activate 2>/dev/null || true
python test_live.py

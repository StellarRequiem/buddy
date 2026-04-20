#!/bin/bash
# Buddy startup script — run by launchd on login
# Activates venv, loads .env, starts FastAPI server

set -euo pipefail

BUDDY_DIR="$HOME/Projects/buddy"
LOG_DIR="$HOME/BuddyVault/logs"
LOG_FILE="$LOG_DIR/buddy.log"

mkdir -p "$LOG_DIR"

cd "$BUDDY_DIR"

# Load .env so ANTHROPIC_API_KEY is available to the process
set -a
source "$BUDDY_DIR/.env"
set +a

exec "$BUDDY_DIR/.venv/bin/python" -m buddy.main >> "$LOG_FILE" 2>&1

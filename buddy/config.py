"""
Buddy configuration — loaded from environment or .env file.
All paths are under ~/BuddyVault/ by default.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load .env before pydantic-settings so values are in os.environ
_ENV_FILE = Path(__file__).parent.parent / ".env"
load_dotenv(_ENV_FILE, override=True)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    # ── Server ─────────────────────────────────────────────────────────────
    host: str = "127.0.0.1"
    port: int = 7437
    debug: bool = False

    # ── Storage ────────────────────────────────────────────────────────────
    vault_path: Path = Path.home() / "BuddyVault"
    db_path: Path = Path.home() / "BuddyVault" / "buddy.db"
    chroma_path: Path = Path.home() / "BuddyVault" / "chroma"
    audit_chain_path: Path = Path.home() / "BuddyVault" / "audit_chain.json"

    # ── Models ─────────────────────────────────────────────────────────────
    # Primary: Claude Opus 4.7 (used when API key is present)
    opus_model: str = "claude-opus-4-7"
    # Grader: Haiku with extended thinking — accurate, cheap relative to Opus
    grader_model: str = "claude-haiku-4-5"
    # Local fallback: used when no API key or Anthropic unreachable
    local_model: str = "qwen2.5:14b"
    fallback_local_model: str = "phi4-mini"
    embed_model: str = "nomic-embed-text"
    # Kept for backwards compat — maps to opus_model now
    frontier_model: str = "claude-opus-4-7"
    # Extended thinking budget for grader (tokens). 0 = disabled.
    grader_thinking_budget: int = 1024

    # ── Ollama ─────────────────────────────────────────────────────────────
    ollama_host: str = "http://127.0.0.1:11434"

    # ── Anthropic ──────────────────────────────────────────────────────────
    anthropic_api_key: str = ""

    # ── Admin security ─────────────────────────────────────────────────────
    # When set, /admin/* endpoints require X-Admin-Token: <value> header.
    # Leave empty for local-only installs (no auth enforced).
    admin_token: str = ""

    # ── Chat ───────────────────────────────────────────────────────────────
    # How many previous turns to inject into each prompt.
    chat_history_limit: int = 20

    # ── Test / dev mode ────────────────────────────────────────────────────
    # When True: uses phi4-mini, skips all grading & memory ops.
    # Toggle at runtime: POST /admin/test-mode  {"enabled": true/false}
    # Or set TEST_MODE=true in .env before starting.
    test_mode: bool = False

    # ── Routing thresholds ─────────────────────────────────────────────────
    # Escalate to frontier when local response confidence is below this
    escalation_confidence_threshold: float = 0.60
    # Always escalate for these explicit request types
    escalation_keywords: list[str] = [
        "summarize this document",
        "write code",
        "debug",
        "explain in detail",
    ]

    # ── Security ───────────────────────────────────────────────────────────
    # Paths buddy is allowed to read outside BuddyVault
    allowed_read_paths: list[str] = [
        "~/forest-blue-team-guardian/SECURITY_TOOLS_INTEGRATION.md",
        "~/ForestVault",
        "~/Projects/cus-core",
    ]
    # Shell exec is always human-gated — this just adds extra banned patterns
    shell_banned_patterns: list[str] = [
        "rm -rf",
        "sudo",
        "chmod 777",
        "curl | sh",
        "wget | sh",
        "> /etc",
        "> /usr",
    ]

    def ensure_vault(self) -> None:
        self.vault_path.mkdir(parents=True, exist_ok=True)
        self.chroma_path.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_vault()

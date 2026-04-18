"""
Buddy configuration — loaded from environment or .env file.
All paths are under ~/BuddyVault/ by default.
"""
from __future__ import annotations

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

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
    local_model: str = "qwen2.5:14b"
    fallback_local_model: str = "phi4-mini"   # if 14b OOM
    embed_model: str = "nomic-embed-text"
    frontier_model: str = "claude-haiku-4-5"  # escalation target

    # ── Ollama ─────────────────────────────────────────────────────────────
    ollama_host: str = "http://127.0.0.1:11434"

    # ── Anthropic ──────────────────────────────────────────────────────────
    anthropic_api_key: str = ""

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

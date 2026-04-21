"""Tests for filesystem and shell tools."""
import os
import pytest
from pathlib import Path
from buddy.tools.filesystem import read_file, list_dir, _resolve_allowed
from buddy.tools.shell import requires_confirmation, ShellDeniedError


@pytest.fixture(autouse=True)
def _shell_db(tmp_path, monkeypatch):
    """Point the DB at a temp path and initialise the schema before each test."""
    db = tmp_path / "test.db"
    from buddy import config
    monkeypatch.setattr(config.settings, "db_path", db)
    monkeypatch.setattr(config.settings, "vault_path", tmp_path)
    from buddy.memory.db import init_db
    init_db()


# ── Filesystem ──────────────────────────────────────────────────────────────

def test_vault_path_allowed(tmp_path, monkeypatch):
    from buddy import config
    monkeypatch.setattr(config.settings, "vault_path", tmp_path)
    f = tmp_path / "test.txt"
    f.write_text("hello")
    result = read_file(str(f))
    assert result == "hello"


def test_path_outside_scope_raises(tmp_path, monkeypatch):
    from buddy import config
    monkeypatch.setattr(config.settings, "vault_path", tmp_path / "vault")
    monkeypatch.setattr(config.settings, "allowed_read_paths", [])
    with pytest.raises(ValueError, match="outside allowed scope"):
        _resolve_allowed("/etc/passwd")


def test_missing_file_raises(tmp_path, monkeypatch):
    from buddy import config
    monkeypatch.setattr(config.settings, "vault_path", tmp_path)
    with pytest.raises(FileNotFoundError):
        read_file(str(tmp_path / "nonexistent.txt"))


def test_list_dir(tmp_path, monkeypatch):
    from buddy import config
    monkeypatch.setattr(config.settings, "vault_path", tmp_path)
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.txt").write_text("b")
    listing = list_dir(str(tmp_path))
    assert "a.txt" in listing and "b.txt" in listing


# ── Shell ───────────────────────────────────────────────────────────────────

def test_safe_command_returns_confirmation():
    result = requires_confirmation("ls -la")
    assert result["type"] == "shell_confirmation"
    assert "ls -la" in result["command"]


def test_banned_pattern_raises():
    with pytest.raises(ShellDeniedError, match="rm -rf"):
        requires_confirmation("rm -rf /tmp/test")


def test_sudo_blocked():
    with pytest.raises(ShellDeniedError):
        requires_confirmation("sudo rm foo")

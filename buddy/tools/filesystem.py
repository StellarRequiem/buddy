"""
Filesystem tools — scoped to ~/BuddyVault/ and an explicit allow-list.
Reads, writes, lists, and searches files/directories within allowed scope.
Path traversal blocked; write operations restricted to BuddyVault.
"""
from __future__ import annotations

import fnmatch
import os
from pathlib import Path

from buddy.config import settings as cfg

_MAX_READ_BYTES = 50_000   # 50 KB cap per read
_MAX_WRITE_BYTES = 200_000  # 200 KB cap per write


def _resolve_allowed(path_str: str) -> Path:
    """Resolve path and verify it is within an allowed read root. Raises ValueError if not."""
    p = Path(path_str).expanduser().resolve()
    vault = cfg.vault_path.resolve()
    if str(p).startswith(str(vault)):
        return p
    for allowed in cfg.allowed_read_paths:
        allowed_resolved = Path(allowed).expanduser().resolve()
        if str(p).startswith(str(allowed_resolved)) or p == allowed_resolved:
            return p
    raise ValueError(
        f"Path '{p}' is outside allowed scope. "
        f"Allowed roots: ~/BuddyVault/ + {cfg.allowed_read_paths}"
    )


def _resolve_write_allowed(path_str: str) -> Path:
    """Resolve path and verify it is within BuddyVault (writes are more restricted)."""
    p = Path(path_str).expanduser().resolve()
    vault = cfg.vault_path.resolve()
    if not str(p).startswith(str(vault)):
        raise ValueError(
            f"Writes are restricted to ~/BuddyVault/. Path '{p}' is outside the vault."
        )
    return p


# ── Read ───────────────────────────────────────────────────────────────────────

def read_file(path_str: str) -> str:
    """Read a file and return its contents (capped at 50 KB)."""
    p = _resolve_allowed(path_str)
    if not p.exists():
        raise FileNotFoundError(f"File not found: {p}")
    if not p.is_file():
        raise ValueError(f"Not a file: {p}")
    raw = p.read_bytes()
    truncated = len(raw) > _MAX_READ_BYTES
    chunk = raw[:_MAX_READ_BYTES]
    try:
        text = chunk.decode("utf-8")
    except UnicodeDecodeError:
        text = chunk.decode("latin-1")
    if truncated:
        text += f"\n\n[...truncated at {_MAX_READ_BYTES} bytes — file is {len(raw)} bytes total]"
    return text


# ── Write ──────────────────────────────────────────────────────────────────────

def write_file(path_str: str, content: str, overwrite: bool = True) -> str:
    """
    Write content to a file inside ~/BuddyVault/.
    Creates parent directories as needed.
    Returns a confirmation string.
    """
    if len(content.encode()) > _MAX_WRITE_BYTES:
        raise ValueError(f"Content exceeds {_MAX_WRITE_BYTES} byte write limit")
    p = _resolve_write_allowed(path_str)
    if p.exists() and not overwrite:
        raise FileExistsError(f"File already exists and overwrite=False: {p}")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Wrote {len(content.encode())} bytes to {p}"


def append_file(path_str: str, content: str) -> str:
    """Append content to a file inside ~/BuddyVault/. Creates file if missing."""
    p = _resolve_write_allowed(path_str)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(content)
    return f"Appended {len(content.encode())} bytes to {p}"


# ── List / search ──────────────────────────────────────────────────────────────

def list_dir(path_str: str) -> list[str]:
    """List directory contents (names only, non-recursive)."""
    p = _resolve_allowed(path_str)
    if not p.is_dir():
        raise ValueError(f"Not a directory: {p}")
    entries = []
    for item in sorted(p.iterdir()):
        suffix = "/" if item.is_dir() else ""
        entries.append(item.name + suffix)
    return entries


def search_files(pattern: str, directory: str = "~/BuddyVault") -> list[str]:
    """
    Recursively find files matching a glob pattern inside an allowed directory.
    Returns relative paths from the search root. Max 200 results.
    """
    root = _resolve_allowed(directory)
    if not root.is_dir():
        raise ValueError(f"Not a directory: {root}")
    matches = []
    for dirpath, _dirs, filenames in os.walk(root):
        for fname in filenames:
            if fnmatch.fnmatch(fname, pattern):
                full = Path(dirpath) / fname
                matches.append(str(full.relative_to(root)))
                if len(matches) >= 200:
                    matches.append("...(truncated at 200 results)")
                    return matches
    return matches

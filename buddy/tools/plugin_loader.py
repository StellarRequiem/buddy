"""
Plugin loader -- auto-discovers and registers buddy tools from the plugins/ directory.

Plugin interface
----------------
Drop a .py file in plugins/ (project root). It must define:

  PLUGIN_NAME: str          -- short identifier used in PLUGIN: directives
  PLUGIN_DESCRIPTION: str   -- one-line description shown to the LLM
  execute(args: str) -> str -- called when the LLM emits PLUGIN: name <args>

Example (plugins/datetime_plugin.py):
  PLUGIN_NAME = "datetime"
  PLUGIN_DESCRIPTION = "Get the current date and time"
  def execute(args: str) -> str:
      import datetime
      return datetime.datetime.now().strftime("%A %B %d %Y, %H:%M")

Plugins are loaded once at server startup via load_plugins() called from main.py
lifespan. Restart the server after adding or editing plugins.
Files starting with _ are ignored.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

# ── Registry ───────────────────────────────────────────────────────────────────
_plugins: dict[str, dict] = {}   # name.lower() -> {name, description, execute, path}

# Default to plugins/ at the project root (two levels above this file)
_PLUGINS_DIR = Path(__file__).parent.parent.parent / "plugins"


def load_plugins(plugins_dir: Path | None = None) -> None:
    """
    Scan plugins_dir for valid plugins and register them.
    Safe to call multiple times -- re-scans and rebuilds the registry.
    """
    global _plugins
    _plugins = {}
    target = plugins_dir or _PLUGINS_DIR

    if not target.exists():
        return

    for path in sorted(target.glob("*.py")):
        if path.name.startswith("_"):
            continue
        try:
            spec = importlib.util.spec_from_file_location(
                f"buddy_plugin_{path.stem}", path
            )
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore[union-attr]

            name: str | None = getattr(mod, "PLUGIN_NAME", None)
            desc: str | None = getattr(mod, "PLUGIN_DESCRIPTION", None)
            fn = getattr(mod, "execute", None)

            if name and desc and callable(fn):
                _plugins[name.lower()] = {
                    "name": name,
                    "description": desc,
                    "execute": fn,
                    "path": str(path),
                }
                print(f"[plugins] loaded: {name} ({path.name})")
            else:
                print(f"[plugins] skipped {path.name} — missing PLUGIN_NAME, PLUGIN_DESCRIPTION, or execute()")
        except Exception as exc:
            print(f"[plugins] failed to load {path.name}: {exc}")


def get_plugins() -> dict[str, dict]:
    """Return a copy of the current plugin registry."""
    return dict(_plugins)


def call_plugin(name: str, args: str) -> str:
    """
    Invoke a plugin by name with the given args string.
    Returns the plugin output, or an error message if not found or execution fails.
    """
    plugin = _plugins.get(name.lower())
    if not plugin:
        available = ", ".join(_plugins.keys()) or "none"
        return f"[Plugin '{name}' not found. Available: {available}]"
    try:
        return str(plugin["execute"](args.strip()))
    except Exception as exc:
        return f"[Plugin '{name}' error: {exc}]"


def plugin_system_prompt_section() -> str:
    """
    Return formatted plugin listing for injection into the system prompt.
    Returns empty string when no plugins are loaded.
    """
    if not _plugins:
        return ""
    lines = ["PLUGINS (use these tools when relevant):"]
    for p in _plugins.values():
        lines.append(f"  PLUGIN: {p['name']} <args>  -- {p['description']}")
    lines.append("")
    return "\n".join(lines)

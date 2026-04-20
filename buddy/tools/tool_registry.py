"""
Master tool registry for the buddy conductor agent.

Every tool is a ToolDef with:
  schema       -- OpenAI-compatible JSON schema sent to the LLM
  execute      -- async callable(args: dict) -> str
  human_gate   -- if True, execution is paused for user approval (shell only)

The agent loop in agent.py iterates: LLM picks tools → execute → inject results
→ LLM decides next action, until it produces a plain text response or max iterations.

Tool categories
---------------
FILESYSTEM  read_file, write_file, append_file, list_directory, search_files
SYSTEM      shell_execute (human-gated), run_python, get_datetime, get_sysinfo
WEB         web_search, http_get
MEMORY      memory_search, remember_fact
TASKS       list_tasks, create_task
FOREST      forest_status
"""
from __future__ import annotations

import asyncio
import datetime
import json
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

import httpx

from buddy.config import settings as cfg
from buddy.tools.filesystem import (
    read_file as _read_file,
    write_file as _write_file,
    append_file as _append_file,
    list_dir as _list_dir,
    search_files as _search_files,
)


# ── ToolDef ────────────────────────────────────────────────────────────────────

@dataclass
class ToolDef:
    schema: dict            # full OpenAI-compatible tool object {type, function{name,desc,params}}
    execute: Callable[..., Awaitable[str]]
    human_gate: bool = False   # True = pause loop, show confirmation gate


# ── Executors ──────────────────────────────────────────────────────────────────

async def _exec_read_file(path: str) -> str:
    try:
        return _read_file(path)
    except Exception as e:
        return f"[read_file error] {e}"


async def _exec_write_file(path: str, content: str, overwrite: bool = True) -> str:
    try:
        return _write_file(path, content, overwrite=overwrite)
    except Exception as e:
        return f"[write_file error] {e}"


async def _exec_append_file(path: str, content: str) -> str:
    try:
        return _append_file(path, content)
    except Exception as e:
        return f"[append_file error] {e}"


async def _exec_list_directory(path: str = "~/BuddyVault") -> str:
    try:
        items = _list_dir(path)
        return "\n".join(items) if items else "(empty directory)"
    except Exception as e:
        return f"[list_directory error] {e}"


async def _exec_search_files(pattern: str, directory: str = "~/BuddyVault") -> str:
    try:
        results = _search_files(pattern, directory)
        return "\n".join(results) if results else "No files matched."
    except Exception as e:
        return f"[search_files error] {e}"


async def _exec_shell_execute(command: str) -> str:
    """Placeholder — actual execution handled by the shell gate in agent.py."""
    return f"[SHELL_GATE_PENDING] {command}"


async def _exec_run_python(code: str, timeout: int = 10) -> str:
    """Run Python code in a subprocess with timeout. Returns stdout+stderr."""
    # Basic safety: block obviously dangerous patterns
    blocked = ["import os", "import subprocess", "import sys", "__import__",
               "open(", "exec(", "eval(", "compile("]
    code_lower = code.lower()
    for b in blocked:
        if b in code_lower:
            return f"[run_python blocked] Pattern '{b}' is not allowed in sandboxed execution."
    try:
        proc = await asyncio.create_subprocess_exec(
            "python3", "-c", code,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return f"[run_python timeout] Execution exceeded {timeout}s."
        out = stdout.decode("utf-8", errors="replace").strip()
        err = stderr.decode("utf-8", errors="replace").strip()
        result = out
        if err:
            result += f"\n[stderr] {err}"
        return result or "(no output)"
    except Exception as e:
        return f"[run_python error] {e}"


async def _exec_get_datetime() -> str:
    now = datetime.datetime.now()
    return now.strftime("Date: %A, %B %d, %Y  |  Time: %H:%M:%S (local)")


async def _exec_get_sysinfo() -> str:
    lines = []
    try:
        vm = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=3)
        if vm.returncode == 0:
            vm_map = {l.split(":")[0].strip(): l.split(":")[1].strip().rstrip(".")
                      for l in vm.stdout.splitlines() if ":" in l}
            ps = 16384
            free = int(vm_map.get("Pages free", "0")) * ps
            inactive = int(vm_map.get("Pages inactive", "0")) * ps
            wired = int(vm_map.get("Pages wired down", "0")) * ps
            active = int(vm_map.get("Pages active", "0")) * ps
            total = free + inactive + wired + active
            used = wired + active
            lines.append(f"RAM: {used/1024**3:.1f} GB used / {total/1024**3:.1f} GB total")
    except Exception:
        pass
    try:
        load = subprocess.run(["sysctl", "-n", "vm.loadavg"],
                              capture_output=True, text=True, timeout=3)
        if load.returncode == 0:
            lines.append(f"Load avg: {load.stdout.strip()}")
    except Exception:
        pass
    try:
        total, used, free = shutil.disk_usage("/")
        lines.append(f"Disk: {used/1024**3:.1f}/{total/1024**3:.1f} GB ({free/1024**3:.1f} free)")
    except Exception:
        pass
    return "\n".join(lines) if lines else "Could not retrieve system info."


async def _exec_web_search(query: str, max_results: int = 5) -> str:
    """
    Web search. Uses Brave Search API if BRAVE_SEARCH_API_KEY is set,
    otherwise falls back to DuckDuckGo Instant Answer API.
    """
    if cfg.brave_search_api_key:
        return await _brave_search(query, max_results)
    return await _ddg_search(query)


async def _brave_search(query: str, max_results: int) -> str:
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            resp = await c.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": max_results, "text_decorations": False},
                headers={"Accept": "application/json",
                         "X-Subscription-Token": cfg.brave_search_api_key},
            )
            data = resp.json()
        results = data.get("web", {}).get("results", [])
        if not results:
            return "No results found."
        lines = []
        for r in results[:max_results]:
            lines.append(f"**{r.get('title', '')}**\n{r.get('url', '')}\n{r.get('description', '')}")
        return "\n\n".join(lines)
    except Exception as e:
        return f"[brave_search error] {e}"


async def _ddg_search(query: str) -> str:
    """DuckDuckGo Instant Answer API — no key required."""
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as c:
            resp = await c.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"},
                headers={"User-Agent": "buddy-agent/1.0"},
            )
            data = resp.json()
        parts = []
        abstract = data.get("AbstractText", "")
        if abstract:
            parts.append(f"**Summary:** {abstract}\nSource: {data.get('AbstractURL', '')}")
        for item in data.get("RelatedTopics", [])[:4]:
            if isinstance(item, dict) and item.get("Text"):
                parts.append(f"• {item['Text']}")
        if not parts:
            return (
                f"DuckDuckGo returned no instant answer for '{query}'. "
                "Try a more specific query, or set BRAVE_SEARCH_API_KEY for full web results."
            )
        return "\n\n".join(parts)
    except Exception as e:
        return f"[web_search error] {e}"


async def _exec_http_get(url: str, timeout: int = 10) -> str:
    """Fetch a URL and return the first 4 KB of response text."""
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as c:
            resp = await c.get(url, headers={"User-Agent": "buddy-agent/1.0"})
            text = resp.text[:4096]
        return f"[HTTP {resp.status_code} {url}]\n{text}"
    except Exception as e:
        return f"[http_get error] {e}"


async def _exec_memory_search(query: str, n: int = 5) -> str:
    from buddy.memory.vectors import search_memory
    try:
        loop = asyncio.get_event_loop()
        from buddy.llm.router import _GRADE_EXECUTOR
        results = await loop.run_in_executor(_GRADE_EXECUTOR, search_memory, query, n)
        if not results:
            return "No relevant memories found."
        return "\n".join(f"• {r['text'][:200]}" for r in results)
    except Exception as e:
        return f"[memory_search error] {e}"


async def _exec_remember_fact(key: str, value: str) -> str:
    from buddy.memory.store import upsert_fact
    try:
        upsert_fact(key, value, source="agent")
        return f"Remembered: {key} = {value}"
    except Exception as e:
        return f"[remember_fact error] {e}"


async def _exec_list_tasks(status: str = "") -> str:
    from buddy.memory.store import list_tasks
    try:
        tasks = list_tasks(status=status or None)
        if not tasks:
            return "No tasks found."
        return "\n".join(
            f"[{t['status'].upper()}] {t['title']} (id: {t['id'][:8]})"
            for t in tasks
        )
    except Exception as e:
        return f"[list_tasks error] {e}"


async def _exec_create_task(title: str) -> str:
    from buddy.memory.store import create_task
    try:
        task_id = create_task(title)
        return f"Task created: '{title}' (id: {task_id[:8]})"
    except Exception as e:
        return f"[create_task error] {e}"


async def _exec_forest_status() -> str:
    from buddy.api.admin import is_test_mode
    if is_test_mode():
        return "Forest monitoring is paused (test mode active)."
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            resp = await c.get(f"{cfg.forest_host}/forest/status")
            d = resp.json()
        status = d.get("status", "unknown")
        if status == "offline":
            return "Forest swarm is offline."
        active = d.get("active_incidents", [])
        sev = d.get("severity_breakdown", {})
        summary = (
            f"Status: {status} | Logged: {d.get('total_logged', 0)} incidents | "
            f"Chain: {d.get('chain_length', 0)}"
        )
        if sev:
            summary += " | Severity: " + ", ".join(f"{k}:{v}" for k, v in sev.items())
        if active:
            summary += f"\nActive incidents:\n" + "\n".join(
                f"  [{i['severity']}] {i['threat_type']} ({i.get('phase', '')})"
                for i in active
            )
        return summary
    except Exception as e:
        return f"[forest_status error] {e}"


# ── Tool definitions ────────────────────────────────────────────────────────────

TOOLS: list[ToolDef] = [

    ToolDef(
        schema={
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read the contents of a file. Allowed paths: ~/BuddyVault/ and the configured allow-list.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Absolute or ~ path to the file"}
                    },
                    "required": ["path"],
                },
            },
        },
        execute=lambda args: _exec_read_file(args["path"]),
    ),

    ToolDef(
        schema={
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Write text content to a file inside ~/BuddyVault/. Creates directories as needed.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Path inside ~/BuddyVault/"},
                        "content": {"type": "string", "description": "Text content to write"},
                        "overwrite": {"type": "boolean", "description": "Overwrite if exists (default true)", "default": True},
                    },
                    "required": ["path", "content"],
                },
            },
        },
        execute=lambda args: _exec_write_file(args["path"], args["content"], args.get("overwrite", True)),
    ),

    ToolDef(
        schema={
            "type": "function",
            "function": {
                "name": "append_file",
                "description": "Append text to an existing file inside ~/BuddyVault/ (or create it).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
            },
        },
        execute=lambda args: _exec_append_file(args["path"], args["content"]),
    ),

    ToolDef(
        schema={
            "type": "function",
            "function": {
                "name": "list_directory",
                "description": "List files and subdirectories in an allowed directory.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Directory path (default: ~/BuddyVault)", "default": "~/BuddyVault"},
                    },
                    "required": [],
                },
            },
        },
        execute=lambda args: _exec_list_directory(args.get("path", "~/BuddyVault")),
    ),

    ToolDef(
        schema={
            "type": "function",
            "function": {
                "name": "search_files",
                "description": "Find files matching a glob pattern inside an allowed directory.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": "Glob pattern e.g. '*.md' or 'test_*.py'"},
                        "directory": {"type": "string", "description": "Root directory to search (default: ~/BuddyVault)", "default": "~/BuddyVault"},
                    },
                    "required": ["pattern"],
                },
            },
        },
        execute=lambda args: _exec_search_files(args["pattern"], args.get("directory", "~/BuddyVault")),
    ),

    ToolDef(
        schema={
            "type": "function",
            "function": {
                "name": "shell_execute",
                "description": "Run a shell command. REQUIRES human approval before execution. Use for system tasks, file ops, or running scripts.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "Shell command to run"},
                    },
                    "required": ["command"],
                },
            },
        },
        execute=lambda args: _exec_shell_execute(args["command"]),
        human_gate=True,
    ),

    ToolDef(
        schema={
            "type": "function",
            "function": {
                "name": "run_python",
                "description": "Execute a short Python snippet (no I/O, no imports of os/sys/subprocess). Returns stdout. 10s timeout.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string", "description": "Python code to execute"},
                        "timeout": {"type": "integer", "description": "Max seconds (default 10)", "default": 10},
                    },
                    "required": ["code"],
                },
            },
        },
        execute=lambda args: _exec_run_python(args["code"], args.get("timeout", 10)),
    ),

    ToolDef(
        schema={
            "type": "function",
            "function": {
                "name": "get_datetime",
                "description": "Get the current local date and time.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        execute=lambda args: _exec_get_datetime(),
    ),

    ToolDef(
        schema={
            "type": "function",
            "function": {
                "name": "get_sysinfo",
                "description": "Get current RAM usage, CPU load average, and disk space.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        execute=lambda args: _exec_get_sysinfo(),
    ),

    ToolDef(
        schema={
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "Search the web for current information. Uses Brave Search if API key configured, else DuckDuckGo.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "max_results": {"type": "integer", "description": "Max results (default 5)", "default": 5},
                    },
                    "required": ["query"],
                },
            },
        },
        execute=lambda args: _exec_web_search(args["query"], args.get("max_results", 5)),
    ),

    ToolDef(
        schema={
            "type": "function",
            "function": {
                "name": "http_get",
                "description": "Fetch a URL and return the first 4 KB of response text.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "URL to fetch"},
                        "timeout": {"type": "integer", "description": "Timeout in seconds (default 10)", "default": 10},
                    },
                    "required": ["url"],
                },
            },
        },
        execute=lambda args: _exec_http_get(args["url"], args.get("timeout", 10)),
    ),

    ToolDef(
        schema={
            "type": "function",
            "function": {
                "name": "memory_search",
                "description": "Search buddy's vector memory for relevant past conversations and facts.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "n": {"type": "integer", "description": "Number of results (default 5)", "default": 5},
                    },
                    "required": ["query"],
                },
            },
        },
        execute=lambda args: _exec_memory_search(args["query"], args.get("n", 5)),
    ),

    ToolDef(
        schema={
            "type": "function",
            "function": {
                "name": "remember_fact",
                "description": "Persist a key=value fact to memory for future sessions.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "description": "Fact key (no spaces)"},
                        "value": {"type": "string", "description": "Fact value"},
                    },
                    "required": ["key", "value"],
                },
            },
        },
        execute=lambda args: _exec_remember_fact(args["key"], args["value"]),
    ),

    ToolDef(
        schema={
            "type": "function",
            "function": {
                "name": "list_tasks",
                "description": "List tasks from the task queue.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string", "description": "Filter by status: queued, running, done, failed (empty = all)", "default": ""},
                    },
                    "required": [],
                },
            },
        },
        execute=lambda args: _exec_list_tasks(args.get("status", "")),
    ),

    ToolDef(
        schema={
            "type": "function",
            "function": {
                "name": "create_task",
                "description": "Add a new task to the task queue.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Task title"},
                    },
                    "required": ["title"],
                },
            },
        },
        execute=lambda args: _exec_create_task(args["title"]),
    ),

    ToolDef(
        schema={
            "type": "function",
            "function": {
                "name": "forest_status",
                "description": "Get the current Forest blue-team security swarm status, active incidents, and severity breakdown.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        execute=lambda args: _exec_forest_status(),
    ),
]

# ── Registry lookups ───────────────────────────────────────────────────────────

_TOOL_MAP: dict[str, ToolDef] = {t.schema["function"]["name"]: t for t in TOOLS}
TOOL_SCHEMAS: list[dict] = [t.schema for t in TOOLS]


def get_tool(name: str) -> ToolDef | None:
    return _TOOL_MAP.get(name)


async def execute_tool(name: str, args: dict) -> str:
    """Dispatch a tool call by name. Returns result string."""
    tool = _TOOL_MAP.get(name)
    if not tool:
        available = ", ".join(_TOOL_MAP.keys())
        return f"[Unknown tool '{name}'. Available: {available}]"
    return await tool.execute(args)

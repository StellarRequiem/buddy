#!/usr/bin/env python3
"""
seed_demo.py — Populate BuddyVault with realistic demo conversations.

Creates 5 sessions that showcase buddy's core capabilities:
  1. Forest security monitoring + incident triage
  2. Agentic tool chain (web search → file write → shell)
  3. qwen3 thinking mode — complex reasoning visible in UI
  4. Memory + user facts recall
  5. Code workflow (git status → code search → run_python)

Usage:
    # Use the default vault (~/.BuddyVault):
    python scripts/seed_demo.py

    # Or point at a custom vault:
    VAULT_PATH=/tmp/demo-vault python scripts/seed_demo.py

    # Wipe existing sessions and start clean:
    python scripts/seed_demo.py --reset
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

# ── Make sure buddy is importable from project root ───────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Apply VAULT_PATH before importing buddy.config
if vault_env := os.environ.get("VAULT_PATH"):
    os.environ["VAULT_PATH"] = vault_env
    os.environ["DB_PATH"] = str(Path(vault_env) / "buddy.db")

from buddy.config import settings                          # noqa: E402
from buddy.memory.db import init_db                        # noqa: E402
from buddy.memory.store import (                           # noqa: E402
    append_message, upsert_fact,
    log_tool_call, log_grade, log_audit,
)


# ── ANSI colours ──────────────────────────────────────────────────────────────
_GREEN  = "\033[32m"
_CYAN   = "\033[36m"
_YELLOW = "\033[33m"
_RESET  = "\033[0m"

def _ok(msg: str)   -> None: print(f"  {_GREEN}✓{_RESET}  {msg}")
def _info(msg: str) -> None: print(f"  {_CYAN}·{_RESET}  {msg}")
def _warn(msg: str) -> None: print(f"  {_YELLOW}!{_RESET}  {msg}")


# ── Session data ──────────────────────────────────────────────────────────────

SESSIONS: list[dict] = [
    # ── Session 1: Forest incident triage ─────────────────────────────────────
    {
        "id": "demo-forest-triage",
        "label": "Forest incident triage",
        "messages": [
            ("user",      "What's the current Forest security status?"),
            ("assistant", "I'll check the Forest monitoring swarm right now.\n\n"
                          "**[Tool: forest_status]**\n\n"
                          "Forest is **CRITICAL** — 3 active incidents detected:\n\n"
                          "| # | Severity | Component | Summary |\n"
                          "|---|----------|-----------|----|\n"
                          "| 1 | 🔴 CRITICAL | API Gateway | Anomalous egress spike — 4.2 GB in 8 min |\n"
                          "| 2 | 🟠 HIGH    | Auth Service | Credential stuffing attempt — 1,200 req/min |\n"
                          "| 3 | 🟡 MEDIUM  | Telemetry   | Log ingestion lag >45 s |\n\n"
                          "The egress spike is the most urgent. Want me to pull full details "
                          "on incident #1?",
                          "qwen2.5:14b"),
            ("user",      "Yes, pull the full details on incident 1 and also check "
                          "if there are any recent scan findings."),
            ("assistant", "**[Tool: forest_incidents]** → `severity=CRITICAL, limit=5`\n\n"
                          "**Incident INC-2024-0891** — API Gateway Egress Anomaly\n"
                          "- **Detected:** 2024-11-14 03:47:12 UTC\n"
                          "- **Severity:** CRITICAL\n"
                          "- **Destination IPs:** 185.220.101.x range (Tor exit nodes)\n"
                          "- **Volume:** 4.2 GB outbound in 8 minutes\n"
                          "- **Affected service:** `api-gw-prod-3`\n"
                          "- **Recommended action:** Isolate `api-gw-prod-3`, rotate "
                          "credentials for all services it proxies\n\n"
                          "**[Tool: forest_scan]** → running full environment scan…\n\n"
                          "Scan complete. **2 new findings:**\n"
                          "1. `api-gw-prod-3` container has outbound rule to `0.0.0.0/0` "
                          "— should be allowlisted destinations only\n"
                          "2. `auth-svc` rate-limit config missing for `/token` endpoint\n\n"
                          "**My recommendation:** immediate isolation of `api-gw-prod-3` "
                          "and patch `auth-svc` rate limiting before market open. "
                          "Shall I draft the incident runbook?",
                          "qwen2.5:14b"),
            ("user",      "Draft the runbook as a markdown file."),
            ("assistant", "**[Tool: write_file]** → `path=~/BuddyVault/notes/INC-2024-0891-runbook.md`\n\n"
                          "Runbook saved. Here's the summary:\n\n"
                          "```\n# INC-2024-0891 Response Runbook\n"
                          "## Phase 1 — Containment (0–15 min)\n"
                          "- [ ] kubectl cordon api-gw-prod-3\n"
                          "- [ ] Block 185.220.101.0/24 at perimeter FW\n"
                          "- [ ] Rotate API gateway service account credentials\n"
                          "## Phase 2 — Investigation (15–60 min)\n"
                          "- [ ] Capture network flow logs from api-gw-prod-3\n"
                          "- [ ] Check audit trail for privilege escalation events\n"
                          "## Phase 3 — Remediation\n"
                          "- [ ] Patch egress firewall rule to allowlist only\n"
                          "- [ ] Add rate limit to /token endpoint\n"
                          "```\n\n"
                          "File is at `~/BuddyVault/notes/INC-2024-0891-runbook.md`. "
                          "Ping me when you're ready to start Phase 1.",
                          "qwen2.5:14b"),
        ],
        "tool_calls": [
            ("forest_status",    True, 312),
            ("forest_incidents", True, 489),
            ("forest_scan",      True, 1204),
            ("write_file",       True, 87),
        ],
        "grade": (78.5, True),
    },

    # ── Session 2: Agentic tool chain ─────────────────────────────────────────
    {
        "id": "demo-tool-chain",
        "label": "Agentic tool chain — research + write",
        "messages": [
            ("user",      "Search for the latest news on Anthropic's Claude tool use, "
                          "summarise it, and save the summary to a file."),
            ("assistant", "On it — I'll chain three tools: search, summarise, save.\n\n"
                          "**[Tool: web_search]** → `query='Anthropic Claude tool use 2024'`\n\n"
                          "Found 6 results. Top stories:\n"
                          "- *Claude 3.5 achieves 49% on SWE-bench with computer use* (Anthropic blog)\n"
                          "- *Native tool-calling vs text directives: latency comparison* (LessWrong)\n"
                          "- *Claude as an orchestrator for multi-step agentic workflows* (Hacker News)\n\n"
                          "**[Tool: get_datetime]**  →  `2024-11-14 09:31 UTC`\n\n"
                          "**[Tool: write_file]** → `path=~/BuddyVault/notes/claude-tool-use-digest.md`\n\n"
                          "Saved. Summary:\n\n"
                          "> Claude's native tool-calling lets models invoke structured JSON "
                          "functions and receive typed results — faster and more reliable than "
                          "parsing free-text directives. The SWE-bench 49% result used a "
                          "computer-use loop where Claude controlled a real browser and terminal "
                          "across hundreds of steps.\n\n"
                          "Anything else you'd like me to research?",
                          "qwen2.5:14b"),
            ("user",      "Now run a quick Python snippet to count how many words are in "
                          "that summary file."),
            ("assistant", "**[Tool: read_file]** → `path=~/BuddyVault/notes/claude-tool-use-digest.md`\n\n"
                          "**[Tool: run_python]**\n"
                          "```python\ntext = open('claude-tool-use-digest.md').read()\n"
                          "words = len(text.split())\nprint(f'Word count: {words}')\n```\n\n"
                          "Result: **Word count: 74**\n\n"
                          "The digest is concise — 74 words. Want me to expand any section?",
                          "qwen2.5:14b"),
        ],
        "tool_calls": [
            ("web_search",   True, 943),
            ("get_datetime", True, 8),
            ("write_file",   True, 53),
            ("read_file",    True, 21),
            ("run_python",   True, 192),
        ],
        "grade": (82.0, True),
    },

    # ── Session 3: qwen3 thinking mode ────────────────────────────────────────
    {
        "id": "demo-qwen3-thinking",
        "label": "qwen3 extended reasoning — probability puzzle",
        "messages": [
            ("user",      "Three boxes: one has a car, two have goats. You pick box 1. "
                          "The host opens box 3 (a goat). Should you switch to box 2? "
                          "Show your reasoning."),
            ("assistant", "<think>\nLet me work through the Monty Hall problem carefully.\n\n"
                          "Initial setup: P(car in box 1) = 1/3, P(car in box 2) = 1/3, "
                          "P(car in box 3) = 1/3.\n\n"
                          "After I pick box 1, the host — who knows where the car is — "
                          "opens box 3 (goat). Key insight: the host's action is *not* "
                          "random. He cannot open box 1 (I chose it) and he will never "
                          "reveal the car. So his action carries information.\n\n"
                          "Using Bayes' theorem:\n"
                          "- If car is in box 1 (prob 1/3): host can open box 2 or 3 — "
                          "he picks box 3 with prob 1/2.\n"
                          "- If car is in box 2 (prob 1/3): host must open box 3 — "
                          "prob 1.\n"
                          "- If car is in box 3 (prob 1/3): host must open box 2 — "
                          "prob 0 of opening box 3.\n\n"
                          "P(host opens 3) = (1/3)(1/2) + (1/3)(1) + (1/3)(0) = 1/2\n\n"
                          "P(car in box 1 | host opens 3) = (1/3 · 1/2) / (1/2) = 1/3\n"
                          "P(car in box 2 | host opens 3) = (1/3 · 1) / (1/2) = 2/3\n\n"
                          "Switching wins with probability 2/3.\n</think>\n\n"
                          "**Yes, you should switch.** Here's the full reasoning:\n\n"
                          "Before the host acts, each box has a 1/3 chance of hiding the car. "
                          "After you pick box 1, the host opens box 3 (a goat) — but this "
                          "isn't random. The host *knows* where the car is and will never "
                          "reveal it.\n\n"
                          "Using Bayes' theorem:\n\n"
                          "| Scenario | Prior | P(host opens 3) | Posterior |\n"
                          "|----------|-------|------------------|-----------|\n"
                          "| Car in box 1 | 1/3 | 1/2 | **1/3** |\n"
                          "| Car in box 2 | 1/3 | 1   | **2/3** |\n"
                          "| Car in box 3 | 1/3 | 0   | 0 |\n\n"
                          "**Switching wins 2/3 of the time. Staying wins only 1/3.**\n\n"
                          "Counterintuitive but provably correct — the host's constrained "
                          "action transfers probability mass from box 3 to box 2.",
                          "qwen3:14b"),
        ],
        "tool_calls": [],
        "grade": (91.5, True),
    },

    # ── Session 4: Memory + user facts ────────────────────────────────────────
    {
        "id": "demo-memory-recall",
        "label": "Memory recall + personalisation",
        "messages": [
            ("user",      "My name is Alex and I work on the Forest blue-team swarm. "
                          "Remember that I prefer concise bullet answers."),
            ("assistant", "Got it, Alex! I've saved:\n"
                          "- **Name:** Alex\n"
                          "- **Project:** Forest blue-team swarm\n"
                          "- **Preference:** concise bullet answers\n\n"
                          "I'll keep that in mind for every response going forward.",
                          "qwen2.5:14b"),
            ("user",      "What do you know about me?"),
            ("assistant", "**[Tool: memory_search]** → `query='user preferences project'`\n\n"
                          "Here's what I know about you:\n"
                          "- **Name:** Alex\n"
                          "- **Project:** Forest blue-team AI monitoring swarm\n"
                          "- **Answer style:** concise bullets preferred\n"
                          "- **Platform:** macOS, M4 Mac Mini 16 GB\n"
                          "- **Recent focus:** agentic tool-calling, security incident automation",
                          "qwen2.5:14b"),
            ("user",      "Set a task to review the audit log every Monday morning."),
            ("assistant", "**[Tool: create_task]** → `title='Weekly audit log review'`\n\n"
                          "Task created:\n"
                          "- **ID:** `task-0042`\n"
                          "- **Title:** Weekly audit log review\n"
                          "- **Status:** queued\n\n"
                          "Reminder: I can't set calendar alerts directly, but you can wire "
                          "this task ID into a cron job via `GET /tasks` on Monday mornings.",
                          "qwen2.5:14b"),
        ],
        "tool_calls": [
            ("remember_fact",  True, 34),
            ("remember_fact",  True, 28),
            ("memory_search",  True, 144),
            ("create_task",    True, 41),
        ],
        "grade": (76.0, True),
    },

    # ── Session 5: Code workflow ───────────────────────────────────────────────
    {
        "id": "demo-code-workflow",
        "label": "Code workflow — git + search + run",
        "messages": [
            ("user",      "What's the git status of the buddy repo and are there any "
                          "Python files with TODO comments?"),
            ("assistant", "**[Tool: git_status]** → `path=~/Projects/buddy`\n\n"
                          "```\nOn branch main\nYour branch is up to date with 'origin/main'.\n"
                          "nothing to commit, working tree clean\n```\n\n"
                          "**[Tool: code_search]** → `pattern='# TODO', path=~/Projects/buddy`\n\n"
                          "Found 3 TODOs:\n"
                          "- `buddy/llm/agent.py:187` — `# TODO: add retry on 503`\n"
                          "- `buddy/tools/tool_registry.py:412` — `# TODO: cache http_get results`\n"
                          "- `tests/test_integration.py:88` — `# TODO: test streaming endpoint`\n\n"
                          "All low-priority. The streaming test is the most useful to add — "
                          "want me to scaffold it?",
                          "qwen2.5:14b"),
            ("user",      "Run a quick benchmark — how fast does the echo tool respond?"),
            ("assistant", "**[Tool: run_python]**\n"
                          "```python\nimport time, statistics\ntimes = []\n"
                          "for _ in range(10):\n"
                          "    t = time.monotonic()\n"
                          "    # simulate get_datetime tool latency\n"
                          "    _ = __import__('datetime').datetime.utcnow().isoformat()\n"
                          "    times.append((time.monotonic() - t) * 1000)\n"
                          "print(f'mean={statistics.mean(times):.2f}ms  "
                          "p99={max(times):.2f}ms')\n```\n\n"
                          "Result: `mean=0.04ms  p99=0.12ms`\n\n"
                          "In-process tools (datetime, sysinfo) are sub-millisecond. "
                          "HTTP tools (web_search, forest_*) dominate latency — "
                          "300–1200 ms depending on upstream.",
                          "qwen2.5:14b"),
            ("user",      "Show the last 5 git commits."),
            ("assistant", "**[Tool: git_log]** → `path=~/Projects/buddy, n=5`\n\n"
                          "```\nabc1234  feat: add forest_incidents + forest_scan tools\n"
                          "def5678  feat: qwen3 think-tag streaming parser\n"
                          "ghi9012  feat: admin tool test runner + toggle endpoints\n"
                          "jkl3456  feat: audit_log migration + APIKeyMiddleware\n"
                          "mno7890  test: 19 FastAPI integration tests (55% coverage)\n```",
                          "qwen2.5:14b"),
        ],
        "tool_calls": [
            ("git_status",  True, 218),
            ("code_search", True, 534),
            ("run_python",  True, 167),
            ("git_log",     True, 205),
        ],
        "grade": (80.5, True),
    },
]


# ── User facts to seed ────────────────────────────────────────────────────────

FACTS: dict[str, tuple[str, str]] = {
    "user_name":      ("Alex",                               "user"),
    "user_project":   ("Forest blue-team swarm",             "user"),
    "user_platform":  ("macOS, M4 Mac Mini 16 GB",           "inferred"),
    "answer_style":   ("concise bullets",                    "user"),
    "user_timezone":  ("America/Chicago",                    "inferred"),
    "buddy_version":  ("0.3.0",                              "system"),
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _seed_session(sess: dict) -> None:
    sid = sess["id"]
    for role, content, *rest in sess["messages"]:
        model = rest[0] if rest else ""
        append_message(sid, role, content, model)

    for tool_name, success, latency in sess.get("tool_calls", []):
        log_tool_call(
            tool_name=tool_name,
            success=success,
            latency_ms=latency,
            session_id=sid,
            args_summary="demo seed",
            result_preview="(demo data)",
        )
        # Tiny sleep to stagger timestamps so ORDER BY id gives a nice sequence
        time.sleep(0.002)

    if grade := sess.get("grade"):
        score, passed = grade
        log_grade(
            session_id=sid,
            call_type="chat",
            model="qwen2.5:14b",
            composite_score=score,
            passed=passed,
            detail={"seeded": True},
        )

    log_audit("chat_message", f"demo session seeded: {sid}", session_id=sid)


def _reset_sessions(session_ids: list[str]) -> None:
    from buddy.memory.db import get_conn
    with get_conn() as conn:
        for sid in session_ids:
            conn.execute("DELETE FROM conversations WHERE session_id=?", (sid,))
            conn.execute("DELETE FROM grading_log   WHERE session_id=?", (sid,))
            conn.execute("DELETE FROM tool_calls    WHERE session_id=?", (sid,))
    _ok(f"Cleared {len(session_ids)} existing demo sessions")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reset", action="store_true",
                        help="Wipe existing demo sessions before seeding")
    parser.add_argument("--vault", metavar="PATH",
                        help="Override vault path (also accepts VAULT_PATH env var)")
    args = parser.parse_args()

    if args.vault:
        settings.vault_path = Path(args.vault)
        settings.db_path    = Path(args.vault) / "buddy.db"

    settings.ensure_vault()
    init_db()

    print(f"\n{_CYAN}buddy demo seed{_RESET}  —  vault: {settings.vault_path}\n")

    if args.reset:
        _reset_sessions([s["id"] for s in SESSIONS])

    # Seed user facts
    print("Seeding user facts …")
    for key, (value, source) in FACTS.items():
        upsert_fact(key, value, source)
        _ok(f"{key} = {value!r}")

    # Seed sessions
    print("\nSeeding demo sessions …")
    seeded = 0
    for sess in SESSIONS:
        n_msgs  = len(sess["messages"])
        n_tools = len(sess.get("tool_calls", []))
        _info(f"[{sess['id']}]  {sess['label']}  "
              f"({n_msgs} messages, {n_tools} tool calls)")
        _seed_session(sess)
        _ok(f"done — grade {sess.get('grade', ('–', '–'))[0]}")
        seeded += 1

    print(f"\n{_GREEN}✓ Seed complete{_RESET}  —  "
          f"{seeded} sessions, {len(FACTS)} facts, "
          f"{sum(len(s.get('tool_calls',[])) for s in SESSIONS)} tool-call records\n"
          f"  DB: {settings.db_path}\n"
          f"  Start buddy and open http://127.0.0.1:7437 to see the demo data.\n")


if __name__ == "__main__":
    main()

"""System prompts and prompt builders for Buddy."""
from __future__ import annotations

from buddy.memory.store import get_facts
from buddy.tools.plugin_loader import plugin_system_prompt_section

BUDDY_SYSTEM_PROMPT = """You are Buddy, a local-first personal assistant running on Alexander's Mac Mini M4.

IDENTITY:
- You run locally (phi4-mini by default, Claude Opus 4.7 for complex tasks)
- You have persistent memory across sessions (SQLite + vector store)
- You can read files in ~/BuddyVault/ and a small allow-list
- Shell commands require explicit human confirmation before execution
- You never connect to external services without telling the user

STYLE:
- Direct and concise. No filler. No "great question."
- Push back when the user is steering into walls
- Flag when you're guessing vs. confident
- Admit what you don't know
- Answer factual questions directly from your knowledge — do NOT output special commands for simple questions

MEMORY:
When you learn something persistent about the user, add a line AFTER your response in this format:
REMEMBER: key=value
Example: REMEMBER: preferred_editor=neovim

TOOL CALLS (only when genuinely needed):
When you need to read a specific file:
READ_FILE: /path/to/file

When you need a shell command (will trigger human confirmation):
SHELL: command here

When you want to call a plugin tool:
PLUGIN: name <args>
"""


def build_chat_prompt(history: list[dict], user_message: str,
                       memory_context: list[dict] | None = None) -> list[dict]:
    """Assemble messages list for the LLM."""
    facts = get_facts()
    facts_str = "\n".join(f"- {k}: {v}" for k, v in facts.items()) if facts else "None yet."

    mem_str = ""
    if memory_context:
        mem_str = "\n\nRELEVANT MEMORY:\n" + "\n".join(
            f"- {m['text'][:200]}" for m in memory_context
        )

    system = BUDDY_SYSTEM_PROMPT
    plugin_section = plugin_system_prompt_section()
    if plugin_section:
        system += f"\n\n{plugin_section}"
    if facts_str or mem_str:
        system += f"\n\nUSER FACTS:\n{facts_str}{mem_str}"

    messages = [{"role": "system", "content": system}]
    for msg in history[-20:]:   # keep last 20 turns in context
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": user_message})
    return messages

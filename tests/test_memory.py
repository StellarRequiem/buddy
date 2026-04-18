"""Tests for SQLite memory store."""
import pytest
from buddy.memory.db import init_db
from buddy.memory.store import (
    append_message, get_history, upsert_fact, get_facts,
    create_task, update_task, list_tasks,
)


@pytest.fixture(autouse=True)
def use_temp_db(tmp_path, monkeypatch):
    """Point DB at a temp file for each test."""
    from buddy import config
    db = tmp_path / "test_buddy.db"
    monkeypatch.setattr(config.settings, "db_path", db)
    init_db()


def test_append_and_get_history():
    sid = "test-session-1"
    append_message(sid, "user", "hello")
    append_message(sid, "assistant", "hi there", model="local")
    history = get_history(sid)
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[1]["content"] == "hi there"


def test_history_scoped_to_session():
    append_message("sess-a", "user", "msg a")
    append_message("sess-b", "user", "msg b")
    assert len(get_history("sess-a")) == 1
    assert len(get_history("sess-b")) == 1


def test_upsert_fact():
    upsert_fact("editor", "neovim")
    assert get_facts()["editor"] == "neovim"
    upsert_fact("editor", "vscode")
    assert get_facts()["editor"] == "vscode"


def test_task_lifecycle():
    task_id = create_task("Deploy to prod", {"priority": "high"})
    tasks = list_tasks()
    assert any(t["id"] == task_id for t in tasks)

    update_task(task_id, "running")
    running = list_tasks(status="running")
    assert any(t["id"] == task_id for t in running)

    update_task(task_id, "done", result="Deployed at 03:14")
    done = list_tasks(status="done")
    assert any(t["id"] == task_id for t in done)

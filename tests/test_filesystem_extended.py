"""Extended filesystem and metrics tests."""
import pytest
from pathlib import Path


@pytest.fixture(autouse=True)
def use_temp_vault(tmp_path, monkeypatch):
    from buddy import config
    monkeypatch.setattr(config.settings, "vault_path", tmp_path)
    monkeypatch.setattr(config.settings, "db_path", tmp_path / "test.db")
    from buddy.memory.db import init_db
    init_db()


# ── write_file / append_file / search_files ───────────────────────────────────

def test_write_file(tmp_path, monkeypatch):
    from buddy.tools.filesystem import write_file, read_file
    path = str(tmp_path / "hello.txt")
    write_file(path, "world")
    assert read_file(path) == "world"


def test_write_file_creates_subdirs(tmp_path):
    from buddy.tools.filesystem import write_file, read_file
    path = str(tmp_path / "a" / "b" / "c.txt")
    write_file(path, "deep")
    assert read_file(path) == "deep"


def test_write_file_no_overwrite_raises(tmp_path):
    from buddy.tools.filesystem import write_file
    p = str(tmp_path / "once.txt")
    write_file(p, "first")
    with pytest.raises(FileExistsError):
        write_file(p, "second", overwrite=False)


def test_append_file(tmp_path):
    from buddy.tools.filesystem import append_file, read_file
    p = str(tmp_path / "log.txt")
    append_file(p, "line1\n")
    append_file(p, "line2\n")
    content = read_file(p)
    assert "line1" in content and "line2" in content


def test_search_files(tmp_path, monkeypatch):
    from buddy.tools.filesystem import search_files
    (tmp_path / "foo.py").write_text("x")
    (tmp_path / "bar.txt").write_text("y")
    results = search_files("*.py", str(tmp_path))
    assert any("foo.py" in r for r in results)
    assert all("bar.txt" not in r for r in results)


def test_write_outside_vault_rejected(tmp_path, monkeypatch):
    from buddy.tools.filesystem import write_file
    with pytest.raises(ValueError, match="Writes are restricted"):
        write_file("/tmp/escape.txt", "bad")


# ── tool call metrics ─────────────────────────────────────────────────────────

def test_log_and_get_tool_metrics():
    from buddy.memory.store import log_tool_call, get_tool_metrics
    log_tool_call("web_search", True, 350, session_id="s1",
                  args_summary="query=python", result_preview="Python is...")
    log_tool_call("web_search", True, 280, session_id="s1",
                  args_summary="query=rust")
    log_tool_call("read_file", False, 10, session_id="s1")

    data = get_tool_metrics()
    assert "aggregate" in data and "recent" in data

    agg = {r["tool_name"]: r for r in data["aggregate"]}
    assert "web_search" in agg
    assert agg["web_search"]["calls"] == 2
    assert agg["web_search"]["successes"] == 2
    assert "read_file" in agg
    assert agg["read_file"]["successes"] == 0


def test_tool_metrics_recent_order():
    from buddy.memory.store import log_tool_call, get_tool_metrics
    for i in range(5):
        log_tool_call(f"tool_{i}", True, i * 10)
    data = get_tool_metrics()
    names = [r["tool_name"] for r in data["recent"]]
    # Most recent first
    assert names[0] == "tool_4"

"""测试 memory/history.py 的会话持久化。"""

import json
from pathlib import Path

from minicode.memory.history import (
    save_history,
    load_history,
    load_latest,
    list_sessions,
    format_session_list,
)
from minicode.model.message import Message


def test_save_and_load_history(tmp_path: Path):
    """保存 → 加载 → 消息一致。"""
    history_dir = tmp_path / "history"
    session_id = "test123"
    messages = [
        Message.user("你好"),
        Message.assistant_text("你好！有什么可以帮你的？"),
        Message.user("查看文件"),
    ]

    saved_path = save_history(history_dir, session_id, messages, cwd="/tmp")
    assert saved_path is not None
    assert saved_path.is_file()

    loaded = load_history(history_dir, session_id)
    assert loaded is not None
    assert len(loaded) == 3
    assert loaded[0].text() == "你好"
    assert loaded[1].text() == "你好！有什么可以帮你的？"
    assert loaded[2].text() == "查看文件"


def test_save_empty_history_skipped(tmp_path: Path):
    """空 history 不保存。"""
    history_dir = tmp_path / "history"
    result = save_history(history_dir, "empty", [], cwd="/tmp")
    assert result is None


def test_load_nonexistent_returns_none(tmp_path: Path):
    """加载不存在的 session → None。"""
    history_dir = tmp_path / "history"
    assert load_history(history_dir, "nonexistent") is None


def test_list_sessions_sorted_by_time(tmp_path: Path):
    """list_sessions 按时间倒序。"""
    history_dir = tmp_path / "history"
    save_history(history_dir, "old", [Message.user("old")], cwd="/tmp")
    save_history(history_dir, "new", [Message.user("new")], cwd="/tmp")

    sessions = list_sessions(history_dir)
    assert len(sessions) == 2
    # new 应该在前（时间倒序）
    assert sessions[0].session_id == "new"
    assert sessions[1].session_id == "old"


def test_load_latest(tmp_path: Path):
    """load_latest 返回最近的会话。"""
    history_dir = tmp_path / "history"
    save_history(history_dir, "first", [Message.user("first")], cwd="/tmp")
    save_history(history_dir, "second", [Message.user("second")], cwd="/tmp")

    result = load_latest(history_dir)
    assert result is not None
    session_id, messages = result
    assert session_id == "second"
    assert len(messages) == 1
    assert messages[0].text() == "second"


def test_load_latest_empty(tmp_path: Path):
    """无历史时 load_latest → None。"""
    history_dir = tmp_path / "history"
    assert load_latest(history_dir) is None


def test_format_session_list(tmp_path: Path):
    """format_session_list 输出可读。"""
    history_dir = tmp_path / "history"
    save_history(history_dir, "s1", [Message.user("hello world")], cwd="/tmp")

    sessions = list_sessions(history_dir)
    output = format_session_list(sessions)
    assert "hello world" in output
    assert "s1" in output


def test_format_empty_session_list():
    """空列表 → (no saved sessions)。"""
    output = format_session_list([])
    assert "no saved sessions" in output.lower()


def test_save_overwrites_same_session(tmp_path: Path):
    """同一 session_id 再次保存 → 覆盖旧文件。"""
    history_dir = tmp_path / "history"
    save_history(history_dir, "s1", [Message.user("first")], cwd="/tmp")
    save_history(history_dir, "s1", [Message.user("first"), Message.user("second")], cwd="/tmp")

    sessions = list_sessions(history_dir)
    assert len(sessions) == 1
    assert sessions[0].message_count == 2


def test_preview_from_first_user_message(tmp_path: Path):
    """preview 取第一条 user 消息。"""
    history_dir = tmp_path / "history"
    save_history(history_dir, "s1", [
        Message.assistant_text("system init"),
        Message.user("这是用户的问题"),
    ], cwd="/tmp")

    sessions = list_sessions(history_dir)
    assert sessions[0].preview == "这是用户的问题"

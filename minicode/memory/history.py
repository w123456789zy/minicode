"""
会话历史持久化。

存储位置：.minicode/history/{session_id}.json
格式：JSON，包含 session_id、时间戳、消息数、cwd、messages 列表。

用法：
- save_history()    退出时保存当前会话
- load_history()    加载指定 session_id 的会话
- load_latest()     加载最近一次会话
- list_sessions()   列出所有已保存的会话（按时间倒序）
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from minicode.model.message import Message


@dataclass
class SessionMeta:
    """已保存会话的元信息（不含 messages）。"""
    session_id: str
    timestamp: str           # ISO 格式
    message_count: int
    cwd: str
    preview: str             # 第一条 user 消息的前 80 字符
    file_path: Path


def _history_file(history_dir: Path, session_id: str) -> Path:
    """返回某个 session 的历史文件路径。"""
    # session_id 可能含特殊字符，用安全的文件名
    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)
    return history_dir / f"{safe_id}.json"


def save_history(
    history_dir: Path,
    session_id: str,
    messages: List[Message],
    cwd: str,
) -> Optional[Path]:
    """保存会话历史到 .minicode/history/{session_id}.json。

    返回保存的文件路径；messages 为空则跳过（返回 None）。
    """
    if not messages:
        return None

    history_dir.mkdir(parents=True, exist_ok=True)

    # 取第一条 user 消息作为预览
    preview = ""
    for m in messages:
        if m.role.value == "user" and m.text():
            preview = m.text()[:80]
            break

    data = {
        "session_id": session_id,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "message_count": len(messages),
        "cwd": cwd,
        "preview": preview,
        "messages": [m.model_dump() for m in messages],
    }

    file_path = _history_file(history_dir, session_id)
    # 先写临时文件再 rename，避免写一半崩溃导致文件损坏
    tmp_path = file_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(file_path)
    return file_path


def load_history(history_dir: Path, session_id: str) -> Optional[List[Message]]:
    """加载指定 session_id 的会话。

    文件不存在或解析失败 → 返回 None。
    """
    file_path = _history_file(history_dir, session_id)
    if not file_path.is_file():
        return None
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
        raw_msgs = data.get("messages", [])
        return [Message.model_validate(m) for m in raw_msgs]
    except (json.JSONDecodeError, KeyError, ValueError, OSError):
        return None


def load_latest(history_dir: Path) -> Optional[tuple]:
    """加载最近一次会话。

    返回 (session_id, messages) 或 None（无历史）。
    """
    sessions = list_sessions(history_dir)
    if not sessions:
        return None
    latest = sessions[0]  # list_sessions 已按时间倒序
    msgs = load_history(history_dir, latest.session_id)
    if msgs is None:
        return None
    return (latest.session_id, msgs)


def list_sessions(history_dir: Path) -> List[SessionMeta]:
    """列出所有已保存的会话，按时间倒序。

    只读元信息（不加载 messages），轻量。
    """
    if not history_dir.is_dir():
        return []

    result: List[SessionMeta] = []
    for f in history_dir.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            result.append(SessionMeta(
                session_id=data.get("session_id", f.stem),
                timestamp=data.get("timestamp", ""),
                message_count=data.get("message_count", 0),
                cwd=data.get("cwd", ""),
                preview=data.get("preview", ""),
                file_path=f,
            ))
        except (json.JSONDecodeError, OSError):
            continue

    # 按文件修改时间倒序（新的在前）；mtime 有亚秒精度，比 timestamp 字符串更准
    result.sort(key=lambda s: s.file_path.stat().st_mtime, reverse=True)
    return result


def format_session_list(sessions: List[SessionMeta], max_show: int = 10) -> str:
    """格式化会话列表为可读字符串。"""
    if not sessions:
        return "(no saved sessions)"

    lines = []
    for i, s in enumerate(sessions[:max_show]):
        # 时间戳截短到分钟
        ts = s.timestamp[:16].replace("T", " ") if s.timestamp else "?"
        preview = s.preview or "(empty)"
        lines.append(f"  [{i}] {s.session_id[:8]}  {ts}  ({s.message_count} msgs)  {preview}")
    if len(sessions) > max_show:
        lines.append(f"  ... and {len(sessions) - max_show} more")
    return "\n".join(lines)

"""
Hook 事件 / 响应 / 上下文 的类型定义。

事件协议（统一 JSON，Python hook 接 dict，Shell hook 接 JSON 字符串）：

    {
        "event": "tool_call_before",
        "session_id": "abc",
        "timestamp": "2026-06-19T10:30:00Z",
        "data": { ... }   # 事件相关数据（见下表）
    }

    EventName        | data 字段                                  | 可拒绝 | 可改写
    -----------------+--------------------------------------------+--------+--------
    session_start    | {cwd, project, model, ...}                 |   ✗    |   ✗
    session_end      | {duration_s, message_count}                |   ✗    |   ✗
    user_prompt_submit| {prompt}                                  |   ✓    |   ✓ prompt
    assistant_message| {text, tool_calls: [...]}                 |   ✗    |   ✗
    tool_call_before | {tool, args, call_id}                      |   ✓    |   ✓ args
    tool_call_after  | {tool, args, call_id, output, error}       |   ✗    |   ✓ output
    error            | {exc_type, exc_msg, context}               |   ✗    |   ✗
    stop             | {reason}                                  |   ✓    |   ✗
    compact          | {old_count, new_count, summary_len}        |   ✗    |   ✗

Hook 响应（Python hook 返回 dict，Shell hook stdout 输出 JSON）：

    {
        "action":  "allow" | "deny" | "modify",
        "reason":  "...",      # 可选：deny/modify 时的说明
        "data":    { ... }     # 可选：modify 时的新数据（替换 data 字段）
    }

默认 action = "allow"（hook 不返回任何东西时）。

多 hook 聚合规则（由 dispatcher 实现）：
- 并行执行所有 hook
- 任一返回 deny → 整个事件 deny
- 多个返回 modify → 按顺序串联（data 合并）
- 其余为 allow

Fail-open：hook 抛异常 / 超时 / 返回非 JSON → 视为 allow，仅记 warning。
"""

from __future__ import annotations

import enum
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


class EventName(str, Enum):
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    USER_PROMPT_SUBMIT = "user_prompt_submit"
    ASSISTANT_MESSAGE = "assistant_message"
    TOOL_CALL_BEFORE = "tool_call_before"
    TOOL_CALL_AFTER = "tool_call_after"
    ERROR = "error"
    STOP = "stop"
    COMPACT = "compact"


ALL_EVENTS: List[EventName] = list(EventName)

# 这些事件返回 deny 会真正阻止原动作
BLOCKING_EVENTS = {
    EventName.USER_PROMPT_SUBMIT,
    EventName.TOOL_CALL_BEFORE,
    EventName.STOP,
}

# 这些事件可以返回 modify 来改 data
MODIFIABLE_EVENTS = {
    EventName.USER_PROMPT_SUBMIT,
    EventName.TOOL_CALL_BEFORE,
    EventName.TOOL_CALL_AFTER,
}


class Action(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    MODIFY = "modify"


def parse_action(s: Any) -> Action:
    if isinstance(s, Action):
        return s
    if s is None:
        return Action.ALLOW
    if isinstance(s, str):
        try:
            return Action(s)
        except ValueError:
            return Action.ALLOW
    return Action.ALLOW


@dataclass
class HookEvent:
    """Hook 事件。

    用 dataclass 而不是 dict，便于 IDE 提示和静态分析。
    转 JSON 时直接 dataclasses.asdict 即可。
    """
    event: EventName
    session_id: str
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event": self.event.value,
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "data": self.data,
        }

    @classmethod
    def make(cls, name: EventName, session_id: str, **data: Any) -> "HookEvent":
        return cls(event=name, session_id=session_id, data=dict(data))


@dataclass
class HookResponse:
    """Hook 的响应。"""
    action: Action = Action.ALLOW
    reason: Optional[str] = None
    data: Optional[Dict[str, Any]] = None  # modify 时的新 data

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"action": self.action.value}
        if self.reason:
            d["reason"] = self.reason
        if self.data is not None:
            d["data"] = self.data
        return d

    @classmethod
    def allow(cls) -> "HookResponse":
        return cls(action=Action.ALLOW)

    @classmethod
    def deny(cls, reason: str) -> "HookResponse":
        return cls(action=Action.DENY, reason=reason)

    @classmethod
    def modify(cls, data: Dict[str, Any], reason: Optional[str] = None) -> "HookResponse":
        return cls(action=Action.MODIFY, data=data, reason=reason)


def parse_response(obj: Any) -> HookResponse:
    """从 dict/JSON 字符串/None 解析为 HookResponse。None → allow。"""
    if obj is None:
        return HookResponse.allow()
    if isinstance(obj, HookResponse):
        return obj
    if isinstance(obj, str):
        if not obj.strip():
            return HookResponse.allow()
        try:
            obj = json.loads(obj)
        except json.JSONDecodeError:
            return HookResponse.allow()
    if not isinstance(obj, dict):
        return HookResponse.allow()
    return HookResponse(
        action=parse_action(obj.get("action", "allow")),
        reason=obj.get("reason"),
        data=obj.get("data"),
    )


@dataclass
class HookContext:
    """传给 hook 的额外上下文（cwd、env、minicode 版本等）。"""
    cwd: Path
    project_root: Path
    minicode_version: str
    env: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cwd": str(self.cwd),
            "project_root": str(self.project_root),
            "minicode_version": self.minicode_version,
        }


class HookError(Exception):
    """Hook 执行失败（异常 / 超时 / 返回非 JSON）时抛出，由 dispatcher 捕获并降级。"""
    def __init__(self, hook_name: str, reason: str, original: Optional[BaseException] = None):
        super().__init__(f"hook {hook_name!r}: {reason}")
        self.hook_name = hook_name
        self.reason = reason
        self.original = original

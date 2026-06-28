"""
minicode.permission：工具调用前的权限确认。

设计：
- 3 个选项：1) Yes  2) Yes, and always (本次会话总是允许)  3) No
- always 集合：per-session 状态，存 tool_id → 直接放过
- 阻塞 input 询问（可注入 prompt_fn 方便测试）

参考 mimo code 类似设计（opencode 的 permission system 在 UI 层做）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, Optional, Union


class PermissionAction(str, Enum):
    """用户对一个工具调用的决策。"""
    ALLOW = "allow"
    ALLOW_ALWAYS = "allow_always"
    DENY = "deny"

    def is_allowed(self) -> bool:
        return self in (PermissionAction.ALLOW, PermissionAction.ALLOW_ALWAYS)


@dataclass
class PermissionRequest:
    """一次询问。"""
    tool_id: str
    args: Dict[str, Any] = field(default_factory=dict)
    # 给 prompt 用的额外上下文（cwd、调用方等）
    context: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PermissionResult:
    """用户的决定（可带 reason 给 user/audit 读）。"""
    action: PermissionAction
    reason: Optional[str] = None

    @classmethod
    def allow(cls) -> "PermissionResult":
        return cls(action=PermissionAction.ALLOW)

    @classmethod
    def allow_always(cls) -> "PermissionResult":
        return cls(action=PermissionAction.ALLOW_ALWAYS)

    @classmethod
    def deny(cls, reason: Optional[str] = None) -> "PermissionResult":
        return cls(action=PermissionAction.DENY, reason=reason)


# prompt 函数的类型：同步 / 异步都行
PromptFn = Callable[[PermissionRequest], Union[PermissionResult, Awaitable[PermissionResult]]]

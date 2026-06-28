"""minicode.permission：工具调用前的权限确认。

设计：
- 3 个选项：1) Yes  2) Yes, and always (本次会话总是允许)  3) No
- always 集合：per-session 状态，存 tool_id → 直接放过
- 阻塞 input 询问（可注入 prompt_fn 方便测试）
"""

from minicode.permission.types import (
    PermissionAction,
    PermissionRequest,
    PermissionResult,
    PromptFn,
)
from minicode.permission.service import PermissionService, default_prompt

__all__ = [
    "PermissionAction",
    "PermissionRequest",
    "PermissionResult",
    "PermissionService",
    "PromptFn",
    "default_prompt",
]

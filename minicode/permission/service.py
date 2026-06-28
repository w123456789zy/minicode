"""
PermissionService：per-session 的 always 状态 + 询问逻辑。

- request(req)：always 集合命中 → 直接放行；否则调 prompt_fn
- always_allow / always_deny / always_clear：CLI 显式管理
- 计数：allow / allow_always / deny
"""

from __future__ import annotations

import inspect
import logging
import sys
from typing import Any, Dict, Optional, Set

from minicode.permission.types import (
    PermissionAction,
    PermissionRequest,
    PermissionResult,
    PromptFn,
)


_log = logging.getLogger("minicode.permission")


def default_prompt(req: PermissionRequest) -> PermissionResult:
    """默认阻塞 prompt：打印工具调用，3 选项让用户选。

    提示格式：
      [permission] tool 'bash' wants to run
        args: {"command": "ls -la"}
      1) Yes  2) Yes, and always  3) No  [default 1]:

    输入 '1' / '2' / '3' 或直接回车（默认 1）/'y'/'n'/'a' 简写。
    """
    import json

    print()
    print(f"[permission] tool {req.tool_id!r} wants to run")
    args_str = json.dumps(req.args, ensure_ascii=False, default=str)
    if len(args_str) > 200:
        args_str = args_str[:200] + "..."
    print(f"            args: {args_str}")
    if req.context:
        for k, v in req.context.items():
            print(f"            {k}: {v}")
    print("            [1] Yes")
    print("            [2] Yes, and always (allow this tool for the rest of the session)")
    print("            [3] No  [default: 1]")
    sys.stdout.write("> ")
    sys.stdout.flush()
    try:
        line = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        return PermissionResult.deny("(interrupted)")

    if line in ("", "1", "y", "yes"):
        return PermissionResult.allow()
    if line in ("2", "a", "always"):
        return PermissionResult.allow_always()
    if line in ("3", "n", "no"):
        # 允许带 reason
        sys.stdout.write("reason (optional): ")
        sys.stdout.flush()
        try:
            reason = input().strip() or None
        except (EOFError, KeyboardInterrupt):
            reason = None
        return PermissionResult.deny(reason)
    # 无法识别 → 默认 deny（保守）
    return PermissionResult.deny(f"unrecognized choice: {line!r}")


class PermissionService:
    """per-session 的 permission 状态机。"""

    def __init__(
        self,
        prompt_fn: Optional[PromptFn] = None,
        session_id: str = "",
    ) -> None:
        self._prompt_fn: PromptFn = prompt_fn or default_prompt
        self._session_id = session_id
        self._always_allow: Set[str] = set()
        self._always_deny: Set[str] = set()
        # 拒绝时是否使用 prompt（v0 简化：永远 prompt）
        self._stats: Dict[str, int] = {
            "allow": 0,
            "allow_always": 0,
            "deny": 0,
            "skipped": 0,  # 因 always 直接命中
        }

    # ── 状态查询 ─────────────────────────────────────────

    def is_always_allowed(self, tool_id: str) -> bool:
        return tool_id in self._always_allow

    def is_always_denied(self, tool_id: str) -> bool:
        return tool_id in self._always_deny

    def always_allowed(self) -> Set[str]:
        return set(self._always_allow)

    def always_denied(self) -> Set[str]:
        return set(self._always_deny)

    # ── 状态修改 ─────────────────────────────────────────

    def always_allow(self, tool_id: str) -> None:
        self._always_allow.add(tool_id)
        self._always_deny.discard(tool_id)

    def always_deny(self, tool_id: str) -> None:
        self._always_deny.add(tool_id)
        self._always_allow.discard(tool_id)

    def clear(self, tool_id: Optional[str] = None) -> int:
        """清空 always 状态。返回被清的 tool 数。"""
        if tool_id is None:
            n = len(self._always_allow) + len(self._always_deny)
            self._always_allow.clear()
            self._always_deny.clear()
            return n
        n = 0
        if tool_id in self._always_allow:
            self._always_allow.discard(tool_id)
            n += 1
        if tool_id in self._always_deny:
            self._always_deny.discard(tool_id)
            n += 1
        return n

    def set_prompt_fn(self, fn: PromptFn) -> None:
        self._prompt_fn = fn

    # ── 核心询问 ─────────────────────────────────────────

    async def request(self, req: PermissionRequest) -> PermissionResult:
        """询问用户（若已在 always 集合则直接放行/拒绝）。"""
        if not isinstance(req, PermissionRequest):
            raise TypeError(f"req must be PermissionRequest, got {type(req).__name__}")
        if not req.tool_id:
            raise ValueError("PermissionRequest.tool_id must be non-empty")

        if req.tool_id in self._always_allow:
            self._stats["skipped"] += 1
            self._stats["allow"] += 1
            return PermissionResult.allow()

        if req.tool_id in self._always_deny:
            self._stats["skipped"] += 1
            self._stats["deny"] += 1
            return PermissionResult.deny("in always-deny set")

        # 调 prompt（同步 / 异步都支持）
        result = self._prompt_fn(req)
        if inspect.isawaitable(result):
            result = await result  # type: ignore[misc]

        if not isinstance(result, PermissionResult):
            raise TypeError(
                f"prompt_fn must return PermissionResult, got {type(result).__name__}"
            )

        # 记 always
        if result.action == PermissionAction.ALLOW_ALWAYS:
            self.always_allow(req.tool_id)
            self._stats["allow_always"] += 1
        elif result.action == PermissionAction.ALLOW:
            self._stats["allow"] += 1
        elif result.action == PermissionAction.DENY:
            self._stats["deny"] += 1

        return result

    # ── 状态展示 ─────────────────────────────────────────

    def status(self) -> Dict[str, Any]:
        return {
            "session_id": self._session_id,
            "always_allow": sorted(self._always_allow),
            "always_deny": sorted(self._always_deny),
            "stats": dict(self._stats),
        }

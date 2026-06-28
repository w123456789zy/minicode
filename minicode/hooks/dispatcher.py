"""
HookDispatcher：加载 + 并行执行 + 聚合多个 hook。

聚合规则（参考 opencode 风格 + 简化）：
- 同一事件可挂在多个 hook 上
- 并行执行（asyncio.gather）
- 任一返回 deny → 整体 deny（带第一个 deny 的 reason）
- 多个返回 modify → 按 order 串行：后一个的 data 覆盖前一个的 data（dict 浅合并）
- 全 allow → allow
- 任何 hook 抛错（HookError / 异常 / 超时）→ fail-open（记 warning，不影响其他 hook）

公开 API：
- dispatcher.load()                    扫描目录加载所有 hook
- dispatcher.dispatch(event, ctx)     触发事件，返回 DispatchResult
- dispatcher.hooks                    所有 hook 列表
- dispatcher.by_event(event_name)     监听某事件的 hook 列表
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from minicode.hooks.python import (
    DEFAULT_TIMEOUT_S as PY_DEFAULT_TIMEOUT,
    PythonHook,
    discover_python_hooks,
)
from minicode.hooks.shell import (
    DEFAULT_TIMEOUT_S as SH_DEFAULT_TIMEOUT,
    ShellHook,
    discover_shell_hooks,
)
from minicode.hooks.types import (
    Action,
    EventName,
    HookContext,
    HookError,
    HookEvent,
    HookResponse,
)


_log = logging.getLogger("minicode.hooks.dispatcher")


@dataclass
class HookInfo:
    """展示用：每个 hook 的元信息。"""
    name: str
    kind: str           # "python" | "shell"
    path: Path
    description: str


@dataclass
class DispatchResult:
    """dispatch() 的返回值。"""
    action: Action = Action.ALLOW
    reason: Optional[str] = None
    # 改写后的 data（如果有 modify）
    data: Optional[Dict[str, Any]] = None
    # 哪些 hook 返回了 deny（用于调试 / 日志）
    denied_by: List[str] = field(default_factory=list)
    # 哪些 hook 失败（不影响结果，只记 warning）
    failed: List[str] = field(default_factory=list)
    # 总耗时（秒）
    elapsed_s: float = 0.0

    @property
    def allowed(self) -> bool:
        return self.action == Action.ALLOW

    @property
    def denied(self) -> bool:
        return self.action == Action.DENY


Hook = Union[PythonHook, ShellHook]


class HookDispatcher:
    def __init__(self, timeout_s: float = 10.0, fail_open: bool = True):
        self._hooks: List[Hook] = []
        self._timeout_s = timeout_s
        self._fail_open = fail_open
        # 记录最近一次 dispatch 的结果（CLI 调试用）
        self.last_result: Optional[DispatchResult] = None

    # ─────────────────────────────────────────
    # 加载
    # ─────────────────────────────────────────

    def load(self, dirs: List[Path]) -> int:
        """从所有目录加载 hook，返回加载成功的数量。"""
        self._hooks.clear()
        for h in discover_python_hooks(dirs):
            self._hooks.append(h)
        for h in discover_shell_hooks(dirs):
            self._hooks.append(h)
        _log.info("loaded %d hook(s) from %s", len(self._hooks), dirs)
        return len(self._hooks)

    def reload(self, dirs: List[Path]) -> int:
        return self.load(dirs)

    def hooks(self) -> List[Hook]:
        return list(self._hooks)

    def infos(self) -> List[HookInfo]:
        return [
            HookInfo(name=h.name, kind=h.kind, path=h.path, description=h.description)
            for h in self._hooks
        ]

    def by_event(self, event: EventName) -> List[Hook]:
        """所有可能监听这个事件的 hook（hook 内部自己判断要不要处理）。"""
        return list(self._hooks)

    # ─────────────────────────────────────────
    # 触发
    # ─────────────────────────────────────────

    async def dispatch(
        self,
        event: HookEvent,
        context: HookContext,
        # 兼容：有些 hook 内部用 try/except，没我们 timeout 也行
        timeout_s: Optional[float] = None,
    ) -> DispatchResult:
        """触发事件。

        不区分"哪些 hook 关心这个事件"——把事件扔给所有 hook，hook 自己决定要不要处理。
        这样 hook 编写者自由度更高（一个 hook 可以同时处理多个事件类型）。
        """
        if not self._hooks:
            self.last_result = DispatchResult(action=Action.ALLOW)
            return self.last_result

        timeout = timeout_s or self._timeout_s
        t0 = time.monotonic()

        # 并行调用所有 hook
        tasks = [self._safe_run(h, event, context, timeout) for h in self._hooks]
        raw_results: List[HookResponse] = await asyncio.gather(*tasks, return_exceptions=False)

        result = self._aggregate(raw_results)
        result.elapsed_s = time.monotonic() - t0
        self.last_result = result
        return result

    async def _safe_run(
        self,
        hook: Hook,
        event: HookEvent,
        context: HookContext,
        timeout: float,
    ) -> HookResponse:
        """跑一个 hook，失败时按 fail_open 决定行为。"""
        try:
            return await hook.run(event, context, timeout=timeout)
        except HookError as e:
            _log.warning("%s hook %s failed: %s", hook.kind, hook.name, e.reason)
            # fail-open：返回 allow
            return HookResponse.allow()
        except Exception as e:
            # 真的意外错误：fail-open
            _log.warning("%s hook %s unexpected: %s", hook.kind, hook.name, e)
            return HookResponse.allow()

    def _aggregate(self, responses: List[HookResponse]) -> DispatchResult:
        """聚合多个 hook 响应。

        规则（deny 优先于 modify）：
        1. 任一 deny → 整体 deny（reason 取第一个 deny 的 reason）
        2. 否则：合并所有 modify 的 data（后到的覆盖先到的）
        3. 全 allow → allow
        """
        result = DispatchResult()

        # 第一遍：找 deny
        for resp in responses:
            if resp.action == Action.DENY:
                result.action = Action.DENY
                if result.reason is None:
                    result.reason = resp.reason
                # 不 break：denied_by 需要在 caller 端补（这里不知道 hook 名字）
                # 但本版 v0 简化：只记 action + reason

        # 已有 deny → 不再看 modify
        if result.action == Action.DENY:
            return result

        # 第二遍：合并 modify
        for resp in responses:
            if resp.action == Action.MODIFY and resp.data is not None:
                if result.data is None:
                    result.data = dict(resp.data)
                else:
                    result.data.update(resp.data)

        if result.data is not None:
            result.action = Action.MODIFY

        return result

    # 便捷方法 ─────────────────────────────────────────

    async def emit(
        self,
        event_name: EventName,
        session_id: str,
        context: HookContext,
        **data: Any,
    ) -> DispatchResult:
        """快速构造 + dispatch。"""
        ev = HookEvent.make(event_name, session_id, **data)
        return await self.dispatch(ev, context)

"""
minicode.bus：进程内类型化 + wildcard 事件总线。

参考 mimo code packages/opencode/src/bus 的设计，简化为 Python asyncio。

核心 API：
- define(type_name)         注册一个事件类型（v0 只是字符串常量，未来可挂 zod-like schema）
- Bus()                     pub/sub 实例
- Bus.publish(type, dict)   异步广播
- Bus.subscribe(type, cb)   订阅某个 type（返回 unsubscribe 函数）
- Bus.subscribe_all(cb)     wildcard 订阅（收到所有事件）

设计取舍：
- 同步 + 异步 callback 都接受；异步 callback 在 publish 内被 await
- callback 抛错被吞（fail-open），不阻断其他订阅者
- publish_nowait：fire-and-forget，从同步 context 也能发事件
- 用 asyncio.Lock 保护 typed 集合变更；publish 内快照后释放
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any, Awaitable, Callable, DefaultDict, Dict, List, Set, Union


_log = logging.getLogger("minicode.bus")


# 预定义事件类型（参考 mimo code 的 Event.* 常量集）
SESSION_START = "session.start"
SESSION_END = "session.end"
SESSION_ERROR = "session.error"

MESSAGE_USER = "message.user"          # 外部用户输入
MESSAGE_ASSISTANT = "message.assistant"  # assistant 响应

TOOL_CALL_STARTED = "tool.call.started"
TOOL_CALL_COMPLETED = "tool.call.completed"
TOOL_CALL_FAILED = "tool.call.failed"

CHAT_INCOMING = "chat.incoming"        # adapter 收到外部消息
CHAT_OUTGOING = "chat.outgoing"        # adapter 发出消息
CHAT_BRIDGE_STARTED = "chat.bridge.started"
CHAT_BRIDGE_STOPPED = "chat.bridge.stopped"

ALL_EVENT_TYPES: List[str] = [
    SESSION_START, SESSION_END, SESSION_ERROR,
    MESSAGE_USER, MESSAGE_ASSISTANT,
    TOOL_CALL_STARTED, TOOL_CALL_COMPLETED, TOOL_CALL_FAILED,
    CHAT_INCOMING, CHAT_OUTGOING,
    CHAT_BRIDGE_STARTED, CHAT_BRIDGE_STOPPED,
]


Callback = Callable[[Dict[str, Any]], Union[None, Awaitable[None]]]


class Bus:
    """进程内类型化 + wildcard pub/sub。

    不是线程安全的；单 event loop 上下文里用。
    """

    def __init__(self) -> None:
        self._typed: DefaultDict[str, Set[Callback]] = defaultdict(set)
        self._wildcard: Set[Callback] = set()
        self._lock = asyncio.Lock() if self._has_loop() else None  # type: ignore[assignment]
        self._closed = False
        self._published = 0
        self._dropped_async = 0

    @staticmethod
    def _has_loop() -> bool:
        try:
            asyncio.get_event_loop()
            return True
        except RuntimeError:
            return False

    # ── publish ─────────────────────────────────────────

    async def publish(self, type: str, properties: Dict[str, Any]) -> None:
        """异步广播事件。

        所有 typed + wildcard callback 都被调（任意一个抛错不阻断其他）。
        同步 + 异步 callback 都接受。
        """
        self._validate(type, properties)

        # 快照订阅者（避免边迭代边修改）
        typed_cbs: List[Callback] = list(self._typed.get(type, ()))
        wild_cbs: List[Callback] = list(self._wildcard)

        payload = {"type": type, "properties": properties}
        self._published += 1
        _log.debug("publish %s", type)

        for cb in typed_cbs:
            await self._invoke(cb, payload)
        for cb in wild_cbs:
            await self._invoke(cb, payload)

    def publish_nowait(self, type: str, properties: Dict[str, Any]) -> None:
        """Fire-and-forget：从同步 context 发事件。

        - 若当前 event loop 在跑 → 调度成 task
        - 否则 → 同步调用 callback（异步 callback 会被 drop 并计数）
        """
        self._validate(type, properties)
        payload = {"type": type, "properties": properties}
        typed_cbs: List[Callback] = list(self._typed.get(type, ()))
        wild_cbs: List[Callback] = list(self._wildcard)
        self._published += 1

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self._dispatch(payload, typed_cbs, wild_cbs))
                return
        except RuntimeError:
            pass

        # 同步 fallback
        for cb in typed_cbs + wild_cbs:
            try:
                r = cb(payload)
                if asyncio.iscoroutine(r):
                    self._dropped_async += 1
                    _log.warning("dropping async callback in sync context")
            except Exception as e:
                _log.warning("sync callback error: %s", e)

    async def _dispatch(
        self,
        payload: Dict[str, Any],
        typed_cbs: List[Callback],
        wild_cbs: List[Callback],
    ) -> None:
        for cb in typed_cbs:
            await self._invoke(cb, payload)
        for cb in wild_cbs:
            await self._invoke(cb, payload)

    # ── subscribe ─────────────────────────────────────────

    def subscribe(self, type: str, callback: Callback) -> Callable[[], None]:
        """订阅一个事件类型。返回 unsubscribe 函数。"""
        if not isinstance(type, str) or not type:
            raise ValueError("type must be a non-empty string")
        self._typed[type].add(callback)
        _log.debug("subscribe %s (total=%d)", type, len(self._typed[type]))

        def unsub() -> None:
            self._typed[type].discard(callback)
            _log.debug("unsubscribe %s", type)

        return unsub

    def subscribe_all(self, callback: Callback) -> Callable[[], None]:
        """订阅所有事件。"""
        self._wildcard.add(callback)
        _log.debug("subscribe_all (total=%d)", len(self._wildcard))

        def unsub() -> None:
            self._wildcard.discard(callback)
            _log.debug("unsubscribe_all")

        return unsub

    # ── 状态 ─────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        return {
            "published": self._published,
            "dropped_async": self._dropped_async,
            "typed": {k: len(v) for k, v in self._typed.items()},
            "wildcard": len(self._wildcard),
            "closed": self._closed,
        }

    def clear(self) -> None:
        """清空所有订阅（不影响已发出的事件）。"""
        self._typed.clear()
        self._wildcard.clear()

    async def aclose(self) -> None:
        """关闭 bus，丢弃所有订阅。"""
        self._closed = True
        self.clear()

    # ── 内部 ─────────────────────────────────────────

    @staticmethod
    def _validate(type: str, properties: Dict[str, Any]) -> None:
        if not isinstance(type, str) or not type:
            raise ValueError("type must be a non-empty string")
        if not isinstance(properties, dict):
            raise ValueError(f"properties must be a dict, got {properties.__class__.__name__}")

    async def _invoke(self, cb: Callback, payload: Dict[str, Any]) -> None:
        try:
            r = cb(payload)
            if asyncio.iscoroutine(r):
                await r
        except Exception as e:
            _log.warning("callback error for %s: %s", payload.get("type"), e)

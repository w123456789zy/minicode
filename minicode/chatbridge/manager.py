"""
ChatBridgeManager：协调 bus + history + model + 多个 ChatAdapter。

职责：
1. 注册 / 注销 adapter
2. 监听 adapter 的入站消息 → 写 history → 通过 bus 广播 → 调 model → 写 history → 广播 → 调所有 adapter.send()
3. 提供 session 隔离（同一 thread_key 复用同一 session_id）
4. 启动/停止 status

v0 简化：
- 不实现真正的 session 隔离（minicode v0 没 session 概念），用 thread_key 作 key
- model_runner 是个 async callable(history) -> str
- fail-open：所有异常被吞 + 通过 bus 广播 SESSION_ERROR
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional

from minicode.bus import (
    Bus,
    CHAT_BRIDGE_STARTED,
    CHAT_BRIDGE_STOPPED,
    CHAT_INCOMING,
    CHAT_OUTGOING,
    MESSAGE_ASSISTANT,
    MESSAGE_USER,
    SESSION_ERROR,
)
from minicode.chatbridge.adapter import ChatAdapter, IncomingMessage, OutgoingMessage
from minicode.model.message import Message


_log = logging.getLogger("minicode.chatbridge")


ModelRunner = Callable[[List[Message]], Awaitable[str]]


class ChatBridgeManager:
    def __init__(
        self,
        bus: Bus,
        history: List[Message],
        model_runner: Optional[ModelRunner] = None,
        session_id: str = "",
    ) -> None:
        """
        - bus:                事件总线
        - history:            共享 history 列表（外部引用，会被 manager 追加）
        - model_runner:       async (history) -> assistant text；v0 默认 = echo 模式
        - session_id:         主 session id（v0 单 session）
        """
        self.bus = bus
        self.history = history
        self._model_runner = model_runner or self._default_runner
        self._session_id = session_id or f"bridge-{uuid.uuid4().hex[:8]}"

        self._adapters: Dict[str, ChatAdapter] = {}
        # thread_key → session_id（v0 都用主 session_id，但保留结构供未来扩展）
        self._threads: Dict[str, str] = {}
        # 计数
        self._incoming_count = 0
        self._outgoing_count = 0
        self._error_count = 0
        self._start_ts: Optional[float] = None

    # ── 默认 runner ─────────────────────────────────────────

    async def _default_runner(self, history: List[Message]) -> str:
        """v0 fallback: 把最后一条 user 消息原样回显。"""
        if not history:
            return ""
        last = history[-1]
        return f"[echo] {last.text()[:500]}"

    # ── adapter 生命周期 ─────────────────────────────────────────

    async def register(self, adapter: ChatAdapter) -> None:
        # 先停掉已存在的同名 adapter
        existing = self._adapters.get(adapter.name)
        if existing is not None:
            try:
                await existing.stop()
            except Exception as e:
                _log.warning("existing adapter %s stop error: %s", adapter.name, e)
        adapter.set_incoming_handler(self._on_incoming)
        await adapter.start()
        self._adapters[adapter.name] = adapter
        if self._start_ts is None:
            self._start_ts = time.monotonic()
        _log.info("adapter registered: %s", adapter.name)
        await self.bus.publish(CHAT_BRIDGE_STARTED, {
            "adapter": adapter.name,
            "session_id": self._session_id,
        })

    async def unregister(self, name: str) -> bool:
        adapter = self._adapters.pop(name, None)
        if adapter is None:
            return False
        try:
            await adapter.stop()
        except Exception as e:
            _log.warning("adapter %s stop error: %s", name, e)
        await self.bus.publish(CHAT_BRIDGE_STOPPED, {
            "adapter": name,
            "session_id": self._session_id,
        })
        _log.info("adapter unregistered: %s", name)
        return True

    async def stop_all(self) -> int:
        n = 0
        for name in list(self._adapters.keys()):
            if await self.unregister(name):
                n += 1
        return n

    def list_adapters(self) -> List[Dict[str, Any]]:
        return [a.status() for a in self._adapters.values()]

    def has_adapter(self, name: str) -> bool:
        return name in self._adapters

    # ── 入站处理 ─────────────────────────────────────────

    async def _on_incoming(self, msg: IncomingMessage) -> None:
        self._incoming_count += 1
        key = msg.thread_key()
        # 分配 / 复用 session id
        if key not in self._threads:
            self._threads[key] = self._session_id
        session_id = self._threads[key]

        # 广播入站事件
        await self.bus.publish(CHAT_INCOMING, {
            "user": msg.user,
            "channel": msg.channel,
            "thread": msg.thread,
            "text": msg.text,
            "session_id": session_id,
        })
        await self.bus.publish(MESSAGE_USER, {
            "user": msg.user,
            "channel": msg.channel,
            "thread": msg.thread,
            "text": msg.text,
            "session_id": session_id,
        })

        # 写 history
        self.history.append(
            Message.user(
                f"[{msg.user}@{msg.channel}:{msg.thread}] {msg.text}"
            )
        )

        # 调 model
        try:
            response = await self._model_runner(list(self.history))
        except Exception as e:
            self._error_count += 1
            _log.exception("model_runner failed")
            await self.bus.publish(SESSION_ERROR, {
                "error": str(e),
                "session_id": session_id,
                "source": "chat_bridge",
            })
            # 仍然回执一条错误消息给用户
            response = f"[bridge error] {e}"

        # 写 history + 广播
        self.history.append(Message.assistant_text(response))
        await self.bus.publish(MESSAGE_ASSISTANT, {
            "text": response,
            "session_id": session_id,
            "channel": msg.channel,
            "thread": msg.thread,
        })

        # 发到所有 adapter
        out = OutgoingMessage(
            channel=msg.channel,
            thread=msg.thread,
            text=response,
            reply_to=None,
        )
        await self._broadcast_out(out)

    async def _broadcast_out(self, msg: OutgoingMessage) -> None:
        for name, adapter in list(self._adapters.items()):
            try:
                await adapter.send(msg)
                self._outgoing_count += 1
                await self.bus.publish(CHAT_OUTGOING, {
                    "adapter": name,
                    "channel": msg.channel,
                    "thread": msg.thread,
                    "text": msg.text,
                })
            except Exception as e:
                self._error_count += 1
                _log.warning("adapter %s send failed: %s", name, e)
                await self.bus.publish(SESSION_ERROR, {
                    "error": f"adapter {name} send failed: {e}",
                    "session_id": self._session_id,
                    "source": "chat_bridge",
                })

    # ── 状态 ─────────────────────────────────────────

    def status(self) -> Dict[str, Any]:
        return {
            "session_id": self._session_id,
            "uptime_s": (time.monotonic() - self._start_ts) if self._start_ts else 0.0,
            "adapters": self.list_adapters(),
            "incoming": self._incoming_count,
            "outgoing": self._outgoing_count,
            "errors": self._error_count,
            "threads": dict(self._threads),
            "history_len": len(self.history),
        }

    @property
    def session_id(self) -> str:
        return self._session_id

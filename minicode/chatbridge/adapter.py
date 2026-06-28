"""
ChatAdapter 抽象 + 消息数据类。

参考 mimo code packages/slack/src/index.ts 的设计，
抽象"外部聊天软件 → minicode session"的桥接。
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional


@dataclass
class IncomingMessage:
    """从外部聊天软件收到的入站消息。"""
    user: str
    channel: str
    thread: str
    text: str
    raw: Optional[Dict[str, Any]] = None  # 原始 payload（adapter 私有）

    def thread_key(self) -> str:
        """thread 在 bus 内部用作 session/会话 key。"""
        return f"{self.channel}:{self.thread}"


@dataclass
class OutgoingMessage:
    """发回外部聊天软件的出站消息。"""
    channel: str
    thread: str
    text: str
    reply_to: Optional[str] = None  # 回引用的入站消息 id（如果有）


# 适配器在收到消息时，调用这个回调把消息交给 manager
IncomingHandler = Callable[[IncomingMessage], Awaitable[None]]


class ChatAdapter(abc.ABC):
    """所有外部聊天软件桥接的抽象基类。

    生命周期：
    - start() 注册到 manager 后调一次
    - set_incoming_handler() 让 manager 注入回调
    - 收到外部消息时构造 IncomingMessage 并 await on_incoming(msg)
    - send(msg) 发出消息
    - stop() 清理资源
    """

    name: str = "abstract"

    def __init__(self) -> None:
        self._on_incoming: Optional[IncomingHandler] = None

    def set_incoming_handler(self, handler: IncomingHandler) -> None:
        self._on_incoming = handler

    async def on_incoming(self, msg: IncomingMessage) -> None:
        """Adapter 调用：把收到的消息交给 manager。"""
        if self._on_incoming is None:
            return
        await self._on_incoming(msg)

    @abc.abstractmethod
    async def start(self) -> None:
        """启动 adapter（listen / connect / spawn thread）。"""

    @abc.abstractmethod
    async def stop(self) -> None:
        """关闭 adapter，释放资源。"""

    @abc.abstractmethod
    async def send(self, msg: OutgoingMessage) -> None:
        """发送一条消息到外部聊天软件。"""

    @property
    @abc.abstractmethod
    def running(self) -> bool:
        """adapter 是否在跑。"""

    def status(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "running": self.running,
            "type": self.__class__.__name__,
        }

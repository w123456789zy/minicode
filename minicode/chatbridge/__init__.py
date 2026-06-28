"""
minicode.chatbridge：把 minicode 桥接到外部聊天软件。

参考 mimo code packages/slack（一个独立子包，依赖 opencode SDK 订阅事件，
把外部 chat 软件的消息映射成 session 交互）。

minicode v0 简化版：
- 不依赖外部 SDK，直接复用 in-process Bus + History + Model
- 内置两个 adapter：webhook（HTTP POST 接收入站）+ stdio（stdin 接收入站）
- 想要接 Slack/Telegram 只需要再写一个 ChatAdapter 子类

公开 API：
- ChatAdapter              抽象基类
- IncomingMessage          入站消息
- OutgoingMessage          出站消息
- ChatBridgeManager        协调 bus + history + model + adapters
- builtin_webhook_adapter  工厂函数：构造 WebhookAdapter
- builtin_stdio_adapter    工厂函数：构造 StdioAdapter
"""

from minicode.chatbridge.adapter import (
    ChatAdapter,
    IncomingHandler,
    IncomingMessage,
    OutgoingMessage,
)
from minicode.chatbridge.manager import ChatBridgeManager, ModelRunner
from minicode.chatbridge.adapters import builtin_stdio_adapter, builtin_webhook_adapter

__all__ = [
    "ChatAdapter",
    "IncomingHandler",
    "IncomingMessage",
    "OutgoingMessage",
    "ChatBridgeManager",
    "ModelRunner",
    "builtin_webhook_adapter",
    "builtin_stdio_adapter",
]

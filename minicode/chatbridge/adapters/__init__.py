"""
chatbridge.adapters 子包入口。

提供两个内置 adapter 的工厂函数：
- builtin_webhook_adapter(port, host, outbound_path)  → WebhookAdapter
- builtin_stdio_adapter(prompt)                      → StdioAdapter

加新 adapter 的步骤：
1. 在本包下加一个 .py 文件，定义 MyAdapter(ChatAdapter)
2. 在本 __init__.py 暴露工厂函数
3. CLI 端把名字注册到 /chat start <name>
"""

from minicode.chatbridge.adapters.stdio import StdioAdapter
from minicode.chatbridge.adapters.webhook import WebhookAdapter

__all__ = ["WebhookAdapter", "StdioAdapter", "builtin_webhook_adapter", "builtin_stdio_adapter"]


def builtin_webhook_adapter(port: int = 8765, host: str = "127.0.0.1", outbound_path: str = "chat-outbox.jsonl") -> WebhookAdapter:
    return WebhookAdapter(port=port, host=host, outbound_path=outbound_path)


def builtin_stdio_adapter(prompt: str = "chat> ") -> StdioAdapter:
    return StdioAdapter(prompt=prompt)

"""
Demo provider：假 LLM。

用途：没 apikey 时也能跑通端到端流程（stream → 攒成 response）。
行为：
- 把 user 的最后一条消息内容当作 "echo" 回显，前面加上 [demo: <model_name>]
- 如果有 tools 且消息里含 "tool" 字样，会回一个 tool_call（让 ReAct 循环测试也能跑）
- 给一点 usage（假装 100 input / 50 output）让 metric 链不空

不是生产用，仅用于：
- /model test 命令演示
- 单元测试 / CI 不需要 mock 网络
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator, List, Optional

from minicode.model.base import Model, ModelEvent, ModelInfo, ModelUsage
from minicode.model.message import (
    Message,
    Role,
    TextPart,
    ToolCallPart,
    ToolSchema,
)


class DemoModel(Model):
    async def stream(
        self,
        messages: List[Message],
        tools: Optional[List[ToolSchema]] = None,
        system: Optional[str] = None,
    ) -> AsyncIterator[ModelEvent]:
        # 模拟网络延迟，让流式感更真实
        await asyncio.sleep(0.05)

        # 取最后一条 user 消息内容
        last_user = next(
            (m for m in reversed(messages) if m.role == Role.USER), None
        )
        text = last_user.text() if last_user else ""
        text_lower = text.lower()
        has_tools = bool(tools)

        # usage
        yield ModelEvent(
            type="usage",
            usage=ModelUsage(input_tokens=len(text) // 4 + 10, output_tokens=0),
        )

        # 如果有 tools 且 user 问 "call tool ..." 或 "use ...", 就回一个 tool_call
        if has_tools and ("tool" in text_lower or "用工具" in text_lower or "调用" in text_lower):
            first = tools[0]
            tcid = "demo_call_001"
            yield ModelEvent(
                type="tool_call_delta",
                tool_call_id=tcid,
                tool_name=first.name,
            )
            # 给 args 一个简单的默认值
            import json
            default_args = {}
            for prop_name, prop in (first.parameters.get("properties") or {}).items():
                ptype = prop.get("type")
                if ptype == "string":
                    default_args[prop_name] = "demo"
                elif ptype == "integer":
                    default_args[prop_name] = 1
                elif ptype == "boolean":
                    default_args[prop_name] = True
                else:
                    default_args[prop_name] = None
            yield ModelEvent(
                type="tool_call_delta",
                tool_args_delta=json.dumps(default_args, ensure_ascii=False),
            )
            yield ModelEvent(type="finish", finish_reason="tool_calls")
            return

        # 否则回显
        reply = f"[demo:{self._info.model}] echo: {text}"
        # 分 chunk 模拟流式
        chunk_size = 8
        for i in range(0, len(reply), chunk_size):
            await asyncio.sleep(0.02)
            yield ModelEvent(type="text_delta", text=reply[i:i + chunk_size])

        yield ModelEvent(type="finish", finish_reason="stop")
        yield ModelEvent(
            type="usage",
            usage=ModelUsage(output_tokens=len(reply) // 4 + 5),
        )

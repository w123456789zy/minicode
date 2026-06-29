"""端到端验证 thinking 合并修复。

模拟 DeepSeek/R1 风格：16 个 thinking_delta + 16 个 text_delta + finish
验证 CLI 只输出 1 个 thinking block。
"""
import asyncio, sys
from contextlib import redirect_stdout
from io import StringIO

from minicode.agent.runtime import AgentEvent, run_agent
from minicode.model.base import Model, ModelEvent, ModelInfo, ModelUsage
from minicode.model.message import Message
from minicode.tool.base import ToolContext


class _MockThinkingModel(Model):
    """模拟 DeepSeek：reasoning_content + content 交替的 stream。"""
    def __init__(self):
        self._info = ModelInfo(
            id="mock",
            provider_id="mock",
            type="mock",
            base_url="",
            model="mock-thinking",
            context_window=8000,
        )

    async def stream(self, messages, tools=None, system=None):
        thinking = "用户打了招呼，我简单回应一下，保持直接简洁的风格。"
        text = "你好！有什么想做的？"
        # 模拟 DeepSeek：thinking 和 text 交替输出，但 thinking 可能更长
        # 先按短的交替，剩余的 thinking 单独输出
        shorter = min(len(thinking), len(text))
        for i in range(shorter):
            yield ModelEvent(type="thinking_delta", text=thinking[i])
            yield ModelEvent(type="text_delta", text="")
        # 剩余的 thinking token（如果 thinking 比 text 长）
        for i in range(shorter, len(thinking)):
            yield ModelEvent(type="thinking_delta", text=thinking[i])
        # 正式的 text 输出
        for c in text:
            yield ModelEvent(type="text_delta", text=c)
        yield ModelEvent(type="finish", finish_reason="stop")

    async def complete(self, messages, tools=None, system=None):
        raise NotImplementedError

    @property
    def info(self):
        return self._info


class _MiniRegistry:
    def __init__(self):
        self._schemas = []

    async def execute(self, name, args, ctx):
        raise NotImplementedError

    @property
    def schemas(self):
        return []


async def main():
    events = []

    async def cb(ev: AgentEvent):
        events.append(ev)

    model = _MockThinkingModel()
    history = [Message.user("你好")]
    reg = _MiniRegistry()
    ctx = ToolContext(
        session_id="test", cwd=".", project_root=".",
        tool_registry=reg, permission_service=None,
        hook_dispatcher=None, bus=None, model_registry=None,
        config=None, history=history, context_budget=None,
    )

    await run_agent(model, "sys", history, reg, ctx, [], on_event=cb)

    done_events = [e for e in events if e.type == "thinking_done"]
    print(f"thinking_done events: {len(done_events)}")
    if done_events:
        print(f"content: {done_events[0].text}")
    assert len(done_events) == 1, f"Expected 1 thinking_done, got {len(done_events)}"
    assert "用户" in done_events[0].text and "风格" in done_events[0].text
    print("PASS: thinking is merged into 1 block")


asyncio.run(main())
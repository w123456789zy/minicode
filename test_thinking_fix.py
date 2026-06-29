"""端到端验证 thinking 合并修复 + 显示顺序。

模拟真实 DeepSeek 流式：先全部 thinking_delta，再全部 text_delta + finish
验证：
1. 只有 1 个 thinking_done 事件
2. thinking_done 在第一个 text_delta 之前 emit（显示在上方）
"""
import asyncio, sys
from contextlib import redirect_stdout
from io import StringIO

from minicode.agent.runtime import AgentEvent, run_agent
from minicode.model.base import Model, ModelEvent, ModelInfo, ModelUsage
from minicode.model.message import Message
from minicode.tool.base import ToolContext


class _MockThinkingModel(Model):
    """模拟真实 DeepSeek：reasoning_content 全部先出，再出 content。"""
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
        # 真实 DeepSeek 流式：先全部 thinking_delta，再全部 text_delta
        for c in thinking:
            yield ModelEvent(type="thinking_delta", text=c)
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

    # 1. 验证只有一个 thinking_done
    done_events = [e for e in events if e.type == "thinking_done"]
    print(f"thinking_done events: {len(done_events)}")
    assert len(done_events) == 1, f"Expected 1 thinking_done, got {len(done_events)}"
    assert "用户" in done_events[0].text and "风格" in done_events[0].text
    print(f"content: {done_events[0].text}")

    # 2. 验证 thinking_done 在第一个 text_delta 之前（显示顺序）
    event_types = [e.type for e in events]
    td_idx = event_types.index("thinking_done")
    first_text_idx = event_types.index("text_delta")
    assert td_idx < first_text_idx, (
        f"thinking_done (idx={td_idx}) must appear before first text_delta (idx={first_text_idx})"
    )
    print(f"thinking_done at index {td_idx}, first text_delta at index {first_text_idx}")

    print("PASS: thinking is merged into 1 block and appears before text")


asyncio.run(main())
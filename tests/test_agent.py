"""
测试 agent.runtime.run_agent：主 ReAct 循环。
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Dict, List, Optional

from pydantic import BaseModel

from minicode.agent import AgentEvent, run_agent
from minicode.model.base import Model, ModelEvent, ModelInfo
from minicode.model.message import (
    Message,
    Role,
    ToolResultPart,
    ToolSchema,
)
from minicode.tool.base import (
    Tool,
    ToolContext,
    ToolResult,
)


# ─────────────────────────────────────────────────────────────
# Mock Model
# ─────────────────────────────────────────────────────────────


class MockModel(Model):
    """可编程的 mock model。"""
    def __init__(self, scripts: List[List[ModelEvent]]):
        super().__init__(ModelInfo(id="mock", type="mock", base_url="-", model="mock"))
        self._scripts = list(scripts)
        self._i = 0

    async def stream(
        self,
        messages: List[Message],
        tools: Optional[List[ToolSchema]] = None,
        system: Optional[str] = None,
    ) -> AsyncIterator[ModelEvent]:
        if self._i >= len(self._scripts):
            yield ModelEvent(type="text_delta", text="(no more)")
            yield ModelEvent(type="finish", finish_reason="stop")
            return
        for ev in self._scripts[self._i]:
            yield ev
        self._i += 1


# ─────────────────────────────────────────────────────────────
# Fake Tool Registry
# ─────────────────────────────────────────────────────────────


class _In(BaseModel):
    x: int = 0


class _EchoTool(Tool):
    """一个最简的 tool：返回 args dict。"""
    @property
    def id(self):
        return "echo"

    @property
    def description(self):
        return "echoes args"

    @property
    def parameters(self):
        return _In

    async def execute(self, args, ctx):
        return ToolResult(
            title="ok",
            output=f"echoed: {args.x}",
            metadata={"echoed_x": args.x},
        )


class _BoomTool(Tool):
    @property
    def id(self):
        return "boom"

    @property
    def description(self):
        return "raises"

    @property
    def parameters(self):
        return _In

    async def execute(self, args, ctx):
        raise RuntimeError("kaboom")


class _MetaEchoTool(Tool):
    """tool 自身支持 code_change metadata（CLI 会识别）"""
    @property
    def id(self):
        return "meta_echo"

    @property
    def description(self):
        return "echo with code_change meta"

    @property
    def parameters(self):
        return _In

    async def execute(self, args, ctx):
        return ToolResult(
            title="ok",
            output="did it",
            metadata={
                "code_change": {
                    "path": "x.py",
                    "added": 3,
                    "removed": 1,
                    "old_text": "old",
                    "new_text": "new",
                },
            },
        )


# 跑一个 echo tool 上去的最小 registry
class MiniRegistry:
    """最小 'ToolRegistry' duck-type：run_agent 只需要 .execute(name, args, ctx)。"""
    def __init__(self, tool: Tool):
        self._tool = tool

    async def execute(self, name: str, args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        if name != self._tool.id:
            return ToolResult(
                title="not found",
                output=f"tool {name!r} not found",
                metadata={"error": True, "not_found": True},
            )
        # 校验 / 实例化 Pydantic
        m = self._tool.parameters.model_validate(args)
        return await self._tool.execute(m, ctx)


# ─────────────────────────────────────────────────────────────
# 基础流程
# ─────────────────────────────────────────────────────────────


class TestRunAgentBasic:
    async def test_text_only_no_tools(self):
        model = MockModel([
            [
                ModelEvent(type="text_delta", text="你好"),
                ModelEvent(type="text_delta", text="世界"),
                ModelEvent(type="finish", finish_reason="stop"),
            ]
        ])
        history: List[Message] = [Message.user("ping")]
        reg = MiniRegistry(_EchoTool())
        ctx = ToolContext(cwd="/tmp")
        out = await run_agent(
            model, "sys", history, reg, ctx, tool_schemas=[],
        )
        assert out == "你好世界"
        # history 末尾应是 assistant_text
        assert history[-1].role == Role.ASSISTANT
        assert history[-1].text() == "你好世界"

    async def test_single_tool_call(self):
        model = MockModel([
            # 第 1 轮：text + tool_call
            [
                ModelEvent(type="text_delta", text="我先看看..."),
                ModelEvent(type="tool_call_delta", tool_call_id="c1", tool_name="echo",
                           tool_args_delta='{"x":'),
                ModelEvent(type="tool_call_delta", tool_call_id="c1", tool_name="echo",
                           tool_args_delta='42}'),
                ModelEvent(type="finish", finish_reason="tool_calls"),
            ],
            # 第 2 轮：text final
            [
                ModelEvent(type="text_delta", text="拿到了"),
                ModelEvent(type="finish", finish_reason="stop"),
            ],
        ])
        history: List[Message] = [Message.user("ping")]
        reg = MiniRegistry(_EchoTool())
        out = await run_agent(model, "sys", history, reg, ToolContext(), [])
        assert out == "拿到了"
        # history 应该有 user / assistant(tc) / tool / assistant(text)
        roles = [m.role for m in history]
        assert roles == [Role.USER, Role.ASSISTANT, Role.TOOL, Role.ASSISTANT]
        # tool result 内容
        tr = history[2].parts[0]
        assert isinstance(tr, ToolResultPart)
        assert tr.tool_call_id == "c1"
        assert "echoed: 42" in tr.content

    async def test_max_iterations(self):
        # 一直 tool_call，永远不收敛
        model = MockModel([
            [
                ModelEvent(type="tool_call_delta", tool_call_id="c1", tool_name="echo",
                           tool_args_delta='{"x":1}'),
                ModelEvent(type="finish", finish_reason="tool_calls"),
            ]
        ] * 100)
        history: List[Message] = [Message.user("ping")]
        reg = MiniRegistry(_EchoTool())
        out = await run_agent(
            model, "sys", history, reg, ToolContext(), [],
            max_iterations=3,
        )
        # 3 轮都该 tool_call；到 3 轮还没收敛 → 返回最后 text（空）+ error 事件
        assert isinstance(out, str)
        # 触发了 max_iterations → history 里至少 3 个 assistant + 3 个 tool
        assistant_count = sum(1 for m in history if m.role == Role.ASSISTANT)
        tool_count = sum(1 for m in history if m.role == Role.TOOL)
        assert assistant_count == 3
        assert tool_count == 3

    async def test_tool_not_found(self):
        # model 调了一个不存在的 tool
        model = MockModel([
            [
                ModelEvent(type="tool_call_delta", tool_call_id="c1", tool_name="ghost",
                           tool_args_delta='{}'),
                ModelEvent(type="finish", finish_reason="tool_calls"),
            ],
            [
                ModelEvent(type="text_delta", text="好的"),
                ModelEvent(type="finish", finish_reason="stop"),
            ],
        ])
        history: List[Message] = [Message.user("ping")]
        reg = MiniRegistry(_EchoTool())
        out = await run_agent(model, "sys", history, reg, ToolContext(), [])
        # 第二轮 model 给出 final text
        assert out == "好的"
        # tool result 应该是 error
        tr = history[2].parts[0]
        assert tr.is_error is True
        assert "ghost" in tr.content

    async def test_tool_raises(self):
        model = MockModel([
            [
                ModelEvent(type="tool_call_delta", tool_call_id="c1", tool_name="boom",
                           tool_args_delta='{}'),
                ModelEvent(type="finish", finish_reason="tool_calls"),
            ],
            [
                ModelEvent(type="text_delta", text="知道了"),
                ModelEvent(type="finish", finish_reason="stop"),
            ],
        ])
        history: List[Message] = [Message.user("ping")]
        reg = MiniRegistry(_BoomTool())
        out = await run_agent(model, "sys", history, reg, ToolContext(), [])
        assert out == "知道了"
        tr = history[2].parts[0]
        assert tr.is_error is True
        assert "kaboom" in tr.content


# ─────────────────────────────────────────────────────────────
# 事件回调
# ─────────────────────────────────────────────────────────────


class TestRunAgentEvents:
    async def test_event_order_basic(self):
        events: List[AgentEvent] = []

        async def cb(ev: AgentEvent) -> None:
            events.append(ev)

        model = MockModel([
            [
                ModelEvent(type="text_delta", text="a"),
                ModelEvent(type="finish", finish_reason="stop"),
            ]
        ])
        history: List[Message] = [Message.user("hi")]
        reg = MiniRegistry(_EchoTool())
        await run_agent(model, "sys", history, reg, ToolContext(), [], on_event=cb)

        types = [e.type for e in events]
        assert types[0] == "iteration_start"
        assert "text_delta" in types
        assert "finish" in types
        assert types[-1] == "done"
        # final_text 应在 done 事件里
        done = [e for e in events if e.type == "done"][0]
        assert done.final_text == "a"
        assert done.iterations == 1

    async def test_event_tool_call_metadata(self):
        """tool 返回的 metadata 应出现在 tool_result 事件里。"""
        events: List[AgentEvent] = []

        async def cb(ev: AgentEvent) -> None:
            events.append(ev)

        model = MockModel([
            [
                ModelEvent(type="tool_call_delta", tool_call_id="c1", tool_name="meta_echo",
                           tool_args_delta='{"x":1}'),
                ModelEvent(type="finish", finish_reason="tool_calls"),
            ],
            [
                ModelEvent(type="text_delta", text="done"),
                ModelEvent(type="finish", finish_reason="stop"),
            ],
        ])
        history: List[Message] = [Message.user("hi")]
        reg = MiniRegistry(_MetaEchoTool())
        await run_agent(model, "sys", history, reg, ToolContext(), [], on_event=cb)

        # 找 tool_result 事件
        tr_events = [e for e in events if e.type == "tool_result"]
        assert len(tr_events) == 1
        ev = tr_events[0]
        assert ev.tool_name == "meta_echo"
        assert ev.tool_result_is_error is False
        # metadata 里有 code_change
        assert "code_change" in ev.tool_result_metadata
        cc = ev.tool_result_metadata["code_change"]
        assert cc["path"] == "x.py"
        assert cc["added"] == 3

    async def test_event_thinking_delta(self):
        events: List[AgentEvent] = []

        async def cb(ev: AgentEvent) -> None:
            events.append(ev)

        model = MockModel([
            [
                ModelEvent(type="thinking_delta", text="让我想想"),
                ModelEvent(type="text_delta", text="hi"),
                ModelEvent(type="finish", finish_reason="stop"),
            ]
        ])
        history: List[Message] = [Message.user("hi")]
        reg = MiniRegistry(_EchoTool())
        await run_agent(model, "sys", history, reg, ToolContext(), [], on_event=cb)

        # 应该有 thinking_delta
        types = [e.type for e in events]
        assert "thinking_delta" in types
        td = [e for e in events if e.type == "thinking_delta"]
        assert "".join(e.text for e in td) == "让我想想"

    async def test_event_error_propagates(self):
        events: List[AgentEvent] = []

        async def cb(ev: AgentEvent) -> None:
            events.append(ev)

        # model 在第一轮 yield error
        class ErrModel(MockModel):
            def __init__(self):
                super().__init__([[
                    ModelEvent(type="text_delta", text="partial"),
                    ModelEvent(type="error", error="network down"),
                ]])

        history: List[Message] = [Message.user("hi")]
        reg = MiniRegistry(_EchoTool())
        out = await run_agent(ErrModel(), "sys", history, reg, ToolContext(), [], on_event=cb)
        # error 事件应被发出
        err_events = [e for e in events if e.type == "error"]
        assert err_events
        assert "network down" in err_events[0].error
        # final text = 已经流出来的 "partial"
        assert out == "partial"


# ─────────────────────────────────────────────────────────────
# 边界
# ─────────────────────────────────────────────────────────────


class TestRunAgentEdge:
    async def test_empty_history(self):
        """history 为空也能跑（model 自己输出）。"""
        model = MockModel([
            [
                ModelEvent(type="text_delta", text="self-start"),
                ModelEvent(type="finish", finish_reason="stop"),
            ]
        ])
        history: List[Message] = []
        reg = MiniRegistry(_EchoTool())
        out = await run_agent(model, "sys", history, reg, ToolContext(), [])
        assert out == "self-start"
        assert history[-1].text() == "self-start"

    async def test_bad_json_args_falls_back(self):
        """tool_call_delta 给的不是合法 JSON → 不炸，回退到 _raw。"""
        model = MockModel([
            [
                ModelEvent(type="tool_call_delta", tool_call_id="c1", tool_name="echo",
                           tool_args_delta='{x:'),  # 非法 JSON
                ModelEvent(type="finish", finish_reason="tool_calls"),
            ],
            [
                ModelEvent(type="text_delta", text="ok"),
                ModelEvent(type="finish", finish_reason="stop"),
            ],
        ])
        history: List[Message] = [Message.user("hi")]
        reg = MiniRegistry(_EchoTool())
        out = await run_agent(model, "sys", history, reg, ToolContext(), [])
        # echo tool 还是被调了（args fallback 到 {"_raw": "{x:"}）
        assert out == "ok"
        # tool result 不应抛
        assert history[2].role == Role.TOOL

    async def test_no_callback(self):
        """on_event=None 也能跑。"""
        model = MockModel([
            [ModelEvent(type="text_delta", text="x"),
             ModelEvent(type="finish", finish_reason="stop")],
        ])
        history: List[Message] = [Message.user("hi")]
        reg = MiniRegistry(_EchoTool())
        out = await run_agent(model, "sys", history, reg, ToolContext(), [], on_event=None)
        assert out == "x"

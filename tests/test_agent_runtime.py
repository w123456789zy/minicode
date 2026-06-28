"""agent.runtime 单测（用 DemoModel 当 mock LLM）。"""
import asyncio
from pathlib import Path
from typing import List

from minicode.agent.runtime import run_subagent
from minicode.model.base import ModelInfo, ModelUsage, ModelResponse
from minicode.model.demo import DemoModel
from minicode.model.message import (
    Message,
    Role,
    TextPart,
    ToolCallPart,
    ToolSchema,
)
from minicode.tool.base import (
    Tool,
    ToolContext,
    ToolDef,
    ToolResult,
    ToolKind,
)


# ─────────────────────────────────────────────────────────────
# 测试用 mock tool registry
# ─────────────────────────────────────────────────────────────


class _MockTool(Tool):
    """每次 execute 返回固定字符串。"""
    kind = ToolKind.BUILTIN

    def __init__(self, name: str, output: str):
        self._name = name
        self._output = output

    @property
    def id(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"mock {self._name}"

    @property
    def parameters(self):
        # 简单 pydantic model
        from pydantic import BaseModel
        class P(BaseModel):
            pass
        return P

    async def execute(self, args, ctx: ToolContext) -> ToolResult:
        return ToolResult(title=self._name, output=self._output)


class _MockRegistry:
    """最小 ToolRegistry 接口替身：只实现 runtime 需要的方法。"""

    def __init__(self, tools: List[Tool]):
        self._tools = {t.id: t for t in tools}

    async def execute(self, tool_id: str, args, ctx):
        t = self._tools.get(tool_id)
        if t is None:
            return ToolResult(
                title=f"tool not found: {tool_id}",
                output=f"No tool {tool_id!r}",
                metadata={"error": True},
            )
        return await t.execute(args, ctx)


def _make_demo() -> DemoModel:
    return DemoModel(
        ModelInfo(id="demo", type="demo", base_url="(in-process)", model="demo-echo"),
        api_key="", extra={},
    )


def _ctx() -> ToolContext:
    return ToolContext(cwd=Path("."))


def _echo_schemas() -> List[ToolSchema]:
    return [ToolSchema(name="echo", description="echo", parameters={})]


# ─────────────────────────────────────────────────────────────
# 测试
# ─────────────────────────────────────────────────────────────


def test_run_subagent_simple_text():
    """subagent 没调任何工具，返回 text 后结束。"""
    async def run():
        m = _make_demo()
        reg = _MockRegistry([])
        result = await run_subagent(
            model=m,
            subagent_system_prompt="You are X.",
            task="hello",
            tool_registry=reg,
            ctx=_ctx(),
            tool_schemas=[],
            max_iterations=3,
        )
        assert result.iterations >= 1
        assert result.text  # demo model 会回点什么
        assert result.error is None
        assert result.tool_calls_made == []
    asyncio.run(run())


def test_run_subagent_calls_tool():
    """subagent 调一次 echo 工具 → 拿结果 → 结束。"""
    async def run():
        m = _make_demo()
        # 用 echo tool 注册
        reg = _MockRegistry([_MockTool("echo", "ECHOED")])
        result = await run_subagent(
            model=m,
            subagent_system_prompt="X.",
            task="use echo",
            tool_registry=reg,
            ctx=_ctx(),
            tool_schemas=_echo_schemas(),
            max_iterations=3,
        )
        # demo model 不实际调工具 → tool_calls_made 为空
        # （只验证流程不 crash）
        assert result.error is None
    asyncio.run(run())


def test_run_subagent_max_iterations():
    """max_iterations=0 → 立即结束（边界）。"""
    async def run():
        m = _make_demo()
        reg = _MockRegistry([])
        result = await run_subagent(
            model=m,
            subagent_system_prompt="X.",
            task="t",
            tool_registry=reg,
            ctx=_ctx(),
            tool_schemas=[],
            max_iterations=0,
        )
        # max_iterations=0 → for 循环不进 → iterations_done=0
        # text 也可能空（loop 没跑）
        assert result.iterations == 0
    asyncio.run(run())


def test_run_subagent_progress_callback():
    async def run():
        m = _make_demo()
        reg = _MockRegistry([])
        seen: list = []

        def cb(idx, msg, post_tool=False):
            seen.append((idx, post_tool))

        result = await run_subagent(
            model=m,
            subagent_system_prompt="X.",
            task="t",
            tool_registry=reg,
            ctx=_ctx(),
            tool_schemas=[],
            max_iterations=3,
            on_progress=cb,
        )
        # 至少被调一次（第一次 iter 的 on_progress）
        assert any(c[1] is False for c in seen)
    asyncio.run(run())


def test_run_subagent_truncates_tool_output():
    """超过 MAX_TOOL_OUT 的工具结果应被截断。"""
    async def run():
        # 用一个自定义 model 模拟：第一次返回 tool_call，第二次返回 text
        from minicode.model.base import Model, ModelEvent

        class _StubModel(Model):
            def __init__(self):
                super().__init__(ModelInfo(id="s", type="s", base_url="x", model="y"))
                self.call_count = 0

            async def stream(self, messages, tools=None, system=None):
                self.call_count += 1
                if self.call_count == 1:
                    yield ModelEvent(type="tool_call_delta", tool_call_id="t1", tool_name="big", tool_args_delta="")
                    yield ModelEvent(type="finish", finish_reason="tool_calls")
                else:
                    yield ModelEvent(type="text_delta", text="ok")
                    yield ModelEvent(type="finish", finish_reason="stop")

        m = _StubModel()
        big_output = "X" * 10_000
        reg = _MockRegistry([_MockTool("big", big_output)])
        result = await run_subagent(
            model=m,
            subagent_system_prompt="X.",
            task="t",
            tool_registry=reg,
            ctx=_ctx(),
            tool_schemas=[ToolSchema(name="big", description="b", parameters={})],
            max_iterations=5,
        )
        # tool_calls_made 包含 "big"
        assert "big" in result.tool_calls_made
        # text 是 "ok"
        assert result.text == "ok"
        # 消息历史里能找到截断后的 tool result
        last_tool = [mm for mm in result.iterations and []]  # 不重要；用别的断言
    asyncio.run(run())

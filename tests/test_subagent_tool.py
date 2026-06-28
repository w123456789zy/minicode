"""tool.builtin.subagent 单测。"""
import asyncio
from pathlib import Path

from minicode.tool.base import ToolContext
from minicode.tool.builtin.subagent import SubagentTool, SubagentParams


# ─────────────────────────────────────────────────────────────
# mock loader + mock runner
# ─────────────────────────────────────────────────────────────


class _MockLoader:
    def __init__(self, by_name):
        self._by_name = by_name

    def get(self, name):
        return self._by_name.get(name)

    def all(self):
        return list(self._by_name.values())


class _MockSubagent:
    def __init__(self, name, system_prompt=""):
        self.name = name
        self.description = f"mock {name}"
        self.system_prompt = system_prompt
        self.location = Path(f"/fake/{name}.md")


async def _fake_runner(**kwargs):
    """替身 runner：返回固定结构。"""
    from minicode.agent.runtime import SubagentResult
    return SubagentResult(
        text=f"RESULT for {kwargs['subagent_name']}: {kwargs['task']}",
        iterations=2,
        tool_calls_made=["grep", "read"],
        usage_input=10,
        usage_output=20,
    )


async def _error_runner(**kwargs):
    from minicode.agent.runtime import SubagentResult
    return SubagentResult(
        text="partial",
        iterations=1,
        tool_calls_made=[],
        error="LLM crashed",
    )


# ─────────────────────────────────────────────────────────────
# 测试
# ─────────────────────────────────────────────────────────────


def test_subagent_tool_metadata():
    t = SubagentTool()
    assert t.id == "delegate_to_subagent"
    assert "subagent" in t.description.lower()
    assert t.parameters is SubagentParams


def test_subagent_tool_execute_success():
    async def run():
        t = SubagentTool()
        t.set_loader(_MockLoader({"foo": _MockSubagent("foo", "you are foo")}))
        t.set_runner(_fake_runner)
        ctx = ToolContext(cwd=Path("."))
        result = await t.execute(SubagentParams(name="foo", task="do X"), ctx)
        assert "foo" in result.title
        assert "<subagent_result" in result.output
        assert "RESULT for foo" in result.output
        assert result.metadata["iterations"] == 2
        assert "grep" in result.metadata["tool_calls_made"]
    asyncio.run(run())


def test_subagent_tool_not_found():
    async def run():
        t = SubagentTool()
        t.set_loader(_MockLoader({}))
        t.set_runner(_fake_runner)
        ctx = ToolContext(cwd=Path("."))
        result = await t.execute(SubagentParams(name="ghost", task="x"), ctx)
        assert "not found" in result.title
        assert result.metadata["error"] is True
        assert "ghost" not in (result.metadata.get("available") or [])
    asyncio.run(run())


def test_subagent_tool_runner_error():
    async def run():
        t = SubagentTool()
        t.set_loader(_MockLoader({"foo": _MockSubagent("foo")}))
        t.set_runner(_error_runner)
        ctx = ToolContext(cwd=Path("."))
        result = await t.execute(SubagentParams(name="foo", task="x"), ctx)
        assert "errored" in result.title
        assert "LLM crashed" in result.output
        assert result.metadata["error"] == "LLM crashed"
    asyncio.run(run())


def test_subagent_tool_no_loader():
    async def run():
        t = SubagentTool()
        t.set_runner(_fake_runner)
        ctx = ToolContext(cwd=Path("."))
        result = await t.execute(SubagentParams(name="x", task="y"), ctx)
        assert result.metadata["error"] is True
    asyncio.run(run())


def test_subagent_tool_no_runner():
    async def run():
        t = SubagentTool()
        t.set_loader(_MockLoader({"x": _MockSubagent("x")}))
        ctx = ToolContext(cwd=Path("."))
        result = await t.execute(SubagentParams(name="x", task="y"), ctx)
        assert result.metadata["error"] is True
        assert "Runner" in result.output
    asyncio.run(run())

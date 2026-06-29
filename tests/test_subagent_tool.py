"""tool.builtin.subagent 单测。"""
import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional

from minicode.model.base import Model, ModelEvent, ModelInfo
from minicode.model.message import Message, ToolSchema
from minicode.tool.base import Tool, ToolContext, ToolResult
from minicode.tool.builtin.subagent import SubagentTool, SubagentParams


# ─────────────────────────────────────────────────────────────
# mock loader
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


# ─────────────────────────────────────────────────────────────
# mock model
# ─────────────────────────────────────────────────────────────


class _MockModel(Model):
    """简单的 mock model：返回固定文本。"""
    def __init__(self):
        super().__init__(ModelInfo(id="mock", type="mock", base_url="-", model="mock"))

    async def stream(self, messages, tools=None, system=None):
        yield ModelEvent(type="text_delta", text="mock result")
        yield ModelEvent(type="finish", finish_reason="stop")


# ─────────────────────────────────────────────────────────────
# mock tool registry
# ─────────────────────────────────────────────────────────────


class _MockRegistry:
    """最小 registry duck-type。"""
    def all(self):
        return []

    async def execute(self, name, args, ctx):
        return ToolResult(title="ok", output="done")


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
        t.set_model(_MockModel())
        t.set_tool_registry(_MockRegistry())
        ctx = ToolContext(cwd=Path("."))
        result = await t.execute(SubagentParams(name="foo", task="do X"), ctx)
        assert "foo" in result.title
        assert "<subagent_result" in result.output
        assert "mock result" in result.output
        assert result.metadata["iterations"] == 1
    asyncio.run(run())


def test_subagent_tool_not_found():
    async def run():
        t = SubagentTool()
        t.set_loader(_MockLoader({}))
        t.set_model(_MockModel())
        t.set_tool_registry(_MockRegistry())
        ctx = ToolContext(cwd=Path("."))
        result = await t.execute(SubagentParams(name="ghost", task="x"), ctx)
        assert "not found" in result.title
        assert result.metadata["error"] is True
        assert "ghost" not in (result.metadata.get("available") or [])
    asyncio.run(run())


def test_subagent_tool_no_loader():
    async def run():
        t = SubagentTool()
        t.set_model(_MockModel())
        t.set_tool_registry(_MockRegistry())
        ctx = ToolContext(cwd=Path("."))
        result = await t.execute(SubagentParams(name="x", task="y"), ctx)
        assert result.metadata["error"] is True
        assert "loader" in result.output.lower()
    asyncio.run(run())


def test_subagent_tool_no_model():
    async def run():
        t = SubagentTool()
        t.set_loader(_MockLoader({"x": _MockSubagent("x")}))
        t.set_tool_registry(_MockRegistry())
        ctx = ToolContext(cwd=Path("."))
        result = await t.execute(SubagentParams(name="x", task="y"), ctx)
        assert result.metadata["error"] is True
        assert "model" in result.output.lower()
    asyncio.run(run())


def test_subagent_tool_no_registry():
    async def run():
        t = SubagentTool()
        t.set_loader(_MockLoader({"x": _MockSubagent("x")}))
        t.set_model(_MockModel())
        ctx = ToolContext(cwd=Path("."))
        result = await t.execute(SubagentParams(name="x", task="y"), ctx)
        assert result.metadata["error"] is True
        assert "toolregistry" in result.output.lower()
    asyncio.run(run())
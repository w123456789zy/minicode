"""Tool 抽象基类的单测。"""
import asyncio
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from minicode.tool.base import (
    AskRequest,
    Tool,
    ToolContext,
    ToolDef,
    ToolKind,
    ToolResult,
)


class _Params(BaseModel):
    name: str = Field(..., description="name to greet")
    count: int = Field(1, description="repeat times")


class _HelloTool(Tool):
    kind = ToolKind.BUILTIN

    @property
    def id(self) -> str:
        return "hello"

    @property
    def description(self) -> str:
        return "say hello"

    @property
    def parameters(self):
        return _Params

    async def execute(self, args: _Params, ctx: ToolContext) -> ToolResult:
        msg = ", ".join([f"hello {args.name}"] * args.count)
        return ToolResult(title="greet", output=msg, metadata={"name": args.name})


def test_tool_def_serialization():
    tool = _HelloTool()
    defn = tool.to_def()
    assert defn.id == "hello"
    assert defn.kind == ToolKind.BUILTIN
    assert defn.parameters is _Params
    assert "name" in defn.json_schema()["properties"]


def test_tool_context_sub():
    ctx = ToolContext(cwd=Path("/tmp"), session_id="abc")
    sub = ctx.sub(session_id="xyz")
    assert sub.session_id == "xyz"
    assert sub.cwd == Path("/tmp")
    # 原对象未变
    assert ctx.session_id == "abc"


@pytest.mark.asyncio
async def test_tool_execute_runs():
    tool = _HelloTool()
    ctx = ToolContext(cwd=Path("."))
    args = _Params.model_validate({"name": "world", "count": 2})
    result = await tool.execute(args, ctx)
    assert result.title == "greet"
    assert result.output == "hello world, hello world"
    assert result.metadata == {"name": "world"}


def test_ask_request_minimal():
    req = AskRequest(permission="bash", patterns=["ls"])
    assert req.permission == "bash"
    assert req.patterns == ["ls"]
    assert req.metadata == {}

"""Demo provider 单测。"""
import pytest

from minicode.model.base import ModelInfo
from minicode.model.demo import DemoModel
from minicode.model.message import Message, ToolSchema


@pytest.mark.asyncio
async def test_demo_echo():
    info = ModelInfo(id="demo", type="demo", base_url="", model="echo-1")
    m = DemoModel(info)
    resp = await m.complete([Message.user("hi")])
    assert "echo" in resp.message.text()
    assert "hi" in resp.message.text()


@pytest.mark.asyncio
async def test_demo_with_tools_returns_tool_call():
    info = ModelInfo(id="demo", type="demo", base_url="", model="echo-1")
    m = DemoModel(info)
    tools = [ToolSchema(name="greet", description="", parameters={
        "type": "object",
        "properties": {"name": {"type": "string"}},
    })]
    resp = await m.complete([Message.user("please call tool greet with name=zhang")], tools=tools)
    tcs = resp.message.tool_calls()
    assert len(tcs) == 1
    assert tcs[0].name == "greet"
    assert resp.finish_reason == "tool_calls"

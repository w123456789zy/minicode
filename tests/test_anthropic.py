"""Anthropic provider 单测。"""
import json

import httpx
import pytest

from minicode.model.anthropic import AnthropicModel
from minicode.model.base import ModelInfo
from minicode.model.message import Message, ToolCallPart, ToolSchema


def _sse_event(name: str, obj: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(obj, ensure_ascii=False)}"


def _make_model_with_mock(responder, *, model_name: str = "claude-test") -> tuple[AnthropicModel, list[httpx.Request]]:
    captured: list[httpx.Request] = []
    transport = httpx.MockTransport(lambda req: (captured.append(req), responder(req))[1])
    info = ModelInfo(id="test", type="anthropic", base_url="https://api.anthropic.com", model=model_name)
    m = AnthropicModel(info, api_key="sk-ant", extra={"timeout": 5, "max_tokens": 100})
    m._make_client = lambda timeout: httpx.AsyncClient(transport=transport, timeout=timeout)
    return m, captured


@pytest.mark.asyncio
async def test_anthropic_stream_text():
    body_str = "\n".join([
        _sse_event("message_start", {"message": {"usage": {"input_tokens": 11, "output_tokens": 0}}}),
        _sse_event("content_block_start", {"content_block": {"type": "text"}}),
        _sse_event("content_block_delta", {"delta": {"type": "text_delta", "text": "Hi"}}),
        _sse_event("content_block_delta", {"delta": {"type": "text_delta", "text": " there"}}),
        _sse_event("content_block_stop", {}),
        _sse_event("message_delta", {"delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 2}}),
        _sse_event("message_stop", {}),
    ])
    body = (body_str + "\n\n").encode("utf-8")

    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    m, captured = _make_model_with_mock(responder)
    events = []
    async for ev in m.stream([Message.user("hi")]):
        events.append(ev)
    text = "".join(ev.text for ev in events if ev.type == "text_delta")
    assert text == "Hi there"
    finishes = [ev.finish_reason for ev in events if ev.type == "finish"]
    assert "end_turn" in finishes

    # 验证 headers 和 body
    assert len(captured) == 1
    req = captured[0]
    assert req.headers.get("x-api-key") == "sk-ant"
    assert req.headers.get("anthropic-version") == "2023-06-01"
    body = json.loads(req.content)
    assert body["max_tokens"] == 100
    assert body["model"] == "claude-test"


def test_anthropic_body_build():
    info = ModelInfo(id="test", type="anthropic", base_url="https://api.anthropic.com", model="claude-test")
    m = AnthropicModel(info, api_key="sk", extra={"max_tokens": 256, "temperature": 0.2})
    msgs = [
        Message.user("hello"),
        Message.assistant(
            text="thinking",
            tool_calls=[ToolCallPart(id="toolu_1", name="do_x", arguments={"a": 1})],
        ),
        Message.tool_result("toolu_1", "ok"),
    ]
    tools = [ToolSchema(name="do_x", description="d", parameters={"type": "object", "properties": {"a": {"type": "integer"}}})]
    body = m._build_body(msgs, tools=tools, system="be nice")
    assert body["model"] == "claude-test"
    assert body["max_tokens"] == 256
    assert body["temperature"] == 0.2
    assert body["system"] == "be nice"
    assert body["messages"][0] == {"role": "user", "content": "hello"}
    # assistant 包含 text + tool_use
    a = body["messages"][1]
    assert a["role"] == "assistant"
    assert {"type": "text", "text": "thinking"} in a["content"]
    assert {"type": "tool_use", "id": "toolu_1", "name": "do_x", "input": {"a": 1}} in a["content"]
    # tool_result 合并到上一条 user
    u = body["messages"][2]
    assert u["role"] == "user"
    assert {"type": "tool_result", "tool_use_id": "toolu_1", "content": "ok", "is_error": False} in u["content"]
    # tools
    assert body["tools"][0]["name"] == "do_x"
    assert body["tools"][0]["input_schema"]["type"] == "object"

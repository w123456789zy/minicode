"""OpenAI-compat provider 单测（用 httpx.MockTransport 模拟 SSE）。"""
import json
from typing import List

import httpx
import pytest

from minicode.model.base import ModelInfo
from minicode.model.message import Message, ToolCallPart, ToolResultPart, ToolSchema
from minicode.model.openai_compat import OpenAICompatModel


def _sse_event(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}"


def _make_model_with_mock(responder, *, model_name: str = "gpt-test") -> tuple[OpenAICompatModel, list[httpx.Request]]:
    """构造一个用 MockTransport 的 OpenAICompatModel。"""
    captured: list[httpx.Request] = []
    transport = httpx.MockTransport(lambda req: (captured.append(req), responder(req))[1])
    info = ModelInfo(id="test", type="openai-compat", base_url="https://example.com", model=model_name)
    m = OpenAICompatModel(info, api_key="sk-test", extra={"timeout": 5})
    # 覆盖 _make_client，注入 MockTransport
    m._make_client = lambda timeout: httpx.AsyncClient(transport=transport, timeout=timeout)
    return m, captured


@pytest.mark.asyncio
async def test_stream_text_deltas():
    chunks = [
        _sse_event({"choices": [{"delta": {"content": "Hello"}}]}),
        _sse_event({"choices": [{"delta": {"content": " world"}}]}),
        _sse_event({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
        _sse_event({"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 3}}),
        "data: [DONE]",
    ]
    body = ("\n".join(chunks) + "\n").encode("utf-8")
    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=body,
        )

    m, captured = _make_model_with_mock(responder)
    msgs = [Message.user("hi")]
    text: list[str] = []
    usage = None
    finish = ""
    async for ev in m.stream(msgs):
        if ev.type == "text_delta":
            text.append(ev.text)
        elif ev.type == "usage" and ev.usage:
            usage = ev.usage
        elif ev.type == "finish":
            finish = ev.finish_reason

    assert "".join(text) == "Hello world"
    assert finish == "stop"
    assert usage.input_tokens == 7
    assert usage.output_tokens == 3

    # 验证请求体
    assert len(captured) == 1
    body = json.loads(captured[0].content)
    assert body["model"] == "gpt-test"
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    assert str(captured[0].url) == "https://example.com/chat/completions"


@pytest.mark.asyncio
async def test_stream_tool_call():
    chunks = [
        _sse_event({"choices": [{"delta": {"tool_calls": [{
            "id": "call_001",
            "function": {"name": "get_weather", "arguments": ""},
        }]}}]}),
        _sse_event({"choices": [{"delta": {"tool_calls": [{
            "function": {"arguments": '{"city":'},
        }]}}]}),
        _sse_event({"choices": [{"delta": {"tool_calls": [{
            "function": {"arguments": '"Beijing"}'},
        }]}}]}),
        _sse_event({"choices": [{"finish_reason": "tool_calls"}]}),
        "data: [DONE]",
    ]
    body = ("\n".join(chunks) + "\n").encode("utf-8")

    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    m, _ = _make_model_with_mock(responder)
    tools = [ToolSchema(name="get_weather", description="", parameters={
        "type": "object", "properties": {"city": {"type": "string"}},
    })]
    resp = await m.complete([Message.user("weather?")], tools=tools)
    assert resp.finish_reason == "tool_calls"
    tcs = resp.message.tool_calls()
    assert len(tcs) == 1
    assert tcs[0].name == "get_weather"
    assert tcs[0].arguments == {"city": "Beijing"}


def test_complete_round_trip_with_tool_result():
    """把 assistant text + tool_call → tool_result → assistant text 的多轮对话完整编码成 body。"""
    info = ModelInfo(id="test", type="openai-compat", base_url="https://example.com", model="gpt-test")
    m = OpenAICompatModel(info, api_key="sk-test", extra={"timeout": 5})
    msgs = [
        Message.user("weather Beijing?"),
        Message.assistant(
            text="",
            tool_calls=[ToolCallPart(id="call_001", name="get_weather", arguments={"city": "Beijing"})],
        ),
        Message.tool_result("call_001", "sunny"),
        Message.assistant_text("It's sunny in Beijing."),
    ]
    body = m._build_body(msgs, tools=None, system="you are helpful")
    assert body["model"] == "gpt-test"
    assert body["stream"] is True
    assert body["messages"][0] == {"role": "system", "content": "you are helpful"}
    assert body["messages"][1] == {"role": "user", "content": "weather Beijing?"}
    assert body["messages"][2]["role"] == "assistant"
    assert body["messages"][2]["tool_calls"][0]["function"]["arguments"] == '{"city": "Beijing"}'
    assert body["messages"][3] == {"role": "tool", "tool_call_id": "call_001", "content": "sunny"}
    assert body["messages"][4] == {"role": "assistant", "content": "It's sunny in Beijing."}


@pytest.mark.asyncio
async def test_error_yielded_as_event():
    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, content=b"Unauthorized")

    m, _ = _make_model_with_mock(responder)
    events = []
    async for ev in m.stream([Message.user("hi")]):
        events.append(ev)
    assert any(e.type == "error" for e in events)
    err = next(e for e in events if e.type == "error")
    assert "401" in err.error


def test_extra_blocked_fields_not_overwritten():
    info = ModelInfo(id="test", type="openai-compat", base_url="https://example.com", model="gpt-4o")
    m = OpenAICompatModel(
        info,
        api_key="sk",
        extra={"model": "should-not-overwrite", "temperature": 0.5, "max_tokens": 100},
    )
    body = m._build_body([Message.user("x")], tools=None, system=None)
    assert body["model"] == "gpt-4o"
    assert body["temperature"] == 0.5
    assert body["max_tokens"] == 100


# ─────────────────────────────────────────────────────────────
# Reasoning content（DeepSeek-R1 / Kimi / QwQ 等）
# ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stream_reasoning_content_extracted():
    """reasoning_content 字段应该被解析成 thinking_delta。"""
    chunks = [
        _sse_event({"choices": [{"delta": {"reasoning_content": "我需要想想..."}}]}),
        _sse_event({"choices": [{"delta": {"reasoning_content": " 计算完毕"}}]}),
        _sse_event({"choices": [{"delta": {"content": "答案是 42"}}]}),
        _sse_event({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
        "data: [DONE]",
    ]
    body = ("\n".join(chunks) + "\n").encode("utf-8")

    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=body,
        )

    m, _ = _make_model_with_mock(responder)
    events = [ev async for ev in m.stream([Message.user("q")], tools=None, system=None)]
    think = [e for e in events if e.type == "thinking_delta"]
    text = [e for e in events if e.type == "text_delta"]
    assert "".join(e.text for e in think) == "我需要想想... 计算完毕"
    assert "".join(e.text for e in text) == "答案是 42"


@pytest.mark.asyncio
async def test_stream_reasoning_text_fallback():
    """reasoning_text 字段名（Kimi / 一些代理）也要支持。"""
    chunks = [
        _sse_event({"choices": [{"delta": {"reasoning_text": "思考中..."}}]}),
        _sse_event({"choices": [{"delta": {"content": "ok"}}]}),
        _sse_event({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
        "data: [DONE]",
    ]
    body = ("\n".join(chunks) + "\n").encode("utf-8")

    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    m, _ = _make_model_with_mock(responder)
    events = [ev async for ev in m.stream([Message.user("q")], tools=None, system=None)]
    think = [e for e in events if e.type == "thinking_delta"]
    text = [e for e in events if e.type == "text_delta"]
    assert "".join(e.text for e in think) == "思考中..."
    assert "".join(e.text for e in text) == "ok"


@pytest.mark.asyncio
async def test_stream_usage_and_content_in_same_chunk():
    """OpenAI 标准：最后一个 chunk 同时含 choices + usage + finish_reason。

    之前 bug：碰到 usage 早 return，把 content 丢了。
    修复后应该三种 event 都拿到。
    """
    chunks = [
        _sse_event({"choices": [{"delta": {"content": "你好"}}]}),
        # 末尾 chunk：content + usage + finish_reason 一起
        _sse_event({
            "choices": [{
                "delta": {"content": "世界"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        }),
        "data: [DONE]",
    ]
    body = ("\n".join(chunks) + "\n").encode("utf-8")

    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    m, _ = _make_model_with_mock(responder)
    events = [ev async for ev in m.stream([Message.user("q")], tools=None, system=None)]
    text = [e for e in events if e.type == "text_delta"]
    usage = [e for e in events if e.type == "usage"]
    finish = [e for e in events if e.type == "finish"]
    # 关键：content 必须有
    assert "".join(e.text for e in text) == "你好世界", f"got text events: {text}"
    # usage 也要有
    assert len(usage) == 1
    assert usage[0].usage.input_tokens == 5
    assert usage[0].usage.output_tokens == 3
    # finish 也要有
    assert len(finish) == 1
    assert finish[0].finish_reason == "stop"


@pytest.mark.asyncio
async def test_stream_usage_only_chunk():
    """纯 usage chunk（choices 为空）—— 也要能正确处理。"""
    chunks = [
        _sse_event({"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}),
        _sse_event({
            "choices": [],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }),
        "data: [DONE]",
    ]
    body = ("\n".join(chunks) + "\n").encode("utf-8")

    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    m, _ = _make_model_with_mock(responder)
    events = [ev async for ev in m.stream([Message.user("q")], tools=None, system=None)]
    text = [e for e in events if e.type == "text_delta"]
    usage = [e for e in events if e.type == "usage"]
    assert "".join(e.text for e in text) == "ok"
    assert len(usage) == 1
    assert usage[0].usage.input_tokens == 1

"""
OpenAI Chat Completions 兼容 provider。

适用：OpenAI、DeepSeek、Moonshot、ollama（/v1）、vllm、lm-studio 等所有
实现了 `POST {base_url}/chat/completions` 的服务。

支持：
- 流式（SSE）
- 工具调用（function calling）
- extra 字段透传（temperature / top_p / max_tokens / presence_penalty ...）
"""

from __future__ import annotations

import json
from typing import AsyncIterator, Dict, List, Optional

import httpx

from minicode.model.base import Model, ModelEvent, ModelInfo, ModelUsage
from minicode.model.message import (
    Message,
    Role,
    TextPart,
    ToolCallPart,
    ToolResultPart,
    ToolSchema,
)


# 不透传的安全黑名单（这些字段我们自己控制）
_BLOCKED_EXTRA = {"messages", "tools", "stream", "model"}


class OpenAICompatModel(Model):
    async def stream(
        self,
        messages: List[Message],
        tools: Optional[List[ToolSchema]] = None,
        system: Optional[str] = None,
    ) -> AsyncIterator[ModelEvent]:
        body = self._build_body(messages, tools, system)
        try:
            resp_cm = await self._send_request(body)
        except httpx.HTTPError as e:
            yield ModelEvent(type="error", error=str(e))
            return
        try:
            async with resp_cm as resp:
                if resp.status_code != 200:
                    text = await resp.aread()
                    yield ModelEvent(
                        type="error",
                        error=f"HTTP {resp.status_code}: {text.decode('utf-8', errors='replace')[:500]}",
                    )
                    return

                async for raw in resp.aiter_lines():
                    if not raw:
                        continue
                    if not raw.startswith("data:"):
                        continue
                    payload = raw[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        data = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    for ev in self._parse_chunk(data):
                        yield ev
        except httpx.HTTPError as e:
            yield ModelEvent(type="error", error=str(e))

    # ─────────────────────────────────────────
    # HTTP 层（可被子类 / 测试覆盖）
    # ─────────────────────────────────────────

    async def _send_request(self, body: dict):
        """发请求，返回 httpx.Response 的 context manager。"""
        url = self._info.base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        timeout_s = float(self._extra.get("timeout", 60))
        timeout = httpx.Timeout(timeout_s, read=timeout_s * 2)
        client = self._make_client(timeout)
        return client.stream("POST", url, headers=headers, json=body)

    def _make_client(self, timeout: httpx.Timeout) -> httpx.AsyncClient:
        """构造 httpx client。测试里可以 override 注入 MockTransport。"""
        return httpx.AsyncClient(timeout=timeout)

    # ─────────────────────────────────────────
    # 内部
    # ─────────────────────────────────────────

    def _build_body(
        self,
        messages: List[Message],
        tools: Optional[List[ToolSchema]],
        system: Optional[str],
    ) -> Dict:
        body: Dict = {
            "model": self._info.model,
            "stream": True,
            "messages": [],
        }

        # system 拼接：来自 top-level system 串 + messages 中 role=system
        sys_segments: List[str] = []
        if system:
            sys_segments.append(system)
        for m in messages:
            if m.role == Role.SYSTEM:
                t = m.text()
                if t:
                    sys_segments.append(t)
        if sys_segments:
            body["messages"].append({
                "role": "system",
                "content": "\n\n".join(sys_segments),
            })

        for m in messages:
            if m.role == Role.SYSTEM:
                continue
            if m.role == Role.USER:
                body["messages"].append({"role": "user", "content": m.text()})
            elif m.role == Role.ASSISTANT:
                msg: Dict = {"role": "assistant"}
                txt = m.text()
                if txt:
                    msg["content"] = txt
                tcs = m.tool_calls()
                if tcs:
                    msg["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                            },
                        }
                        for tc in tcs
                    ]
                body["messages"].append(msg)
            elif m.role == Role.TOOL:
                # role=tool + tool_call_id（OpenAI 协议）
                for tr in m.tool_results():
                    body["messages"].append({
                        "role": "tool",
                        "tool_call_id": tr.tool_call_id,
                        "content": tr.content,
                    })

        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters or {"type": "object", "properties": {}},
                    },
                }
                for t in tools
            ]

        # 透传 extra（不覆盖已设字段）
        for k, v in self._extra.items():
            if k in _BLOCKED_EXTRA:
                continue
            if k not in body:
                body[k] = v
        # OpenAI 在 stream 里默认不给 usage，主动要
        if "stream_options" not in body:
            body["stream_options"] = {"include_usage": True}
        return body

    def _parse_chunk(self, data: Dict) -> list[ModelEvent]:
        out: list[ModelEvent] = []

        # 关键：usage 和 choices 可能同 chunk（OpenAI 标准）
        # 不能早 return，否则把最后一个 content 丢了
        if data.get("usage"):
            u = data["usage"]
            out.append(ModelEvent(
                type="usage",
                usage=ModelUsage(
                    input_tokens=u.get("prompt_tokens", 0) or 0,
                    output_tokens=u.get("completion_tokens", 0) or 0,
                ),
            ))

        choices = data.get("choices") or []
        if not choices:
            return out
        ch = choices[0]
        delta = ch.get("delta") or {}
        # 标准 content（OpenAI / 多数 OpenAI-compat）
        if "content" in delta and delta["content"] is not None:
            out.append(ModelEvent(type="text_delta", text=delta["content"]))
        # reasoning content（DeepSeek-R1 / Kimi / Qwen-QwQ / 一些 OpenAI-compat）
        # 不同供应商字段名不一样：reasoning_content / reasoning_text / reasoning
        for rk in ("reasoning_content", "reasoning_text", "reasoning"):
            rv = delta.get(rk)
            if rv:
                out.append(ModelEvent(type="thinking_delta", text=rv))
                break
        for tc in (delta.get("tool_calls") or []):
            fn = tc.get("function") or {}
            out.append(ModelEvent(
                type="tool_call_delta",
                tool_call_id=tc.get("id") or "",
                tool_name=fn.get("name") or "",
                tool_args_delta=fn.get("arguments") or "",
            ))
        if ch.get("finish_reason"):
            out.append(ModelEvent(type="finish", finish_reason=ch["finish_reason"]))
        return out

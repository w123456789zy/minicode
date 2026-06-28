"""
Anthropic Messages API provider。

支持：
- 流式（SSE 事件流：message_start / content_block_start / content_block_delta / ...）
- 工具调用（tool_use block）
- system 字段独立
- extra 字段透传（max_tokens / temperature / top_p / top_k ...）

协议差异（与 OpenAI）：
- 没有 role=tool 的概念：工具结果作为 user message 内嵌一个 tool_result block
- tool_call id 形如 toolu_xxx
- system 是顶层字段，不是 messages 数组里
- max_tokens 必填（不填默认 1024，不够用会让长输出截断）
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator, Dict, List, Optional

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


_BLOCKED_EXTRA = {"messages", "system", "tools", "stream", "model"}


class AnthropicModel(Model):
    async def stream(
        self,
        messages: List[Message],
        tools: Optional[List[ToolSchema]] = None,
        system: Optional[str] = None,
    ) -> AsyncIterator[ModelEvent]:
        body = self._build_body(messages, tools, system)

        # 解析状态
        tool_id: Optional[str] = None
        tool_name: Optional[str] = None
        tool_args: List[str] = []

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

                ev_type = ""
                async for raw in resp.aiter_lines():
                    if not raw:
                        continue
                    if raw.startswith("event:"):
                        ev_type = raw[6:].strip()
                        continue
                    if not raw.startswith("data:"):
                        continue
                    payload = raw[5:].strip()
                    try:
                        data = json.loads(payload)
                    except json.JSONDecodeError:
                        continue

                    if ev_type == "message_start":
                        msg = data.get("message") or {}
                        u = msg.get("usage") or {}
                        yield ModelEvent(
                            type="usage",
                            usage=ModelUsage(
                                input_tokens=u.get("input_tokens", 0) or 0,
                                output_tokens=u.get("output_tokens", 0) or 0,
                            ),
                        )
                    elif ev_type == "content_block_start":
                        cb = data.get("content_block") or {}
                        if cb.get("type") == "tool_use":
                            tool_id = cb.get("id") or ""
                            tool_name = cb.get("name") or ""
                            tool_args = []
                    elif ev_type == "content_block_delta":
                        delta = data.get("delta") or {}
                        if delta.get("type") == "text_delta":
                            yield ModelEvent(type="text_delta", text=delta.get("text", ""))
                        elif delta.get("type") == "input_json_delta":
                            tool_args.append(delta.get("partial_json", ""))
                    elif ev_type == "content_block_stop":
                        if tool_id and tool_name is not None:
                            yield ModelEvent(
                                type="tool_call_delta",
                                tool_call_id=tool_id,
                                tool_name=tool_name,
                                tool_args_delta="".join(tool_args),
                            )
                            tool_id = None
                            tool_name = None
                            tool_args = []
                    elif ev_type == "message_delta":
                        delta = data.get("delta") or {}
                        if "stop_reason" in delta:
                            yield ModelEvent(type="finish", finish_reason=delta["stop_reason"])
                        u = data.get("usage") or {}
                        if "output_tokens" in u:
                            yield ModelEvent(
                                type="usage",
                                usage=ModelUsage(output_tokens=u.get("output_tokens", 0) or 0),
                            )
                    elif ev_type == "error":
                        err = data.get("error") or {}
                        yield ModelEvent(type="error", error=str(err))
        except httpx.HTTPError as e:
            yield ModelEvent(type="error", error=str(e))

    # ─────────────────────────────────────────
    # HTTP 层（可被子类 / 测试覆盖）
    # ─────────────────────────────────────────

    async def _send_request(self, body: dict):
        url = self._info.base_url.rstrip("/") + "/v1/messages"
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "anthropic-version": "2023-06-01",
        }
        if self._api_key:
            headers["x-api-key"] = self._api_key
        timeout_s = float(self._extra.get("timeout", 60))
        timeout = httpx.Timeout(timeout_s, read=timeout_s * 2)
        client = self._make_client(timeout)
        return client.stream("POST", url, headers=headers, json=body)

    def _make_client(self, timeout: httpx.Timeout) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout)

    def _build_body(
        self,
        messages: List[Message],
        tools: Optional[List[ToolSchema]],
        system: Optional[str],
    ) -> Dict[str, Any]:
        # system 段
        sys_segments: List[str] = []
        if system:
            sys_segments.append(system)
        for m in messages:
            if m.role == Role.SYSTEM:
                t = m.text()
                if t:
                    sys_segments.append(t)

        body: Dict[str, Any] = {
            "model": self._info.model,
            "max_tokens": int(self._extra.get("max_tokens", 4096)),
            "stream": True,
        }
        if sys_segments:
            body["system"] = "\n\n".join(sys_segments)

        # 转换 messages
        out_messages: List[Dict] = []
        for m in messages:
            if m.role == Role.SYSTEM:
                continue
            if m.role == Role.USER:
                out_messages.append({"role": "user", "content": m.text()})
            elif m.role == Role.ASSISTANT:
                blocks: List[Dict] = []
                txt = m.text()
                if txt:
                    blocks.append({"type": "text", "text": txt})
                for tc in m.tool_calls():
                    blocks.append({
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.arguments or {},
                    })
                out_messages.append({"role": "assistant", "content": blocks})
            elif m.role == Role.TOOL:
                # Anthropic: user message 包含 tool_result block
                blocks = []
                for tr in m.tool_results():
                    blocks.append({
                        "type": "tool_result",
                        "tool_use_id": tr.tool_call_id,
                        "content": tr.content,
                        "is_error": tr.is_error,
                    })
                # 合并到最后一个 user message
                if out_messages and out_messages[-1]["role"] == "user":
                    out_messages[-1]["content"] = (
                        out_messages[-1]["content"] if isinstance(out_messages[-1]["content"], list) else [{"type": "text", "text": out_messages[-1]["content"]}]
                    )
                    out_messages[-1]["content"].extend(blocks)
                else:
                    out_messages.append({"role": "user", "content": blocks})
        body["messages"] = out_messages

        # tools
        if tools:
            body["tools"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.parameters or {"type": "object", "properties": {}},
                }
                for t in tools
            ]

        # 透传 extra
        for k, v in self._extra.items():
            if k in _BLOCKED_EXTRA:
                continue
            if k not in body:
                body[k] = v

        return body

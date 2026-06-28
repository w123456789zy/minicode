"""
Model 抽象基类。

设计：
- 一个 Model 实例 = 一个具体的 LLM endpoint + model name
- 提供 stream()（必须）+ complete()（默认基于 stream 攒起来）
- Model 不感知 Tool/MCP，只接收 LLM 视角的 tool schema（List[ToolSchema]）和 Message 列表
- 真实 HTTP 错误用 yield ModelEvent(type="error", error=...) 表达，不抛

为什么流式先行：
- CLI 场景几乎都是流式（用户能即时看到 token）
- 把流攒成非流是 O(n) 的简单操作，反之很难做
- 这样所有 provider 实现只需写 stream，complete 复用基类
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Dict, List, Optional

from pydantic import BaseModel, Field

from minicode.model.message import (
    Message,
    Role,
    TextPart,
    ToolCallPart,
    ToolResultPart,
    ToolSchema,
)


# ─────────────────────────────────────────────────────────────
# 元数据 / 数据类
# ─────────────────────────────────────────────────────────────


class ModelInfo(BaseModel):
    """一个 Model 的标识。"""
    id: str                              # provider id（来自 config.yaml）
    type: str                            # openai-compat / anthropic / demo
    base_url: str
    model: str


class ModelUsage(BaseModel):
    """token 用量统计。"""
    input_tokens: int = 0
    output_tokens: int = 0


# ─────────────────────────────────────────────────────────────
# 流式事件
# ─────────────────────────────────────────────────────────────


class ModelEvent(BaseModel):
    """stream() 逐步产出的事件。"""
    # 事件类型
    type: str  # "text_delta" | "tool_call_delta" | "usage" | "finish" | "error"
    # text_delta 字段
    text: str = ""
    # tool_call_delta 字段
    tool_call_id: str = ""
    tool_name: str = ""
    tool_args_delta: str = ""             # 部分 JSON 字符串
    # usage 字段
    usage: Optional[ModelUsage] = None
    # finish 字段
    finish_reason: str = ""               # "stop" | "tool_calls" | "length" | "error"
    # error 字段
    error: str = ""


# ─────────────────────────────────────────────────────────────
# 完整响应（非流式）
# ─────────────────────────────────────────────────────────────


class ModelResponse(BaseModel):
    """complete() 的返回。"""
    message: Message
    usage: ModelUsage = Field(default_factory=ModelUsage)
    finish_reason: str = "stop"
    raw: Optional[Dict[str, Any]] = None  # 原始 provider 响应，调试用


# ─────────────────────────────────────────────────────────────
# Model 抽象基类
# ─────────────────────────────────────────────────────────────


class Model(ABC):
    """LLM provider 抽象。

    子类必须实现 stream()；complete() 复用基类实现。
    """

    def __init__(self, info: ModelInfo, api_key: str = "", extra: Optional[Dict[str, Any]] = None):
        self._info = info
        self._api_key = api_key
        self._extra: Dict[str, Any] = dict(extra or {})

    @property
    def info(self) -> ModelInfo:
        return self._info

    @property
    def api_key(self) -> str:
        return self._api_key

    @property
    def extra(self) -> Dict[str, Any]:
        return self._extra

    @abstractmethod
    async def stream(
        self,
        messages: List[Message],
        tools: Optional[List[ToolSchema]] = None,
        system: Optional[str] = None,
    ) -> AsyncIterator[ModelEvent]:
        """流式调用。必须实现。"""
        # 子类用 `yield ...` 产出 ModelEvent
        raise NotImplementedError
        yield  # pragma: no cover  # 让该函数成为 generator

    async def complete(
        self,
        messages: List[Message],
        tools: Optional[List[ToolSchema]] = None,
        system: Optional[str] = None,
    ) -> ModelResponse:
        """非流式：把 stream 攒起来。子类一般不需要 override。"""
        text_parts: List[str] = []
        tool_calls: Dict[str, _PartialToolCall] = {}  # id -> partial
        usage = ModelUsage()
        finish = "stop"
        err: Optional[str] = None

        async for ev in self.stream(messages, tools, system):
            if ev.type == "text_delta":
                text_parts.append(ev.text)
            elif ev.type == "thinking_delta":
                # complete() 不持久化 reasoning（用户没要求；以后可加）
                pass
            elif ev.type == "tool_call_delta":
                if not ev.tool_call_id:
                    # 没有 id 的 delta：附加到最后一条
                    if tool_calls:
                        last = next(reversed(tool_calls.values()))
                        last.args_delta += ev.tool_args_delta
                        if ev.tool_name:
                            last.name = ev.tool_name
                    continue
                if ev.tool_call_id not in tool_calls:
                    tool_calls[ev.tool_call_id] = _PartialToolCall(
                        id=ev.tool_call_id,
                        name=ev.tool_name,
                        args_delta=ev.tool_args_delta,
                    )
                else:
                    p = tool_calls[ev.tool_call_id]
                    p.args_delta += ev.tool_args_delta
                    if ev.tool_name and not p.name:
                        p.name = ev.tool_name
            elif ev.type == "usage" and ev.usage:
                usage = ev.usage
            elif ev.type == "finish":
                if ev.finish_reason:
                    finish = ev.finish_reason
            elif ev.type == "error":
                err = ev.error
                finish = "error"

        if err:
            return ModelResponse(
                message=Message(
                    role=Role.ASSISTANT,
                    parts=[TextPart(text=f"[model error] {err}")],
                ),
                usage=usage,
                finish_reason="error",
            )

        # 构造 assistant message
        parts: List[Any] = []
        if text_parts:
            parts.append(TextPart(text="".join(text_parts)))
        for tc in tool_calls.values():
            try:
                args = json.loads(tc.args_delta) if tc.args_delta else {}
            except json.JSONDecodeError:
                # 流式截断时 args 不完整，回退到空 dict
                args = {"_raw": tc.args_delta}
            parts.append(ToolCallPart(id=tc.id, name=tc.name, arguments=args))

        return ModelResponse(
            message=Message(role=Role.ASSISTANT, parts=parts),
            usage=usage,
            finish_reason=finish,
        )


class _PartialToolCall:
    """流式攒起来的 tool call 片段。"""
    __slots__ = ("id", "name", "args_delta")

    def __init__(self, id: str, name: str, args_delta: str):
        self.id = id
        self.name = name
        self.args_delta = args_delta

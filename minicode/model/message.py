"""
Message / Part 类型。

设计参考 mimo code 的 message 抽象（role + parts 列表），
但用 Pydantic v2 表达，方便序列化、JSON Schema 化。

Part 类型：
- TextPart       普通文本
- ToolCallPart   assistant 发出的工具调用
- ToolResultPart 工具返回结果（用 role=tool 承载）

Message：
- role  user / assistant / system / tool
- parts 上述 Part 的列表

assistant 的 message 可同时含 text 和 tool_call；tool 的 message 通常只有一个 tool_result part。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union
from pydantic import BaseModel, Field


class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class TextPart(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ToolCallPart(BaseModel):
    type: Literal["tool_call"] = "tool_call"
    id: str                              # 对应 LLM 工具调用 id
    name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)


class ToolResultPart(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    tool_call_id: str
    content: str
    is_error: bool = False


Part = Union[TextPart, ToolCallPart, ToolResultPart]


class Message(BaseModel):
    role: Role
    parts: List[Part] = Field(default_factory=list)

    # ── 便捷构造 ──

    @classmethod
    def system(cls, text: str) -> "Message":
        return cls(role=Role.SYSTEM, parts=[TextPart(text=text)])

    @classmethod
    def user(cls, text: str) -> "Message":
        return cls(role=Role.USER, parts=[TextPart(text=text)])

    @classmethod
    def assistant_text(cls, text: str) -> "Message":
        return cls(role=Role.ASSISTANT, parts=[TextPart(text=text)])

    @classmethod
    def assistant(
        cls,
        text: Optional[str] = None,
        tool_calls: Optional[List[ToolCallPart]] = None,
    ) -> "Message":
        parts: List[Part] = []
        if text:
            parts.append(TextPart(text=text))
        if tool_calls:
            parts.extend(tool_calls)
        return cls(role=Role.ASSISTANT, parts=parts)

    @classmethod
    def tool_result(
        cls,
        tool_call_id: str,
        content: str,
        is_error: bool = False,
    ) -> "Message":
        return cls(
            role=Role.TOOL,
            parts=[ToolResultPart(tool_call_id=tool_call_id, content=content, is_error=is_error)],
        )

    # ── 查询 ──

    def text(self) -> str:
        """把所有 TextPart 拼起来。tool_call/tool_result 不算。"""
        return "".join(p.text for p in self.parts if isinstance(p, TextPart))

    def tool_calls(self) -> List[ToolCallPart]:
        return [p for p in self.parts if isinstance(p, ToolCallPart)]

    def tool_results(self) -> List[ToolResultPart]:
        return [p for p in self.parts if isinstance(p, ToolResultPart)]


# ─────────────────────────────────────────────────────────────
# Tool schema：给 LLM 用的工具描述（与 Tool 层解耦）
# ─────────────────────────────────────────────────────────────


class ToolSchema(BaseModel):
    """LLM 视角的工具描述。

    与 minicode.tool.ToolDef 的关系：ToolDef.tool → ToolSchema（中间转换在 caller 里做）
    """
    name: str
    description: str
    parameters: Dict[str, Any] = Field(default_factory=dict)   # JSON Schema dict

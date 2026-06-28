"""
/context 命令：像 Claude Code 一样展示当前上下文窗口占用。

把 context 拆成几类（类似 mimo code 的 budget breakdown）：
- system prompt
- tools schema
- history messages（按 role / tool 细分）
- 保留余量（预留 output）
- 剩余可用

核心函数：
- compute_breakdown()  计算各类 token 占用
- format_context_box() 把 breakdown 渲染成一长方形窗口
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from typing import List, Optional

from minicode.memory.budget import (
    estimate_message_tokens,
    estimate_tokens,
)
from minicode.model.message import Message, Role, ToolSchema


# 默认输出预留（token）
_DEFAULT_OUTPUT_RESERVE = 4000


@dataclass
class ContextBreakdown:
    """上下文窗口的各部分 token 占用。"""
    limit: int = 8000

    system_prompt_tokens: int = 0
    tools_schema_tokens: int = 0

    # history 按类型细分
    user_text_tokens: int = 0
    assistant_text_tokens: int = 0
    tool_call_tokens: int = 0            # assistant tool_call 指令
    tool_result_tokens: int = 0          # tool result content
    other_history_tokens: int = 0        # system/summary 等

    # 派生
    output_reserve: int = _DEFAULT_OUTPUT_RESERVE

    @property
    def history_tokens(self) -> int:
        return (
            self.user_text_tokens
            + self.assistant_text_tokens
            + self.tool_call_tokens
            + self.tool_result_tokens
            + self.other_history_tokens
        )

    @property
    def input_tokens(self) -> int:
        """实际作为模型输入的 token（system + tools + history）。"""
        return self.system_prompt_tokens + self.tools_schema_tokens + self.history_tokens

    @property
    def reserved_tokens(self) -> int:
        """为输出预留的 token。"""
        return self.output_reserve

    @property
    def total_used(self) -> int:
        """input + reserved。"""
        return self.input_tokens + self.reserved_tokens

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.total_used)

    @property
    def usage_ratio(self) -> float:
        if self.limit <= 0:
            return 0.0
        return min(1.0, self.total_used / self.limit)

    @property
    def pressure_level(self) -> int:
        r = self.usage_ratio
        if r < 0.50:
            return 0
        if r < 0.70:
            return 1
        if r < 0.85:
            return 2
        return 3


def _estimate_tools_schema_tokens(tools: List[ToolSchema]) -> int:
    """估算 tools schema 的 token。每个 schema 大概 name+desc+parameters。"""
    total = 0
    for t in tools:
        total += estimate_tokens(t.name)
        total += estimate_tokens(t.description)
        total += estimate_tokens(str(t.parameters))
    # 加少量 overhead（tool 列表结构）
    return total + 20 * max(len(tools), 1)


def compute_breakdown(
    system_prompt: str,
    tools: List[ToolSchema],
    history: List[Message],
    limit: int = 8000,
    output_reserve: int = _DEFAULT_OUTPUT_RESERVE,
) -> ContextBreakdown:
    """计算当前 context 窗口的各部分 token 占用。"""
    bd = ContextBreakdown(
        limit=limit,
        system_prompt_tokens=estimate_tokens(system_prompt),
        tools_schema_tokens=_estimate_tools_schema_tokens(tools),
        output_reserve=output_reserve,
    )

    for msg in history:
        if msg.role == Role.USER:
            bd.user_text_tokens += estimate_tokens(msg.text())
            bd.other_history_tokens += 4  # role overhead
        elif msg.role == Role.ASSISTANT:
            bd.assistant_text_tokens += estimate_tokens(msg.text())
            for tc in msg.tool_calls():
                bd.tool_call_tokens += estimate_tokens(tc.name)
                bd.tool_call_tokens += estimate_tokens(str(tc.arguments))
            bd.other_history_tokens += 4
        elif msg.role == Role.TOOL:
            for tr in msg.tool_results():
                bd.tool_result_tokens += estimate_tokens(tr.content)
            bd.other_history_tokens += 4
        else:
            bd.other_history_tokens += estimate_message_tokens(msg)

    return bd


def _bar(ratio: float, width: int = 20) -> str:
    """横向进度条。"""
    filled = round(ratio * width)
    if filled < 0:
        filled = 0
    if filled > width:
        filled = width
    return "█" * filled + "░" * (width - filled)


def _pad_line(text: str, width: int, fill: str = " ") -> str:
    """把一行内容截断或填充到指定宽度。"""
    if len(text) > width:
        return text[: width - 1] + "…"
    return text + fill * (width - len(text))


def _label_value(label: str, value: str, width: int) -> str:
    """生成一行 label + value，value 右对齐。"""
    total = len(label) + len(value)
    if total > width:
        # 先截断 label
        label = label[: max(0, width - len(value) - 1)] + "…"
        total = len(label) + len(value)
    pad = width - total
    return label + " " * pad + value


def format_context_box(
    bd: ContextBreakdown,
    max_width: Optional[int] = None,
) -> str:
    """把 ContextBreakdown 渲染成一长方形文本窗口。

    类似 Claude Code 的 context view：
    ┌─ Context Window ────────────────────────┐
    │ total limit  : 128K                     │
    │ used         : 12,345 (9.6%)            │
    │ [████████████████░░░░░░░░░░░░░░░░░░░░░░]│
    │                                          │
    │ breakdown                                │
    │   system prompt     466 (0.4%)           │
    │   tools schema      1,234 (1.0%)         │
    │   history           6,145 (4.8%)         │
    │     user text         345                │
    │     assistant text  1,200                │
    │     tool calls        800                │
    │     tool results    3,800                │
    │                                          │
    │   output reserve    4,000 (3.1%)         │
    │   remaining       116,155 (90.7%)        │
    └─────────────────────────────────────────┘
    """
    if max_width is None:
        try:
            max_width = max(60, shutil.get_terminal_size().columns - 2)
        except (OSError, ValueError):
            max_width = 70

    # 内部内容宽度 = 总宽 - 2（左右边框）
    inner_width = max_width - 2
    lines: List[str] = []

    def _add(content: str = "") -> None:
        lines.append("│" + _pad_line(content, inner_width) + "│")

    # 顶部边框
    lines.append("┌" + "─" * inner_width + "┐")

    # 标题
    _add("Context Window")
    _add()

    def _fmt(n: int) -> str:
        return f"{n:,}"

    def _pct(n: int) -> str:
        if bd.limit <= 0:
            return "0.0%"
        return f"{n / bd.limit * 100:.1f}%"

    # 总览
    _add(_label_value("  limit", f"{_fmt(bd.limit)} ({_pct(bd.limit)})", inner_width))
    _add(_label_value("  used", f"{_fmt(bd.total_used)} ({_pct(bd.total_used)})", inner_width))

    # 进度条行
    bar = _bar(bd.usage_ratio, width=max(inner_width - 2, 10))
    _add(f"  [{bar}]")
    _add()

    # 分项明细
    _add("  breakdown")
    _add(_label_value("    system prompt", f"{_fmt(bd.system_prompt_tokens)} ({_pct(bd.system_prompt_tokens)})", inner_width))
    _add(_label_value("    tools schema", f"{_fmt(bd.tools_schema_tokens)} ({_pct(bd.tools_schema_tokens)})", inner_width))
    _add(_label_value("    history", f"{_fmt(bd.history_tokens)} ({_pct(bd.history_tokens)})", inner_width))

    # history 子项
    if bd.user_text_tokens:
        _add(_label_value("      user text", f"{_fmt(bd.user_text_tokens)}", inner_width))
    if bd.assistant_text_tokens:
        _add(_label_value("      assistant text", f"{_fmt(bd.assistant_text_tokens)}", inner_width))
    if bd.tool_call_tokens:
        _add(_label_value("      tool calls", f"{_fmt(bd.tool_call_tokens)}", inner_width))
    if bd.tool_result_tokens:
        _add(_label_value("      tool results", f"{_fmt(bd.tool_result_tokens)}", inner_width))
    if bd.other_history_tokens:
        _add(_label_value("      other", f"{_fmt(bd.other_history_tokens)}", inner_width))

    _add()
    _add(_label_value("  output reserve", f"{_fmt(bd.reserved_tokens)} ({_pct(bd.reserved_tokens)})", inner_width))
    _add(_label_value("  remaining", f"{_fmt(bd.remaining)} ({_pct(bd.remaining)})", inner_width))

    # 压力等级提示
    level = bd.pressure_level
    labels = ["low", "medium", "high", "critical"]
    label = labels[level]
    _add()
    _add(_label_value("  pressure", f"{label} (level {level})", inner_width))

    # 底部边框
    lines.append("└" + "─" * inner_width + "┘")

    return "\n".join(lines)

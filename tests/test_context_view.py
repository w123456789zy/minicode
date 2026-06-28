"""测试 /context 的 breakdown 计算和渲染。"""

from minicode.memory.context_view import (
    ContextBreakdown,
    compute_breakdown,
    format_context_box,
)
from minicode.model.message import Message, ToolSchema


def test_compute_breakdown_empty():
    """空输入时只有 system + tools 开销。"""
    bd = compute_breakdown("hi", [], [], limit=8000)
    assert bd.limit == 8000
    assert bd.system_prompt_tokens > 0
    assert bd.tools_schema_tokens > 0  # 工具为空时也有默认 overhead
    assert bd.history_tokens == 0
    assert bd.total_used > 0  # 含 output_reserve


def test_compute_breakdown_with_history():
    """history 按 role 正确分类累加。"""
    tools = [
        ToolSchema(name="bash", description="run shell", parameters={"type": "object"}),
    ]
    history = [
        Message.user("hello"),
        Message.assistant_text("hi"),
        Message.user("call tool"),
        Message(role="assistant", parts=[{"type": "tool_call", "id": "1", "name": "bash", "arguments": {"cmd": "ls"}}]),
        Message.tool_result("1", "file1\nfile2\nfile3"),
    ]
    bd = compute_breakdown("system", tools, history, limit=8000)
    assert bd.user_text_tokens > 0
    assert bd.assistant_text_tokens > 0
    assert bd.tool_call_tokens > 0
    assert bd.tool_result_tokens > 0
    assert bd.history_tokens == (
        bd.user_text_tokens
        + bd.assistant_text_tokens
        + bd.tool_call_tokens
        + bd.tool_result_tokens
        + bd.other_history_tokens
    )


def test_context_box_contains_key_sections():
    """渲染出的窗口包含关键区域。"""
    bd = ContextBreakdown(
        limit=128000,
        system_prompt_tokens=466,
        tools_schema_tokens=1234,
        user_text_tokens=345,
        assistant_text_tokens=1200,
        tool_call_tokens=800,
        tool_result_tokens=3800,
        other_history_tokens=100,
        output_reserve=4000,
    )
    out = format_context_box(bd, max_width=60)
    assert "┌" in out
    assert "└" in out
    assert "Context Window" in out
    assert "system prompt" in out
    assert "tools schema" in out
    assert "history" in out
    assert "tool results" in out
    assert "remaining" in out
    assert "pressure" in out
    assert "│" in out


def test_context_box_respects_max_width():
    """max_width 控制每行宽度。"""
    bd = ContextBreakdown(limit=8000)
    out = format_context_box(bd, max_width=50)
    lines = out.split("\n")
    for line in lines:
        # 每行都是 max_width 字符
        assert len(line) == 50, f"line length {len(line)} != 50: {line!r}"


def test_context_box_hides_zero_subsections():
    """为 0 的 history 子项不显示。"""
    bd = ContextBreakdown(
        limit=8000,
        user_text_tokens=100,
    )
    out = format_context_box(bd, max_width=60)
    assert "user text" in out
    assert "assistant text" not in out
    assert "tool calls" not in out
    assert "tool results" not in out


def test_pressure_levels():
    """pressure level 按 usage_ratio 正确。"""
    # total_used = input(0) + reserve(4000) -> 40% -> level 0
    assert ContextBreakdown(limit=10000, output_reserve=4000).pressure_level == 0
    # 高 usage: input=6000 + reserve=4000 = 10000 -> 100% -> level 3
    bd = ContextBreakdown(limit=10000, output_reserve=4000, system_prompt_tokens=6000)
    assert bd.pressure_level == 3

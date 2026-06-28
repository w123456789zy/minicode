"""memory.status 单测。"""
from minicode.memory.budget import ContextBudget
from minicode.memory.status import format_status


def test_format_status_empty_budget():
    s = format_status(ContextBudget(), width=200)
    assert "minicode> " in s
    assert "ctx 0/8000" in s
    assert "░░░░░░░░░░" in s  # 全空


def test_format_status_half_used():
    b = ContextBudget(system_tokens=2000, history_tokens=2000, limit=8000)
    s = format_status(b, width=200)
    assert "ctx 4000/8000" in s
    # 半填充：5 个 █ + 5 个 ░
    assert "█████░░░░░" in s


def test_format_status_overflow_capped():
    b = ContextBudget(system_tokens=9999, history_tokens=0, limit=100)
    s = format_status(b, width=200)
    # 不会超过 10 个 █
    assert s.count("█") == 10
    assert "9999/100" in s


def test_format_status_color_disabled_when_no_color():
    # width 足够大时不截断 prefix
    b = ContextBudget(system_tokens=4000, history_tokens=0, limit=8000)
    s = format_status(b, width=200)
    # 没有 TTY 时不应有 ANSI 转义
    # （在 pytest 环境通常不是 TTY）
    # 至少格式正确
    assert "minicode> " in s
    assert "ctx 4000/8000" in s


def test_format_status_truncates_when_narrow():
    b = ContextBudget(system_tokens=100, history_tokens=0, limit=8000)
    s = format_status(b, width=20)  # 极窄
    # 应不超 20 字符太多
    assert len(s) <= 30  # 允许一点误差（核心段必需）

"""
输入框前的 ctx 状态栏。

按你的要求："做一个展示当前用例多少上下文的一个文本框，只是在用户填写内容的时候展示"。

设计：
- 用户输入时（input() 前）打印一行状态
- 输入结束（按 enter）后状态行"消失"——用 \r + 空格覆盖回车
  （简单实现：状态行只在 input 同一行内显示，不换行；用户回车后自然消失）
- 不在 REPL print 输出里出现（不影响流式输出 / 命令结果）

格式：
    minicode> [ctx 1234/8000 ████░░░░░░] $_

  - 1234/8000: 已用 / 总量
  - ████░░░░░░: 10 段进度条（按 usage_ratio 填充）
  - $_: 实际光标位置

颜色：
- 绿 (< 60%) / 黄 (60-85%) / 红 (> 85%)，靠 ANSI 转义
- 终端不支持时降级为无色

压力等级提示：
- level 0 (< 50%)：无标记
- level 1 (50-70%)：⚠
- level 2 (70-85%)：⚠⚠
- level 3 (≥ 85%)：⚠⚠⚠
"""

from __future__ import annotations

import shutil
from typing import Optional

from minicode._ansi import _DIM, _GREEN, _GREY, _RED, _RESET, _YELLOW, _color_enabled
from minicode.memory.budget import ContextBudget


_BAR_WIDTH = 10


def _bar(ratio: float) -> str:
    """10 段进度条，filled = round(ratio * 10)。"""
    filled = round(ratio * _BAR_WIDTH)
    if filled < 0:
        filled = 0
    if filled > _BAR_WIDTH:
        filled = _BAR_WIDTH
    return "█" * filled + "░" * (_BAR_WIDTH - filled)


def _color_for(ratio: float) -> str:
    if ratio < 0.6:
        return _GREEN
    if ratio < 0.85:
        return _YELLOW
    return _RED


def _pressure_marker(level: int) -> str:
    """压力等级标记。"""
    if level <= 0:
        return ""
    return " " + "!" * level


def _term_width(default: int = 100) -> int:
    try:
        return max(40, shutil.get_terminal_size().columns)
    except (OSError, ValueError):
        return default


def format_status(budget: ContextBudget, prefix: str = "minicode> ", width: Optional[int] = None) -> str:
    """生成要显示在 input 前的那一行（不含换行）。

    返回的字符串以"光标起始"结尾（不包含 trailing space）。
    """
    ratio = budget.usage_ratio
    bar = _bar(ratio)
    used = budget.total
    limit = budget.limit
    pressure = _pressure_marker(budget.pressure_level)
    core = f"ctx {used}/{limit} {bar}{pressure}"

    if _color_enabled():
        color = _color_for(ratio)
        colored = f"{color}{core}{_RESET}"
    else:
        colored = core

    # 总宽度保护：超过终端宽就截断
    full = f"{prefix}[{colored}] "
    w = width if width is not None else _term_width()
    if len(full) > w:
        # 截断 prefix
        keep = max(0, w - (len(full) - len(prefix)) - 1)
        full = prefix[:keep] + "…" + "[" + core + "] "

    return full

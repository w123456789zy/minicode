"""ANSI 颜色常量与 TTY 探测工具，供各模块共用。"""

import os
import sys

# ── 颜色常量 ──
_RESET = "\x1b[0m"
_BOLD = "\x1b[1m"
_DIM = "\x1b[2m"
_RED = "\x1b[31m"
_GREEN = "\x1b[32m"
_YELLOW = "\x1b[33m"
_MAGENTA = "\x1b[35m"
_CYAN = "\x1b[36m"
_GREY = "\x1b[90m"


def _is_tty() -> bool:
    return hasattr(sys.stdout, "isatty") and bool(sys.stdout.isatty())


def _color_enabled() -> bool:
    return _is_tty() and os.environ.get("TERM", "") != "dumb"


def _c(text: str, color: str) -> str:
    """条件着色：颜色启用时包裹 ANSI 码，否则原样返回。"""
    if _color_enabled():
        return f"{color}{text}{_RESET}"
    return text
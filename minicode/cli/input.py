"""
带斜杠命令补全的自定义输入读取。

在 TTY 环境下，用户输入 `/` 时在下方显示可用命令列表；
继续输入字符时实时过滤匹配的命令。
按 Tab 自动补全到最长公共前缀；按 Enter 确认；按 Ctrl+C / Ctrl+D 中断。

非 TTY 环境下直接降级为 input()。

实现方式：
- Windows: msvcrt.getch() 逐字符读取
- POSIX: termios + sys.stdin 逐字符读取
- 不依赖 prompt_toolkit / rich 等第三方库
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from typing import Callable, Iterator, List, Optional, Tuple

from minicode._ansi import _BOLD, _CYAN, _DIM, _GREEN, _GREY, _RESET, _YELLOW, _color_enabled, _is_tty

# input 专用 ANSI 转义码
_CLEAR_LINE = "\x1b[2K"
_MOVE_UP = "\x1b[1A"
_MOVE_DOWN = "\x1b[1B"


# ─────────────────────────────────────────────────────────────
# 命令列表（与 HELP_TEXT 保持一致）
# ─────────────────────────────────────────────────────────────

BUILTIN_SLASH_COMMANDS: List[Tuple[str, str]] = [
    ("/tools", "列出所有工具"),
    ("/skills", "列出所有 skill"),
    ("/agents", "列出所有 subagent"),
    ("/hooks", "列出已加载的 hook"),
    ("/mcp", "列出 MCP 服务和状态"),
    ("/model", "显示/测试当前 model"),
    ("/memory", "显示 AGENTS.md + rules"),
    ("/context", "显示上下文窗口占用"),
    ("/history", "列出历史会话"),
    ("/compact", "压缩历史对话"),
    ("/goal", "设置/查看停止条件"),
    ("/chat", "chat bridge 管理"),
    ("/permission", "权限管理"),
    ("/display", "渲染 demo"),
    ("/paths", "打印路径解析结果"),
    ("/call", "手动调用工具"),
    ("/reload", "重新 build registry"),
    ("/help", "打印帮助"),
    ("/commands", "列出自定义命令"),
    ("/exit", "退出"),
    ("/quit", "退出"),
]


def _filter_commands(prefix: str, extra: Optional[List[Tuple[str, str]]] = None) -> List[Tuple[str, str]]:
    all_cmds = list(BUILTIN_SLASH_COMMANDS)
    if extra:
        all_cmds.extend(extra)
    if not prefix:
        return all_cmds
    return [(c, d) for c, d in all_cmds if c.startswith(prefix)]


def _longest_common_prefix(commands: List[str]) -> str:
    if not commands:
        return ""
    shortest = min(commands, key=len)
    for i, ch in enumerate(shortest):
        for cmd in commands:
            if cmd[i] != ch:
                return shortest[:i]
    return shortest


# ─────────────────────────────────────────────────────────────
# 渲染补全提示
# ─────────────────────────────────────────────────────────────

def _render_suggestions(buffer: str, extra: Optional[List[Tuple[str, str]]] = None, max_show: int = 8) -> List[str]:
    matches = _filter_commands(buffer, extra=extra)
    if not matches:
        return []

    lines: List[str] = []
    lines.append(f"{_DIM}┌─ commands ──────────────────────────{_RESET}")

    max_cmd_len = max(len(c) for c, _ in matches)
    col_width = max(max_cmd_len, 10)

    shown = matches[:max_show]
    for cmd, desc in shown:
        if _color_enabled() and buffer:
            highlighted = f"{_CYAN}{buffer}{_RESET}{cmd[len(buffer):]}"
        else:
            highlighted = cmd
        lines.append(f"{_DIM}│{_RESET} {highlighted:<{col_width}} {desc}")

    if len(matches) > max_show:
        lines.append(f"{_DIM}│ … +{len(matches) - max_show} more{_RESET}")

    lines.append(f"{_DIM}└─────────────────────────────────────{_RESET}")
    return lines


def _clear_suggestion_lines(n_lines: int) -> None:
    if n_lines <= 0:
        return
    sys.stdout.write("\r")
    for _ in range(n_lines):
        sys.stdout.write(_MOVE_DOWN)
        sys.stdout.write(_CLEAR_LINE)
    sys.stdout.write("\r")
    sys.stdout.write(_MOVE_UP * n_lines)
    sys.stdout.write("\r")
    sys.stdout.flush()


def _show_suggestions(buffer: str, prompt: str, old_count: int, extra: Optional[List[Tuple[str, str]]] = None) -> int:
    lines = _render_suggestions(buffer, extra=extra)
    new_count = len(lines)
    total = max(old_count, new_count)

    if total == 0:
        return 0

    # 向下清空 total 行，再回来
    sys.stdout.write("\r")
    for _ in range(total):
        sys.stdout.write(_MOVE_DOWN)
        sys.stdout.write(_CLEAR_LINE)
    sys.stdout.write("\r")
    sys.stdout.write(_MOVE_UP * total)
    sys.stdout.write("\r")

    # 重画输入行
    sys.stdout.write(_CLEAR_LINE)
    sys.stdout.write(prompt + buffer)

    if new_count == 0:
        sys.stdout.flush()
        return 0

    # 逐行写补全提示
    sys.stdout.write(_MOVE_DOWN)
    for line in lines:
        sys.stdout.write("\r")
        sys.stdout.write(_CLEAR_LINE)
        sys.stdout.write(line)
        sys.stdout.write(_MOVE_DOWN)

    # 光标回输入行
    sys.stdout.write("\r")
    sys.stdout.write(_MOVE_UP * (new_count + 1))
    sys.stdout.write("\r")
    sys.stdout.write(prompt + buffer)
    sys.stdout.flush()
    return new_count


# ─────────────────────────────────────────────────────────────
# 平台差异：字符读取
# ─────────────────────────────────────────────────────────────

if sys.platform == "win32":
    def _get_char() -> str:
        import msvcrt
        return msvcrt.getwch()

    _EOF_CHAR = "\x1a"  # Ctrl+Z

    @contextmanager
    def _terminal_raw() -> Iterator[None]:
        yield
else:
    def _get_char() -> str:
        return sys.stdin.read(1)

    _EOF_CHAR = "\x04"  # Ctrl+D

    @contextmanager
    def _terminal_raw() -> Iterator[None]:
        import termios
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            yield
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


# ─────────────────────────────────────────────────────────────
# 统一逐字符读取（平台差异仅 _get_char / _EOF_CHAR / _terminal_raw）
# ─────────────────────────────────────────────────────────────

def _read_line(prompt: str, extra: Optional[List[Tuple[str, str]]] = None) -> Optional[str]:
    buffer = ""
    suggestion_lines = 0

    sys.stdout.write(prompt)
    sys.stdout.flush()

    with _terminal_raw():
        while True:
            ch = _get_char()

            if ch in ("\r", "\n"):
                _clear_suggestion_lines(suggestion_lines)
                sys.stdout.write("\n")
                sys.stdout.flush()
                return buffer

            if ch == "\x03":  # Ctrl+C
                _clear_suggestion_lines(suggestion_lines)
                sys.stdout.write("\n")
                sys.stdout.flush()
                raise KeyboardInterrupt

            if ch == _EOF_CHAR:
                _clear_suggestion_lines(suggestion_lines)
                sys.stdout.write("\n")
                sys.stdout.flush()
                raise EOFError

            if ch in ("\x08", "\x7f"):  # Backspace
                if buffer:
                    buffer = buffer[:-1]
                    sys.stdout.write("\b \b")
                    sys.stdout.flush()
                    suggestion_lines = _show_suggestions(buffer, prompt, suggestion_lines, extra=extra)
                continue

            if ch == "\t":  # Tab 补全
                if buffer.startswith("/"):
                    matches = _filter_commands(buffer, extra=extra)
                    if matches:
                        cmds = [c for c, _ in matches]
                        lcp = _longest_common_prefix(cmds)
                        if lcp and lcp != buffer:
                            added = lcp[len(buffer):]
                            buffer = lcp
                            sys.stdout.write(added)
                            sys.stdout.flush()
                            suggestion_lines = _show_suggestions(buffer, prompt, suggestion_lines, extra=extra)
                continue

            if ch == "\x1b":  # Escape 清空
                _clear_suggestion_lines(suggestion_lines)
                suggestion_lines = 0
                for _ in range(len(buffer)):
                    sys.stdout.write("\b \b")
                buffer = ""
                sys.stdout.flush()
                continue

            if ch.isprintable():
                buffer += ch
                sys.stdout.write(ch)
                sys.stdout.flush()
                suggestion_lines = _show_suggestions(buffer, prompt, suggestion_lines, extra=extra)


# ─────────────────────────────────────────────────────────────
# 公共入口
# ─────────────────────────────────────────────────────────────

def read_line_with_completion(prompt: str = "minicode> ", extra_commands: Optional[List[Tuple[str, str]]] = None) -> str:
    """读取一行用户输入，支持斜杠命令补全。

    - TTY 环境：逐字符读取，输入 `/` 时显示命令列表
    - 非 TTY：降级为 input()
    - extra_commands: 额外的自定义命令列表 [(cmd, desc), ...]，用于补全
    """
    if not _is_tty():
        return input(prompt)
    return _read_line(prompt, extra=extra_commands)
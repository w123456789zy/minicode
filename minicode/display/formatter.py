"""
minicode.display.formatter：结构化展示 thinking / model-input / tool-call / code-change。

所有 render_*() 函数返回 str（多行），调用方负责 print。

输出风格：
- 用 box-drawing 横线 `─` / `┌┐└┘` 围出一块
- TTY 环境下加 ANSI 颜色提升可读性（非 TTY 自动降级）
- indent = 2 空格
- 长内容截断策略（truncate）：
    - 短于 max_len：原样返回
    - 超过：保留 head = max_len * 70% 头，tail = max_len * 20% 尾，中间一行 `... [truncated NNNN chars] ...`
- 工具调用 args 展示：默认 json.dumps 缩进 80 字符后截断
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from minicode._ansi import _BOLD, _CYAN, _DIM, _GREEN, _GREY, _MAGENTA, _RED, _RESET, _YELLOW, _c


# ─────────────────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────────────────


@dataclass
class ThinkingBlock:
    """模型思考内容（来自 reasoning content / 思考链）。"""
    content: str
    model: Optional[str] = None       # 哪个模型产出的（debug 用）
    duration_ms: Optional[int] = None # 可选：思考耗时


@dataclass
class ModelInputView:
    """一次模型调用前要发出去的东西。"""
    system: str = ""
    messages: List[Dict[str, Any]] = field(default_factory=list)
    # 简化：messages 一律 [{"role": ..., "content": str | parts}], 不强制结构
    model: Optional[str] = None
    tools: Optional[List[Dict[str, Any]]] = None  # tool schemas 的简版


@dataclass
class ToolCallView:
    """一次工具调用。"""
    name: str
    args: Dict[str, Any] = field(default_factory=dict)
    call_id: Optional[str] = None
    source: Optional[str] = None       # "model" / "user" / "subagent:<name>"


@dataclass
class ToolResultView:
    """一次工具执行结果。"""
    name: str
    call_id: Optional[str] = None
    content: str = ""
    is_error: bool = False
    exit_code: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CodeChange:
    """一次代码修改（来自 edit/write 工具，或模型声明的 diff）。"""
    path: str
    old_text: Optional[str] = None
    new_text: Optional[str] = None
    # 简化的 diff 统计（行级），可由调用方算好后塞进来
    added: int = 0
    removed: int = 0
    note: Optional[str] = None  # e.g. "applied via write tool" / "model proposed"


# ─────────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────────


def truncate(s: str, max_len: int = 600) -> str:
    """长字符串截断：保留头 70% / 尾 20%。"""
    if not isinstance(s, str):
        s = str(s)
    if max_len <= 0 or len(s) <= max_len:
        return s
    head = int(max_len * 0.7)
    tail = max_len - head
    return s[:head] + f"\n... [truncated {len(s) - head - tail} chars] ...\n" + s[-tail:]


def format_args(args: Any, max_len: int = 240) -> str:
    """把 args dict（或其他）格式化成可读单行（必要时多行 + 截断）。"""
    if args is None:
        return "(no args)"
    if not isinstance(args, (dict, list)):
        s = str(args)
        return truncate(s, max_len)
    try:
        s = json.dumps(args, ensure_ascii=False, default=str)
    except Exception:
        s = repr(args)
    return truncate(s, max_len)


def _line(c: str = "─", n: int = 60) -> str:
    return c * n


def _indent(text: str, prefix: str = "  ") -> str:
    """给每行加 prefix（不处理空行）。"""
    if not text:
        return ""
    return "\n".join(prefix + line if line else line for line in text.splitlines())


# ─────────────────────────────────────────────────────────────
# 思考内容
# ─────────────────────────────────────────────────────────────


def render_thinking(block: ThinkingBlock, max_len: int = 1200) -> str:
    """展示模型思考链。

    格式：
      ┌─ [thinking]  (model-name) ────────────────────────────
      │ (内容，可能多行缩进)
      └─ (5 lines, 230ms) ───────────────────────────────────
    """
    title = "[thinking]"
    if block.model:
        title += f"  ({block.model})"
    head = f"{_c('┌─', _DIM)} {_c(title, _MAGENTA)} {_c(_line('─', max(8, 50 - len(title))), _DIM)}"
    body = _indent(_c(truncate(block.content or "(empty)", max_len), _DIM), f"{_c('│', _DIM)} ")
    if block.duration_ms is not None:
        meta = f"({_rough_lines(block.content)} lines, {block.duration_ms}ms)"
    else:
        meta = f"({_rough_lines(block.content)} lines)"
    tail = f"{_c('└─', _DIM)} {_c(meta, _GREY)} {_c(_line('─', max(8, 50 - len(meta))), _DIM)}"
    return "\n".join([head, body, tail])


# ─────────────────────────────────────────────────────────────
# 模型输入
# ─────────────────────────────────────────────────────────────


def render_model_input(view: ModelInputView, max_msg_len: int = 200) -> str:
    """展示一次调 LLM 前准备的内容：system + messages + tools。

    格式：
      ── model input ──
        model   : gpt-4o
        system  : (120 tokens, 540 chars)
                  <preview>
        messages: 3 turn(s)
          #00 [user     ]  hello
          #01 [assistant]  I'm going to ...
          #02 [tool     ]  exit=0, 12 lines
        tools   : 5 schema(s): bash, read, edit, glob, grep
      ── end ──
    """
    lines = [f"{_c('──', _DIM)} {_c('model input', _CYAN)} {_c('──', _DIM)}"]
    if view.model:
        lines.append(f"  {_c('model', _GREY)}   : {_c(view.model, _BOLD)}")

    # system
    sys_text = view.system or ""
    sys_info = f"({len(sys_text)} chars"
    if sys_text:
        sys_info += f", ~{_rough_tokens(sys_text)} tokens"
    sys_info += ")"
    lines.append(f"  {_c('system', _GREY)}  : {sys_info}")
    if sys_text:
        preview = truncate(sys_text, max_msg_len).replace("\n", " ⏎ ")
        lines.append(f"            {_c(preview, _DIM)}")

    # messages
    if view.messages:
        lines.append(f"  {_c('messages', _GREY)}: {len(view.messages)} turn(s)")
        for i, m in enumerate(view.messages):
            role = m.get("role", "?") if isinstance(m, dict) else "?"
            content = m.get("content", "")
            if not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False, default=str)
            preview = truncate(content, max_msg_len).replace("\n", " ⏎ ")
            # role 用颜色区分
            role_color = _role_color(role)
            lines.append(f"    {_c(f'#{i:02d}', _GREY)} [{_c(f'{role:9s}', role_color)}]  {preview}")
            # 额外：tool_call 提示
            tcs = m.get("tool_calls") if isinstance(m, dict) else None
            if tcs:
                names = ",".join(tc.get("name", "?") for tc in tcs)
                lines.append(f"            {_c('↳ tool_calls:', _YELLOW)} {names}")
            # 额外：tool 消息
            if role == "tool":
                tc_id = m.get("tool_call_id", "?")
                lines.append(f"            {_c('↳ tool_call_id:', _YELLOW)} {tc_id}")
    else:
        lines.append(f"  {_c('messages', _GREY)}: {_c('(none)', _DIM)}")

    # tools
    if view.tools is not None:
        if not view.tools:
            lines.append(f"  {_c('tools', _GREY)}   : {_c('(none)', _DIM)}")
        else:
            names = [t.get("name", "?") for t in view.tools]
            preview = ", ".join(names[:8]) + (f" … +{len(names) - 8} more" if len(names) > 8 else "")
            lines.append(f"  {_c('tools', _GREY)}   : {len(view.tools)} schema(s): {_c(preview, _GREEN)}")

    lines.append(f"{_c('──', _DIM)} {_c('end', _DIM)} {_c('──', _DIM)}")
    return "\n".join(lines)


def _role_color(role: str) -> str:
    """按 role 选颜色。"""
    if role == "user":
        return _CYAN
    if role == "assistant":
        return _GREEN
    if role == "tool":
        return _YELLOW
    return _GREY


# ─────────────────────────────────────────────────────────────
# 工具调用
# ─────────────────────────────────────────────────────────────


def render_tool_call(tc: ToolCallView, max_len: int = 240) -> str:
    """展示一次工具调用（不展示结果——这是用户明确要求）。

    格式：
      ── tool call ──
        name    : bash
        id      : abc12345
        source  : model
        args    : {"command": "ls -la"}
      ── end ──
    """
    lines = [f"{_c('──', _DIM)} {_c('tool call', _YELLOW)} {_c('──', _DIM)}"]
    lines.append(f"  {_c('name', _GREY)}    : {_c(tc.name, _BOLD)}")
    if tc.call_id:
        lines.append(f"  {_c('id', _GREY)}      : {_c(tc.call_id, _DIM)}")
    if tc.source:
        lines.append(f"  {_c('source', _GREY)}  : {_c(tc.source, _CYAN)}")
    lines.append(f"  {_c('args', _GREY)}    : {format_args(tc.args, max_len)}")
    lines.append(f"{_c('──', _DIM)} {_c('end', _DIM)} {_c('──', _DIM)}")
    return "\n".join(lines)


def render_tool_result(tr: ToolResultView, max_len: int = 600) -> str:
    """展示一次工具执行结果。

    格式：
      ── tool result ──
        name    : bash
        id      : abc12345
        exit    : 0
        error   : false
        content : (12 lines, 345 chars)
                  <preview>
      ── end ──
    """
    lines = [f"{_c('──', _DIM)} {_c('tool result', _YELLOW)} {_c('──', _DIM)}"]
    lines.append(f"  {_c('name', _GREY)}    : {_c(tr.name, _BOLD)}")
    if tr.call_id:
        lines.append(f"  {_c('id', _GREY)}      : {_c(tr.call_id, _DIM)}")
    if tr.exit_code is not None:
        exit_color = _GREEN if tr.exit_code == 0 else _RED
        lines.append(f"  {_c('exit', _GREY)}    : {_c(str(tr.exit_code), exit_color)}")
    lines.append(f"  {_c('error', _GREY)}   : {_c(str(tr.is_error).lower(), _RED if tr.is_error else _GREEN)}")
    if tr.content:
        content_preview = truncate(tr.content, max_len)
        lines.append(f"  {_c('content', _GREY)} : ({_rough_lines(tr.content)} lines, {len(tr.content)} chars)")
        lines.append(f"            {_c(content_preview.replace(chr(10), ' ⏎ '), _DIM)}")
    else:
        lines.append(f"  {_c('content', _GREY)} : {_c('(empty)', _DIM)}")
    lines.append(f"{_c('──', _DIM)} {_c('end', _DIM)} {_c('──', _DIM)}")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# 代码更改
# ─────────────────────────────────────────────────────────────


def render_code_change_header(changes: List[CodeChange]) -> str:
    """展示一组 code change 的汇总（一次大改前/后用）。

    格式：
      ── code changes (3 files) ──
        src/foo.py        +12 -4
        src/bar.py        +0  -8 (delete)
        README.md         +2  -2
      ── end ──
    """
    if not changes:
        return f"{_c('──', _DIM)} {_c('code changes (0 files)', _DIM)} {_c('──', _DIM)}"
    lines = [f"{_c('──', _DIM)} {_c(f'code changes ({len(changes)} file(s))', _GREEN)} {_c('──', _DIM)}"]
    for c in changes:
        marker = ""
        if c.old_text is None:
            marker = f" {_c('(new)', _GREEN)}"
        elif c.new_text is None or c.new_text == "":
            marker = f" {_c('(delete)', _RED)}"
        elif c.new_text == c.old_text:
            marker = f" {_c('(no-op)', _DIM)}"
        added_str = _c(f"+{c.added}", _GREEN)
        removed_str = _c(f"-{c.removed}", _RED)
        lines.append(f"  {c.path:40s}  {added_str} {removed_str}{marker}")
    lines.append(f"{_c('──', _DIM)} {_c('end', _DIM)} {_c('──', _DIM)}")
    return "\n".join(lines)


def render_code_change(change: CodeChange, context: int = 3, max_total: int = 2000) -> str:
    """展示单文件 code change 的 diff 风格（unified diff 简化版）。

    - 头几行：路径 / 增删行数 / note
    - 旧文本有 → 旧行用 `  - `
    - 新文本有 → 新行用 `  + `
    - 共同行：保留 context 行，用 `    ` 前缀
    - 长 diff：截断
    """
    header = (
        f"{_c('──', _DIM)} {_c('code change:', _GREEN)} {change.path} "
        f"({_c(f'+{change.added}', _GREEN)} {_c(f'-{change.removed}', _RED)}) "
        f"{_c(_line('─', max(8, 40 - len(change.path))), _DIM)}"
    )
    if change.note:
        header += f"\n   {_c('note:', _GREY)} {change.note}"

    old_lines = (change.old_text or "").splitlines()
    new_lines = (change.new_text or "").splitlines()

    if not old_lines and not new_lines:
        body = "  (empty change)"
        return header + "\n" + body

    if change.old_text is None:
        body_lines = [f"  {_c('+', _GREEN)} {ln}" for ln in new_lines]
    elif change.new_text is None or change.new_text == "":
        body_lines = [f"  {_c('-', _RED)} {ln}" for ln in old_lines]
    elif change.old_text == change.new_text:
        body_lines = ["  (no-op)"]
    else:
        body_lines = _simple_unified(old_lines, new_lines, context=context)

    body = truncate("\n".join(body_lines), max_total)
    return header + "\n" + _indent(body, "  ").lstrip()


def _simple_unified(
    old: List[str], new: List[str], context: int = 3
) -> List[str]:
    """极简 unified diff：用 difflib 算 opcodes，输出 +/-/  前缀。

    不做复杂 merge；目标是给用户一个看得懂的概览，不是 patch 工具。
    """
    import difflib

    sm = difflib.SequenceMatcher(a=old, b=new, autojunk=False)
    out: List[str] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            # 头 / 尾各保留 context 行，中间用 ... 省略
            block = old[i1:i2]
            if len(block) <= context * 2 + 1:
                for ln in block:
                    out.append(f"    {_c(ln, _DIM)}")
            else:
                head = block[:context]
                tail = block[-context:]
                for ln in head:
                    out.append(f"    {_c(ln, _DIM)}")
                out.append(f"    {_c('...', _DIM)}")
                for ln in tail:
                    out.append(f"    {_c(ln, _DIM)}")
        elif tag == "delete":
            for ln in old[i1:i2]:
                out.append(f"  {_c('-', _RED)} {ln}")
        elif tag == "insert":
            for ln in new[j1:j2]:
                out.append(f"  {_c('+', _GREEN)} {ln}")
        elif tag == "replace":
            for ln in old[i1:i2]:
                out.append(f"  {_c('-', _RED)} {ln}")
            for ln in new[j1:j2]:
                out.append(f"  {_c('+', _GREEN)} {ln}")
    return out


# ─────────────────────────────────────────────────────────────
# 内部小工具
# ─────────────────────────────────────────────────────────────


def _rough_lines(text: str) -> int:
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def _rough_tokens(text: str) -> int:
    """粗估 token 数（中英文混合 4 字符/token）。"""
    if not text:
        return 0
    # 中文按字符算（接近 1.5 字符/token），英文按空格分词
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    other = len(text) - cjk
    return max(1, cjk // 2 + other // 4)

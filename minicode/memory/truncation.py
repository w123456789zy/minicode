"""
滑动窗口裁剪历史消息。

策略（分级，参考 mimo code 的 prune.ts）：
- system message 永不动（由 context.assemble_system 拼好后单独传）
- 只裁 messages 列表
- 保留规则：tool 角色永远跟它对应的 assistant tool_call 一起保留或一起裁
  → 简化：按"轮"切，1 轮 = 1 个 user + 后面所有 assistant/tool 直到下一个 user
- 保留最近 K 轮
- 不足 K 轮 → 原样返回

三级裁剪（由 budget.pressure_level 驱动）：
- soft_trim_tool_results：旧 tool result 内容 → head+tail（保留结构，压缩体积）
- hard_trim_tool_results：旧 tool result 内容 → 清空标记（保留 tool_call 配对）
- truncate_messages：丢弃整轮旧消息（最后的兜底）

不动：消息内容（不重写、不摘要）—— soft/hard trim 只动 tool result 的 content 字段
"""

from __future__ import annotations

import re
from typing import List, Tuple

from minicode.model.message import Message, Role, ToolResultPart


# ─────────────────────────────────────────────────────────────
# 轮切分
# ─────────────────────────────────────────────────────────────


def _split_into_turns(messages: List[Message]) -> List[List[Message]]:
    """把 messages 切成"轮"。

    一轮 = 1 个 user 开头 + 后面连续的 assistant/tool（直到下一个 user）。
    - system 不开新轮（一般是初始化注入的），归到第一轮
    - assistant 不开新轮：assistant 永远属于它前面的 user
    - tool 跟在 assistant 后面，归当前轮
    """
    turns: List[List[Message]] = []
    cur: List[Message] = []
    for m in messages:
        if m.role == Role.USER:
            if cur:
                turns.append(cur)
                cur = []
            cur.append(m)
        elif m.role == Role.ASSISTANT:
            # assistant 不开新轮——如果 cur 为空（异常情况：首条就是 assistant），
            # 当成独立轮起点
            if not cur:
                cur = [m]
            else:
                cur.append(m)
        elif m.role == Role.SYSTEM:
            # system 放当前轮（通常是第一条），不开新轮
            cur.append(m)
        elif m.role == Role.TOOL:
            # tool 永远跟在 assistant 后面，归当前轮
            if not cur:
                # 异常：tool 没有任何前置。当成独立轮。
                cur.append(m)
            else:
                cur.append(m)
    if cur:
        turns.append(cur)
    return turns


def truncate_messages(messages: List[Message], keep_turns: int = 20) -> List[Message]:
    """保留最近 keep_turns 轮。

    不足 keep_turns 轮 → 原样返回。
    """
    if keep_turns <= 0 or not messages:
        return list(messages)

    turns = _split_into_turns(messages)
    if len(turns) <= keep_turns:
        return list(messages)

    # 保留最后 K 轮
    kept = turns[-keep_turns:]
    out: List[Message] = []
    for t in kept:
        out.extend(t)
    return out


def split_old_recent(
    messages: List[Message], keep_turns: int = 20
) -> Tuple[List[Message], List[Message]]:
    """把 messages 切成 (old, recent)。

    - recent: 最后 keep_turns 轮
    - old: 之前的所有轮

    用于 /compact：把 old 调 LLM 压成 summary，再拼到 recent 最前。
    """
    if keep_turns <= 0 or not messages:
        return [], list(messages)

    turns = _split_into_turns(messages)
    if len(turns) <= keep_turns:
        return [], list(messages)

    cut = len(turns) - keep_turns
    old: List[Message] = []
    for t in turns[:cut]:
        old.extend(t)
    recent: List[Message] = []
    for t in turns[cut:]:
        recent.extend(t)
    return old, recent


# ─────────────────────────────────────────────────────────────
# 分级裁剪：soft / hard trim tool results
# ─────────────────────────────────────────────────────────────

# 错误特征（用于 head+tail 时判断是否保留尾部）
_ERROR_PATTERN = re.compile(
    r"error|exception|failed|fatal|traceback|panic|exit code",
    re.IGNORECASE,
)

# 软裁剪：超此字符数的 tool result → head+tail
_SOFT_TRIM_THRESHOLD = 4096
_SOFT_TRIM_HEAD = 1536
_SOFT_TRIM_TAIL = 1536

# 硬裁剪：清空内容，只留标记
_HARD_TRIM_MARKER = "[old tool result content cleared to save context]"

# 保护最近 N 轮不动（避免裁到当前正在用的上下文）
_PROTECT_RECENT_TURNS = 2


def _is_error_content(content: str) -> bool:
    """扫尾部 2048 字符找错误特征。"""
    tail = content[-2048:] if len(content) > 2048 else content
    return bool(_ERROR_PATTERN.search(tail))


def _soft_trim_content(content: str) -> str:
    """软裁剪：保留 head + tail，中间省略。

    错误感知：如果尾部有 error/exception 等特征，多留 tail（70% head / 30% tail → 50/50）。
    """
    if len(content) <= _SOFT_TRIM_THRESHOLD:
        return content
    # 错误感知：有错误特征时 head/tail 各半，否则 head 多 tail 少
    if _is_error_content(content):
        head_len = _SOFT_TRIM_HEAD
        tail_len = _SOFT_TRIM_TAIL
    else:
        head_len = _SOFT_TRIM_HEAD + 512
        tail_len = _SOFT_TRIM_TAIL - 512
    head = content[:head_len]
    tail = content[-tail_len:] if tail_len > 0 else ""
    return (
        head
        + f"\n\n[... trimmed — kept first {head_len} and last {tail_len} of {len(content)} chars ...]\n\n"
        + tail
    )


def _trim_tool_results_in_old_turns(
    messages: List[Message],
    protect_turns: int,
    trim_fn,
) -> List[Message]:
    """对旧轮（非最近 protect_turns 轮）的 tool result 应用 trim_fn。

    trim_fn(content) -> new_content
    返回新列表（浅拷贝 + 替换 tool result part）。
    """
    if not messages:
        return list(messages)

    turns = _split_into_turns(messages)
    if len(turns) <= protect_turns:
        return list(messages)

    # 旧轮 = 除了最后 protect_turns 轮
    old_turns = turns[:-protect_turns] if protect_turns > 0 else turns
    recent_turns = turns[-protect_turns:] if protect_turns > 0 else []

    out: List[Message] = []
    for turn in old_turns:
        for m in turn:
            # 只动 tool message 的 ToolResultPart content
            if m.role == Role.TOOL and m.tool_results():
                new_parts = []
                changed = False
                for p in m.parts:
                    if isinstance(p, ToolResultPart):
                        new_content = trim_fn(p.content)
                        if new_content != p.content:
                            changed = True
                            new_parts.append(ToolResultPart(
                                tool_call_id=p.tool_call_id,
                                content=new_content,
                                is_error=p.is_error,
                            ))
                        else:
                            new_parts.append(p)
                    else:
                        new_parts.append(p)
                if changed:
                    out.append(Message(role=m.role, parts=new_parts))
                else:
                    out.append(m)
            else:
                out.append(m)

    for turn in recent_turns:
        out.extend(turn)

    return out


def soft_trim_tool_results(
    messages: List[Message],
    protect_turns: int = _PROTECT_RECENT_TURNS,
) -> List[Message]:
    """软裁剪：旧 tool result 内容 → head+tail（保留结构，压缩体积）。

    保护最近 protect_turns 轮不动。
    返回新列表，不修改原列表。
    """
    return _trim_tool_results_in_old_turns(
        messages, protect_turns, _soft_trim_content,
    )


def hard_trim_tool_results(
    messages: List[Message],
    protect_turns: int = _PROTECT_RECENT_TURNS,
) -> List[Message]:
    """硬裁剪：旧 tool result 内容 → 清空标记（保留 tool_call 配对）。

    比 soft 更激进：完全清空内容，只留一个标记字符串。
    保护最近 protect_turns 轮不动。
    """
    def _hard(content: str) -> str:
        if len(content) <= 200:
            return content
        return _HARD_TRIM_MARKER

    return _trim_tool_results_in_old_turns(
        messages, protect_turns, _hard,
    )

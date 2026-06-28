"""
/compact 手动压缩。

逻辑：
1. 用 truncation.split_old_recent 把历史切成 (old, recent)
2. 把 old 喂给 LLM，让它生成一段紧凑的 summary
3. 用一条 assistant summary message 替换 old
4. 拼回 recent

如果 old 为空 → 不调 LLM，直接返回原 messages + 提示"无可压缩内容"。

prompt 设计：
- 让 LLM 保留：决定、约束、关键状态、未完成的事项
- 让 LLM 丢弃：闲聊、寒暄、已完成的细节
- 限制长度：~500 tokens
"""

from __future__ import annotations

import json
from typing import List, Tuple

from minicode.model.base import Model
from minicode.model.message import (
    Message,
    Role,
    TextPart,
)


_SUMMARY_SYSTEM = """You are a conversation compressor. \
Your job is to produce a concise summary of a conversation segment that will be used as context for continuing the work.

Preserve:
- Key decisions and constraints
- File paths and code identifiers that are still relevant
- Open issues / pending tasks
- Important facts the user mentioned

Discard:
- Greetings and small talk
- Completed one-off commands
- Verbose explanations that can be re-derived

Output format: a single plain-text paragraph (no bullet points, no JSON). \
Aim for around 400-600 tokens. Start with "[Summary of earlier conversation]" so it's clearly identifiable.
"""


def _format_old_for_summary(old: List[Message]) -> str:
    """把 old messages 序列化成 prompt 友好的纯文本。

    tool result 截断到 1500 字符（比原来的 200 多，保留更多关键信息）。
    """
    lines: List[str] = []
    for m in old:
        if m.role == Role.USER:
            lines.append(f"USER: {m.text()}")
        elif m.role == Role.ASSISTANT:
            txt = m.text()
            tcs = m.tool_calls()
            if txt:
                lines.append(f"ASSISTANT: {txt}")
            for tc in tcs:
                lines.append(f"ASSISTANT: [tool_call {tc.name}({json.dumps(tc.arguments, ensure_ascii=False)})]")
        elif m.role == Role.TOOL:
            for tr in m.tool_results():
                # 截断到 1500 字符：保留足够信息让 LLM 摘要，又不至于撑爆 prompt
                content = tr.content[:1500]
                if len(tr.content) > 1500:
                    content += f"... (+{len(tr.content) - 1500} chars)"
                lines.append(f"TOOL[{tr.tool_call_id}]: {content}")
        elif m.role == Role.SYSTEM:
            lines.append(f"SYSTEM: {m.text()}")
    return "\n".join(lines)


async def compact_messages(
    model: Model,
    messages: List[Message],
    keep_turns: int = 20,
) -> Tuple[List[Message], str]:
    """压缩旧消息。

    返回 (new_messages, summary_text)：
    - new_messages: 替换掉 old 的版本（old → 1 条 summary assistant + recent）
    - summary_text: 摘要内容（空字符串表示没压缩）

    异常传播：LLM 报错就直接 raise（让 CLI 显示错误）
    """
    from minicode.memory.truncation import split_old_recent

    old, recent = split_old_recent(messages, keep_turns=keep_turns)
    if not old:
        return list(messages), ""

    old_text = _format_old_for_summary(old)
    prompt_messages = [
        Message(role=Role.USER, parts=[TextPart(text=old_text)]),
    ]

    # 调 LLM 拿完整 response（用 complete 更简单）
    resp = await model.complete(prompt_messages, system=_SUMMARY_SYSTEM)

    summary_text = resp.message.text().strip()
    if not summary_text:
        return list(messages), ""

    # 用 assistant role 承载 summary + 明确前缀，让 LLM 知道这是上下文摘要不是真实回复。
    # 保持 assistant role 是为了 API 兼容（recent[0] 是 user，需要交替）。
    if not summary_text.startswith("[Summary"):
        summary_text = f"[Summary of earlier conversation]\n{summary_text}"
    summary_msg = Message(
        role=Role.ASSISTANT,
        parts=[TextPart(text=summary_text)],
    )

    new_messages: List[Message] = [summary_msg] + list(recent)
    return new_messages, summary_text

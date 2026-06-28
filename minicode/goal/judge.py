"""
judge：独立 LLM 调用判断 transcript 是否满足 /goal condition。

设计要点（对齐 mimo code session/goal.ts 的 evaluate()）：
1. judge 是独立模型调用，不复用工作 agent 的工具 —— 只读 transcript
2. system prompt 固定，要求返回严格 JSON
3. minicode 没有结构化输出（schema），用 prompt + 容错 JSON 解析
4. Fail-open：judge 失败时返回 Verdict(error=True)，不假装 ok
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional

from minicode.goal.types import Verdict
from minicode.model.base import Model
from minicode.model.message import Message, Role


_log = logging.getLogger("minicode.goal.judge")


JUDGE_SYSTEM = """你是 minicode 的停止条件评估器（judge）。你的工作**只**是阅读会话 transcript，
判断用户的停止条件是否已经被满足。你不执行任何工具，不修改任何状态。

你的响应**必须**是**且仅是**一个 JSON 对象，不带 markdown 包裹、不带解释、不带前后缀。
允许的两种 shape：

- {"ok": true, "reason": "引用 transcript 中的具体证据"}
- {"ok": false, "reason": "引用缺失的部分或阻碍条件达成的内容"}
- {"ok": false, "impossible": true, "reason": "为什么该 condition 客观上永远无法满足"}

判定规则：
1. 必须基于 transcript 中的**实际证据**判断；如果 transcript 不足以证明满足，给 {"ok": false, ...}
2. reason 字段必须**引用 transcript 原文**（加引号），不要泛泛而谈
3. "impossible" 是兜底：仅当 condition 自相矛盾、依赖不可用资源、或助手已显式尝试并穷尽合理路径后
   声明"无法完成"时使用。**不能**仅因为目标尚未达成或进度缓慢就标 impossible；遇疑请用 {"ok": false}
4. 工作 agent 自我评估"impossible"是**证据**，不是**证明**；请独立判断是否真的无法达成
5. 不要把"用户没确认"当成"没满足"——judge 只看工作是否完成
"""

_JUDGE_USER_TEMPLATE = """以下是 minicode 会话的 transcript。

===== TRANSCRIPT =====
{transcript}
===== END TRANSCRIPT =====

停止条件：
{condition}

该 condition 是否已被满足？只返回 JSON。"""


# 抓一段里**第一个**完整 JSON 对象（非贪婪，处理模型偶尔在后面加废话的情况）
_JSON_OBJ_RE = re.compile(r"\{[\s\S]*?\}")


def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    """从 LLM 输出里提取 JSON 对象。"""
    if not text:
        return None
    # 先尝试直接 parse
    try:
        v = json.loads(text)
        return v if isinstance(v, dict) else None
    except json.JSONDecodeError:
        pass
    # 抓第一个 {...}
    m = _JSON_OBJ_RE.search(text)
    if not m:
        return None
    try:
        v = json.loads(m.group(0))
        return v if isinstance(v, dict) else None
    except json.JSONDecodeError:
        return None


def _format_transcript(messages: List[Message], max_chars_per_msg: int = 2000) -> str:
    """把 Message 列表格式化为 transcript 文本。

    - system 不显示（噪音）
    - text 截断到 max_chars_per_msg（防超长）
    - tool_call / tool_result 用单行展示
    """
    lines: List[str] = []
    for m in messages:
        role = m.role.value
        if role == Role.SYSTEM.value:
            continue
        prefix = {
            Role.USER.value: "User",
            Role.ASSISTANT.value: "Assistant",
            Role.TOOL.value: "Tool",
        }.get(role, role.capitalize())

        text = m.text()
        if text:
            if len(text) > max_chars_per_msg:
                text = text[:max_chars_per_msg] + f" ...[+{len(text) - max_chars_per_msg} chars]"
            lines.append(f"{prefix}: {text}")

        for tc in m.tool_calls():
            try:
                args = json.dumps(tc.arguments, ensure_ascii=False)
            except (TypeError, ValueError):
                args = str(tc.arguments)
            if len(args) > max_chars_per_msg:
                args = args[:max_chars_per_msg] + "...(truncated)"
            lines.append(f"{prefix}: [tool_call:{tc.name}] {args}")

        for tr in m.tool_results():
            content = tr.content or ""
            if len(content) > max_chars_per_msg:
                content = content[:max_chars_per_msg] + f" ...[+{len(content) - max_chars_per_msg} chars]"
            err_mark = " (error)" if tr.is_error else ""
            lines.append(f"Tool[{tr.tool_call_id}{err_mark}]: {content}")

    return "\n".join(lines) if lines else "(empty transcript)"


async def judge(
    model: Model,
    condition: str,
    messages: List[Message],
    attempt: int = 1,
    timeout_s: float = 30.0,
) -> Verdict:
    """调 model 判断 transcript 是否满足 condition。

    返回：
    - ok=True     condition 已满足
    - ok=False + impossible=True   不可达
    - ok=False + error=True        judge 调用本身失败（网络/JSON 解析），不假装 ok
    """
    transcript = _format_transcript(messages)
    user_prompt = _JUDGE_USER_TEMPLATE.format(transcript=transcript, condition=condition)

    try:
        resp = await asyncio.wait_for(
            model.complete([Message.user(user_prompt)], system=JUDGE_SYSTEM),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        _log.warning("judge timed out after %ss", timeout_s)
        return Verdict(ok=False, error=True, reason=f"judge timeout after {timeout_s}s", attempt=attempt)
    except Exception as e:
        _log.warning("judge call failed: %s", e)
        return Verdict(ok=False, error=True, reason=f"judge call failed: {e}", attempt=attempt)

    text = resp.message.text()
    parsed = _extract_json_object(text)
    if parsed is None:
        return Verdict(
            ok=False, error=True,
            reason=f"judge returned non-JSON: {(text or '')[:120]!r}",
            attempt=attempt,
        )

    v = Verdict.from_dict(parsed)
    v.attempt = attempt
    return v


def render_verdict(v: Verdict) -> str:
    """给 CLI 展示用。"""
    if v.error:
        return f"[judge error] {v.reason}"
    if v.ok:
        return f"[goal satisfied] {v.reason}"
    if v.impossible:
        return f"[goal impossible] {v.reason}"
    return f"[goal not yet] {v.reason}"

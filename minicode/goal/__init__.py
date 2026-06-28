"""
/goal 子包入口。

公开 API：
- Goal / Verdict          数据类
- GoalService             per-session 状态机
- judge()                 独立 LLM judge 调用
- JUDGE_SYSTEM            judge system prompt（给想自己调 LLM 的人用）
- render_verdict()        verdict → 人话字符串
- _format_transcript()    内部：Message 列表 → transcript 文本
- _extract_json_object()  内部：从 LLM 输出抓 JSON 对象
"""

from minicode.goal.types import Goal, Verdict
from minicode.goal.service import GoalService
from minicode.goal.judge import (
    judge,
    JUDGE_SYSTEM,
    render_verdict,
    _format_transcript,
    _extract_json_object,
)

__all__ = [
    "Goal",
    "Verdict",
    "GoalService",
    "judge",
    "JUDGE_SYSTEM",
    "render_verdict",
    "_format_transcript",
    "_extract_json_object",
]

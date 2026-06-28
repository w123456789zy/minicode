"""
GoalService：per-session 的 goal 状态机。

职责：
- 维护 session_id → Goal 的内存映射（重启即丢失，跟 mimo code 一致）
- 提供 set / get / clear / bump_react / record_verdict
- 跟踪每个 session 累计 judge 调用次数（attempt 从 1 开始）

线程安全：v0 单线程 REPL，不加锁；后续接 async 调度时再考虑。
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional

from minicode.goal.types import Goal, Verdict


class GoalService:
    def __init__(self) -> None:
        self._goals: Dict[str, Goal] = {}
        # 每个 session 的 judge 调用次数（独立的 attempt 计数器）
        self._judge_attempts: Dict[str, int] = {}

    # ── CRUD ─────────────────────────────────────────

    def set(self, session_id: str, condition: str) -> Goal:
        """设置 / 替换 session 的 goal。set 会把 react 重置为 0。"""
        if not session_id:
            raise ValueError("session_id must be non-empty")
        if not condition or not condition.strip():
            raise ValueError("goal condition must be non-empty")
        goal = Goal(condition=condition.strip(), react=0, set_at=time.monotonic())
        self._goals[session_id] = goal
        # 重新设置时也重置 judge 计数
        self._judge_attempts[session_id] = 0
        return goal

    def get(self, session_id: str) -> Optional[Goal]:
        return self._goals.get(session_id)

    def clear(self, session_id: str) -> bool:
        """清除 session 的 goal。返回是否有 goal 被清除。"""
        had = self._goals.pop(session_id, None) is not None
        self._judge_attempts.pop(session_id, None)
        return had

    def has_goal(self, session_id: str) -> bool:
        return session_id in self._goals

    # ── 计数器 ─────────────────────────────────────────

    def bump_react(self, session_id: str) -> int:
        """goal 存在时 react +1，返回新值；不存在返回 0。"""
        goal = self._goals.get(session_id)
        if goal is None:
            return 0
        goal.react += 1
        return goal.react

    def next_attempt(self, session_id: str) -> int:
        """取下一个 judge attempt 编号（从 1 开始）。每次 judge 前调用。"""
        n = self._judge_attempts.get(session_id, 0) + 1
        self._judge_attempts[session_id] = n
        return n

    def record_verdict(self, session_id: str, verdict: Verdict) -> None:
        """把 judge 的 verdict 记到当前 goal 上。"""
        goal = self._goals.get(session_id)
        if goal is not None:
            goal.last_verdict = verdict

    # ── 批量 / 调试 ─────────────────────────────────────────

    def all(self) -> List[Goal]:
        return list(self._goals.values())

    def sessions(self) -> List[str]:
        return list(self._goals.keys())

    def clear_all(self) -> int:
        """清空所有 session 的 goal。返回被清的 session 数。"""
        n = len(self._goals)
        self._goals.clear()
        self._judge_attempts.clear()
        return n

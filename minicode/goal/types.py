"""
/goal 数据类型。

参考 mimo code session/goal.ts 的设计，但简化为 Python dataclass。

Goal：per-session 的停止条件
- condition  用户的条件文本（如 "tests pass"）
- react      judge 触发工作 agent 重新进入的次数（v0 占位）
- last_verdict  最近一次 judge 的判定
- set_at     设置时间（monotonic）

Verdict：judge 的判定
- ok          condition 已满足 → /goal 完结
- impossible  condition 客观上不可达成 → /goal 强制完结
- reason      引用 transcript 的证据 / 解释
- attempt     第几次 judge 调用
- error       judge 调用本身失败（非 ok 也非 impossible，是 fail-open）
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class Verdict:
    """judge 对 condition 的判定。"""
    ok: bool = False
    impossible: bool = False
    reason: str = ""
    attempt: int = 0
    error: bool = False  # judge 调用本身失败

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "ok": self.ok,
            "reason": self.reason,
            "attempt": self.attempt,
        }
        if self.impossible:
            d["impossible"] = True
        if self.error:
            d["error"] = True
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Verdict":
        if not isinstance(d, dict):
            return cls(error=True, reason=f"verdict is not a dict: {d!r}")
        return cls(
            ok=bool(d.get("ok", False)),
            impossible=bool(d.get("impossible", False)),
            reason=str(d.get("reason", "") or ""),
            attempt=int(d.get("attempt", 0) or 0),
            error=bool(d.get("error", False)),
        )

    @property
    def satisfied(self) -> bool:
        """ok 或 impossible 都算 condition 已完结（无需再 judge）。"""
        return self.ok or self.impossible


@dataclass
class Goal:
    """一个 session 的当前 goal。"""
    condition: str
    react: int = 0
    last_verdict: Optional[Verdict] = None
    set_at: float = field(default_factory=time.monotonic)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "condition": self.condition,
            "react": self.react,
            "set_at": self.set_at,
            "last_verdict": self.last_verdict.to_dict() if self.last_verdict else None,
        }

    def short(self) -> str:
        """一行展示用。"""
        return self.condition

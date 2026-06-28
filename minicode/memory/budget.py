"""
Token 预算 / 估算。

设计要点：
- 不引入 tiktoken（避免依赖 + 模型不同 token 数差异大）
- 用 char/4 粗估：英文 ≈ 1 token / 4 字符；中文 ≈ 1 token / 1.5 字符
  → 实际我们用统一公式 chars / 3（中英混合更接近，足够 v0 排版用）
- ContextBudget：跟踪当前 system + history 的 token 估算
- pressure_level（0-3）：驱动分级裁剪策略（参考 mimo code 的 overflow.pressureLevel）
  · 0 (< 50%)：无压力
  · 1 (< 70%)：低压力，可软裁剪旧 tool result
  · 2 (< 85%)：中压力，应硬裁剪旧 tool result
  · 3 (≥ 85%)：高压力，应触发 compact
- 配合 truncation / compact / status 用
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from minicode.model.message import Message


# 经验值：英文 4 chars ≈ 1 token，中文 1.5 chars ≈ 1 token
# 中英混合取 chars / 3（保守一点，避免低估导致超窗口）
_CHARS_PER_TOKEN = 3

# 默认上下文窗口（用户没配 context_window 时用）
_DEFAULT_LIMIT = 8000


def estimate_tokens(text: str) -> int:
    """粗估一段文本的 token 数。空串 → 0。"""
    if not text:
        return 0
    # 至少 1 token，避免空内容显示 0
    return max(1, len(text) // _CHARS_PER_TOKEN)


def estimate_message_tokens(msg: Message) -> int:
    """估算一条 message 的 token 数。包含 role + 所有 parts 文本。"""
    total = 4  # role + 一些结构化开销
    total += estimate_tokens(msg.text())
    for tc in msg.tool_calls():
        total += estimate_tokens(tc.name)
        total += estimate_tokens(str(tc.arguments))
    for tr in msg.tool_results():
        total += estimate_tokens(tr.content)
    return total


@dataclass
class ContextBudget:
    """当前 context 的 token 使用情况。

    limit 来自模型配置（context_window）；未配置时用 _DEFAULT_LIMIT。
    阈值都按 limit 的比例自动算，避免硬编码不一致。
    """
    system_tokens: int = 0
    history_tokens: int = 0
    limit: int = _DEFAULT_LIMIT
    history_window: int = 20          # 保留最近 K 轮

    def __post_init__(self) -> None:
        # limit 太小（< 100）说明配置异常或为 0，兜底用默认值
        if self.limit < 100:
            self.limit = _DEFAULT_LIMIT

    # ── 派生属性 ──

    @property
    def total(self) -> int:
        return self.system_tokens + self.history_tokens

    @property
    def usage_ratio(self) -> float:
        if self.limit <= 0:
            return 0.0
        return min(1.0, self.total / self.limit)

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.total)

    # ── 压力等级（驱动分级裁剪）──

    @property
    def pressure_level(self) -> int:
        """0-3：无 / 低 / 中 / 高压力。"""
        r = self.usage_ratio
        if r < 0.50:
            return 0
        if r < 0.70:
            return 1
        if r < 0.85:
            return 2
        return 3

    # ── 阈值（按比例算，不再硬编码）──

    @property
    def soft_trim_threshold(self) -> int:
        """超过此值触发软裁剪（level 1）：旧 tool result → head+tail。"""
        return int(self.limit * 0.50)

    @property
    def hard_trim_threshold(self) -> int:
        """超过此值触发硬裁剪（level 2）：旧 tool result 清空内容。"""
        return int(self.limit * 0.70)

    @property
    def compact_threshold(self) -> int:
        """超过此值触发 compact（level 3）：调 LLM 压缩历史。"""
        return int(self.limit * 0.85)

    @property
    def should_truncate(self) -> bool:
        """旧 API 兼容：pressure >= 2 时需要裁剪。"""
        return self.total >= self.hard_trim_threshold

    @property
    def should_compact(self) -> bool:
        """是否应该触发 compact。"""
        return self.total >= self.compact_threshold

    # ── 测量 ──

    def measure(self, system: str, history: List[Message]) -> "ContextBudget":
        """重算 system + history 的 token，返回新 budget（不动 limit 字段）。

        这是 O(n) 的全量重算。长会话下频繁调用有开销，但实现简单、可靠。
        如果性能成瓶颈，可改用增量 add_message（见下）。
        """
        sys_t = estimate_tokens(system)
        hist_t = sum(estimate_message_tokens(m) for m in history)
        return ContextBudget(
            system_tokens=sys_t,
            history_tokens=hist_t,
            limit=self.limit,
            history_window=self.history_window,
        )

    def with_added_tokens(self, delta: int) -> "ContextBudget":
        """增量更新：已知新增了 delta 个 token，返回新 budget。

        用于 run_agent 循环中每轮 append 后快速更新，避免 O(n) 重算。
        delta 可以是负数（compact / truncate 后）。
        """
        return ContextBudget(
            system_tokens=self.system_tokens,
            history_tokens=max(0, self.history_tokens + delta),
            limit=self.limit,
            history_window=self.history_window,
        )

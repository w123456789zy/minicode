"""memory.budget 单测。"""
from minicode.memory.budget import (
    ContextBudget,
    estimate_message_tokens,
    estimate_tokens,
)
from minicode.model.message import Message


def test_estimate_tokens_empty():
    assert estimate_tokens("") == 0


def test_estimate_tokens_short():
    # 1 char → 1 token（至少 1）
    assert estimate_tokens("a") == 1
    # 4 chars → 1 token（4/3 = 1）
    assert estimate_tokens("abcd") == 1


def test_estimate_tokens_long():
    text = "a" * 120
    # 120 / 3 = 40
    assert estimate_tokens(text) == 40


def test_estimate_message_tokens_user():
    m = Message.user("hello world")
    # 4 (role overhead) + 4 ("hello world" 11/3=3) = 7
    assert estimate_message_tokens(m) >= 7


def test_estimate_message_tokens_empty():
    m = Message.user("")
    # 4 (overhead) + 0 (empty text)
    assert estimate_message_tokens(m) == 4


def test_budget_initial():
    b = ContextBudget()
    assert b.total == 0
    assert b.usage_ratio == 0.0
    assert not b.should_truncate
    assert b.remaining == b.limit


def test_budget_measure():
    b = ContextBudget(limit=100)
    b2 = b.measure("hello", [])
    assert b2.system_tokens > 0
    assert b2.history_tokens == 0
    assert b2.limit == 100  # 保持不变
    # hard_trim_threshold 按 limit 的 70% 自动算
    assert b2.hard_trim_threshold == 70


def test_budget_should_truncate():
    # 100 tokens，hard_trim_threshold = 70（70%）
    b = ContextBudget(system_tokens=50, history_tokens=20, limit=100)
    assert b.total == 70
    assert b.should_truncate  # 70 >= 70
    # 压力等级：70/100 = 0.7 → level 2
    assert b.pressure_level == 2


def test_budget_pressure_levels():
    # level 0: < 50%
    b0 = ContextBudget(system_tokens=40, history_tokens=0, limit=100)
    assert b0.pressure_level == 0
    # level 1: 50-70%
    b1 = ContextBudget(system_tokens=60, history_tokens=0, limit=100)
    assert b1.pressure_level == 1
    # level 2: 70-85%
    b2 = ContextBudget(system_tokens=75, history_tokens=0, limit=100)
    assert b2.pressure_level == 2
    # level 3: >= 85%
    b3 = ContextBudget(system_tokens=90, history_tokens=0, limit=100)
    assert b3.pressure_level == 3
    assert b3.should_compact


def test_budget_usage_ratio_capped():
    b = ContextBudget(system_tokens=200, history_tokens=0, limit=100)
    assert b.usage_ratio == 1.0  # 不会超过 1.0


def test_budget_remaining():
    b = ContextBudget(system_tokens=30, history_tokens=20, limit=100)
    assert b.remaining == 50

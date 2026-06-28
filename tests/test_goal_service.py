"""
测试 minicode.goal.service.GoalService：per-session 状态机。
"""

from __future__ import annotations

import pytest

from minicode.goal.service import GoalService
from minicode.goal.types import Verdict


class TestGoalService:
    def test_empty_state(self):
        svc = GoalService()
        assert svc.get("s1") is None
        assert svc.has_goal("s1") is False
        assert svc.all() == []
        assert svc.sessions() == []

    def test_set_get(self):
        svc = GoalService()
        g = svc.set("s1", "tests pass")
        assert g.condition == "tests pass"
        assert g.react == 0
        assert svc.get("s1") is g
        assert svc.has_goal("s1") is True

    def test_set_strips_condition(self):
        svc = GoalService()
        g = svc.set("s1", "  spaces around  ")
        assert g.condition == "spaces around"

    def test_set_replaces(self):
        svc = GoalService()
        svc.set("s1", "old")
        svc.bump_react("s1")
        g2 = svc.set("s1", "new")
        assert g2.condition == "new"
        assert g2.react == 0  # 重置

    def test_set_validates_empty(self):
        svc = GoalService()
        with pytest.raises(ValueError):
            svc.set("", "x")
        with pytest.raises(ValueError):
            svc.set("s1", "")
        with pytest.raises(ValueError):
            svc.set("s1", "   ")

    def test_set_validates_session_id(self):
        svc = GoalService()
        with pytest.raises(ValueError):
            svc.set("", "x")

    def test_clear(self):
        svc = GoalService()
        svc.set("s1", "x")
        assert svc.clear("s1") is True
        assert svc.get("s1") is None
        assert svc.has_goal("s1") is False
        # 再 clear → False
        assert svc.clear("s1") is False

    def test_clear_unknown(self):
        svc = GoalService()
        assert svc.clear("nonexistent") is False

    def test_bump_react_increments(self):
        svc = GoalService()
        svc.set("s1", "x")
        assert svc.bump_react("s1") == 1
        assert svc.bump_react("s1") == 2
        assert svc.get("s1").react == 2

    def test_bump_react_no_goal(self):
        svc = GoalService()
        assert svc.bump_react("nope") == 0

    def test_bump_react_does_not_create(self):
        svc = GoalService()
        svc.bump_react("nope")
        assert svc.has_goal("nope") is False

    def test_next_attempt_starts_at_1(self):
        svc = GoalService()
        svc.set("s1", "x")
        assert svc.next_attempt("s1") == 1
        assert svc.next_attempt("s1") == 2
        assert svc.next_attempt("s1") == 3

    def test_next_attempt_resets_on_set(self):
        svc = GoalService()
        svc.set("s1", "a")
        svc.next_attempt("s1")
        svc.next_attempt("s1")
        svc.set("s1", "b")
        assert svc.next_attempt("s1") == 1

    def test_record_verdict(self):
        svc = GoalService()
        svc.set("s1", "x")
        v = Verdict(ok=True, reason="done", attempt=1)
        svc.record_verdict("s1", v)
        assert svc.get("s1").last_verdict is v
        assert svc.get("s1").last_verdict.ok is True

    def test_record_verdict_no_goal(self):
        svc = GoalService()
        v = Verdict(ok=True)
        # 不应抛错
        svc.record_verdict("nope", v)
        assert svc.get("nope") is None

    def test_multi_session_isolation(self):
        svc = GoalService()
        svc.set("s1", "a")
        svc.set("s2", "b")
        svc.bump_react("s1")
        svc.bump_react("s1")
        assert svc.get("s1").react == 2
        assert svc.get("s2").react == 0
        # 清除一个不影响另一个
        svc.clear("s1")
        assert svc.has_goal("s1") is False
        assert svc.has_goal("s2") is True

    def test_all_and_sessions(self):
        svc = GoalService()
        svc.set("s1", "a")
        svc.set("s2", "b")
        svc.set("s3", "c")
        assert sorted(svc.sessions()) == ["s1", "s2", "s3"]
        assert len(svc.all()) == 3
        conds = sorted(g.condition for g in svc.all())
        assert conds == ["a", "b", "c"]

    def test_clear_all(self):
        svc = GoalService()
        svc.set("s1", "a")
        svc.set("s2", "b")
        n = svc.clear_all()
        assert n == 2
        assert svc.all() == []

    def test_clear_all_empty(self):
        svc = GoalService()
        assert svc.clear_all() == 0

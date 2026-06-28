"""
测试 minicode.goal.types：Goal / Verdict 数据类。
"""

from __future__ import annotations

from minicode.goal.types import Goal, Verdict


class TestVerdict:
    def test_default_is_not_satisfied(self):
        v = Verdict()
        assert v.ok is False
        assert v.impossible is False
        assert v.satisfied is False
        assert v.error is False

    def test_ok_satisfied(self):
        assert Verdict(ok=True).satisfied is True

    def test_impossible_satisfied(self):
        assert Verdict(impossible=True).satisfied is True

    def test_error_not_satisfied(self):
        """error 表示 judge 失败，不代表 condition 已达成。"""
        v = Verdict(error=True)
        assert v.satisfied is False

    def test_to_dict_minimal(self):
        v = Verdict(ok=False, reason="no", attempt=0)
        d = v.to_dict()
        assert d["ok"] is False
        assert d["reason"] == "no"
        assert d["attempt"] == 0
        # impossible/error 缺省不出现
        assert "impossible" not in d
        assert "error" not in d

    def test_to_dict_with_impossible(self):
        d = Verdict(ok=False, impossible=True, reason="x").to_dict()
        assert d["impossible"] is True

    def test_to_dict_with_error(self):
        d = Verdict(error=True, reason="boom").to_dict()
        assert d["error"] is True
        assert d["reason"] == "boom"

    def test_roundtrip(self):
        v = Verdict(ok=True, reason="all tests pass", attempt=3)
        v2 = Verdict.from_dict(v.to_dict())
        assert v2.ok is True
        assert v2.reason == "all tests pass"
        assert v2.attempt == 3

    def test_from_dict_handles_non_dict(self):
        v = Verdict.from_dict("not a dict")
        assert v.error is True
        assert "not a dict" in v.reason

    def test_from_dict_handles_missing(self):
        v = Verdict.from_dict({})
        assert v.ok is False
        assert v.impossible is False
        assert v.reason == ""
        assert v.attempt == 0

    def test_from_dict_coerces_types(self):
        v = Verdict.from_dict({"ok": 1, "attempt": "7", "reason": None})
        assert v.ok is True
        assert v.attempt == 7
        assert v.reason == ""  # None → ""


class TestGoal:
    def test_default(self):
        g = Goal(condition="tests pass")
        assert g.condition == "tests pass"
        assert g.react == 0
        assert g.last_verdict is None

    def test_to_dict_includes_verdict_when_set(self):
        v = Verdict(ok=True, reason="ok", attempt=1)
        g = Goal(condition="x", last_verdict=v)
        d = g.to_dict()
        assert d["condition"] == "x"
        assert d["react"] == 0
        assert d["last_verdict"]["ok"] is True
        assert d["last_verdict"]["reason"] == "ok"

    def test_to_dict_no_verdict(self):
        g = Goal(condition="x")
        d = g.to_dict()
        assert d["last_verdict"] is None

    def test_short(self):
        assert Goal(condition="a long condition description").short() == "a long condition description"

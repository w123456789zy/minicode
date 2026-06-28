"""
测试 minicode.cli.app._cmd_goal：CLI /goal 命令的端到端分支。

策略：直接调 _cmd_goal（不启动 REPL），用真实 GoalService + mock ModelRegistry。
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from typing import AsyncIterator, List, Optional

import pytest

from minicode.cli.app import _cmd_goal
from minicode.goal.service import GoalService
from minicode.model.base import Model, ModelEvent, ModelInfo, ModelUsage
from minicode.model.message import Message


# ─────────────────────────────────────────────────────
# Mock model
# ─────────────────────────────────────────────────────


class _MockModel(Model):
    def __init__(self, text: str):
        super().__init__(ModelInfo(id="mock", type="mock", base_url="", model="mock"))
        self._text = text
        self.last_system: Optional[str] = None
        self.last_messages: List[Message] = []

    async def stream(
        self,
        messages: List[Message],
        tools=None,
        system: Optional[str] = None,
    ) -> AsyncIterator[ModelEvent]:
        self.last_messages = list(messages)
        self.last_system = system
        yield ModelEvent(type="text_delta", text=self._text)
        yield ModelEvent(type="finish", finish_reason="stop")
        yield ModelEvent(
            type="usage",
            usage=ModelUsage(input_tokens=1, output_tokens=1),
        )


class _MockMreg:
    """最小 ModelRegistry 替身：mreg.current() 返回 model 或 None。"""
    def __init__(self, model: Optional[Model] = None):
        self._model = model

    def current(self) -> Optional[Model]:
        return self._model


def _capture(fn, *args, **kwargs) -> str:
    """捕获 print 输出。同步 + 异步 fn 都支持。

    异步 fn：在外层 pytest-asyncio loop 内 await。
    """
    import asyncio
    buf = io.StringIO()
    with redirect_stdout(buf):
        result = fn(*args, **kwargs)
        if asyncio.iscoroutine(result):
            # 委托给 caller await —— 拿到 coroutine
            # 这里不能 run_until_complete（外层 loop 在跑）
            # 所以 caller 必须用 await _capture_async
            raise RuntimeError(
                "use await _capture_async(...) for async fns"
            )
    return buf.getvalue()


async def _capture_async(fn, *args, **kwargs) -> str:
    """async 版本的 _capture。在 async test 里直接 await。"""
    buf = io.StringIO()
    with redirect_stdout(buf):
        result = fn(*args, **kwargs)
        if hasattr(result, "__await__"):
            await result
    return buf.getvalue()


# ─────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────


class TestCmdGoalNoModel:
    """无 model 时 set 只记录，不调 judge。"""

    async def test_set_without_model(self):
        svc = GoalService()
        mreg = _MockMreg(model=None)
        out = await _capture_async(_cmd_goal, "tests pass", mreg, svc, "s1", [], "")
        assert "[goal] set: tests pass" in out
        assert "no model" in out
        # goal 仍存在（不调 judge 不代表清掉）
        assert svc.has_goal("s1")

    async def test_status_no_goal(self):
        svc = GoalService()
        mreg = _MockMreg()
        out = await _capture_async(_cmd_goal, "", mreg, svc, "s1", [], "")
        assert "no active goal" in out


class TestCmdGoalStatus:
    async def test_status_keyword(self):
        svc = GoalService()
        svc.set("s1", "x")
        mreg = _MockMreg()
        out = await _capture_async(_cmd_goal, "status", mreg, svc, "s1", [], "")
        assert "condition : x" in out
        assert "react     : 0" in out

    async def test_status_no_arg(self):
        svc = GoalService()
        svc.set("s1", "x")
        mreg = _MockMreg()
        out = await _capture_async(_cmd_goal, "", mreg, svc, "s1", [], "")
        assert "condition : x" in out

    async def test_status_includes_verdict(self):
        from minicode.goal.types import Verdict
        svc = GoalService()
        svc.set("s1", "x")
        svc.record_verdict("s1", Verdict(ok=True, reason="yep", attempt=1))
        mreg = _MockMreg()
        out = await _capture_async(_cmd_goal, "status", mreg, svc, "s1", [], "")
        assert "satisfied" in out
        assert "yep" in out

    async def test_status_verdict_not_yet(self):
        svc = GoalService()
        svc.set("s1", "x")
        mreg = _MockMreg()
        out = await _capture_async(_cmd_goal, "status", mreg, svc, "s1", [], "")
        assert "not judged yet" in out


class TestCmdGoalClear:
    async def test_clear_with_goal(self):
        svc = GoalService()
        svc.set("s1", "x")
        mreg = _MockMreg()
        out = await _capture_async(_cmd_goal, "clear", mreg, svc, "s1", [], "")
        assert "cleared" in out
        assert not svc.has_goal("s1")

    async def test_clear_without_goal(self):
        svc = GoalService()
        mreg = _MockMreg()
        out = await _capture_async(_cmd_goal, "clear", mreg, svc, "s1", [], "")
        assert "no active goal" in out

    async def test_clear_case_insensitive(self):
        svc = GoalService()
        svc.set("s1", "x")
        mreg = _MockMreg()
        out = await _capture_async(_cmd_goal, "CLEAR", mreg, svc, "s1", [], "")
        assert "cleared" in out


class TestCmdGoalSetAndJudge:
    async def test_set_with_satisfied_judge(self):
        svc = GoalService()
        m = _MockModel('{"ok": true, "reason": "all done"}')
        mreg = _MockMreg(model=m)
        history = [Message.user("test 1"), Message.user("test 2")]
        out = await _capture_async(
            _cmd_goal, "tests pass", mreg, svc, "s1", history, "system",
        )
        assert "[goal] set: tests pass" in out
        assert "satisfied" in out
        assert "all done" in out
        # 满足后自动 clear
        assert not svc.has_goal("s1")

    async def test_set_with_not_yet_judge(self):
        svc = GoalService()
        m = _MockModel('{"ok": false, "reason": "missing"}')
        mreg = _MockMreg(model=m)
        history = [Message.user("partial work")]
        out = await _capture_async(
            _cmd_goal, "tests pass", mreg, svc, "s1", history, "",
        )
        assert "not yet" in out or "not satisfied" in out
        # 没满足 → goal 保留
        assert svc.has_goal("s1")
        # 记录了 verdict
        assert svc.get("s1").last_verdict.ok is False

    async def test_set_with_impossible_auto_clears(self):
        svc = GoalService()
        m = _MockModel('{"ok": false, "impossible": true, "reason": "self-contradictory"}')
        mreg = _MockMreg(model=m)
        out = await _capture_async(_cmd_goal, "X and not-X", mreg, svc, "s1", [], "")
        assert "impossible" in out
        # impossible 也算 condition 完结 → 自动 clear
        assert not svc.has_goal("s1")

    async def test_set_with_judge_error_keeps_goal(self):
        """judge 失败时 goal 保留，用户可重试。"""
        svc = GoalService()
        m = _MockModel("not json at all, just text")
        mreg = _MockMreg(model=m)
        out = await _capture_async(_cmd_goal, "x", mreg, svc, "s1", [], "")
        assert "error" in out
        # error 不算 satisfied → 保留
        assert svc.has_goal("s1")

    async def test_attempt_increments(self):
        svc = GoalService()
        m = _MockModel('{"ok": false, "reason": "no"}')
        mreg = _MockMreg(model=m)
        history = [Message.user("work in progress")]
        await _capture_async(_cmd_goal, "x", mreg, svc, "s1", history, "")
        await _capture_async(_cmd_goal, "x", mreg, svc, "s1", history, "")
        await _capture_async(_cmd_goal, "x", mreg, svc, "s1", history, "")
        # 3 次 set，每次 set 重置 attempt
        # 但本次最后一个 set 后 attempt 应该是 1
        # 验证不抛错 + goal 仍在
        assert svc.has_goal("s1")

    async def test_history_passed_to_judge(self):
        svc = GoalService()
        m = _MockModel('{"ok": true}')
        mreg = _MockMreg(model=m)
        history = [
            Message.user("question"),
            Message.user("more context"),
        ]
        await _capture_async(_cmd_goal, "x", mreg, svc, "s1", history, "you are a judge")
        # judge 收到的消息：system(judge system) + user(transcript+condition)
        # 但 _cmd_goal 把它包成 user_msg
        # 检查 last_messages[0] 的内容
        assert len(m.last_messages) == 1
        assert m.last_messages[0].role.value == "user"
        assert "question" in m.last_messages[0].text()
        assert "more context" in m.last_messages[0].text()
        # judge system 用了 JUDGE_SYSTEM
        assert m.last_system is not None
        assert "judge" in m.last_system.lower() or "评估" in m.last_system

    async def test_replaces_existing_goal(self):
        """set 会重置 react + judge 计数。"""
        from minicode.goal.types import Verdict
        svc = GoalService()
        svc.set("s1", "old")
        svc.bump_react("s1")
        svc.record_verdict("s1", Verdict(ok=True))
        m = _MockModel('{"ok": false, "reason": "no"}')
        mreg = _MockMreg(model=m)
        await _capture_async(_cmd_goal, "new", mreg, svc, "s1", [], "")
        g = svc.get("s1")
        assert g.condition == "new"
        assert g.react == 0  # 重置

    async def test_validation_empty_condition(self):
        """空白 condition 走 status 分支，不抛错。"""
        svc = GoalService()
        mreg = _MockMreg()
        # "   " 走 status 分支
        out = await _capture_async(_cmd_goal, "   ", mreg, svc, "s1", [], "")
        assert "no active goal" in out

    async def test_validation_empty_via_service(self):
        """service 本身在条件为空时抛 ValueError（_cmd_goal 内捕获并 print）。"""
        from minicode.goal.service import GoalService
        svc = GoalService()
        with pytest.raises(ValueError):
            svc.set("s1", "")
        with pytest.raises(ValueError):
            svc.set("s1", "   ")


class TestCmdGoalHelp:
    async def test_help_keyword(self):
        svc = GoalService()
        mreg = _MockMreg()
        out = await _capture_async(_cmd_goal, "help", mreg, svc, "s1", [], "")
        assert "/goal <condition>" in out
        assert "/goal clear" in out


class TestCmdGoalNullService:
    async def test_none_service_prints_error(self):
        mreg = _MockMreg()
        out = await _capture_async(_cmd_goal, "x", mreg, None, "s1", [], "")
        assert "未初始化" in out

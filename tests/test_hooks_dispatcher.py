"""hooks.dispatcher 单测。"""
import asyncio
from pathlib import Path

import pytest

from minicode.hooks.dispatcher import HookDispatcher
from minicode.hooks.types import (
    Action,
    EventName,
    HookContext,
    HookError,
    HookEvent,
    HookResponse,
)


def _ctx() -> HookContext:
    return HookContext(
        cwd=Path("."),
        project_root=Path("."),
        minicode_version="0.0.0",
    )


def _ev(name=EventName.TOOL_CALL_BEFORE, **data) -> HookEvent:
    return HookEvent.make(name, "test-session", **data)


# ─────────────────────────────────────────────
# 加载
# ─────────────────────────────────────────────


def test_load_empty_dirs(tmp_path: Path):
    d = HookDispatcher()
    assert d.load([tmp_path]) == 0
    assert d.hooks() == []


def test_load_python_files(tmp_path: Path):
    d = tmp_path / "hooks"
    d.mkdir()
    (d / "a.py").write_text("async def hook(e,c): return {}\n", encoding="utf-8")
    (d / "b.py").write_text("async def hook(e,c): return {}\n", encoding="utf-8")
    disp = HookDispatcher()
    n = disp.load([d])
    assert n == 2


def test_infos_returns_metadata(tmp_path: Path):
    d = tmp_path / "hooks"
    d.mkdir()
    (d / "myhook.py").write_text(
        '"""First line docstring."""\n'
        "async def hook(e,c): return {}\n",
        encoding="utf-8",
    )
    disp = HookDispatcher()
    disp.load([d])
    infos = disp.infos()
    assert len(infos) == 1
    assert infos[0].name == "myhook"
    assert infos[0].kind == "python"
    assert "First line" in infos[0].description


# ─────────────────────────────────────────────
# 触发
# ─────────────────────────────────────────────


def test_dispatch_no_hooks_returns_allow():
    async def run():
        disp = HookDispatcher()
        r = await disp.dispatch(_ev(), _ctx())
        assert r.action == Action.ALLOW
        assert r.denied is False
        assert r.allowed is True
    asyncio.run(run())


def test_dispatch_one_allow():
    async def run():
        d = HookDispatcher()
        d._hooks = [_HookStub(allow())]
        r = await d.dispatch(_ev(), _ctx())
        assert r.action == Action.ALLOW
    asyncio.run(run())


def test_dispatch_one_deny():
    async def run():
        d = HookDispatcher()
        d._hooks = [_HookStub(deny("x"))]
        r = await d.dispatch(_ev(), _ctx())
        assert r.action == Action.DENY
        assert r.reason == "x"
        assert r.denied is True
    asyncio.run(run())


def test_dispatch_deny_wins_over_modify():
    async def run():
        d = HookDispatcher()
        d._hooks = [
            _HookStub(modify({"prompt": "new"})),
            _HookStub(deny("no!")),
        ]
        r = await d.dispatch(_ev(), _ctx())
        assert r.action == Action.DENY
        assert r.reason == "no!"
    asyncio.run(run())


def test_dispatch_modify_merges():
    async def run():
        d = HookDispatcher()
        d._hooks = [
            _HookStub(modify({"a": 1, "b": 2})),
            _HookStub(modify({"b": 99, "c": 3})),
        ]
        r = await d.dispatch(_ev(), _ctx())
        assert r.action == Action.MODIFY
        assert r.data == {"a": 1, "b": 99, "c": 3}  # 后到覆盖
    asyncio.run(run())


def test_dispatch_fail_open_on_hook_error():
    async def run():
        d = HookDispatcher(fail_open=True)
        # 用一个会 raise 的 hook
        class _BadHook:
            name = "bad"
            kind = "python"
            path = Path("/tmp/bad.py")
            description = ""
            async def run(self, e, c, timeout=10):
                raise HookError("bad", "boom")
        d._hooks = [_BadHook(), _HookStub(allow())]
        r = await d.dispatch(_ev(), _ctx())
        assert r.action == Action.ALLOW
    asyncio.run(run())


def test_dispatch_runs_in_parallel():
    """两个 hook 都 sleep 0.1s → 总耗时应该 < 0.2s。"""
    async def run():
        import time
        d = HookDispatcher()
        d._hooks = [_SleepHook(0.1), _SleepHook(0.1), _SleepHook(0.1)]
        t0 = time.monotonic()
        r = await d.dispatch(_ev(), _ctx())
        elapsed = time.monotonic() - t0
        assert r.action == Action.ALLOW
        assert elapsed < 0.25, f"should run in parallel, took {elapsed:.3f}s"
    asyncio.run(run())


# ─────────────────────────────────────────────
# 便捷方法
# ─────────────────────────────────────────────


def test_emit_helper():
    async def run():
        d = HookDispatcher()
        d._hooks = [_HookStub(allow())]
        r = await d.emit(EventName.SESSION_START, "s1", _ctx(), cwd="/tmp")
        assert r.action == Action.ALLOW
    asyncio.run(run())


# ─────────────────────────────────────────────
# 工具：构造假 hook
# ─────────────────────────────────────────────


def allow() -> HookResponse:
    return HookResponse.allow()


def deny(reason: str) -> HookResponse:
    return HookResponse.deny(reason)


def modify(data: dict) -> HookResponse:
    return HookResponse.modify(data)


class _HookStub:
    def __init__(self, response: HookResponse):
        self._response = response
        self.name = f"stub_{id(self)}"
        self.kind = "stub"
        self.path = Path("/tmp/stub.py")
        self.description = ""

    async def run(self, event, context, timeout=10):
        return self._response


class _SleepHook:
    def __init__(self, secs: float):
        self._secs = secs
        self.name = f"sleep_{id(self)}"
        self.kind = "stub"
        self.path = Path("/tmp/sleep.py")
        self.description = ""

    async def run(self, event, context, timeout=10):
        await asyncio.sleep(self._secs)
        return HookResponse.allow()

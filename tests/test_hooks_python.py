"""hooks.python 单测。"""
import asyncio
import sys
from pathlib import Path

from minicode.hooks.python import (
    PythonHook,
    discover_python_hooks,
    load_python_hook,
)
from minicode.hooks.types import (
    Action,
    EventName,
    HookContext,
    HookError,
    HookEvent,
)


# ─────────────────────────────────────────────
# 辅助
# ─────────────────────────────────────────────


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


def test_load_missing_file(tmp_path: Path):
    assert load_python_hook(tmp_path / "nope.py") is None


def test_load_no_hook_function(tmp_path: Path):
    p = tmp_path / "x.py"
    p.write_text("x = 1\n", encoding="utf-8")
    assert load_python_hook(p) is None


def test_load_sync_hook_rejected(tmp_path: Path):
    """hook 必须是 async def。"""
    p = tmp_path / "x.py"
    p.write_text("def hook(event, context): pass\n", encoding="utf-8")
    assert load_python_hook(p) is None


def test_load_not_callable(tmp_path: Path):
    p = tmp_path / "x.py"
    p.write_text("hook = 1\n", encoding="utf-8")
    assert load_python_hook(p) is None


def test_load_valid(tmp_path: Path):
    p = tmp_path /"myhook.py"
    p.write_text(
        "async def hook(event, context):\n"
        "    return {}\n",
        encoding="utf-8",
    )
    h = load_python_hook(p)
    assert h is not None
    assert h.name == "myhook"
    assert h.kind == "python"


def test_discover_no_dir(tmp_path: Path):
    assert discover_python_hooks([tmp_path]) == []


def test_discover_underscore_skipped(tmp_path: Path):
    d = tmp_path / "hooks"
    d.mkdir()
    (d / "ok.py").write_text("async def hook(e,c): return {}\n", encoding="utf-8")
    (d / "_private.py").write_text("async def hook(e,c): return {}\n", encoding="utf-8")
    h = discover_python_hooks([d])
    assert [x.name for x in h] == ["ok"]


def test_discover_project_overrides_global(tmp_path: Path):
    proj = tmp_path / "proj"
    glob = tmp_path / "global"
    proj.mkdir()
    glob.mkdir()
    (proj / "a.py").write_text("async def hook(e,c): return {}\n", encoding="utf-8")
    (glob / "a.py").write_text("async def hook(e,c): return {}\n", encoding="utf-8")
    h = discover_python_hooks([proj, glob])
    assert len(h) == 1
    assert h[0].path.parent == proj  # 项目级赢


# ─────────────────────────────────────────────
# 运行
# ─────────────────────────────────────────────


def test_python_hook_returns_dict():
    async def run():
        p = Path("x.py")
        p.write_text(
            "async def hook(e, c):\n"
            "    return {'action': 'allow'}\n",
            encoding="utf-8",
        )
        h = load_python_hook(p)
        assert h is not None
        r = await h.run(_ev(tool="bash"), _ctx())
        assert r.action == Action.ALLOW
    asyncio.run(run())


def test_python_hook_returns_dict_deny():
    async def run():
        p = Path("x.py")
        p.write_text(
            "async def hook(e, c):\n"
            "    return {'action': 'deny', 'reason': 'no!'}\n",
            encoding="utf-8",
        )
        h = load_python_hook(p)
        r = await h.run(_ev(), _ctx())
        assert r.action == Action.DENY
        assert r.reason == "no!"
    asyncio.run(run())


def test_python_hook_returns_none():
    async def run():
        p = Path("x.py")
        p.write_text("async def hook(e, c): pass\n", encoding="utf-8")
        h = load_python_hook(p)
        r = await h.run(_ev(), _ctx())
        assert r.action == Action.ALLOW
    asyncio.run(run())


def test_python_hook_raises_hook_error_on_exception():
    async def run():
        p = Path("x.py")
        p.write_text(
            "async def hook(e, c):\n"
            "    raise RuntimeError('boom')\n",
            encoding="utf-8",
        )
        h = load_python_hook(p)
        try:
            await h.run(_ev(), _ctx())
            assert False, "should have raised"
        except HookError as e:
            assert e.hook_name == "x"
            assert "boom" in e.reason
    asyncio.run(run())


def test_python_hook_timeout():
    async def run():
        p = Path("x.py")
        p.write_text(
            "import asyncio\n"
            "async def hook(e, c):\n"
            "    await asyncio.sleep(10)\n",
            encoding="utf-8",
        )
        h = load_python_hook(p)
        try:
            await h.run(_ev(), _ctx(), timeout=0.1)
            assert False, "should have raised"
        except HookError as e:
            assert "timeout" in e.reason
    asyncio.run(run())

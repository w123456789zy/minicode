"""
测试 minicode.permission：types + service。
"""

from __future__ import annotations

import asyncio
import pytest

from minicode.permission import (
    PermissionAction,
    PermissionRequest,
    PermissionResult,
    PermissionService,
)


# ── PermissionAction ─────────────────────────


class TestPermissionAction:
    def test_values(self):
        assert PermissionAction.ALLOW.value == "allow"
        assert PermissionAction.ALLOW_ALWAYS.value == "allow_always"
        assert PermissionAction.DENY.value == "deny"

    def test_is_allowed(self):
        assert PermissionAction.ALLOW.is_allowed() is True
        assert PermissionAction.ALLOW_ALWAYS.is_allowed() is True
        assert PermissionAction.DENY.is_allowed() is False


# ── PermissionResult helpers ─────────────────────────


class TestPermissionResult:
    def test_allow(self):
        r = PermissionResult.allow()
        assert r.action == PermissionAction.ALLOW
        assert r.reason is None

    def test_allow_always(self):
        r = PermissionResult.allow_always()
        assert r.action == PermissionAction.ALLOW_ALWAYS

    def test_deny_with_reason(self):
        r = PermissionResult.deny("unsafe")
        assert r.action == PermissionAction.DENY
        assert r.reason == "unsafe"


# ── PermissionService: 状态查询/修改 ─────────────────────────


class TestPermissionServiceState:
    def test_empty_init(self):
        svc = PermissionService(session_id="s1")
        assert svc.always_allowed() == set()
        assert svc.always_denied() == set()
        assert svc.is_always_allowed("bash") is False
        assert svc.is_always_denied("bash") is False

    def test_always_allow_adds(self):
        svc = PermissionService()
        svc.always_allow("bash")
        assert svc.is_always_allowed("bash")
        assert not svc.is_always_denied("bash")

    def test_always_deny_adds(self):
        svc = PermissionService()
        svc.always_deny("rm")
        assert svc.is_always_denied("rm")
        assert not svc.is_always_allowed("rm")

    def test_allow_overrides_deny(self):
        svc = PermissionService()
        svc.always_deny("bash")
        svc.always_allow("bash")
        assert svc.is_always_allowed("bash")
        assert not svc.is_always_denied("bash")

    def test_clear_all(self):
        svc = PermissionService()
        svc.always_allow("a")
        svc.always_allow("b")
        svc.always_deny("c")
        n = svc.clear()
        assert n == 3
        assert svc.always_allowed() == set()
        assert svc.always_denied() == set()

    def test_clear_one(self):
        svc = PermissionService()
        svc.always_allow("a")
        svc.always_allow("b")
        n = svc.clear("a")
        assert n == 1
        assert svc.always_allowed() == {"b"}

    def test_status(self):
        svc = PermissionService(session_id="xyz")
        svc.always_allow("bash")
        st = svc.status()
        assert st["session_id"] == "xyz"
        assert st["always_allow"] == ["bash"]
        assert st["always_deny"] == []


# ── PermissionService.request: 同步 prompt ─────────────────────────


class TestPermissionServiceRequestSync:
    def test_yes(self):
        async def go():
            svc = PermissionService(
                prompt_fn=lambda req: PermissionResult.allow(),
            )
            r = await svc.request(PermissionRequest(tool_id="bash"))
            return r
        assert asyncio.run(go()).action == PermissionAction.ALLOW

    def test_yes_always_records(self):
        async def go():
            svc = PermissionService(
                prompt_fn=lambda req: PermissionResult.allow_always(),
            )
            r = await svc.request(PermissionRequest(tool_id="bash"))
            assert r.action == PermissionAction.ALLOW_ALWAYS
            assert svc.is_always_allowed("bash")
        asyncio.run(go())

    def test_no_does_not_record(self):
        async def go():
            svc = PermissionService(
                prompt_fn=lambda req: PermissionResult.deny("no"),
            )
            r = await svc.request(PermissionRequest(tool_id="bash"))
            assert r.action == PermissionAction.DENY
            assert r.reason == "no"
            assert not svc.is_always_allowed("bash")
            assert not svc.is_always_denied("bash")
        asyncio.run(go())

    def test_always_allow_skips_prompt(self):
        called = {"n": 0}

        def prompt(req):
            called["n"] += 1
            return PermissionResult.allow()

        async def go():
            svc = PermissionService(prompt_fn=prompt)
            svc.always_allow("bash")
            r1 = await svc.request(PermissionRequest(tool_id="bash"))
            r2 = await svc.request(PermissionRequest(tool_id="bash"))
            return r1, r2

        r1, r2 = asyncio.run(go())
        assert r1.action == PermissionAction.ALLOW
        assert r2.action == PermissionAction.ALLOW
        assert called["n"] == 0  # 命中 always_allow，不调 prompt

    def test_skipped_counter_increments(self):
        def prompt(req):
            return PermissionResult.allow()

        svc = PermissionService(prompt_fn=prompt)
        svc.always_allow("bash")

        async def go():
            await svc.request(PermissionRequest(tool_id="bash"))

        asyncio.run(go())
        assert svc.status()["stats"]["skipped"] == 1

    def test_always_deny_skips_prompt(self):
        called = {"n": 0}

        def prompt(req):
            called["n"] += 1
            return PermissionResult.allow()

        async def go():
            svc = PermissionService(prompt_fn=prompt)
            svc.always_deny("rm")
            r = await svc.request(PermissionRequest(tool_id="rm"))
            return r

        r = asyncio.run(go())
        assert r.action == PermissionAction.DENY
        assert called["n"] == 0


# ── PermissionService.request: 异步 prompt ─────────────────────────


class TestPermissionServiceRequestAsync:
    def test_async_prompt(self):
        async def prompt(req):
            await asyncio.sleep(0)
            return PermissionResult.allow_always()

        async def go():
            svc = PermissionService(prompt_fn=prompt)
            r = await svc.request(PermissionRequest(tool_id="bash"))
            assert r.action == PermissionAction.ALLOW_ALWAYS
            assert svc.is_always_allowed("bash")
        asyncio.run(go())

    def test_prompt_returning_wrong_type_raises(self):
        def bad_prompt(req):
            return "yes"  # 错的类型

        async def go():
            svc = PermissionService(prompt_fn=bad_prompt)
            with pytest.raises(TypeError, match="prompt_fn must return"):
                await svc.request(PermissionRequest(tool_id="bash"))

        asyncio.run(go())


# ── PermissionService.request: 入参校验 ─────────────────────────


class TestPermissionServiceRequestValidation:
    def test_bad_type_raises(self):
        async def go():
            svc = PermissionService()
            with pytest.raises(TypeError, match="req must be PermissionRequest"):
                await svc.request("not-a-request")  # type: ignore[arg-type]
        asyncio.run(go())

    def test_empty_tool_id_raises(self):
        async def go():
            svc = PermissionService()
            with pytest.raises(ValueError, match="tool_id must be non-empty"):
                await svc.request(PermissionRequest(tool_id=""))
        asyncio.run(go())


# ── default_prompt 行为（用 monkeypatch 模拟 input） ─────────────────────────


class TestDefaultPrompt:
    def test_yes_default(self, monkeypatch, capsys):
        # 模拟用户输入空（默认 = 1）
        monkeypatch.setattr("builtins.input", lambda prompt="": "")
        from minicode.permission.service import default_prompt
        r = default_prompt(PermissionRequest(tool_id="bash", args={"command": "ls"}))
        assert r.action == PermissionAction.ALLOW
        out = capsys.readouterr().out
        assert "[permission]" in out
        assert "bash" in out
        assert "Yes" in out

    def test_yes_always(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda prompt="": "2")
        from minicode.permission.service import default_prompt
        r = default_prompt(PermissionRequest(tool_id="bash"))
        assert r.action == PermissionAction.ALLOW_ALWAYS

    def test_no_with_reason(self, monkeypatch):
        # 第一次 input → "3"（选 no），第二次 input → "because"
        responses = iter(["3", "because"])
        monkeypatch.setattr("builtins.input", lambda prompt="": next(responses))
        from minicode.permission.service import default_prompt
        r = default_prompt(PermissionRequest(tool_id="bash"))
        assert r.action == PermissionAction.DENY
        assert r.reason == "because"

    def test_eof_denies(self, monkeypatch):
        def boom(prompt=""):
            raise EOFError
        monkeypatch.setattr("builtins.input", boom)
        from minicode.permission.service import default_prompt
        r = default_prompt(PermissionRequest(tool_id="bash"))
        assert r.action == PermissionAction.DENY
        assert "interrupted" in (r.reason or "")

    def test_unrecognized_denies(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda prompt="": "???")
        from minicode.permission.service import default_prompt
        r = default_prompt(PermissionRequest(tool_id="bash"))
        assert r.action == PermissionAction.DENY
        assert "unrecognized" in (r.reason or "")


# ── ToolRegistry 集成（端到端） ─────────────────────────


class TestPermissionInToolRegistry:
    """确认 ToolRegistry.execute 会过 permission。"""

    async def test_no_permission_service_runs_normally(self):
        from minicode.tool.registry import ToolRegistry

        from types import SimpleNamespace
        paths = SimpleNamespace(
            project_root=__import__("pathlib").Path("."),
            project_dir=__import__("pathlib").Path(".minicode_test").resolve(),
            global_dir=__import__("pathlib").Path(".minicode_test").resolve(),
            skills_dirs=[],
            agents_dirs=[],
            hooks_dirs=[],
            all_mcp_configs=lambda: [],
            all_config_yamls=lambda: [],
            config_yaml=__import__("pathlib").Path("config.test.yaml"),
        )
        reg = ToolRegistry(paths)  # type: ignore[arg-type]
        # 不装 permission_service → 不应被询问
        from minicode.tool.base import ToolContext
        ctx = ToolContext(cwd=__import__("pathlib").Path("."))
        # tool 不存在，应该直接返回 error（不弹 permission）
        result = await reg.execute("nonexistent_tool", {}, ctx)
        assert result.metadata.get("error") is True
        assert result.metadata.get("denied_by_permission") is None

    async def test_permission_deny_blocks_execution(self):
        from minicode.tool.registry import ToolRegistry

        from types import SimpleNamespace
        paths = SimpleNamespace(
            project_root=__import__("pathlib").Path("."),
            project_dir=__import__("pathlib").Path(".minicode_test").resolve(),
            global_dir=__import__("pathlib").Path(".minicode_test").resolve(),
            skills_dirs=[],
            agents_dirs=[],
            hooks_dirs=[],
            all_mcp_configs=lambda: [],
            all_config_yamls=lambda: [],
            config_yaml=__import__("pathlib").Path("config.test.yaml"),
        )

        async def deny_all(req):
            return PermissionResult.deny("blocked")

        svc = PermissionService(prompt_fn=deny_all)
        reg = ToolRegistry(paths, permission_service=svc)  # type: ignore[arg-type]
        # 自己塞一个 echo tool
        from minicode.tool.base import Tool, ToolContext, ToolResult
        from pydantic import BaseModel

        class _EchoIn(BaseModel):
            msg: str = ""

        class _EchoTool(Tool):
            @property
            def id(self):
                return "echo"

            @property
            def description(self):
                return "echoes msg"

            @property
            def parameters(self):
                return _EchoIn

            async def execute(self, args, ctx):
                return ToolResult(title="ok", output=args.msg, metadata={})

        defn = _EchoTool().to_def()
        reg._defs["echo"] = defn  # type: ignore[attr-defined]

        ctx = ToolContext(cwd=__import__("pathlib").Path("."))
        result = await reg.execute("echo", {"msg": "hi"}, ctx)
        assert result.metadata.get("denied_by_permission") is True
        assert result.metadata.get("action") == "deny"
        # 不应真的执行
        assert "hi" not in result.output

    async def test_always_allow_skips_prompt(self):
        from minicode.tool.registry import ToolRegistry
        from types import SimpleNamespace
        paths = SimpleNamespace(
            project_root=__import__("pathlib").Path("."),
            project_dir=__import__("pathlib").Path(".minicode_test").resolve(),
            global_dir=__import__("pathlib").Path(".minicode_test").resolve(),
            skills_dirs=[],
            agents_dirs=[],
            hooks_dirs=[],
            all_mcp_configs=lambda: [],
            all_config_yamls=lambda: [],
            config_yaml=__import__("pathlib").Path("config.test.yaml"),
        )

        called = {"n": 0}

        def prompt(req):
            called["n"] += 1
            return PermissionResult.allow()

        from minicode.tool.base import Tool, ToolContext, ToolResult
        from pydantic import BaseModel

        class _In(BaseModel):
            msg: str = ""

        class _T(Tool):
            @property
            def id(self):
                return "t1"

            @property
            def description(self):
                return "x"

            @property
            def parameters(self):
                return _In

            async def execute(self, args, ctx):
                return ToolResult(title="ok", output="done", metadata={})

        svc = PermissionService(prompt_fn=prompt)
        svc.always_allow("t1")
        reg = ToolRegistry(paths, permission_service=svc)  # type: ignore[arg-type]
        reg._defs["t1"] = _T().to_def()  # type: ignore[attr-defined]

        ctx = ToolContext(cwd=__import__("pathlib").Path("."))
        r1 = await reg.execute("t1", {"msg": "x"}, ctx)
        r2 = await reg.execute("t1", {"msg": "y"}, ctx)
        assert r1.output == "done"
        assert r2.output == "done"
        assert called["n"] == 0  # 两次都跳过 prompt

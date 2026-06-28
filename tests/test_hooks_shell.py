"""hooks.shell 单测。"""
import asyncio
import os
import sys
from pathlib import Path

import pytest

from minicode.hooks.shell import (
    ShellHook,
    _find_bash_on_windows,
    _is_posix_shebang,
    _is_wsl_bash_launcher,
    _resolve_interpreter,
    discover_shell_hooks,
    load_shell_hook,
)
from minicode.hooks.types import (
    Action,
    EventName,
    HookContext,
    HookError,
    HookEvent,
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
# interpreter 解析
# ─────────────────────────────────────────────


def test_resolve_python_extension_returns_none():
    """不应该处理 .py（那是 PythonHook 的事）。"""
    assert _resolve_interpreter(Path("x.py")) is None


def test_resolve_sh_no_shebang():
    """没有 shebang → 用 bash。"""
    if sys.platform == "win32" and not os.environ.get("PATH"):
        pytest.skip("no PATH on Windows")
    p = Path("x.sh")
    if not p.exists():
        p.write_text("# nothing\n")
    interp = _resolve_interpreter(p)
    # 至少返回一个非空 list
    assert interp is not None
    assert len(interp) >= 1


def test_resolve_sh_with_shebang(tmp_path: Path):
    p = tmp_path / "x.sh"
    p.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    if sys.platform == "win32":
        # Windows 上会 fallback 到 bash
        interp = _resolve_interpreter(p)
    else:
        interp = _resolve_interpreter(p)
    assert interp is not None
    assert "/bin/sh" in interp[0] or "bash" in interp[0]


def test_resolve_powershell():
    if sys.platform != "win32":
        # 非 Windows：powershell 命令不存在 → None
        interp = _resolve_interpreter(Path("x.ps1"))
        # 如果系统装了 pwsh 也不一定能跑（环境差异），不强制
        return
    p = Path("x.ps1")
    interp = _resolve_interpreter(p)
    # 在 Windows 上如果有 pwsh/powershell → 启动器
    # 没装 → None
    if interp is not None:
        assert "powershell" in str(interp).lower() or "pwsh" in str(interp).lower()


# ─────────────────────────────────────────────
# 加载
# ─────────────────────────────────────────────


def test_load_shell_missing(tmp_path: Path):
    assert load_shell_hook(tmp_path / "nope.sh") is None


def test_discover_shell_sorted(tmp_path: Path):
    d = tmp_path / "hooks"
    d.mkdir()
    (d / "z.sh").write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    (d / "a.sh").write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    (d / "ignore.txt").write_text("nope", encoding="utf-8")
    (d / "x.py").write_text("# py, not shell", encoding="utf-8")
    if sys.platform != "win32":
        os.chmod(d / "z.sh", 0o755)
        os.chmod(d / "a.sh", 0o755)
    h = discover_shell_hooks([d])
    names = [x.name for x in h]
    assert "z" in names
    assert "a" in names
    assert "x" not in names
    assert names == sorted(names)


# ─────────────────────────────────────────────
# 运行
# ─────────────────────────────────────────────


@pytest.mark.skipif(sys.platform == "win32", reason="Windows bash 不可靠")
def test_shell_hook_allow(tmp_path: Path):
    p = tmp_path / "myhook.sh"
    p.write_text(
        "#!/bin/bash\n"
        "cat >/dev/null\n"        # 吃掉 stdin
        "echo '{\"action\":\"allow\"}'\n",
        encoding="utf-8",
    )
    os.chmod(p, 0o755)
    h = load_shell_hook(p)
    assert h is not None
    r = asyncio.run(h.run(_ev(), _ctx(), timeout=5.0))
    assert r.action == Action.ALLOW


@pytest.mark.skipif(sys.platform == "win32", reason="Windows bash 不可靠")
def test_shell_hook_deny(tmp_path: Path):
    p = tmp_path / "myhook.sh"
    p.write_text(
        "#!/bin/bash\n"
        "cat >/dev/null\n"
        "echo '{\"action\":\"deny\",\"reason\":\"bad\"}'\n",
        encoding="utf-8",
    )
    os.chmod(p, 0o755)
    h = load_shell_hook(p)
    r = asyncio.run(h.run(_ev(), _ctx(), timeout=5.0))
    assert r.action == Action.DENY
    assert r.reason == "bad"


@pytest.mark.skipif(sys.platform == "win32", reason="Windows bash 不可靠")
def test_shell_hook_empty_stdout(tmp_path: Path):
    """空 stdout → allow。"""
    p = tmp_path / "myhook.sh"
    p.write_text("#!/bin/bash\ncat >/dev/null\n", encoding="utf-8")
    os.chmod(p, 0o755)
    h = load_shell_hook(p)
    r = asyncio.run(h.run(_ev(), _ctx(), timeout=5.0))
    assert r.action == Action.ALLOW


@pytest.mark.skipif(sys.platform == "win32", reason="Windows bash 不可靠")
def test_shell_hook_invalid_json(tmp_path: Path):
    p = tmp_path / "myhook.sh"
    p.write_text("#!/bin/bash\necho 'not json'\n", encoding="utf-8")
    os.chmod(p, 0o755)
    h = load_shell_hook(p)
    try:
        asyncio.run(h.run(_ev(), _ctx(), timeout=5.0))
        assert False, "should raise"
    except HookError as e:
        assert "JSON" in e.reason


@pytest.mark.skipif(sys.platform == "win32", reason="Windows bash 不可靠")
def test_shell_hook_nonzero_exit(tmp_path: Path):
    p = tmp_path / "myhook.sh"
    p.write_text("#!/bin/bash\nexit 1\n", encoding="utf-8")
    os.chmod(p, 0o755)
    h = load_shell_hook(p)
    try:
        asyncio.run(h.run(_ev(), _ctx(), timeout=5.0))
        assert False, "should raise"
    except HookError as e:
        assert "exit code 1" in e.reason


# ─────────────────────────────────────────────
# Windows 特定：bash 解析陷阱
# ─────────────────────────────────────────────


class TestWindowsBashResolution:
    """`_find_bash_on_windows` 必须能避开 WSL 启动器，找到 Git Bash 之类的真 bash。"""

    @pytest.mark.skipif(sys.platform != "win32", reason="windows-only")
    def test_returns_a_bash(self):
        bash = _find_bash_on_windows()
        assert bash is not None
        assert len(bash) >= 1
        # 不能是 WSL 启动器
        assert not _is_wsl_bash_launcher(os.path.normcase(bash[0]))

    def test_wsl_detection(self):
        # 几个 WSL 启动器路径都应识别出来
        for p in (
            r"C:\Windows\System32\bash.exe",
            r"c:\windows\system32\BASH.EXE",
            r"C:\Users\x\AppData\Local\Microsoft\WindowsApps\bash.exe",
        ):
            assert _is_wsl_bash_launcher(os.path.normcase(p)), p
        # 真 bash 不应误判
        for p in (
            r"D:\git\Git\bin\bash.exe",
            r"C:\Program Files\Git\bin\bash.exe",
        ):
            assert not _is_wsl_bash_launcher(os.path.normcase(p)), p

    def test_posix_shebang_detection(self):
        # POSIX 路径
        for p in ("/bin/bash", "/usr/bin/env", "bin/bash"):
            assert _is_posix_shebang(p), p
        # Windows 路径
        for p in (r"C:\bin\bash.exe", r"D:\Git\bin\bash.exe", "C:/bin/bash.exe"):
            assert not _is_posix_shebang(p), p

    @pytest.mark.skipif(sys.platform != "win32", reason="windows-only")
    def test_resolve_sh_with_posix_shebang_falls_through(self, tmp_path: Path):
        """#!/bin/bash 的 shebang 在 Windows 上要 fallback 到 _find_bash_on_windows。"""
        p = tmp_path / "x.sh"
        p.write_text("#!/bin/bash\necho hi\n", encoding="utf-8")
        interp = _resolve_interpreter(p)
        assert interp is not None
        # 第一个元素不能再是 POSIX 路径
        assert not _is_posix_shebang(interp[0])
        # 也不应是 WSL 启动器
        assert not _is_wsl_bash_launcher(os.path.normcase(interp[0]))

    @pytest.mark.skipif(sys.platform != "win32", reason="windows-only")
    def test_block_dangerous_sh_loads(self):
        """项目自带的 .sh hook 在当前环境能正确解析。"""
        p = Path(".minicode/hooks/block_dangerous.sh")
        if not p.exists():
            pytest.skip("hook file not present")
        h = load_shell_hook(p)
        assert h is not None
        assert not _is_wsl_bash_launcher(os.path.normcase(h.interpreter[0]))


# Windows + 真正有 Git Bash 时，跑一次 .sh hook 端到端
@pytest.mark.skipif(sys.platform != "win32", reason="windows-only")
def test_shell_hook_runs_on_windows_with_git_bash(tmp_path: Path):
    bash = _find_bash_on_windows()
    if bash is None:
        pytest.skip("no usable bash on this Windows")
    p = tmp_path / "echo.sh"
    p.write_text(
        "#!/bin/bash\n"
        "cat >/dev/null\n"
        "echo '{\"action\":\"deny\",\"reason\":\"from-win-bash\"}'\n",
        encoding="utf-8",
    )
    h = ShellHook(name="echo", path=p, interpreter=bash)
    r = asyncio.run(h.run(_ev(), _ctx(), timeout=10.0))
    assert r.action == Action.DENY
    assert r.reason == "from-win-bash"

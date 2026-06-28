"""
Shell hook runner。

约定：`.minicode/hooks/<name>.sh` 是可执行文件。
- stdin  收到 JSON 事件（{event, session_id, timestamp, data}）
- stdout 应该输出 JSON 响应（{action, reason?, data?}）
- stderr 任意，会被 minicode 记录（调试用）
- 退出码：0 = 正常；非 0 = 失败（视为 allow + warning）

Windows 兼容：.sh 文件通过 `bash` 启动（Git Bash / WSL / MSYS），
.bat / .cmd 直接执行。两种后缀都识别。

执行：
1. 用 asyncio.create_subprocess_exec 启动
2. 把事件 JSON 灌进 stdin
3. 读 stdout 解析为 HookResponse
4. 超时 kill
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from minicode.hooks.types import (
    HookContext,
    HookError,
    HookEvent,
    HookResponse,
    parse_response,
)


_log = logging.getLogger("minicode.hooks.shell")

DEFAULT_TIMEOUT_S = 10.0

# Windows: .sh → bash；.bat/.cmd → 直接执行
# Posix:  按 shebang 或直接执行
SHELL_EXTENSIONS = {".sh", ".bash", ".bat", ".cmd", ".ps1"}


@dataclass
class ShellHook:
    name: str
    path: Path
    interpreter: List[str]  # e.g. ["bash"] 或 [] (直接执行) 或 ["powershell", "-File"]

    @property
    def kind(self) -> str:
        return "shell"

    @property
    def description(self) -> str:
        return f"shell hook ({self.path.suffix}) at {self.path}"

    async def run(
        self,
        event: HookEvent,
        context: HookContext,
        timeout: float = DEFAULT_TIMEOUT_S,
    ) -> HookResponse:
        payload = json.dumps({
            "event": event.to_dict(),
            "context": context.to_dict(),
        }, ensure_ascii=False)

        cmd = list(self.interpreter) + [str(self.path)]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (FileNotFoundError, PermissionError, OSError) as e:
            raise HookError(self.name, f"cannot start: {e}", original=e) from e

        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(payload.encode("utf-8")),
                timeout=timeout,
            )
        except asyncio.TimeoutError as e:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            raise HookError(self.name, f"timeout after {timeout}s", original=e) from e

        if proc.returncode != 0:
            err = (stderr_b or b"").decode("utf-8", errors="replace").strip()
            raise HookError(
                self.name,
                f"exit code {proc.returncode}: {err[:200] or '(no stderr)'}",
            )

        out = (stdout_b or b"").decode("utf-8", errors="replace").strip()
        if not out:
            return HookResponse.allow()
        try:
            return parse_response(json.loads(out))
        except json.JSONDecodeError as e:
            raise HookError(
                self.name,
                f"stdout is not valid JSON: {out[:200]!r}",
                original=e,
            ) from e


# ─────────────────────────────────────────────────────────────
# 加载
# ─────────────────────────────────────────────────────────────


def _resolve_interpreter(path: Path) -> Optional[List[str]]:
    """根据文件后缀 / 平台决定怎么启动。失败返回 None。"""
    suffix = path.suffix.lower()

    # .py 是 PythonHook 的事，shell runner 不接
    if suffix == ".py":
        return None

    if suffix in (".sh", ".bash"):
        # 优先 shebang，其次 bash
        shebang_interp: Optional[List[str]] = None
        try:
            with path.open("rb") as f:
                first = f.readline(64).decode("utf-8", errors="replace").strip()
            if first.startswith("#!") and "python" not in first and "node" not in first:
                # 信任 shebang
                interp = first[2:].strip().split()
                # 过滤环境变量赋值（PATH=...）
                interp = [x for x in interp if "=" not in x]
                if interp:
                    shebang_interp = interp
        except OSError:
            pass

        # Windows：shebang 是 POSIX 路径（/bin/bash）几乎肯定不存在 → 跳过
        if (
            sys.platform == "win32"
            and shebang_interp is not None
            and _is_posix_shebang(shebang_interp[0])
        ):
            _log.debug(
                "shell hook %s: shebang %r is POSIX path, ignoring on Windows",
                path.name, shebang_interp[0],
            )
            shebang_interp = None

        if shebang_interp is not None:
            return shebang_interp

        # 回退到 bash
        if sys.platform == "win32":
            return _find_bash_on_windows()
        return ["bash"]

    if suffix in (".bat", ".cmd"):
        if sys.platform == "win32":
            return ["cmd", "/c"]
        return None  # 非 Windows 不支持

    if suffix == ".ps1":
        if sys.platform == "win32":
            pwsh = shutil.which("powershell") or shutil.which("pwsh")
            if not pwsh:
                return None
            return [pwsh, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]
        return None

    # 无后缀：尝试直接执行（依赖 shebang / 平台）
    return []


def _find_bash_on_windows() -> Optional[List[str]]:
    """找一个真能在 Windows 上跑 .sh 脚本的 bash。

    关键陷阱：`shutil.which("bash")` 优先返回 WSL 启动器
    （C:\\Windows\\System32\\bash.exe 或 WindowsApps\\bash.exe），
    它不接受 .sh 文件作为参数 → subprocess 启动会报 WinError 2。
    所以要把这些假 bash 跳过，再去 Git Bash 等真 bash 找。
    """
    candidates: List[str] = []

    # 1. PATH 里的 bash（要排除 WSL 启动器）
    which_bash = shutil.which("bash")
    if which_bash:
        norm = os.path.normcase(os.path.normpath(which_bash))
        if not _is_wsl_bash_launcher(norm):
            candidates.append(which_bash)
        else:
            _log.debug("skip WSL bash launcher at %s", which_bash)

    # 2. 常见 Git for Windows 安装路径（顺序：用户级 > 系统级 > Program Files）
    git_bash_paths = [
        os.environ.get("PROGRAMFILES", r"C:\Program Files") + r"\Git\bin\bash.exe",
        os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)") + r"\Git\bin\bash.exe",
        r"D:\git\Git\bin\bash.exe",         # 用户常见的非默认盘
        r"D:\Git\bin\bash.exe",
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
    ]
    for p in git_bash_paths:
        if os.path.isfile(p) and p not in candidates:
            candidates.append(p)

    if not candidates:
        return None
    return [candidates[0]]


def _is_wsl_bash_launcher(norm_path: str) -> bool:
    """判断一个 bash 路径是否是 WSL / Microsoft Store 启动器（不能用 .sh）。"""
    p = norm_path.lower()
    return (
        p.endswith(r"\windows\system32\bash.exe")
        or p.endswith(r"\windowsapps\bash.exe")
        or "\\microsoft\\windowsapps\\" in p
        or p == "c:\\windows\\system32\\bash.exe"
    )


def _is_posix_shebang(interp: str) -> bool:
    """shebang 里的解释器是不是 POSIX 路径（/bin/bash、/usr/bin/env 等）。

    在 Windows 上这类路径几乎都不存在 → 别用它。
    """
    # POSIX 绝对路径：以 / 开头
    if interp.startswith("/"):
        return True
    # 相对路径里只用 / 不用 \\，且不含盘符 → 也算 POSIX
    if "/" in interp and "\\" not in interp and ":" not in interp:
        return True
    return False


def load_shell_hook(path: Path) -> Optional[ShellHook]:
    if not path.is_file():
        return None
    name = path.stem
    interp = _resolve_interpreter(path)
    if interp is None:
        _log.warning("shell hook %s: cannot resolve interpreter for %s", name, path.suffix)
        return None
    if not os.access(path, os.X_OK) and sys.platform != "win32":
        # 类 Unix 系统：要求可执行位
        _log.warning("shell hook %s: not executable, chmod +x first", name)
        return None
    return ShellHook(name=name, path=path, interpreter=interp)


def discover_shell_hooks(dirs: List[Path]) -> List[ShellHook]:
    seen: Dict[str, Path] = {}
    # 项目级后扫 → 覆盖
    for d in reversed(dirs):
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            if not p.is_file():
                continue
            if p.suffix.lower() not in SHELL_EXTENSIONS:
                continue
            if p.name.startswith("_"):
                continue
            seen[p.stem] = p
    out: List[ShellHook] = []
    for name, p in seen.items():
        h = load_shell_hook(p)
        if h is not None:
            out.append(h)
    return sorted(out, key=lambda h: h.name)

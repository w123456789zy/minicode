"""
Python hook runner。

约定：`.minicode/hooks/<name>.py` 里定义一个异步函数 `async def hook(event: dict, context: dict) -> dict`。

event 形如：
    {
        "event": "tool_call_before",
        "session_id": "...",
        "timestamp": "...",
        "data": {...}
    }

context 形如 HookContext.to_dict()。

返回值可以省略（默认 allow），或返回 HookResponse.to_dict() 风格 dict。

执行流程：
1. 用 importlib.util 动态加载 hook 文件
2. 拿 module.hook 属性（必须是 async callable）
3. asyncio.wait_for(timeout=...) 调用
4. 把结果包成 HookResponse
"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional

from minicode.hooks.types import (
    Action,
    EventName,
    HookContext,
    HookError,
    HookEvent,
    HookResponse,
    parse_response,
)


_log = logging.getLogger("minicode.hooks.python")

DEFAULT_TIMEOUT_S = 10.0


@dataclass
class PythonHook:
    """一个加载好的 Python hook。

    名字 = 文件名（不含 .py 后缀）。
    """
    name: str
    path: Path
    _fn: Callable[..., Awaitable[Any]]
    _module_doc: Optional[str] = None   # 模块顶部 docstring（如果有）

    @property
    def kind(self) -> str:
        return "python"

    @property
    def description(self) -> str:
        # 优先级：function docstring → module docstring → 文件名
        doc = inspect.getdoc(self._fn)
        if doc and doc.strip():
            return doc.strip().split("\n", 1)[0].strip()
        if self._module_doc and self._module_doc.strip():
            return self._module_doc.strip().split("\n", 1)[0].strip()
        return f"python hook at {self.path}"

    async def run(
        self,
        event: HookEvent,
        context: HookContext,
        timeout: float = DEFAULT_TIMEOUT_S,
    ) -> HookResponse:
        try:
            coro = self._fn(event.to_dict(), context.to_dict())
            try:
                raw = await asyncio.wait_for(coro, timeout=timeout)
            except asyncio.TimeoutError as e:
                raise HookError(self.name, f"timeout after {timeout}s", original=e) from e
        except HookError:
            raise
        except Exception as e:
            raise HookError(self.name, f"exception: {e}", original=e) from e

        return parse_response(raw)


# ─────────────────────────────────────────────────────────────
# 加载
# ─────────────────────────────────────────────────────────────


def load_python_hook(path: Path) -> Optional[PythonHook]:
    """动态加载一个 .py hook 文件。失败返回 None（记 warning）。"""
    name = path.stem
    if not path.is_file():
        return None
    try:
        spec = importlib.util.spec_from_file_location(
            f"minicode_hook_{name}", str(path),
        )
        if spec is None or spec.loader is None:
            _log.warning("python hook %s: cannot load spec", name)
            return None
        module = importlib.util.module_from_spec(spec)
        # 加到 sys.modules（避免某些 hook 文件里互相 import 失败）
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
        except Exception as e:
            _log.warning("python hook %s: exec failed: %s", name, e)
            return None
        fn = getattr(module, "hook", None)
        if fn is None:
            _log.warning("python hook %s: no `hook` function defined", name)
            return None
        if not callable(fn):
            _log.warning("python hook %s: `hook` is not callable", name)
            return None
        if not inspect.iscoroutinefunction(fn):
            _log.warning("python hook %s: `hook` must be `async def`", name)
            return None
        return PythonHook(
            name=name,
            path=path,
            _fn=fn,
            _module_doc=inspect.getdoc(module),
        )
    except Exception as e:
        _log.warning("python hook %s: load failed: %s", name, e)
        return None


def discover_python_hooks(dirs: list[Path]) -> list[PythonHook]:
    """扫描所有目录，按文件名（去重）加载 .py hook。"""
    seen: Dict[str, Path] = {}
    # 倒序：项目级后扫 → 覆盖全局级同名
    for d in reversed(dirs):
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.py")):
            if not p.is_file():
                continue
            if p.name.startswith("_"):
                continue  # 跳过 _开头（私有用）
            seen[p.stem] = p
    out: list[PythonHook] = []
    for name, p in seen.items():
        h = load_python_hook(p)
        if h is not None:
            out.append(h)
    return sorted(out, key=lambda h: h.name)

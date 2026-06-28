"""
测试 minicode.cli.app._cmd_chat：CLI /chat 命令分支。
"""

from __future__ import annotations

import io
import socket
from contextlib import redirect_stdout


from minicode.bus import Bus
from minicode.chatbridge import ChatBridgeManager
from minicode.cli.app import _cmd_chat


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _make_paths(tmp_path):
    """构造一个 fake MinicodePaths（用 SimpleNamespace 替代）。"""
    from types import SimpleNamespace

    return SimpleNamespace(project_root=tmp_path)


class _MockMreg:
    def __init__(self, model=None):
        self._model = model

    def current(self):
        return self._model


def _capture_sync(fn, *args, **kwargs) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        fn(*args, **kwargs)
    return buf.getvalue()


async def _capture(fn, *args, **kwargs) -> str:
    """async 友好的 capture。"""
    buf = io.StringIO()
    with redirect_stdout(buf):
        result = fn(*args, **kwargs)
        if hasattr(result, "__await__"):
            await result
    return buf.getvalue()


# ── 无 manager ─────────────────────────


class TestCmdChatNoManager:
    async def test_no_bridge(self):
        out = await _capture(_cmd_chat, "list", None, None, _make_paths(__import__("pathlib").Path(".")), [], "", _MockMreg())
        assert "未初始化" in out


# ── help / list / status ─────────────────────────


class TestCmdChatHelp:
    async def test_help_keyword(self):
        mgr = ChatBridgeManager(Bus(), [])
        paths = _make_paths(__import__("pathlib").Path("."))
        out = await _capture(_cmd_chat, "help", mgr, Bus(), paths, [], "", _MockMreg())
        assert "start webhook" in out
        assert "start stdio" in out

    async def test_help_question(self):
        mgr = ChatBridgeManager(Bus(), [])
        paths = _make_paths(__import__("pathlib").Path("."))
        out = await _capture(_cmd_chat, "?", mgr, Bus(), paths, [], "", _MockMreg())
        assert "start webhook" in out


class TestCmdChatList:
    async def test_empty(self):
        mgr = ChatBridgeManager(Bus(), [])
        paths = _make_paths(__import__("pathlib").Path("."))
        out = await _capture(_cmd_chat, "list", mgr, Bus(), paths, [], "", _MockMreg())
        assert "no active adapter" in out

    async def test_with_adapters(self):
        from minicode.chatbridge.adapters.stdio import StdioAdapter

        mgr = ChatBridgeManager(Bus(), [])
        adp = StdioAdapter(input_fn=lambda p: "")
        await mgr.register(adp)
        paths = _make_paths(__import__("pathlib").Path("."))
        out = await _capture(_cmd_chat, "list", mgr, Bus(), paths, [], "", _MockMreg())
        assert "stdio" in out
        await adp.stop()


class TestCmdChatStatus:
    async def test_default_is_status(self):
        mgr = ChatBridgeManager(Bus(), [])
        paths = _make_paths(__import__("pathlib").Path("."))
        out = await _capture(_cmd_chat, "", mgr, Bus(), paths, [], "", _MockMreg())
        assert "session" in out
        assert "in/out/err" in out

    async def test_status_keyword(self):
        mgr = ChatBridgeManager(Bus(), [])
        paths = _make_paths(__import__("pathlib").Path("."))
        out = await _capture(_cmd_chat, "status", mgr, Bus(), paths, [], "", _MockMreg())
        assert "session" in out


# ── start ─────────────────────────


class TestCmdChatStart:
    async def test_start_webhook(self, tmp_path):
        port = _free_port()
        mgr = ChatBridgeManager(Bus(), [])
        paths = _make_paths(tmp_path)
        out = await _capture(
            _cmd_chat, f"start webhook --port {port}", mgr, Bus(), paths, [], "", _MockMreg(),
        )
        assert "webhook started" in out
        assert f"http://127.0.0.1:{port}/chat" in out
        assert "outbound" in out
        assert "curl" in out
        # cleanup
        await mgr.unregister("webhook")

    async def test_start_webhook_default_port(self, tmp_path):
        mgr = ChatBridgeManager(Bus(), [])
        paths = _make_paths(tmp_path)
        out = await _capture(_cmd_chat, "start webhook", mgr, Bus(), paths, [], "", _MockMreg())
        # 用了默认 8765
        assert "8765" in out
        # cleanup
        await mgr.unregister("webhook")

    async def test_start_unknown_adapter(self, tmp_path):
        mgr = ChatBridgeManager(Bus(), [])
        paths = _make_paths(tmp_path)
        out = await _capture(_cmd_chat, "start irc", mgr, Bus(), paths, [], "", _MockMreg())
        assert "unknown adapter" in out

    async def test_start_no_args(self, tmp_path):
        mgr = ChatBridgeManager(Bus(), [])
        paths = _make_paths(tmp_path)
        out = await _capture(_cmd_chat, "start", mgr, Bus(), paths, [], "", _MockMreg())
        assert "start <webhook|stdio>" in out

    async def test_start_bad_port(self, tmp_path):
        mgr = ChatBridgeManager(Bus(), [])
        paths = _make_paths(tmp_path)
        out = await _capture(_cmd_chat, "start webhook --port abc", mgr, Bus(), paths, [], "", _MockMreg())
        assert "bad --port" in out


# ── stop ─────────────────────────


class TestCmdChatStop:
    async def test_stop_all_empty(self):
        mgr = ChatBridgeManager(Bus(), [])
        paths = _make_paths(__import__("pathlib").Path("."))
        out = await _capture(_cmd_chat, "stop", mgr, Bus(), paths, [], "", _MockMreg())
        assert "stopped 0" in out

    async def test_stop_specific(self):
        mgr = ChatBridgeManager(Bus(), [])
        from minicode.chatbridge.adapters.stdio import StdioAdapter

        adp = StdioAdapter(input_fn=lambda p: "")
        await mgr.register(adp)
        paths = _make_paths(__import__("pathlib").Path("."))
        out = await _capture(_cmd_chat, "stop stdio", mgr, Bus(), paths, [], "", _MockMreg())
        assert "stopped: stdio" in out
        assert not mgr.has_adapter("stdio")

    async def test_stop_unknown(self):
        mgr = ChatBridgeManager(Bus(), [])
        paths = _make_paths(__import__("pathlib").Path("."))
        out = await _capture(_cmd_chat, "stop nop", mgr, Bus(), paths, [], "", _MockMreg())
        assert "no such adapter" in out

    async def test_stop_all_keyword(self):
        mgr = ChatBridgeManager(Bus(), [])
        from minicode.chatbridge.adapters.stdio import StdioAdapter

        adp1 = StdioAdapter(input_fn=lambda p: "")
        await mgr.register(adp1)
        paths = _make_paths(__import__("pathlib").Path("."))
        out = await _capture(_cmd_chat, "stop all", mgr, Bus(), paths, [], "", _MockMreg())
        assert "stopped 1" in out


# ── unknown subcommand ─────────────────────────


class TestCmdChatUnknown:
    async def test_unknown(self):
        mgr = ChatBridgeManager(Bus(), [])
        paths = _make_paths(__import__("pathlib").Path("."))
        out = await _capture(_cmd_chat, "weird", mgr, Bus(), paths, [], "", _MockMreg())
        assert "unknown subcommand" in out

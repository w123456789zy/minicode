"""
测试 ChatBridgeManager：bus 集成、history 写入、model_runner 调用、广播。
"""

from __future__ import annotations

import asyncio

import pytest

from minicode.bus import (
    Bus,
    CHAT_BRIDGE_STARTED,
    CHAT_BRIDGE_STOPPED,
    CHAT_INCOMING,
    CHAT_OUTGOING,
    MESSAGE_ASSISTANT,
    MESSAGE_USER,
    SESSION_ERROR,
)
from minicode.chatbridge.adapter import ChatAdapter, IncomingMessage, OutgoingMessage
from minicode.chatbridge.manager import ChatBridgeManager
from minicode.model.message import Message


class _MockAdapter(ChatAdapter):
    name = "mock"

    def __init__(self, name="mock"):
        super().__init__()
        self._name = name
        self.sent: list[OutgoingMessage] = []
        self.started = False
        self.stopped = False
        self.fail_send = False

    @property
    def name(self) -> str:
        return self._name

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True

    async def send(self, msg: OutgoingMessage):
        if self.fail_send:
            raise RuntimeError("send failed")
        self.sent.append(msg)

    @property
    def running(self) -> bool:
        return self.started and not self.stopped


# ── CRUD ─────────────────────────


class TestManagerRegister:
    async def test_register(self):
        bus = Bus()
        mgr = ChatBridgeManager(bus, [])
        adp = _MockAdapter()
        await mgr.register(adp)
        assert mgr.has_adapter("mock")
        assert adp.started
        # 广播 STARTED
        assert bus.stats()["published"] >= 1

    async def test_register_replaces_same_name(self):
        """register 同名 adapter 会先 stop 旧的。"""
        bus = Bus()
        mgr = ChatBridgeManager(bus, [])
        a1 = _MockAdapter("dup")
        a2 = _MockAdapter("dup")
        await mgr.register(a1)
        await mgr.register(a2)
        assert a1.stopped
        assert a2.started
        assert mgr.has_adapter("dup")
        # a2 替换 a1：只一个 adapter
        assert len(mgr.list_adapters()) == 1

    async def test_register_different_names_coexists(self):
        """register 不同 name 的 adapter → 共存。"""
        bus = Bus()
        mgr = ChatBridgeManager(bus, [])
        a1 = _MockAdapter("first")
        a2 = _MockAdapter("second")
        await mgr.register(a1)
        await mgr.register(a2)
        assert a1.started and not a1.stopped
        assert a2.started and not a2.stopped
        assert mgr.has_adapter("first")
        assert mgr.has_adapter("second")
        assert len(mgr.list_adapters()) == 2

    async def test_unregister(self):
        bus = Bus()
        mgr = ChatBridgeManager(bus, [])
        adp = _MockAdapter()
        await mgr.register(adp)
        ok = await mgr.unregister("mock")
        assert ok
        assert not mgr.has_adapter("mock")
        assert adp.stopped

    async def test_unregister_unknown(self):
        bus = Bus()
        mgr = ChatBridgeManager(bus, [])
        ok = await mgr.unregister("nope")
        assert not ok

    async def test_stop_all(self):
        bus = Bus()
        mgr = ChatBridgeManager(bus, [])
        a1 = _MockAdapter("a")
        a2 = _MockAdapter("b")
        await mgr.register(a1)
        await mgr.register(a2)
        n = await mgr.stop_all()
        assert n == 2
        assert a1.stopped
        assert a2.stopped

    def test_list_adapters_empty(self):
        bus = Bus()
        mgr = ChatBridgeManager(bus, [])
        assert mgr.list_adapters() == []

    def test_status_initial(self):
        bus = Bus()
        mgr = ChatBridgeManager(bus, [])
        st = mgr.status()
        assert st["session_id"]
        assert st["incoming"] == 0
        assert st["outgoing"] == 0
        assert st["errors"] == 0
        assert st["threads"] == {}


# ── 入站处理 ─────────────────────────


class TestIncomingFlow:
    async def test_incoming_writes_history(self):
        bus = Bus()
        history: list[Message] = []
        mgr = ChatBridgeManager(bus, history)
        await mgr.register(_MockAdapter())
        msg = IncomingMessage(user="alice", channel="#c", thread="t1", text="hello")
        await mgr._on_incoming(msg)
        # history 应该有 user + assistant
        assert len(history) == 2
        assert "alice" in history[0].text()
        assert "hello" in history[0].text()

    async def test_incoming_calls_runner(self):
        bus = Bus()
        history: list[Message] = []
        called = []

        async def runner(hist: list[Message]) -> str:
            called.append(list(hist))
            return "CUSTOM_REPLY"

        mgr = ChatBridgeManager(bus, history, model_runner=runner)
        await mgr.register(_MockAdapter())
        await mgr._on_incoming(IncomingMessage("u", "c", "t", "hi"))
        assert "CUSTOM_REPLY" in history[-1].text()

    async def test_incoming_publishes_events(self):
        bus = Bus()
        seen: list[dict] = []
        bus.subscribe_all(lambda p: seen.append(p))
        history: list[Message] = []
        mgr = ChatBridgeManager(bus, history)
        await mgr.register(_MockAdapter())
        await mgr._on_incoming(IncomingMessage("u", "#c", "t1", "hi"))
        types = [e["type"] for e in seen]
        assert CHAT_INCOMING in types
        assert MESSAGE_USER in types
        assert MESSAGE_ASSISTANT in types
        assert CHAT_OUTGOING in types

    async def test_incoming_sends_to_all_adapters(self):
        bus = Bus()
        history: list[Message] = []
        mgr = ChatBridgeManager(bus, history)
        a1 = _MockAdapter()
        a2 = _MockAdapter()
        await mgr.register(a1)
        await mgr.register(a2)
        await mgr._on_incoming(IncomingMessage("u", "c", "t", "hi"))
        # 两个 adapter 都收到（注意 register 替换 a1，但 a2 被 register 时 a1 还在吗？）
        # 实际上 register("mock") 会替换 a1，所以只剩 a2
        # 因此只有 a2 收到
        # 调整测试：用不同 name
        # 重新构造
        bus2 = Bus()
        history2: list[Message] = []
        mgr2 = ChatBridgeManager(bus2, history2)

        class A(ChatAdapter):
            name = "a_unique"

            def __init__(self):
                super().__init__()
                self.sent = []

            async def start(self): pass
            async def stop(self): pass
            async def send(self, m): self.sent.append(m)

            @property
            def running(self): return True

        class B(ChatAdapter):
            name = "b_unique"

            def __init__(self):
                super().__init__()
                self.sent = []

            async def start(self): pass
            async def stop(self): pass
            async def send(self, m): self.sent.append(m)

            @property
            def running(self): return True

        a1, a2 = A(), B()
        await mgr2.register(a1)
        await mgr2.register(a2)
        await mgr2._on_incoming(IncomingMessage("u", "c", "t", "hi"))
        assert len(a1.sent) == 1
        assert len(a2.sent) == 1
        assert a1.sent[0].text == a2.sent[0].text  # 同一回复

    async def test_incoming_thread_session_mapping(self):
        bus = Bus()
        mgr = ChatBridgeManager(bus, [])
        await mgr.register(_MockAdapter())
        await mgr._on_incoming(IncomingMessage("u", "c", "t1", "hi"))
        await mgr._on_incoming(IncomingMessage("u", "c", "t2", "hi"))
        st = mgr.status()
        assert "c:t1" in st["threads"]
        assert "c:t2" in st["threads"]

    async def test_incoming_count_increments(self):
        bus = Bus()
        mgr = ChatBridgeManager(bus, [])
        await mgr.register(_MockAdapter())
        for i in range(3):
            await mgr._on_incoming(IncomingMessage("u", "c", f"t{i}", f"hi{i}"))
        st = mgr.status()
        assert st["incoming"] == 3
        assert st["outgoing"] == 3


# ── model_runner 异常 ─────────────────────────


class TestRunnerError:
    async def test_runner_error_returns_bridge_error_message(self):
        bus = Bus()
        history: list[Message] = []
        seen: list[dict] = []
        bus.subscribe(SESSION_ERROR, lambda p: seen.append(p))

        async def bad(hist):
            raise RuntimeError("model down")

        mgr = ChatBridgeManager(bus, history, model_runner=bad)
        await mgr.register(_MockAdapter())
        await mgr._on_incoming(IncomingMessage("u", "c", "t", "hi"))
        # history 最后一条是 [bridge error]
        assert "[bridge error]" in history[-1].text()
        # 广播了 SESSION_ERROR
        assert len(seen) == 1
        # error 计数 +1
        assert mgr.status()["errors"] == 1

    async def test_adapter_send_error_continues(self):
        bus = Bus()
        history: list[Message] = []
        mgr = ChatBridgeManager(bus, history)

        class FailAdapter(ChatAdapter):
            name = "fail"

            async def start(self): pass
            async def stop(self): pass
            async def send(self, m): raise RuntimeError("send failed")
            @property
            def running(self): return True

        await mgr.register(FailAdapter())
        # 不应抛错
        await mgr._on_incoming(IncomingMessage("u", "c", "t", "hi"))
        # 内部计数增加
        assert mgr.status()["errors"] >= 1


# ── 默认 runner（echo）─────────────────────────


class TestDefaultRunner:
    async def test_echo(self):
        bus = Bus()
        history: list[Message] = []
        mgr = ChatBridgeManager(bus, history)  # 用默认 runner
        await mgr.register(_MockAdapter())
        await mgr._on_incoming(IncomingMessage("u", "c", "t", "hi there"))
        # 默认 runner 是 echo
        assert "echo" in history[-1].text() or "hi there" in history[-1].text()


# ── 自定义 session_id ─────────────────────────


class TestSessionId:
    def test_default_session_id(self):
        bus = Bus()
        mgr = ChatBridgeManager(bus, [])
        assert mgr.session_id.startswith("bridge-")

    def test_custom_session_id(self):
        bus = Bus()
        mgr = ChatBridgeManager(bus, [], session_id="my-session-1")
        assert mgr.session_id == "my-session-1"

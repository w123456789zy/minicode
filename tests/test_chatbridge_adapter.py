"""
测试 ChatAdapter 抽象基类 + IncomingMessage / OutgoingMessage 数据类。
"""

from __future__ import annotations

import asyncio

import pytest

from minicode.chatbridge.adapter import (
    ChatAdapter,
    IncomingMessage,
    OutgoingMessage,
)


class _DummyAdapter(ChatAdapter):
    name = "dummy"

    def __init__(self):
        super().__init__()
        self.started = False
        self.stopped = False
        self.sent: list[OutgoingMessage] = []

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True

    async def send(self, msg: OutgoingMessage):
        self.sent.append(msg)

    @property
    def running(self) -> bool:
        return self.started and not self.stopped


class TestIncomingMessage:
    def test_construction(self):
        m = IncomingMessage(user="alice", channel="#test", thread="t1", text="hi")
        assert m.user == "alice"
        assert m.channel == "#test"
        assert m.thread == "t1"
        assert m.text == "hi"
        assert m.raw is None

    def test_raw_default(self):
        m = IncomingMessage(user="u", channel="c", thread="t", text="x")
        assert m.raw is None

    def test_thread_key(self):
        m = IncomingMessage(user="u", channel="#a", thread="t1", text="x")
        assert m.thread_key() == "#a:t1"

    def test_thread_key_distinct(self):
        m1 = IncomingMessage(user="u", channel="#a", thread="t1", text="x")
        m2 = IncomingMessage(user="u", channel="#a", thread="t2", text="x")
        assert m1.thread_key() != m2.thread_key()


class TestOutgoingMessage:
    def test_construction(self):
        m = OutgoingMessage(channel="#x", thread="t1", text="hello")
        assert m.channel == "#x"
        assert m.thread == "t1"
        assert m.text == "hello"
        assert m.reply_to is None

    def test_with_reply_to(self):
        m = OutgoingMessage(channel="#x", thread="t1", text="hi", reply_to="msg-1")
        assert m.reply_to == "msg-1"


class TestChatAdapterBase:
    def test_name(self):
        assert _DummyAdapter().name == "dummy"

    async def test_lifecycle(self):
        a = _DummyAdapter()
        assert a.running is False
        await a.start()
        assert a.running is True
        await a.stop()
        assert a.running is False
        assert a.started and a.stopped

    async def test_set_incoming_handler(self):
        a = _DummyAdapter()
        received: list[IncomingMessage] = []

        async def handler(m: IncomingMessage):
            received.append(m)

        a.set_incoming_handler(handler)
        await a.on_incoming(IncomingMessage("u", "c", "t", "hi"))
        assert len(received) == 1

    async def test_on_incoming_no_handler_is_noop(self):
        a = _DummyAdapter()
        # 不 set handler，调用 on_incoming 不应抛错
        await a.on_incoming(IncomingMessage("u", "c", "t", "hi"))

    async def test_status(self):
        a = _DummyAdapter()
        st = a.status()
        assert st["name"] == "dummy"
        assert st["running"] is False
        assert st["type"] == "_DummyAdapter"

    async def test_send_called(self):
        a = _DummyAdapter()
        await a.start()
        msg = OutgoingMessage("#c", "t", "hi")
        await a.send(msg)
        assert a.sent == [msg]


# ── 自定义 adapter 示例 ─────────────────────────


class TestCustomAdapter:
    """演示：写一个 SlackAdapter 只需要 50 行（v0 mock 即可）。"""

    async def test_custom_subclass(self):
        class SlackLike(ChatAdapter):
            name = "slack_like"

            def __init__(self):
                super().__init__()
                self.sent = []

            async def start(self):
                pass

            async def stop(self):
                pass

            async def send(self, msg: OutgoingMessage):
                self.sent.append(msg)

            @property
            def running(self):
                return True

        s = SlackLike()
        assert s.name == "slack_like"
        assert s.running is True
        await s.send(OutgoingMessage("#x", "t", "hi"))
        assert len(s.sent) == 1

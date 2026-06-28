"""
测试 minicode.bus：进程内 pub/sub。
"""

from __future__ import annotations

import asyncio

import pytest

from minicode.bus import (
    Bus,
    MESSAGE_USER,
    SESSION_START,
)


# ── 同步 + 异步 publish / subscribe ─────────────────────────


class TestPublishSubscribe:
    async def test_typed_subscribe(self):
        b = Bus()
        received = []
        b.subscribe(SESSION_START, lambda p: received.append(p))
        await b.publish(SESSION_START, {"foo": "bar"})
        assert len(received) == 1
        assert received[0]["type"] == SESSION_START
        assert received[0]["properties"] == {"foo": "bar"}

    async def test_wildcard_subscribe(self):
        b = Bus()
        received = []
        b.subscribe_all(lambda p: received.append(p))
        await b.publish(SESSION_START, {})
        await b.publish(MESSAGE_USER, {"text": "hi"})
        assert len(received) == 2
        types = [r["type"] for r in received]
        assert types == [SESSION_START, MESSAGE_USER]

    async def test_unsubscribe(self):
        b = Bus()
        received = []
        unsub = b.subscribe(SESSION_START, lambda p: received.append(p))
        await b.publish(SESSION_START, {})
        assert len(received) == 1
        unsub()
        await b.publish(SESSION_START, {})
        assert len(received) == 1  # 第二个不再收到

    async def test_wildcard_does_not_double(self):
        """typed + wildcard 是两个独立订阅，不重复收。"""
        b = Bus()
        typed = []
        wild = []
        b.subscribe(SESSION_START, lambda p: typed.append(p))
        b.subscribe_all(lambda p: wild.append(p))
        await b.publish(SESSION_START, {})
        assert len(typed) == 1
        assert len(wild) == 1

    async def test_typed_only_receives_its_type(self):
        b = Bus()
        received = []
        b.subscribe(SESSION_START, lambda p: received.append(p))
        await b.publish(MESSAGE_USER, {})
        assert received == []

    async def test_multiple_subscribers(self):
        b = Bus()
        r1, r2 = [], []
        b.subscribe(SESSION_START, lambda p: r1.append(p))
        b.subscribe(SESSION_START, lambda p: r2.append(p))
        await b.publish(SESSION_START, {})
        assert len(r1) == 1
        assert len(r2) == 1


# ── 同步 + 异步 callback ─────────────────────────


class TestSyncAndAsyncCallbacks:
    async def test_sync_callback(self):
        b = Bus()
        received = []
        b.subscribe(SESSION_START, lambda p: received.append(p))
        await b.publish(SESSION_START, {"a": 1})
        assert len(received) == 1

    async def test_async_callback(self):
        b = Bus()
        received = []

        async def cb(p):
            await asyncio.sleep(0)
            received.append(p)

        b.subscribe(SESSION_START, cb)
        await b.publish(SESSION_START, {"a": 1})
        assert len(received) == 1

    async def test_mixed_sync_async(self):
        b = Bus()
        sync, asy = [], []

        async def acb(p):
            asy.append(p)

        b.subscribe(SESSION_START, lambda p: sync.append(p))
        b.subscribe(SESSION_START, acb)
        await b.publish(SESSION_START, {})
        assert len(sync) == 1
        assert len(asy) == 1


# ── 异常隔离 ─────────────────────────


class TestErrorIsolation:
    async def test_sync_error_does_not_block_others(self):
        b = Bus()
        ok = []
        b.subscribe(SESSION_START, lambda p: 1 / 0)  # noqa: B018  # 故意抛错
        b.subscribe(SESSION_START, lambda p: ok.append(p))
        await b.publish(SESSION_START, {})
        assert len(ok) == 1  # 第二个仍收到

    async def test_async_error_does_not_block_others(self):
        b = Bus()
        ok = []

        async def bad(p):
            raise RuntimeError("boom")

        b.subscribe(SESSION_START, bad)
        b.subscribe(SESSION_START, lambda p: ok.append(p))
        await b.publish(SESSION_START, {})
        assert len(ok) == 1


# ── publish_nowait（fire-and-forget）─────────────────────────


class TestPublishNowait:
    def test_publish_in_sync_context(self):
        """从同步 context 也能发事件（callback 是同步时）。"""
        b = Bus()
        received = []
        b.subscribe(SESSION_START, lambda p: received.append(p))
        b.publish_nowait(SESSION_START, {"x": 1})
        assert len(received) == 1
        assert b.stats()["dropped_async"] == 0

    def test_publish_async_callback_drops(self):
        """同步 context + async callback → drop + 计数。"""
        b = Bus()
        received = []

        async def cb(p):
            received.append(p)

        b.subscribe(SESSION_START, cb)
        b.publish_nowait(SESSION_START, {})
        assert b.stats()["dropped_async"] == 1
        # 没有 loop 跑 coroutine，received 空
        assert received == []


# ── 参数校验 ─────────────────────────


class TestValidation:
    async def test_publish_empty_type_raises(self):
        b = Bus()
        with pytest.raises(ValueError):
            await b.publish("", {})

    async def test_publish_non_dict_properties_raises(self):
        b = Bus()
        with pytest.raises(ValueError):
            await b.publish("x", "not a dict")

    def test_subscribe_empty_type_raises(self):
        b = Bus()
        with pytest.raises(ValueError):
            b.subscribe("", lambda p: None)

    def test_publish_nowait_validation(self):
        b = Bus()
        with pytest.raises(ValueError):
            b.publish_nowait("", {})
        with pytest.raises(ValueError):
            b.publish_nowait("x", "not a dict")


# ── stats / clear / aclose ─────────────────────────


class TestStats:
    async def test_stats_published_count(self):
        b = Bus()
        await b.publish(SESSION_START, {})
        await b.publish(MESSAGE_USER, {})
        s = b.stats()
        assert s["published"] == 2

    def test_stats_subscribers(self):
        b = Bus()
        b.subscribe(SESSION_START, lambda p: None)
        b.subscribe(MESSAGE_USER, lambda p: None)
        b.subscribe_all(lambda p: None)
        s = b.stats()
        assert s["typed"][SESSION_START] == 1
        assert s["typed"][MESSAGE_USER] == 1
        assert s["wildcard"] == 1

    def test_clear(self):
        b = Bus()
        b.subscribe(SESSION_START, lambda p: None)
        b.subscribe_all(lambda p: None)
        b.clear()
        s = b.stats()
        assert s["typed"] == {}
        assert s["wildcard"] == 0

    async def test_aclose(self):
        b = Bus()
        b.subscribe(SESSION_START, lambda p: None)
        await b.aclose()
        assert b.stats()["closed"] is True
        # close 后 publish 应该仍能调用（不抛错）
        await b.publish(SESSION_START, {})  # 没有订阅者，no-op


# ── 预定义事件常量 ─────────────────────────


class TestEventConstants:
    def test_all_constants_non_empty(self):
        from minicode.bus import ALL_EVENT_TYPES
        assert len(ALL_EVENT_TYPES) > 0
        for e in ALL_EVENT_TYPES:
            assert isinstance(e, str)
            assert e
            # 全部是 "x.y" 格式
            assert "." in e

    def test_constants_unique(self):
        from minicode.bus import ALL_EVENT_TYPES
        assert len(set(ALL_EVENT_TYPES)) == len(ALL_EVENT_TYPES)

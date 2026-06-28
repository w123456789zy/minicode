"""
测试 WebhookAdapter：HTTP server 启停、JSON 解析、出站写入。
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

import pytest

from minicode.chatbridge.adapters.webhook import WebhookAdapter


def _free_port() -> int:
    """找一个空闲端口。"""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ── Handler 单元测试 ─────────────────────────
# 注：Handler 逻辑通过 TestWebhookAdapter 的真实 HTTP server 测试覆盖，
# 直接 mock BaseHTTPRequestHandler 太脆弱（要模拟 requestline/request_version 等）。



# ── 真实 HTTP server ─────────────────────────


class TestWebhookAdapter:
    @pytest.fixture
    def port(self):
        return _free_port()

    @pytest.fixture
    def outbound_path(self, tmp_path):
        return str(tmp_path / "outbox.jsonl")

    async def test_start_stop(self, port, outbound_path):
        a = WebhookAdapter(port=port, outbound_path=outbound_path)
        assert a.running is False
        await a.start()
        try:
            assert a.running is True
        finally:
            await a.stop()
        assert a.running is False

    async def test_health_endpoint(self, port, outbound_path):
        a = WebhookAdapter(port=port, outbound_path=outbound_path)
        await a.start()
        try:
            # 同步 urllib 请求
            url = f"http://127.0.0.1:{port}/health"
            with urlrequest.urlopen(url, timeout=2.0) as r:
                assert r.status == 200
                data = json.loads(r.read().decode())
                assert data["ok"] is True
        finally:
            await a.stop()

    async def test_chat_endpoint_triggers_on_incoming(self, port, outbound_path):
        a = WebhookAdapter(port=port, outbound_path=outbound_path)
        received: list = []
        await a.start()
        try:
            a.set_incoming_handler(lambda m: received.append(m) or asyncio.sleep(0))
            body = json.dumps({
                "user": "alice", "channel": "#test", "thread": "t1", "text": "hello",
            }).encode()
            req = urlrequest.Request(
                f"http://127.0.0.1:{port}/chat",
                data=body,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urlrequest.urlopen(req, timeout=2.0) as r:
                assert r.status == 200
            # 等 consumer task 处理（最多 1s）
            for _ in range(20):
                if received:
                    break
                await asyncio.sleep(0.05)
            assert len(received) == 1
            assert received[0].text == "hello"
        finally:
            await a.stop()

    async def test_send_writes_outbox(self, port, outbound_path, tmp_path):
        a = WebhookAdapter(port=port, outbound_path=outbound_path)
        await a.start()
        try:
            await a.send(__import__("minicode.chatbridge.adapter", fromlist=["OutgoingMessage"]).OutgoingMessage(
                channel="#c", thread="t1", text="outbound test",
            ))
            # 异步写盘，等一下
            for _ in range(20):
                if os.path.exists(outbound_path):
                    break
                await asyncio.sleep(0.05)
            with open(outbound_path, encoding="utf-8") as f:
                line = f.readline()
            record = json.loads(line)
            assert record["channel"] == "#c"
            assert record["text"] == "outbound test"
        finally:
            await a.stop()

    @pytest.mark.skipif(
        True,  # Windows + SO_REUSEADDR 行为不一致，跨平台不可靠
        reason="port-in-use behavior is platform-dependent; skip in CI",
    )
    async def test_port_in_use_fails(self, port, outbound_path):
        a1 = WebhookAdapter(port=port, outbound_path=outbound_path)
        await a1.start()
        try:
            assert a1.running is True
            # 端口已被 a1 占用，再启 a2 应该抛 OSError
            a2 = WebhookAdapter(port=port, outbound_path=outbound_path)
            raised = False
            try:
                await a2.start()
            except OSError:
                raised = True
            finally:
                await a2.stop()
            assert raised, "expected OSError when port in use"
        finally:
            await a1.stop()

    def test_endpoint(self, port, outbound_path):
        a = WebhookAdapter(port=port, outbound_path=outbound_path)
        assert a.endpoint() == f"http://127.0.0.1:{port}/chat"

"""
WebhookAdapter：std-lib http.server 实现的 HTTP webhook 桥接。

入站：POST /chat with JSON {user, channel, thread, text}
出站：写到一个 outbound JSONL 文件（v0 不主动发 HTTP 回外部；简化集成）

为什么用 stdlib http.server：minicode 的依赖只有 httpx（同步友好但用法啰嗦），
而 http.server + ThreadingHTTPServer + 线程就够用。v0 不需要 aiohttp。

架构：
- start() 启 HTTPServer 在子线程
- 每个请求 → 解析 JSON → 构造 IncomingMessage → 丢到 asyncio.Queue
- 后台 task 持续从 queue 读 → 调 on_incoming
- send() 写一行 JSONL 到 outbound_path（v0 简化）

注意：v0 测试不验证 HTTP 出站（避免依赖网络）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional

from minicode.chatbridge.adapter import ChatAdapter, IncomingMessage, OutgoingMessage


_log = logging.getLogger("minicode.chatbridge.webhook")


class _WebhookHandler(BaseHTTPRequestHandler):
    """单请求处理：解析 JSON → 推 queue → 返回 200。"""

    # 由 server 注入
    queue: asyncio.Queue  # type: ignore[type-arg]
    loop: asyncio.AbstractEventLoop  # type: ignore[type-arg]
    log_extra: Dict[str, Any]

    def log_message(self, format, *args):  # noqa: A002
        # 抑制默认 stderr 输出
        _log.debug("webhook: " + format, *args)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0") or "0")
        try:
            body = self.rfile.read(length) if length > 0 else b"{}"
            data = json.loads(body.decode("utf-8") or "{}")
        except Exception as e:
            self._reply(400, {"ok": False, "error": f"bad json: {e}"})
            return

        if self.path == "/chat":
            msg = IncomingMessage(
                user=str(data.get("user", "anonymous")),
                channel=str(data.get("channel", "default")),
                thread=str(data.get("thread", "main")),
                text=str(data.get("text", "")),
                raw=data,
            )
            # 跨线程 → loop.call_soon_threadsafe
            try:
                self.loop.call_soon_threadsafe(self.queue.put_nowait, msg)
            except Exception as e:
                self._reply(500, {"ok": False, "error": f"queue push failed: {e}"})
                return
            self._reply(200, {"ok": True, "thread": msg.thread, "text_len": len(msg.text)})
            return

        if self.path == "/health":
            self._reply(200, {"ok": True})
            return

        self._reply(404, {"ok": False, "error": f"unknown path: {self.path}"})

    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            self._reply(200, {"ok": True, "adapter": "webhook"})
            return
        self._reply(404, {"ok": False, "error": "GET not supported; use POST"})

    def _reply(self, code: int, body: Dict[str, Any]) -> None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class WebhookAdapter(ChatAdapter):
    name = "webhook"

    def __init__(
        self,
        port: int = 8765,
        host: str = "127.0.0.1",
        outbound_path: str = "chat-outbox.jsonl",
        queue_maxsize: int = 1000,
    ) -> None:
        super().__init__()
        self.port = port
        self.host = host
        self.outbound_path = outbound_path
        self._queue: Optional[asyncio.Queue] = None  # type: ignore[type-arg]
        self._queue_maxsize = queue_maxsize
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._consumer_task: Optional[asyncio.Task] = None  # type: ignore[type-arg]

    @property
    def running(self) -> bool:
        return self._server is not None and self._thread is not None and self._thread.is_alive()

    def endpoint(self) -> str:
        return f"http://{self.host}:{self.port}/chat"

    async def start(self) -> None:
        if self.running:
            return
        self._queue = asyncio.Queue(maxsize=self._queue_maxsize)
        loop = asyncio.get_event_loop()
        q = self._queue

        class Handler(_WebhookHandler):
            pass

        Handler.queue = q
        Handler.loop = loop

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="webhook-adapter",
            daemon=True,
        )
        self._thread.start()

        # 启动 queue consumer
        self._consumer_task = loop.create_task(self._consume())

        _log.info("webhook listening on %s", self.endpoint())

    async def stop(self) -> None:
        if self._consumer_task:
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except (asyncio.CancelledError, Exception):
                pass
            self._consumer_task = None
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        _log.info("webhook stopped")

    async def _consume(self) -> None:
        assert self._queue is not None
        while True:
            try:
                msg = await self._queue.get()
            except asyncio.CancelledError:
                break
            try:
                await self.on_incoming(msg)
            except Exception as e:
                _log.warning("on_incoming failed: %s", e)

    async def send(self, msg: OutgoingMessage) -> None:
        """v0: 写一行 JSONL 到 outbound_path。"""
        record = {
            "channel": msg.channel,
            "thread": msg.thread,
            "text": msg.text,
            "reply_to": msg.reply_to,
        }
        line = json.dumps(record, ensure_ascii=False)
        # 用 run_in_executor 避免阻塞 loop（IO 写入）
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _append_line, self.outbound_path, line)


def _append_line(path: str, line: str) -> None:
    """同步写入单行（executor 用）。"""
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# 测试辅助：直接构造一个能发请求的 handler，不开 server
def make_test_handler(queue: asyncio.Queue, loop: asyncio.AbstractEventLoop) -> type:
    """给单元测试用：构造一个不绑 server 的 Handler 类。"""
    class H(_WebhookHandler):
        pass
    H.queue = queue
    H.loop = loop
    return H

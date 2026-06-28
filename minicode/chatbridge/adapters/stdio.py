"""
StdioAdapter：从 stdin 读行作为入站消息，写到 stdout 作为出站。

用途：让用户在 REPL 里直接用 chat 子会话（不污染主 REPL 的命令空间）。
也方便调试：直接打 `hello` 就能测试。

实现细节：
- start() 启后台 task，循环用 run_in_executor 跑 input()
- 每行 → 构造 IncomingMessage → 调 on_incoming
- send() 直接 print 到 stdout
- stop() 置 _stop event + cancel task

v0 简化：input() 不能 cancel，会等用户输完当前行。
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Optional

from minicode.chatbridge.adapter import ChatAdapter, IncomingMessage, OutgoingMessage


_log = logging.getLogger("minicode.chatbridge.stdio")


class StdioAdapter(ChatAdapter):
    name = "stdio"

    def __init__(self, prompt: str = "chat> ", input_fn=None) -> None:
        super().__init__()
        self.prompt = prompt
        # 给测试用：可注入 input 函数
        self._input_fn = input_fn
        self._task: Optional[asyncio.Task] = None  # type: ignore[type-arg]
        self._stop = asyncio.Event()

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done() and not self._stop.is_set()

    async def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._consume())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _consume(self) -> None:
        loop = asyncio.get_event_loop()
        input_fn = self._input_fn or input
        while not self._stop.is_set():
            try:
                # 阻塞 stdin 读 → executor
                line = await loop.run_in_executor(None, lambda: input_fn(self.prompt))
            except (EOFError, KeyboardInterrupt):
                _log.info("stdio adapter: stdin closed")
                break
            except asyncio.CancelledError:
                break
            except Exception as e:
                _log.warning("stdio read error: %s", e)
                break

            line = (line or "").strip()
            if not line:
                continue
            if line.lower() in ("exit", "quit", "/exit", "/quit"):
                break

            msg = IncomingMessage(
                user="stdio",
                channel="console",
                thread="main",
                text=line,
                raw={"source": "stdin"},
            )
            try:
                await self.on_incoming(msg)
            except Exception as e:
                _log.warning("on_incoming failed: %s", e)

    async def send(self, msg: OutgoingMessage) -> None:
        # 写到 stdout（带 channel/thread 标记，方便多 adapter 共存时区分）
        sys.stdout.write(f"[chat:{msg.channel}/{msg.thread}] {msg.text}\n")
        sys.stdout.flush()

"""
测试 StdioAdapter：注入 input 函数模拟 stdin 行为。
"""

from __future__ import annotations

import asyncio
import io
import sys

import pytest

from minicode.chatbridge.adapters.stdio import StdioAdapter
from minicode.chatbridge.adapter import IncomingMessage, OutgoingMessage


class _FakeStdin:
    """模拟多次 input() 调用。"""

    def __init__(self, lines: list[str]):
        self.lines = list(lines)
        self.idx = 0

    def __call__(self, prompt: str = "") -> str:
        if self.idx >= len(self.lines):
            raise EOFError()
        line = self.lines[self.idx]
        self.idx += 1
        return line


class TestStdioAdapter:
    async def test_start_stop(self):
        a = StdioAdapter(input_fn=_FakeStdin([]))
        assert a.running is False
        await a.start()
        try:
            assert a.running is True
        finally:
            await a.stop()
        assert a.running is False

    async def test_reads_one_line(self):
        a = StdioAdapter(input_fn=_FakeStdin(["hello world"]))
        received: list[IncomingMessage] = []
        a.set_incoming_handler(lambda m: received.append(m) or asyncio.sleep(0))
        await a.start()
        # 等 consumer 处理
        for _ in range(40):
            if received:
                break
            await asyncio.sleep(0.05)
        await a.stop()
        assert len(received) == 1
        assert received[0].text == "hello world"
        assert received[0].user == "stdio"
        assert received[0].channel == "console"
        assert received[0].thread == "main"

    async def test_skips_empty_lines(self):
        a = StdioAdapter(input_fn=_FakeStdin(["", "  ", "real", "exit"]))
        received: list[IncomingMessage] = []
        a.set_incoming_handler(lambda m: received.append(m) or asyncio.sleep(0))
        await a.start()
        for _ in range(40):
            if len(received) >= 1 and not a.running:
                break
            await asyncio.sleep(0.05)
        await a.stop()
        # 只有 "real" 进入（空行被跳过，exit 终止）
        real_msgs = [m for m in received if m.text == "real"]
        assert len(real_msgs) == 1

    async def test_exit_stops(self):
        a = StdioAdapter(input_fn=_FakeStdin(["hi", "exit"]))
        await a.start()
        # 等消费完
        for _ in range(40):
            if not a.running:
                break
            await asyncio.sleep(0.05)
        assert a.running is False
        # 收尾
        try:
            await a.stop()
        except Exception:
            pass

    async def test_eof_stops(self):
        a = StdioAdapter(input_fn=_FakeStdin(["hi"]))  # 一行后 EOF
        await a.start()
        for _ in range(40):
            if not a.running:
                break
            await asyncio.sleep(0.05)
        assert a.running is False

    async def test_send_writes_to_stdout(self, capsys):
        a = StdioAdapter(input_fn=_FakeStdin([]))
        await a.send(OutgoingMessage(channel="#c", thread="t1", text="hi stdout"))
        out = capsys.readouterr().out
        assert "hi stdout" in out
        assert "[chat:#c/t1]" in out

    async def test_unknown_user_does_not_break(self):
        """on_incoming 没设 handler 也不应抛错。"""
        a = StdioAdapter(input_fn=_FakeStdin([]))
        await a.start()
        await a.stop()

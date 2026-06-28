"""memory.compact 单测（用 DemoModel 当 fake LLM）。"""
import asyncio
from pathlib import Path

import pytest

from minicode.memory.compact import compact_messages
from minicode.model.demo import DemoModel
from minicode.model.message import Message, Role, TextPart
from minicode.model.base import ModelInfo


def _user(t: str) -> Message:
    return Message(role=Role.USER, parts=[TextPart(text=t)])


def _make_demo_model() -> DemoModel:
    info = ModelInfo(id="demo", type="demo", base_url="(in-process)", model="demo-echo")
    return DemoModel(info, api_key="", extra={})


def test_compact_no_old_returns_unchanged():
    """历史 < keep_turns → old 为空 → 返回原 messages + 空 summary。"""
    async def run():
        m = _make_demo_model()
        msgs = [_user("a"), _user("b")]
        new_msgs, summary = await compact_messages(m, msgs, keep_turns=10)
        assert new_msgs == msgs
        assert summary == ""
    asyncio.run(run())


def test_compact_replaces_old_with_summary():
    """有 old 时 → 第一条是 summary message，后面是 recent。"""
    async def run():
        m = _make_demo_model()
        msgs = [_user(f"u{i}") for i in range(15)]
        new_msgs, summary = await compact_messages(m, msgs, keep_turns=3)
        # 1 (summary) + 3 (recent) = 4
        assert len(new_msgs) == 4
        # 第一条必须是 assistant
        assert new_msgs[0].role == Role.ASSISTANT
        # summary 文本非空
        assert summary != ""
        # 后 3 条是最后 3 个 user
        assert [mm.text() for mm in new_msgs[1:]] == ["u12", "u13", "u14"]
    asyncio.run(run())


def test_compact_empty_history():
    async def run():
        m = _make_demo_model()
        new_msgs, summary = await compact_messages(m, [], keep_turns=3)
        assert new_msgs == []
        assert summary == ""
    asyncio.run(run())

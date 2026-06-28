"""
测试 minicode.goal.judge：judge() + 辅助函数。

judge() 调 Model.complete()，用 mock model 替代避免真实 HTTP。
"""

from __future__ import annotations

from typing import AsyncIterator, List, Optional

from minicode.goal.judge import (
    _extract_json_object,
    _format_transcript,
    judge,
    render_verdict,
)
from minicode.goal.types import Verdict
from minicode.model.base import Model, ModelEvent, ModelInfo, ModelUsage
from minicode.model.message import (
    Message,
    Role,
    TextPart,
    ToolCallPart,
    ToolResultPart,
    ToolSchema,
)


# ─────────────────────────────────────────────────────
# Mock model
# ─────────────────────────────────────────────────────


class _MockModel(Model):
    """预设 stream 输出的 mock。"""

    def __init__(self, text: str, finish: str = "stop"):
        super().__init__(ModelInfo(id="mock", type="mock", base_url="", model="mock"))
        self._text = text
        self._finish = finish
        self.calls: List[List[Message]] = []
        self.last_system: Optional[str] = None

    async def stream(
        self,
        messages: List[Message],
        tools: Optional[List[ToolSchema]] = None,
        system: Optional[str] = None,
    ) -> AsyncIterator[ModelEvent]:
        self.calls.append(list(messages))
        self.last_system = system
        # 单 chunk
        yield ModelEvent(type="text_delta", text=self._text)
        yield ModelEvent(type="finish", finish_reason=self._finish)
        yield ModelEvent(
            type="usage",
            usage=ModelUsage(input_tokens=10, output_tokens=len(self._text) // 4),
        )


def _sample_transcript() -> List[Message]:
    return [
        Message.user("make tests pass"),
        Message(
            role=Role.ASSISTANT,
            parts=[
                TextPart(text="running tests..."),
                ToolCallPart(id="t1", name="bash", arguments={"command": "pytest"}),
            ],
        ),
        Message(
            role=Role.TOOL,
            parts=[ToolResultPart(tool_call_id="t1", content="10 passed", is_error=False)],
        ),
        Message.assistant_text("All 10 tests passed."),
    ]


# ─────────────────────────────────────────────────────
# _extract_json_object
# ─────────────────────────────────────────────────────


class TestExtractJson:
    def test_pure_json(self):
        assert _extract_json_object('{"ok": true}') == {"ok": True}

    def test_json_with_whitespace(self):
        assert _extract_json_object('  \n {"ok": false}\n  ') == {"ok": False}

    def test_json_embedded_in_text(self):
        text = 'preamble {"ok": true, "reason": "x"} tail'
        assert _extract_json_object(text) == {"ok": True, "reason": "x"}

    def test_empty(self):
        assert _extract_json_object("") is None
        assert _extract_json_object(None) is None

    def test_invalid_json(self):
        assert _extract_json_object("not json at all") is None
        assert _extract_json_object("{incomplete") is None

    def test_non_dict_json(self):
        """返回 list / string 都视作失败。"""
        assert _extract_json_object("[1,2,3]") is None
        assert _extract_json_object('"just a string"') is None

    def test_takes_first_object(self):
        """第一个完整 JSON 胜出（防模型写多个对象）。"""
        text = '{"ok": true} {"junk": }'
        # 第二个 invalid，但第一个合法 → 返回第一个
        assert _extract_json_object(text) == {"ok": True}


# ─────────────────────────────────────────────────────
# _format_transcript
# ─────────────────────────────────────────────────────


class TestFormatTranscript:
    def test_skips_system(self):
        msgs = [Message.system("be helpful"), Message.user("hi")]
        out = _format_transcript(msgs)
        assert "system" not in out.lower()
        assert "User: hi" in out

    def test_user_assistant_tool(self):
        out = _format_transcript(_sample_transcript())
        assert "User: make tests pass" in out
        assert "Assistant: running tests" in out
        assert "[tool_call:bash]" in out
        assert "Tool[t1]" in out
        assert "10 passed" in out

    def test_empty(self):
        assert _format_transcript([]) == "(empty transcript)"

    def test_truncates_long_text(self):
        long = "x" * 5000
        msgs = [Message.user(long)]
        out = _format_transcript(msgs, max_chars_per_msg=100)
        assert "[+" in out  # 截断标记
        assert len(out) < 1000

    def test_truncates_tool_result(self):
        long = "y" * 5000
        msgs = [
            Message(
                role=Role.TOOL,
                parts=[ToolResultPart(tool_call_id="t1", content=long)],
            ),
        ]
        out = _format_transcript(msgs, max_chars_per_msg=50)
        assert "Tool[t1]" in out
        assert len(out) < 500


# ─────────────────────────────────────────────────────
# judge()
# ─────────────────────────────────────────────────────


class TestJudge:
    async def test_satisfied(self):
        m = _MockModel('{"ok": true, "reason": "tests passed"}')
        v = await judge(m, "tests pass", _sample_transcript())
        assert v.ok is True
        assert v.impossible is False
        assert v.error is False
        assert "tests passed" in v.reason
        assert v.satisfied is True

    async def test_not_satisfied(self):
        m = _MockModel('{"ok": false, "reason": "no tests in transcript"}')
        v = await judge(m, "tests pass", _sample_transcript())
        assert v.ok is False
        assert v.satisfied is False
        assert v.error is False
        assert "no tests" in v.reason

    async def test_impossible(self):
        m = _MockModel('{"ok": false, "impossible": true, "reason": "self-contradictory"}')
        v = await judge(m, "do X and not-X", _sample_transcript())
        assert v.ok is False
        assert v.impossible is True
        assert v.satisfied is True  # impossible 也算 condition 完结
        assert v.error is False

    async def test_attempt_recorded(self):
        m = _MockModel('{"ok": true}')
        v = await judge(m, "x", _sample_transcript(), attempt=5)
        assert v.attempt == 5

    async def test_non_json_returns_error(self):
        m = _MockModel("I cannot decide; the world is uncertain.")
        v = await judge(m, "x", _sample_transcript())
        assert v.error is True
        assert v.ok is False
        assert "non-JSON" in v.reason or "not" in v.reason

    async def test_json_with_surrounding_text(self):
        """模型偶尔在 JSON 前后加废话。"""
        m = _MockModel('Sure! Here: {"ok": true, "reason": "yep"}')
        v = await judge(m, "x", _sample_transcript())
        assert v.ok is True
        assert v.error is False

    async def test_transcript_appears_in_call(self):
        m = _MockModel('{"ok": true}')
        await judge(m, "tests pass", _sample_transcript())
        # 至少有一次调用
        assert len(m.calls) == 1
        # user 消息含 transcript 标记
        user_msg = m.calls[0][0]
        assert user_msg.role == Role.USER
        assert "TRANSCRIPT" in user_msg.text()
        assert "tests pass" in user_msg.text()  # condition 也注入

    async def test_uses_judge_system(self):
        m = _MockModel('{"ok": true}')
        await judge(m, "x", _sample_transcript())
        assert m.last_system is not None
        assert "judge" in m.last_system.lower() or "评估器" in m.last_system

    async def test_empty_transcript(self):
        m = _MockModel('{"ok": false, "reason": "insufficient evidence in transcript"}')
        v = await judge(m, "tests pass", [])
        assert v.ok is False
        assert v.error is False

    async def test_timeout_returns_error(self):
        """超时 → error=True，不假装 ok。"""
        import asyncio

        class _SlowModel(_MockModel):
            async def stream(self, *args, **kwargs):
                await asyncio.sleep(10)
                yield ModelEvent(type="text_delta", text="x")
                yield ModelEvent(type="finish", finish_reason="stop")

        m = _SlowModel("ignored")
        v = await judge(m, "x", _sample_transcript(), timeout_s=0.05)
        assert v.error is True
        assert v.ok is False
        assert "timeout" in v.reason.lower()

    async def test_model_exception_returns_error(self):
        class _BrokenModel(_MockModel):
            async def stream(self, *args, **kwargs):
                raise RuntimeError("network down")
                yield  # noqa: never reached

        m = _BrokenModel("ignored")
        v = await judge(m, "x", _sample_transcript())
        assert v.error is True
        assert v.ok is False
        assert "network down" in v.reason
# ─────────────────────────────────────────────────────
# render_verdict
# ─────────────────────────────────────────────────────


class TestRenderVerdict:
    def test_ok(self):
        s = render_verdict(Verdict(ok=True, reason="done"))
        assert "satisfied" in s
        assert "done" in s

    def test_impossible(self):
        s = render_verdict(Verdict(impossible=True, reason="x"))
        assert "impossible" in s
        assert "x" in s

    def test_not_yet(self):
        s = render_verdict(Verdict(ok=False, reason="missing"))
        assert "not yet" in s

    def test_error(self):
        s = render_verdict(Verdict(error=True, reason="boom"))
        assert "error" in s
        assert "boom" in s

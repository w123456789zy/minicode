"""memory.truncation 单测。"""
from minicode.memory.truncation import (
    _split_into_turns,
    split_old_recent,
    truncate_messages,
)
from minicode.model.message import (
    Message,
    Role,
    TextPart,
    ToolCallPart,
    ToolResultPart,
)


def _user(t: str) -> Message:
    return Message(role=Role.USER, parts=[TextPart(text=t)])


def _assistant(t: str) -> Message:
    return Message(role=Role.ASSISTANT, parts=[TextPart(text=t)])


def _tool_result(tcid: str, content: str) -> Message:
    return Message(
        role=Role.TOOL,
        parts=[ToolResultPart(tool_call_id=tcid, content=content)],
    )


def test_split_into_turns_empty():
    assert _split_into_turns([]) == []


def test_split_into_turns_simple():
    """a/b/c/d 中，user 开新轮，assistant 跟随 user。"""
    msgs = [_user("a"), _assistant("b"), _user("c"), _assistant("d")]
    turns = _split_into_turns(msgs)
    # 2 轮：每轮 = user + 它后面的 assistant
    assert len(turns) == 2
    assert [m.text() for m in turns[0]] == ["a", "b"]
    assert [m.text() for m in turns[1]] == ["c", "d"]


def test_split_into_turns_tool_follows_assistant():
    """tool 消息应该和它的 assistant 归同一轮。"""
    msgs = [
        _user("a"),
        Message(
            role=Role.ASSISTANT,
            parts=[
                TextPart(text="let me check"),
                ToolCallPart(id="t1", name="read", arguments={"p": "x"}),
            ],
        ),
        _tool_result("t1", "file content"),
        _user("b"),
    ]
    turns = _split_into_turns(msgs)
    assert len(turns) == 2
    assert len(turns[0]) == 3  # user + assistant + tool
    assert len(turns[1]) == 1


def test_truncate_no_op():
    msgs = [_user("a"), _assistant("b")]
    out = truncate_messages(msgs, keep_turns=5)
    assert out == msgs


def test_truncate_drops_oldest():
    msgs = [_user(f"u{i}") for i in range(10)]
    out = truncate_messages(msgs, keep_turns=3)
    # 3 轮 = 3 条 user
    assert [m.text() for m in out] == ["u7", "u8", "u9"]


def test_truncate_keeps_tool_with_assistant():
    msgs = [
        _user("a"),
        Message(role=Role.ASSISTANT, parts=[ToolCallPart(id="t1", name="x", arguments={})]),
        _tool_result("t1", "ok"),
        _user("b"),
    ]
    out = truncate_messages(msgs, keep_turns=1)
    # 只保留最后 1 轮（user b）→ 1 条
    assert len(out) == 1
    assert out[0].text() == "b"


def test_split_old_recent():
    msgs = [_user(f"u{i}") for i in range(10)]
    old, recent = split_old_recent(msgs, keep_turns=3)
    assert [m.text() for m in old] == ["u0", "u1", "u2", "u3", "u4", "u5", "u6"]
    assert [m.text() for m in recent] == ["u7", "u8", "u9"]


def test_split_old_recent_no_old():
    msgs = [_user(f"u{i}") for i in range(3)]
    old, recent = split_old_recent(msgs, keep_turns=5)
    assert old == []
    assert recent == msgs


def test_truncate_zero_keeps():
    """keep_turns=0 视为非法输入：no-op，返回原 messages。"""
    msgs = [_user("a"), _assistant("b")]
    out = truncate_messages(msgs, keep_turns=0)
    assert out == msgs


def test_truncate_negative_keeps():
    """keep_turns<0 也视为 no-op。"""
    msgs = [_user("a"), _assistant("b")]
    out = truncate_messages(msgs, keep_turns=-5)
    assert out == msgs


def test_assistant_belongs_to_preceding_user_turn():
    """连续 N 个 user + M 个 assistant 应该被算作 N 轮（不是 N+M）。"""
    msgs = [
        _user("a"),
        _assistant("a-reply-1"),
        _assistant("a-reply-2"),
        _user("b"),
        _assistant("b-reply"),
    ]
    turns = _split_into_turns(msgs)
    assert len(turns) == 2
    assert [m.text() for m in turns[0]] == ["a", "a-reply-1", "a-reply-2"]
    assert [m.text() for m in turns[1]] == ["b", "b-reply"]

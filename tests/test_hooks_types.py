"""hooks.types 单测。"""
import json

from minicode.hooks.types import (
    Action,
    EventName,
    HookContext,
    HookEvent,
    HookResponse,
    parse_action,
    parse_response,
)


def test_event_to_dict():
    e = HookEvent.make(EventName.TOOL_CALL_BEFORE, "abc", tool="bash", args={"x": 1})
    d = e.to_dict()
    assert d["event"] == "tool_call_before"
    assert d["session_id"] == "abc"
    assert d["data"] == {"tool": "bash", "args": {"x": 1}}
    assert "timestamp" in d


def test_event_to_dict_chinese():
    e = HookEvent.make(EventName.USER_PROMPT_SUBMIT, "x", prompt="你好")
    d = e.to_dict()
    assert d["data"]["prompt"] == "你好"


def test_response_allow():
    r = HookResponse.allow()
    assert r.action == Action.ALLOW
    assert r.to_dict() == {"action": "allow"}


def test_response_deny():
    r = HookResponse.deny("危险")
    assert r.action == Action.DENY
    assert r.reason == "危险"
    assert r.to_dict() == {"action": "deny", "reason": "危险"}


def test_response_modify():
    r = HookResponse.modify({"prompt": "new"}, reason="r")
    assert r.action == Action.MODIFY
    d = r.to_dict()
    assert d["action"] == "modify"
    assert d["data"] == {"prompt": "new"}
    assert d["reason"] == "r"


def test_parse_response_none():
    assert parse_response(None).action == Action.ALLOW


def test_parse_response_empty_string():
    assert parse_response("").action == Action.ALLOW


def test_parse_response_invalid_string():
    assert parse_response("not json {").action == Action.ALLOW


def test_parse_response_dict():
    r = parse_response({"action": "deny", "reason": "x"})
    assert r.action == Action.DENY
    assert r.reason == "x"


def test_parse_response_json_string():
    r = parse_response('{"action": "modify", "data": {"a": 1}}')
    assert r.action == Action.MODIFY
    assert r.data == {"a": 1}


def test_parse_response_passthrough():
    r = HookResponse.deny("x")
    assert parse_response(r) is r


def test_parse_response_unknown_action():
    r = parse_response({"action": "garbage"})
    assert r.action == Action.ALLOW  # 非法 action → allow


def test_parse_action():
    assert parse_action("deny") == Action.DENY
    assert parse_action(Action.MODIFY) == Action.MODIFY
    assert parse_action(None) == Action.ALLOW
    assert parse_action("weird") == Action.ALLOW
    assert parse_action(123) == Action.ALLOW


def test_context_to_dict():
    from pathlib import Path
    ctx = HookContext(
        cwd=Path("/tmp"),
        project_root=Path("/tmp/proj"),
        minicode_version="0.1.0",
        env={"X": "1"},
    )
    d = ctx.to_dict()
    assert d["cwd"] == str(Path("/tmp"))
    assert d["minicode_version"] == "0.1.0"


def test_all_events_unique():
    assert len(set(EventName)) == len(list(EventName))

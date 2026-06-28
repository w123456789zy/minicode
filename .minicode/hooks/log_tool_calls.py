"""
Log all tool calls to .minicode/logs/tool_calls.jsonl.

事件：tool_call_before / tool_call_after / error
不做：拒绝/改写。纯记录。
"""
import json
import os
from datetime import datetime
from pathlib import Path

LOG_PATH = Path(".minicode/logs/tool_calls.jsonl")


async def hook(event: dict, context: dict) -> dict:
    name = event.get("event", "")
    data = event.get("data", {})
    if name not in ("tool_call_after", "error"):
        return {}

    # 懒建目录
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    record = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "event": name,
        "session_id": event.get("session_id", ""),
        "data": data,
    }
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return {}

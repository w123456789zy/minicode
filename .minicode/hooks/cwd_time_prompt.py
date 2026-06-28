"""
Augment user prompts with current working directory.

事件：user_prompt_submit
行为：把 cwd 拼到 prompt 前面（让 LLM 知道当前目录）。用 modify action。
"""
import os
from datetime import datetime


async def hook(event: dict, context: dict) -> dict:
    if event.get("event") != "user_prompt_submit":
        return {}

    prompt = event.get("data", {}).get("prompt", "")
    cwd = context.get("cwd", os.getcwd())
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    augmented = f"[cwd={cwd}  time={now}]\n\n{prompt}"
    return {
        "action": "modify",
        "data": {"prompt": augmented},
        "reason": "augmented by cwd_time_prompt hook",
    }

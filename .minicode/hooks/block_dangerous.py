"""
Block dangerous bash commands.

事件：tool_call_before
行为：检测到 rm -rf /、git push --force、sudo 等危险操作时返回 deny。
"""
import re

DANGEROUS_PATTERNS = [
    (r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)\s+/", "rm -rf on root path"),
    (r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)\s+~", "rm -rf on home directory"),
    (r"\bgit\s+push\s+.*--force", "force push"),
    (r"\bgit\s+push\s+-f\b", "force push (short flag)"),
    (r"\bsudo\s+", "uses sudo"),
    (r"\bdd\s+if=.*\s+of=/dev/", "dd to device"),
    (r":\(\)\s*\{.*\};:", "fork bomb"),
    (r"\bchmod\s+(-R\s+)?777\s+/", "chmod 777 on root"),
]


async def hook(event: dict, context: dict) -> dict:
    if event.get("event") != "tool_call_before":
        return {}

    data = event.get("data", {})
    tool = data.get("tool", "")
    args = data.get("args", {})

    # 只对 bash 起作用
    if tool != "bash":
        return {}

    command = args.get("command", "")
    if not command:
        return {}

    for pattern, reason in DANGEROUS_PATTERNS:
        if re.search(pattern, command):
            return {
                "action": "deny",
                "reason": f"dangerous command blocked: {reason} (matched: {pattern!r})",
            }

    return {}

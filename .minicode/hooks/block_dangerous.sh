#!/bin/bash
# block_dangerous.sh — 拦截危险命令
# stdin: {"event": "tool_call_before", "data": {"tool": "bash", "args": {"command": "..."}}}
# stdout: {"action": "deny", "reason": "..."} 或 {}

set -e

INPUT=$(cat)

# 只处理 tool_call_before
EVENT=$(echo "$INPUT" | python -c "import json,sys; print(json.loads(sys.stdin.read()).get('event',''))" 2>/dev/null || echo "")
if [ "$EVENT" != "tool_call_before" ]; then
    echo "{}"
    exit 0
fi

# 解析 command
COMMAND=$(echo "$INPUT" | python -c "
import json,sys
d = json.loads(sys.stdin.read())
data = d.get('data', {})
if data.get('tool') == 'bash':
    print(data.get('args', {}).get('command', ''))
" 2>/dev/null || echo "")

# 检测危险模式
if echo "$COMMAND" | grep -qE "rm\s+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)\s+/"; then
    echo '{"action": "deny", "reason": "rm -rf on root path (blocked by shell hook)"}'
    exit 0
fi

if echo "$COMMAND" | grep -qE "git\s+push\s+.*--force"; then
    echo '{"action": "deny", "reason": "force push (blocked by shell hook)"}'
    exit 0
fi

echo "{}"

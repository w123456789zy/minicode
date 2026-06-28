"""一个最小可用的 MCP server（stdio），用于端到端测试 minicode 的 stdio transport。

实现 JSON-RPC 2.0 over stdio，提供两个工具：
- ping(text: str) -> "pong: <text>"
- add(a: int, b: int) -> a + b
"""

import json
import sys


def handle(req):
    method = req.get("method")
    req_id = req.get("id")
    params = req.get("params") or {}

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "demo-mcp-server", "version": "0.0.1"},
            },
        }
    if method == "notifications/initialized":
        return None  # notif, no response
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": [
                    {
                        "name": "ping",
                        "description": "Echo back the input with a prefix",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"text": {"type": "string"}},
                            "required": ["text"],
                        },
                    },
                    {
                        "name": "add",
                        "description": "Add two integers",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "a": {"type": "integer"},
                                "b": {"type": "integer"},
                            },
                            "required": ["a", "b"],
                        },
                    },
                ]
            },
        }
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if name == "ping":
            text = args.get("text", "")
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"pong: {text}"}],
                    "isError": False,
                },
            }
        if name == "add":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": str(args["a"] + args["b"])}],
                    "isError": False,
                },
            }
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Unknown tool: {name}"},
        }
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Unknown method: {method}"},
    }


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = handle(req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()

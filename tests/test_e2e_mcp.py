"""端到端冒烟测试：用一个真实可用的 MCP server 验证 stdio transport。"""
import asyncio
import json
import sys
from pathlib import Path

# 让 minicode 可 import
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from minicode.tool.mcp import StdioServerConfig, McpClient, McpToolAdapter, _schema_to_params_model
from minicode.tool.base import ToolContext


async def main():
    server_path = str((Path(__file__).parent / "demo_mcp_server.py").resolve())
    cfg = StdioServerConfig(
        type="stdio",
        command=sys.executable,
        args=[server_path],
        env={},
    )
    client = McpClient({"demo": cfg}, cwd=Path.cwd())
    await client.connect_all()

    statuses = client.statuses()
    for s in statuses:
        print(f"[status] {s.name}: connected={s.connected}, tools={[t.name for t in s.tools]}")
        if s.error:
            print(f"  err: {s.error}")

    demo_status = statuses[0]
    assert demo_status.connected, f"demo server not connected: {demo_status.error}"
    tool_names = {t.name for t in demo_status.tools}
    assert tool_names == {"ping", "add"}, f"unexpected tools: {tool_names}"
    print("[ok] tools/list worked")

    # 通过 adapter 调 ping
    ping = next(t for t in demo_status.tools if t.name == "ping")
    adapter = McpToolAdapter("demo", ping, client)
    ctx = ToolContext(cwd=Path.cwd())
    res = await adapter.execute(
        adapter.parameters.model_validate({"text": "hello"}),
        ctx,
    )
    print(f"[ping] {res.title!r}: {res.output!r}")
    assert res.output == "pong: hello", f"unexpected: {res.output!r}"
    print("[ok] tools/call ping worked")

    # 直接调 add
    add = next(t for t in demo_status.tools if t.name == "add")
    adapter2 = McpToolAdapter("demo", add, client)
    res2 = await adapter2.execute(
        adapter2.parameters.model_validate({"a": 3, "b": 4}),
        ctx,
    )
    print(f"[add] {res2.output!r}")
    assert res2.output == "7", f"unexpected: {res2.output!r}"
    print("[ok] tools/call add worked")

    await client.close_all()
    print("\n[ALL OK]")


if __name__ == "__main__":
    asyncio.run(main())

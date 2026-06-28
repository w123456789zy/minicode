"""
MCP (Model Context Protocol) 客户端 + 工具适配器。

设计：
- 最小可工作版本，**不依赖官方 mcp SDK**。用 stdlib subprocess / httpx 实现两种 transport。
- 完整 JSON-RPC 2.0 协议（notifications 不发，但 initialize / tools/list / tools/call 都实现）
- 每个 MCP server 启动后调用 list_tools()，把每个工具包装成 minicode Tool
- 工具 id 形式：mcp_<server>_<tool>，便于 /mcp 分组查看

配置格式（.minicode/mcp.json）：

    {
      "mcpServers": {
        "fetch": {
          "type": "stdio",                    # stdio 或 http
          "command": "uvx",
          "args": ["mcp-server-fetch"],
          "env": {"KEY": "VALUE"}
        },
        "github": {
          "type": "http",
          "url": "https://api.github.com/mcp",
          "headers": {"Authorization": "Bearer xxx"}
        }
      }
    }

简化点：
- 不做 OAuth、不做 SSE fallback、不做 hot-reload
- connect/disconnect 同步管理生命周期
"""

import asyncio
import json
import os
import re
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import httpx
from pydantic import BaseModel, Field, ConfigDict

from minicode.tool.base import Tool, ToolContext, ToolResult, ToolKind


# ─────────────────────────────────────────────────────────────
# 配置
# ─────────────────────────────────────────────────────────────


class StdioServerConfig(BaseModel):
    type: str = Field("stdio", description="stdio 传输")
    command: str
    args: List[str] = Field(default_factory=list)
    env: Dict[str, str] = Field(default_factory=dict)


class HttpServerConfig(BaseModel):
    type: str = Field("http", description="http 传输（streamable-http）")
    url: str
    headers: Dict[str, str] = Field(default_factory=dict)


ServerConfig = Union[StdioServerConfig, HttpServerConfig]


class McpFile(BaseModel):
    """对应 mcp.json 顶层"""
    model_config = ConfigDict(extra="ignore")

    mcpServers: Dict[str, ServerConfig] = Field(default_factory=dict)


def load_mcp_config(paths: List[Path]) -> McpFile:
    """合并多个 mcp.json（项目级 > 全局级，后者优先覆盖前者同名 server）。"""
    merged = McpFile()
    for p in paths:
        if not p.is_file():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        file_obj = McpFile.model_validate(data)
        # 后加载的覆盖前面的（项目级晚加载时它会覆盖全局级同名）
        for name, cfg in file_obj.mcpServers.items():
            merged.mcpServers[name] = cfg
    return merged


# ─────────────────────────────────────────────────────────────
# JSON-RPC 2.0 传输
# ─────────────────────────────────────────────────────────────


class _JsonRpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(f"JSON-RPC error {code}: {message}")
        self.code = code
        self.message = message
        self.data = data


class _StdioTransport:
    """stdin/stdout JSON-RPC transport。"""

    def __init__(self, command: str, args: List[str], env: Dict[str, str], cwd: Optional[Path] = None):
        self._command = command
        self._args = args
        self._env = env
        self._cwd = str(cwd) if cwd else None
        self._proc: Optional[subprocess.Popen] = None
        self._lock = asyncio.Lock()
        self._next_id = 1

    async def start(self) -> None:
        full_env = os.environ.copy()
        full_env.update(self._env)
        self._proc = subprocess.Popen(
            [self._command, *self._args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=full_env,
            cwd=self._cwd,
            text=False,  # 用 bytes，自己处理
        )

    async def request(self, method: str, params: Any = None, timeout: float = 30.0) -> Any:
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError("stdio transport not started")
        async with self._lock:
            msg_id = self._next_id
            self._next_id += 1
            payload = {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}}
            data = (json.dumps(payload) + "\n").encode("utf-8")
            try:
                self._proc.stdin.write(data)
                self._proc.stdin.flush()
            except BrokenPipeError as e:
                raise RuntimeError(f"MCP server closed stdin: {e}") from e

            # 读一行响应
            line = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._proc.stdout.readline()
            )
            if not line:
                raise RuntimeError("MCP server closed connection (no response)")
            try:
                resp = json.loads(line)
            except json.JSONDecodeError as e:
                raise RuntimeError(f"Invalid JSON from MCP server: {e}; line={line!r}") from e
            if "error" in resp and resp["error"]:
                err = resp["error"]
                raise _JsonRpcError(err.get("code", -1), err.get("message", ""), err.get("data"))
            return resp.get("result")

    async def close(self) -> None:
        if self._proc is None:
            return
        try:
            if self._proc.stdin and not self._proc.stdin.closed:
                self._proc.stdin.close()
        except Exception:
            pass
        try:
            self._proc.terminate()
            self._proc.wait(timeout=3)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass
        self._proc = None


class _HttpTransport:
    """streamable-http transport：每个请求一次 HTTP POST。"""

    def __init__(self, url: str, headers: Dict[str, str]):
        self._url = url
        self._headers = dict(headers)
        self._client: Optional[httpx.AsyncClient] = None
        self._next_id = 1
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=self._url,
            headers={**self._headers, "Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
            timeout=httpx.Timeout(30.0),
        )

    async def request(self, method: str, params: Any = None, timeout: float = 30.0) -> Any:
        if self._client is None:
            raise RuntimeError("http transport not started")
        async with self._lock:
            msg_id = self._next_id
            self._next_id += 1
            payload = {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}}
            try:
                r = await self._client.post("", json=payload, timeout=timeout)
            except httpx.HTTPError as e:
                raise RuntimeError(f"HTTP error: {e}") from e
            if r.status_code != 200:
                raise RuntimeError(f"MCP HTTP {r.status_code}: {r.text[:200]}")
            data = r.json()
            if "error" in data and data["error"]:
                err = data["error"]
                raise _JsonRpcError(err.get("code", -1), err.get("message", ""), err.get("data"))
            return data.get("result")

    async def close(self) -> None:
        if self._client is None:
            return
        try:
            await self._client.aclose()
        finally:
            self._client = None


# ─────────────────────────────────────────────────────────────
# MCP 客户端
# ─────────────────────────────────────────────────────────────


@dataclass
class McpToolDescriptor:
    name: str
    description: str
    input_schema: Dict[str, Any]


@dataclass
class McpServerStatus:
    name: str
    config: ServerConfig
    connected: bool
    error: Optional[str] = None
    tools: List[McpToolDescriptor] = field(default_factory=list)


class McpClient:
    """管理一组 MCP server。

    启动时 connect_all() 依次连接；调用 list_tools / call_tool。
    关闭时 close_all() 清理资源。
    """

    def __init__(self, servers: Dict[str, ServerConfig], cwd: Optional[Path] = None):
        self._servers = servers
        self._cwd = cwd
        self._statuses: Dict[str, McpServerStatus] = {}
        self._transports: Dict[str, Union[_StdioTransport, _HttpTransport]] = {}

    async def connect_all(self, *, lazy: bool = False) -> None:
        """依次连接所有 server。失败不抛，仅记状态。"""
        for name, cfg in self._servers.items():
            self._statuses[name] = McpServerStatus(name=name, config=cfg, connected=False)
            try:
                await self._connect_one(name, cfg)
            except Exception as e:
                self._statuses[name].connected = False
                self._statuses[name].error = str(e)

    async def _connect_one(self, name: str, cfg: ServerConfig) -> None:
        if isinstance(cfg, StdioServerConfig):
            t = _StdioTransport(cfg.command, cfg.args, cfg.env, cwd=self._cwd)
            await t.start()
            self._transports[name] = t
        elif isinstance(cfg, HttpServerConfig):
            t = _HttpTransport(cfg.url, cfg.headers)
            await t.start()
            self._transports[name] = t
        else:
            raise ValueError(f"Unknown server config type: {cfg}")

        # initialize handshake
        t = self._transports[name]
        await t.request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "minicode", "version": "0.1.0"},
        })
        # initialized notification
        # (MCP 协议要求发一个 notifications/initialized 通知；我们用 request 假装也行，stdio transport 不严格校验)
        # 真实协议：transport.send({jsonrpc:"2.0",method:"notifications/initialized",params:{}})

        # list tools
        result = await t.request("tools/list", {})
        tools_raw = result.get("tools", []) if isinstance(result, dict) else []
        tools = []
        for td in tools_raw:
            tools.append(McpToolDescriptor(
                name=td.get("name", ""),
                description=td.get("description", ""),
                input_schema=td.get("inputSchema", {"type": "object", "properties": {}}),
            ))

        self._statuses[name].connected = True
        self._statuses[name].tools = tools

    async def close_all(self) -> None:
        for t in self._transports.values():
            try:
                await t.close()
            except Exception:
                pass
        self._transports.clear()

    def statuses(self) -> List[McpServerStatus]:
        return list(self._statuses.values())

    async def call_tool(self, server_name: str, tool_name: str, arguments: Dict[str, Any]) -> ToolResult:
        if server_name not in self._transports:
            raise RuntimeError(f"MCP server not connected: {server_name}")
        t = self._transports[server_name]
        result = await t.request("tools/call", {"name": tool_name, "arguments": arguments or {}})
        # MCP 工具返回结构：{"content": [{"type":"text","text":"..."}], "isError": false}
        if not isinstance(result, dict):
            return ToolResult(title=f"mcp:{server_name}.{tool_name}", output=str(result), metadata={})
        is_error = bool(result.get("isError", False))
        content_parts = result.get("content", []) or []
        text_chunks = [p.get("text", "") for p in content_parts if p.get("type") == "text"]
        output = "\n".join(text_chunks) or json.dumps(result, ensure_ascii=False)
        return ToolResult(
            title=f"mcp:{server_name}.{tool_name}",
            output=output,
            metadata={"is_error": is_error, "server": server_name, "tool": tool_name},
        )


# ─────────────────────────────────────────────────────────────
# 适配器：把 MCP 工具包成 minicode Tool
# ─────────────────────────────────────────────────────────────


def _sanitize(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", s)


def _schema_to_params_model(server: str, tool_name: str, schema: Dict[str, Any]) -> type[BaseModel]:
    """把 MCP 的 JSON Schema 转成 Pydantic 模型。"""
    from pydantic import create_model

    properties = schema.get("properties", {}) or {}
    required = set(schema.get("required", []) or [])

    fields: Dict[str, Any] = {}
    for name, spec in properties.items():
        py_type = _json_type_to_py(spec)
        if name in required:
            fields[name] = (py_type, Field(..., description=spec.get("description", "")))
        else:
            fields[name] = (Optional[py_type], Field(default=None, description=spec.get("description", "")))

    model_name = f"{server}_{tool_name}_Params"
    if not fields:
        # Pydantic 不允许空 fields；用 base model
        fields["_"] = (Optional[str], Field(default=None))
    return create_model(model_name, **fields)


def _json_type_to_py(spec: Dict[str, Any]) -> Any:
    t = spec.get("type")
    if t == "string":
        return str
    if t == "integer":
        return int
    if t == "number":
        return float
    if t == "boolean":
        return bool
    if t == "array":
        inner = spec.get("items", {})
        return List[_json_type_to_py(inner)]
    if t == "object":
        return Dict[str, Any]
    return Any


class McpToolAdapter(Tool):
    """把 MCP server 的一个工具包成 minicode Tool。

    id 形如 mcp_<server>_<tool>，description 来自 MCP 工具定义。
    """

    kind = ToolKind.MCP

    def __init__(self, server: str, descriptor: McpToolDescriptor, client: McpClient):
        self._server = server
        self._descriptor = descriptor
        self._client = client
        # 动态生成 Pydantic 参数模型
        self._params_model = _schema_to_params_model(server, descriptor.name, descriptor.input_schema)

    @property
    def id(self) -> str:
        return f"mcp_{_sanitize(self._server)}_{_sanitize(self._descriptor.name)}"

    @property
    def description(self) -> str:
        src = f"[mcp:{self._server}] "
        return src + (self._descriptor.description or self._descriptor.name)

    @property
    def parameters(self):
        return self._params_model

    @property
    def server(self) -> str:
        return self._server

    @property
    def descriptor(self) -> McpToolDescriptor:
        return self._descriptor

    async def execute(self, args: BaseModel, ctx: ToolContext) -> ToolResult:
        data = args.model_dump(exclude_none=True)
        return await self._client.call_tool(self._server, self._descriptor.name, data)

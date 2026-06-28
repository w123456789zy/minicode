"""
ToolRegistry：所有工具的统一入口。

启动时调用 `await registry.build()`，依次：
1. 装配内置工具（8 个：bash/read/write/edit/glob/grep/skill/subagent）
2. 扫描 .minicode/skills/ → 把 SkillLoader 注入到内置 SkillTool
3. 扫描 .minicode/agents/ → 把 SubagentLoader 注入到内置 SubagentTool
4. 解析 .minicode/mcp.json → 连接每个 MCP server → 把每个工具包成 McpToolAdapter
5. 加载 .minicode/hooks/ → 在 execute() 前后触发 hook
6. 同名冲突：mcp > builtin > skill_tool 自身（skill_tool 不会被覆盖）

之后用：
- registry.get(tool_id)              取单个工具
- registry.all()                     取所有工具（带过滤）
- registry.tools_for(model_id)       按 provider 过滤（v0 简化：只按 kind）
- registry.skills() / registry.subagents() / registry.mcp_statuses()  分组
- registry.execute()                 执行工具（会触发 hooks）
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from pydantic import BaseModel

from minicode.agent.loader import SubagentInfo, SubagentLoader
from minicode.hooks import (
    EventName,
    HookContext,
    HookDispatcher,
    HookEvent,
)
from minicode.paths import MinicodePaths
from minicode.permission import PermissionRequest, PermissionService
from minicode.tool.base import Tool, ToolDef, ToolContext, ToolResult, ToolKind
from minicode.tool.builtin import all_builtin_tools
from minicode.tool.builtin.skill import SkillTool
from minicode.tool.builtin.subagent import SubagentTool
from minicode.tool.mcp import (
    McpClient,
    McpFile,
    McpServerStatus,
    McpToolAdapter,
    load_mcp_config,
)
from minicode.tool.skill import SkillInfo, SkillLoader


_log = logging.getLogger("minicode.tool.registry")


@dataclass
class RegistrySummary:
    """build() 完成后的一份快照，方便 /tools 等命令展示。"""
    builtin_count: int
    mcp_servers: int
    mcp_connected: int
    skill_count: int
    subagent_count: int
    hook_count: int


class ToolRegistry:
    def __init__(
        self,
        paths: MinicodePaths,
        session_id: Optional[str] = None,
        permission_service: Optional[PermissionService] = None,
    ):
        self._paths = paths
        self._session_id = session_id or uuid.uuid4().hex[:8]
        self._defs: Dict[str, ToolDef] = {}
        self._loader: Optional[SkillLoader] = None
        self._agent_loader: Optional[SubagentLoader] = None
        self._mcp_client: Optional[McpClient] = None
        self._mcp_statuses: List[McpServerStatus] = []
        self._skill_tool: Optional[SkillTool] = None
        self._subagent_tool: Optional[SubagentTool] = None
        self._summary: Optional[RegistrySummary] = None
        # Hooks
        self._hooks: Optional[HookDispatcher] = None
        # Permission：per-session 的 always 集合 + 询问
        self._permission: Optional[PermissionService] = permission_service

    # ─────────────────────────────────────────
    # 启动
    # ─────────────────────────────────────────

    async def build(self) -> RegistrySummary:
        self._defs.clear()

        # 1. 内置工具
        for tool in all_builtin_tools():
            if isinstance(tool, SkillTool):
                self._skill_tool = tool
            elif isinstance(tool, SubagentTool):
                self._subagent_tool = tool
            self._register(tool)

        # 2. skill loader（不动 self._defs，因为 skill 本身不作为工具注册，只是 SkillTool 的数据源）
        self._loader = SkillLoader(self._paths.skills_dirs)
        self._loader.scan()
        if self._skill_tool is not None:
            self._skill_tool.set_loader(self._loader)

        # 3. subagent loader（同上：数据源，不注册工具）
        self._agent_loader = SubagentLoader(self._paths.agents_dirs)
        self._agent_loader.scan()
        if self._subagent_tool is not None:
            self._subagent_tool.set_loader(self._agent_loader)

        # 4. MCP 客户端
        cfg = load_mcp_config(self._paths.all_mcp_configs())
        self._mcp_client = McpClient(cfg.mcpServers, cwd=self._paths.project_root)
        await self._mcp_client.connect_all()
        self._mcp_statuses = self._mcp_client.statuses()
        for status in self._mcp_statuses:
            if not status.connected:
                continue
            for td in status.tools:
                adapter = McpToolAdapter(status.name, td, self._mcp_client)
                self._register(adapter)

        # 5. Hooks
        self._hooks = HookDispatcher(timeout_s=10.0, fail_open=True)
        if self._paths.hooks_dirs:
            self._hooks.load(self._paths.hooks_dirs)

        # 6. 总结
        self._summary = RegistrySummary(
            builtin_count=sum(1 for d in self._defs.values() if d.kind == ToolKind.BUILTIN),
            mcp_servers=len(self._mcp_statuses),
            mcp_connected=sum(1 for s in self._mcp_statuses if s.connected),
            skill_count=len(self._loader.all()),
            subagent_count=len(self._agent_loader.all()),
            hook_count=len(self._hooks.hooks()),
        )
        return self._summary

    def _register(self, tool: Tool) -> None:
        # MCP 工具 id 由 adapter 自己生成
        defn = tool.to_def()
        # 冲突策略：mcp > builtin > builtin（builtin 内的冲突取先到先得）
        if defn.id in self._defs and defn.kind != ToolKind.MCP:
            return
        self._defs[defn.id] = defn

    # ─────────────────────────────────────────
    # 查询
    # ─────────────────────────────────────────

    def all(self) -> List[ToolDef]:
        return list(self._defs.values())

    def get(self, tool_id: str) -> Optional[ToolDef]:
        return self._defs.get(tool_id)

    def by_kind(self, kind: ToolKind) -> List[ToolDef]:
        return [d for d in self._defs.values() if d.kind == kind]

    def skills(self) -> List[SkillInfo]:
        if self._loader is None:
            return []
        return self._loader.all()

    def subagents(self) -> List[SubagentInfo]:
        if self._agent_loader is None:
            return []
        return self._agent_loader.all()

    def mcp_statuses(self) -> List[McpServerStatus]:
        return list(self._mcp_statuses)

    def hooks_dispatcher(self) -> Optional[HookDispatcher]:
        return self._hooks

    def session_id(self) -> str:
        return self._session_id

    def summary(self) -> Optional[RegistrySummary]:
        return self._summary

    def hook_count(self) -> int:
        return len(self._hooks.hooks()) if self._hooks else 0

    def permission_service(self) -> Optional[PermissionService]:
        return self._permission

    def set_permission_service(self, svc: Optional[PermissionService]) -> None:
        self._permission = svc

    # ─────────────────────────────────────────
    # 执行（透传给 Tool，会触发 hooks）
    # ─────────────────────────────────────────

    async def execute(
        self,
        tool_id: str,
        args: Dict[str, Any],
        ctx: ToolContext,
    ) -> Any:
        # 1. 找 tool
        defn = self._defs.get(tool_id)
        if defn is None:
            return ToolResult(
                title=f"tool not found: {tool_id}",
                output=f"No tool registered with id {tool_id!r}",
                metadata={"error": True},
            )

        # 2. 参数校验（在 hook 之前：参数有问题就别调 hook）
        try:
            model = defn.parameters.model_validate(args)
        except Exception as e:
            return ToolResult(
                title=f"invalid args for {tool_id}",
                output=f"Invalid arguments: {e}",
                metadata={"error": True, "validation": True},
            )

        # 3. tool_call_before hook（可拒绝 / 改参数）
        if self._hooks is not None and self._hooks.hooks():
            hook_ctx = self._build_hook_ctx(ctx)
            call_id = uuid.uuid4().hex[:8]
            before_ev = HookEvent.make(
                EventName.TOOL_CALL_BEFORE,
                self._session_id,
                tool=tool_id,
                args=args,
                call_id=call_id,
            )
            before_result = await self._hooks.dispatch(before_ev, hook_ctx)
            if before_result.denied:
                return ToolResult(
                    title=f"tool call denied by hook: {tool_id}",
                    output=f"Denied: {before_result.reason or '(no reason given)'}",
                    metadata={"error": True, "denied_by_hook": True, "reason": before_result.reason},
                )
            if before_result.action.value == "modify" and isinstance(before_result.data, dict):
                new_args = before_result.data.get("args")
                if isinstance(new_args, dict):
                    args = new_args
                    # 重新校验被改过的参数
                    try:
                        model = defn.parameters.model_validate(args)
                    except Exception as e:
                        return ToolResult(
                            title=f"invalid args after hook for {tool_id}",
                            output=f"Hook-modified args invalid: {e}",
                            metadata={"error": True, "validation": True},
                        )

        # 3.5. 权限询问（用户没装 service → 默认放行；装了就问）
        if self._permission is not None:
            perm_req = PermissionRequest(
                tool_id=tool_id,
                args=args,
                context={"cwd": str(ctx.cwd), "session_id": self._session_id},
            )
            perm_result = await self._permission.request(perm_req)
            if not perm_result.action.is_allowed():
                return ToolResult(
                    title=f"tool call denied by user: {tool_id}",
                    output=f"Denied: {perm_result.reason or '(no reason given)'}",
                    metadata={
                        "error": True,
                        "denied_by_permission": True,
                        "reason": perm_result.reason,
                        "action": perm_result.action.value,
                    },
                )

        # 4. 真正执行
        try:
            result = await defn.tool.execute(model, ctx)
        except Exception as e:
            # 5. 异常 → error hook（v0: 不阻止，只通知）
            if self._hooks is not None and self._hooks.hooks():
                hook_ctx = self._build_hook_ctx(ctx)
                err_ev = HookEvent.make(
                    EventName.ERROR,
                    self._session_id,
                    exc_type=type(e).__name__,
                    exc_msg=str(e),
                    context={"tool": tool_id},
                )
                try:
                    await self._hooks.dispatch(err_ev, hook_ctx)
                except Exception:
                    pass
            raise

        # 6. tool_call_after hook（可改 output）
        if self._hooks is not None and self._hooks.hooks():
            hook_ctx = self._build_hook_ctx(ctx)
            after_ev = HookEvent.make(
                EventName.TOOL_CALL_AFTER,
                self._session_id,
                tool=tool_id,
                args=args,
                call_id=call_id if 'call_id' in dir() else "",
                output=result.output if isinstance(result.output, str) else str(result.output),
                error=bool(result.metadata and result.metadata.get("error")),
            )
            after_result = await self._hooks.dispatch(after_ev, hook_ctx)
            if after_result.action.value == "modify" and isinstance(after_result.data, dict):
                new_output = after_result.data.get("output")
                if isinstance(new_output, str):
                    # 替换 output；如果原来有 metadata 也可扩展
                    result = ToolResult(
                        title=result.title,
                        output=new_output,
                        metadata=result.metadata,
                    )

        return result

    def _build_hook_ctx(self, ctx: ToolContext) -> HookContext:
        import os
        import minicode
        return HookContext(
            cwd=ctx.cwd,
            project_root=self._paths.project_root,
            minicode_version=minicode.__version__,
            env=dict(os.environ),
        )

    # ─────────────────────────────────────────
    # 关闭
    # ─────────────────────────────────────────

    async def aclose(self) -> None:
        if self._mcp_client is not None:
            await self._mcp_client.close_all()
            self._mcp_client = None

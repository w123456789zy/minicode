"""
delegate_to_subagent 工具：让 LLM 把任务委派给一个 subagent。

调用方：父 LLM 在 ReAct 循环中输出 tool_call(name="delegate_to_subagent", args=...)
工具行为：
1. 用 args.name 从 SubagentLoader 找出 subagent definition
2. 用父 LLM 的同一个 model + subagent 的 system_prompt 启动嵌套 ReAct
3. subagent 可以调所有工具（跟父 LLM 一样）
4. subagent 的最终文本作为 tool result 返回给父 LLM

防递归：subagent 的 tool_schemas **不包含** delegate_to_subagent 自身，
防止 sub-subagent → sub-sub-subagent 的无限嵌套。

依赖注入：
- SubagentLoader (via set_loader) — 由 ToolRegistry.build() 注入
- model + tool_registry (via set_model / set_tool_registry) — 由 CLI 注入
"""

from __future__ import annotations

import json
from typing import Any, List, Optional, TYPE_CHECKING

from pydantic import BaseModel, Field

from minicode.tool.base import Tool, ToolContext, ToolResult, ToolKind

if TYPE_CHECKING:
    from minicode.agent.loader import SubagentLoader
    from minicode.model.base import Model


class SubagentParams(BaseModel):
    name: str = Field(..., description="subagent 名（不带 .md 后缀，对应 .minicode/agents/<name>.md 的 frontmatter.name）")
    task: str = Field(..., description="交给 subagent 做的任务描述，完整自包含（subagent 看不到父 LLM 的历史）")


class SubagentTool(Tool):
    """让 LLM 把任务委派给一个 subagent。"""

    kind = ToolKind.BUILTIN

    def __init__(self):
        self._loader: "SubagentLoader | None" = None
        self._model: "Model | None" = None
        self._tool_registry: Any = None  # ToolRegistry（避免循环导入）

    def set_loader(self, loader: "SubagentLoader") -> None:
        self._loader = loader

    def set_model(self, model: "Model") -> None:
        """注入 LLM model（CLI 启动时调用）。"""
        self._model = model

    def set_tool_registry(self, tool_registry) -> None:
        """注入 ToolRegistry（CLI 启动时调用），用于 subagent 的工具执行。"""
        self._tool_registry = tool_registry

    @property
    def id(self) -> str:
        return "delegate_to_subagent"

    @property
    def description(self) -> str:
        return (
            "把任务委派给一个 subagent。subagent 是在 .minicode/agents/<name>.md "
            "中定义的独立 LLM 上下文（自己的 system prompt）。subagent 能调用所有工具，"
            "完成后返回文本结果。\n"
            "适用：需要换人设专门处理的任务（code review、exploration、专门写测试 等）。"
        )

    @property
    def parameters(self):
        return SubagentParams

    async def execute(self, args: SubagentParams, ctx: ToolContext) -> ToolResult:
        if self._loader is None:
            return ToolResult(
                title="subagent loader not configured",
                output="SubagentLoader is not attached. This is a registry wiring bug.",
                metadata={"error": True},
            )
        if self._model is None:
            return ToolResult(
                title="subagent model not configured",
                output="No LLM model available for subagent. Please configure a model in .minicode/config.yaml.",
                metadata={"error": True},
            )
        if self._tool_registry is None:
            return ToolResult(
                title="subagent tool_registry not configured",
                output="ToolRegistry is not attached to SubagentTool. This is a CLI wiring bug.",
                metadata={"error": True},
            )

        info = self._loader.get(args.name)
        if info is None:
            available = [a.name for a in self._loader.all()]
            return ToolResult(
                title=f"subagent {args.name} not found",
                output=f"Available subagents: {', '.join(available) or '(none)'}",
                metadata={"error": True, "available": available},
            )

        from minicode.agent.runtime import run_subagent
        from minicode.model.message import ToolSchema

        def _to_schema(defn) -> ToolSchema:
            return ToolSchema(
                name=defn.id,
                description=defn.description,
                parameters=defn.json_schema(),
            )

        # 构建 subagent 的 tool 列表（排除自身，防递归）
        schemas = [
            _to_schema(d)
            for d in self._tool_registry.all()
            if d.id != "delegate_to_subagent"
        ]

        sub_ctx = ctx.sub()

        result = await run_subagent(
            model=self._model,
            subagent_system_prompt=info.system_prompt,
            task=args.task,
            tool_registry=self._tool_registry,
            ctx=sub_ctx,
            tool_schemas=schemas,
            max_iterations=10,
            context_limit=8000,
        )

        # 渲染输出
        text = result.text if hasattr(result, "text") else str(result)
        meta = {
            "iterations": getattr(result, "iterations", 0),
            "tool_calls_made": getattr(result, "tool_calls_made", []),
            "usage_input": getattr(result, "usage_input", 0),
            "usage_output": getattr(result, "usage_output", 0),
        }
        if getattr(result, "error", None):
            meta["error"] = result.error
            return ToolResult(
                title=f"subagent {info.name} errored",
                output=text + f"\n\n[error: {result.error}]",
                metadata=meta,
            )

        out = (
            f"<subagent_result name=\"{info.name}\">\n"
            f"{text or '(no text output)'}\n"
            f"</subagent_result>"
        )
        return ToolResult(
            title=f"subagent {info.name}: {len(meta['tool_calls_made'])} tool calls, {meta['iterations']} iters",
            output=out,
            metadata=meta,
        )

"""Glob 工具：按 glob 模式找文件。"""

from __future__ import annotations

import glob as _glob
import os
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from minicode.tool.base import Tool, ToolContext, ToolResult, ToolKind


class GlobParams(BaseModel):
    pattern: str = Field(..., description="glob 模式，例如 **/*.py、src/**/*.ts")
    cwd: Optional[str] = Field(None, description="搜索起点，默认 ctx.cwd")
    limit: int = Field(200, description="最多返回多少个文件，默认 200")


class GlobTool(Tool):
    """按 glob 模式找文件。

    简化：直接用 stdlib glob.glob(..., recursive=True)。
    """

    kind = ToolKind.BUILTIN

    @property
    def id(self) -> str:
        return "glob"

    @property
    def description(self) -> str:
        return "按 glob 模式找文件（支持 ** 递归）。"

    @property
    def parameters(self):
        return GlobParams

    async def execute(self, args: GlobParams, ctx: ToolContext) -> ToolResult:
        base = Path(args.cwd) if args.cwd else Path(ctx.cwd)
        if not base.is_absolute():
            base = Path(ctx.cwd) / base
        # 拼接 pattern 到 base
        full_pattern = str(base / args.pattern)
        matches = _glob.glob(full_pattern, recursive=True)
        # 排序 + 截断
        matches = sorted(set(matches))[: args.limit]
        if not matches:
            return ToolResult(
                title=f"glob {args.pattern}",
                output=f"No files matched: {full_pattern}",
                metadata={"count": 0},
            )
        body = "\n".join(matches)
        return ToolResult(
            title=f"glob {args.pattern}",
            output=body,
            metadata={"count": len(matches), "pattern": args.pattern, "base": str(base)},
        )

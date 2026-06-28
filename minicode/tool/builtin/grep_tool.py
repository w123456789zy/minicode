"""Grep 工具：按正则搜内容。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from minicode.tool.base import Tool, ToolContext, ToolResult, ToolKind


class GrepParams(BaseModel):
    pattern: str = Field(..., description="要搜索的正则表达式")
    cwd: Optional[str] = Field(None, description="搜索起点目录")
    include: str = Field("*", description="glob 过滤文件，默认全部")
    limit: int = Field(200, description="最多返回多少条匹配，默认 200")
    case_insensitive: bool = Field(False, description="是否忽略大小写")


class GrepTool(Tool):
    """简化版 grep。

    真实场景应该用 ripgrep；这里走 pathlib + 正则，方便零依赖。
    """

    kind = ToolKind.BUILTIN

    @property
    def id(self) -> str:
        return "grep"

    @property
    def description(self) -> str:
        return "在指定目录下按正则搜文件内容。返回 路径:行号:内容。"

    @property
    def parameters(self):
        return GrepParams

    async def execute(self, args: GrepParams, ctx: ToolContext) -> ToolResult:
        base = Path(args.cwd) if args.cwd else Path(ctx.cwd)
        if not base.is_absolute():
            base = Path(ctx.cwd) / base
        if not base.is_dir():
            return ToolResult(
                title=f"grep {args.pattern}",
                output=f"Not a directory: {base}",
                metadata={"error": True},
            )

        flags = re.IGNORECASE if args.case_insensitive else 0
        try:
            regex = re.compile(args.pattern, flags=flags)
        except re.error as e:
            return ToolResult(
                title=f"grep {args.pattern}",
                output=f"Invalid regex: {e}",
                metadata={"error": True},
            )

        results = []
        # 用 rglob 递归
        for path in base.rglob(args.include):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    results.append(f"{path}:{lineno}:{line.rstrip()}")
                    if len(results) >= args.limit:
                        break
            if len(results) >= args.limit:
                break

        if not results:
            return ToolResult(
                title=f"grep {args.pattern}",
                output=f"No matches in {base}",
                metadata={"count": 0},
            )
        body = "\n".join(results)
        return ToolResult(
            title=f"grep {args.pattern}",
            output=body,
            metadata={"count": len(results), "pattern": args.pattern},
        )

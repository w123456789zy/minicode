"""Read 工具：读取文件内容。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from minicode.tool.base import Tool, ToolContext, ToolResult, ToolKind


class ReadParams(BaseModel):
    file_path: str = Field(..., description="要读取的文件绝对路径")
    offset: Optional[int] = Field(None, description="从第几行开始读（1-indexed）")
    limit: Optional[int] = Field(None, description="最多读多少行，默认 2000")


class ReadTool(Tool):
    """读取文件内容。

    与 mimo code 行为类似：
    - 自动加 <file> 包裹
    - offset / limit 用于分页
    - 大文件走截断（简化版：直接截到 limit 行）
    """

    kind = ToolKind.BUILTIN
    DEFAULT_LIMIT = 2000

    @property
    def id(self) -> str:
        return "read"

    @property
    def description(self) -> str:
        return "读取文件内容（可指定行范围）。"

    @property
    def parameters(self):
        return ReadParams

    async def execute(self, args: ReadParams, ctx: ToolContext) -> ToolResult:
        p = Path(args.file_path)
        if not p.is_absolute():
            p = Path(ctx.cwd) / p
        if not p.exists():
            return ToolResult(
                title=f"read {p.name}",
                output=f"File not found: {p}",
                metadata={"exists": False},
            )
        if p.is_dir():
            return ToolResult(
                title=f"read {p.name}",
                output=f"Is a directory: {p}",
                metadata={"is_dir": True},
            )

        try:
            content = p.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return ToolResult(
                title=f"read {p.name}",
                output=f"Failed to read: {e}",
                metadata={"error": str(e)},
            )

        lines = content.splitlines(keepends=True)
        start = (args.offset or 1) - 1
        end = start + (args.limit or self.DEFAULT_LIMIT)
        page = lines[start:end]
        truncated = len(lines) > len(page) + start

        body = "".join(page)
        if truncated:
            body += f"\n... (truncated, total {len(lines)} lines)\n"

        return ToolResult(
            title=f"read {p.name}",
            output=f"<file path={p}>\n{body}</file>",
            metadata={"lines": len(page), "truncated": truncated, "path": str(p)},
        )

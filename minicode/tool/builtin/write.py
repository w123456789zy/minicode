"""Write 工具：写入文件（覆盖）。"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from minicode.tool.base import Tool, ToolContext, ToolResult, ToolKind


class WriteParams(BaseModel):
    file_path: str = Field(..., description="要写入的文件绝对路径")
    content: str = Field(..., description="要写入的完整内容")


class WriteTool(Tool):
    """写文件（覆盖整个文件）。

    自动创建父目录。
    """

    kind = ToolKind.BUILTIN

    @property
    def id(self) -> str:
        return "write"

    @property
    def description(self) -> str:
        return "把内容写入文件（覆盖）。会自动创建父目录。"

    @property
    def parameters(self):
        return WriteParams

    async def execute(self, args: WriteParams, ctx: ToolContext) -> ToolResult:
        p = Path(args.file_path)
        if not p.is_absolute():
            p = Path(ctx.cwd) / p
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(args.content, encoding="utf-8")
        return ToolResult(
            title=f"wrote {p.name}",
            output=f"Wrote {len(args.content)} bytes to {p}",
            metadata={"path": str(p), "bytes": len(args.content)},
        )

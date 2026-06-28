"""Edit 工具：精确替换文件中的字符串。"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from minicode.tool.base import Tool, ToolContext, ToolResult, ToolKind


class EditParams(BaseModel):
    file_path: str = Field(..., description="要修改的文件绝对路径")
    old_string: str = Field(..., description="要替换的原文（必须完全匹配，包括空白）")
    new_string: str = Field(..., description="替换后的内容")
    replace_all: bool = Field(False, description="是否替换所有出现的位置，默认只替换第一个")


class EditTool(Tool):
    """字符串精确替换。

    行为：
    - old_string 必须出现且唯一（除非 replace_all=True）
    - 写回原文件
    """

    kind = ToolKind.BUILTIN

    @property
    def id(self) -> str:
        return "edit"

    @property
    def description(self) -> str:
        return "精确替换文件中的字符串（old_string → new_string）。"

    @property
    def parameters(self):
        return EditParams

    async def execute(self, args: EditParams, ctx: ToolContext) -> ToolResult:
        p = Path(args.file_path)
        if not p.is_absolute():
            p = Path(ctx.cwd) / p
        if not p.exists():
            return ToolResult(
                title=f"edit {p.name}",
                output=f"File not found: {p}",
                metadata={"exists": False},
            )

        text = p.read_text(encoding="utf-8", errors="replace")
        count = text.count(args.old_string)
        if count == 0:
            return ToolResult(
                title=f"edit {p.name}",
                output=f"old_string not found in {p}",
                metadata={"found": 0},
            )
        if count > 1 and not args.replace_all:
            return ToolResult(
                title=f"edit {p.name}",
                output=f"old_string appears {count} times, must be unique. Pass replace_all=True to replace all.",
                metadata={"found": count, "ambiguous": True},
            )

        if args.replace_all:
            new_text = text.replace(args.old_string, args.new_string)
        else:
            new_text = text.replace(args.old_string, args.new_string, 1)

        p.write_text(new_text, encoding="utf-8")
        return ToolResult(
            title=f"edited {p.name}",
            output=f"Replaced {count if args.replace_all else 1} occurrence(s) in {p}",
            metadata={"path": str(p), "replaced": count if args.replace_all else 1},
        )

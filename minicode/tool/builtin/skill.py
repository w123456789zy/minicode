"""Skill 工具：暴露给 LLM 的 skill loader。

说明：
- 这个工具是 builtin（ToolKind.BUILTIN），它让 LLM 能按 name 加载一个 skill
- skill 本身（.minicode/skills/<name>/SKILL.md）由 minicode.tool.skill.SkillLoader 负责扫描
- registry 在 build 时把 SkillLoader 注入到本工具的构造函数里
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from minicode.tool.base import Tool, ToolContext, ToolResult, ToolKind

if TYPE_CHECKING:
    from minicode.tool.skill import SkillLoader


class SkillParams(BaseModel):
    name: str = Field(..., description="要加载的 skill 名，对应 .minicode/skills/<name>/")


class SkillTool(Tool):
    """让 LLM 通过 name 加载一个 skill 的内容。"""

    kind = ToolKind.BUILTIN

    def __init__(self, loader: "SkillLoader | None" = None):
        self._loader = loader

    def set_loader(self, loader: "SkillLoader") -> None:
        self._loader = loader

    @property
    def id(self) -> str:
        return "skill"

    @property
    def description(self) -> str:
        return "加载一个 .minicode/skills/ 下的 skill，返回完整内容（带 frontmatter）。"

    @property
    def parameters(self):
        return SkillParams

    async def execute(self, args: SkillParams, ctx: ToolContext) -> ToolResult:
        if self._loader is None:
            return ToolResult(
                title="skill loader not configured",
                output="SkillLoader is not attached. This is a registry wiring bug.",
                metadata={"error": True},
            )
        info = self._loader.get(args.name)
        if info is None:
            available = [s.name for s in self._loader.all()]
            return ToolResult(
                title=f"skill {args.name} not found",
                output=f"Available skills: {', '.join(available) or '(none)'}",
                metadata={"error": True, "available": available},
            )

        body = (
            f"<skill_content name=\"{info.name}\">\n"
            f"# Skill: {info.name}\n\n"
            f"{info.content.strip()}\n\n"
            f"Base directory: {info.location.parent}\n"
            f"</skill_content>"
        )
        return ToolResult(
            title=f"loaded skill: {info.name}",
            output=body,
            metadata={"name": info.name, "path": str(info.location)},
        )

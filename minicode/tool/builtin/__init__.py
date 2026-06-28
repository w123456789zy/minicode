"""内置工具集合。"""

from minicode.tool.builtin.bash import BashTool, BashParams
from minicode.tool.builtin.read import ReadTool, ReadParams
from minicode.tool.builtin.write import WriteTool, WriteParams
from minicode.tool.builtin.edit import EditTool, EditParams
from minicode.tool.builtin.glob_tool import GlobTool, GlobParams
from minicode.tool.builtin.grep_tool import GrepTool, GrepParams
from minicode.tool.builtin.skill import SkillTool, SkillParams
from minicode.tool.builtin.subagent import SubagentTool, SubagentParams

__all__ = [
    "BashTool", "BashParams",
    "ReadTool", "ReadParams",
    "WriteTool", "WriteParams",
    "EditTool", "EditParams",
    "GlobTool", "GlobParams",
    "GrepTool", "GrepParams",
    "SkillTool", "SkillParams",
    "SubagentTool", "SubagentParams",
]


def all_builtin_tools():
    """返回所有内置工具实例（按固定顺序）。"""
    return [
        BashTool(),
        ReadTool(),
        WriteTool(),
        EditTool(),
        GlobTool(),
        GrepTool(),
        SkillTool(),
        SubagentTool(),
    ]

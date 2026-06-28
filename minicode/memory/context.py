"""
把 AGENTS.md / rules/*.md 拼成最终 system 字符串。

格式：
    You are minicode, ...

    # AGENTS.md (<rel_path>)
    <content>

    # Project Rules

    ## <rule_name> (<rel_path>)
    <content>

    ## <rule_name_2> (<rel_path>)
    <content>

约定：
- AGENTS.md 在最前（核心身份 / 业务背景）
- rules/*.md 在其后（行为约束）
- 每个 rule 加相对路径 header，方便 LLM 知道来源（调试 / 用户问"哪条规则说的"时有用）
- 文件为空 → 整段省略（不输出空 header）
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from minicode.memory.loaders import AgentsDoc, RuleFile


_BASE_SYSTEM = (
    "You are minicode, a terminal-native AI coding assistant. "
    "You help the user read, edit, and reason about code in the current project.\n"
    "Be concise. Prefer showing concrete file paths and code over long explanations. "
    "When a tool is available for a task, prefer calling the tool over answering from memory."
)


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def assemble_system(
    agents: Optional[AgentsDoc],
    rules: List[RuleFile],
    project_root: Path,
    base: str = _BASE_SYSTEM,
) -> str:
    """拼出最终 system 字符串。"""
    parts: List[str] = [base]

    if agents is not None and not agents.is_empty():
        parts.append("")
        parts.append("# Project Context (.minicode/AGENTS.md)")
        parts.append(agents.content.strip())

    if rules:
        nonempty = [r for r in rules if not r.is_empty()]
        if nonempty:
            parts.append("")
            parts.append("# Project Rules")
            for r in nonempty:
                parts.append("")
                parts.append(f"## {r.name} (.minicode/rules/{r.name}.md)")
                parts.append(r.content.strip())

    return "\n".join(parts)

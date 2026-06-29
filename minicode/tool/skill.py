"""
Skill 加载器。

约定（参照 mimo code 的 skill 格式，但简化为最小可工作版本）：

    .minicode/skills/
    └── code-review/
        └── SKILL.md        # 必须有 frontmatter

    ---
    name: code-review
    description: 严格审查代码改动并给出改进建议
    ---

    # 工作流
    1. ...

frontmatter 字段：
- name        必填，skill 唯一名（也是 LLM 调用时的参数）
- description 必填，列在 /skills 时显示

不实现：
- 嵌套 skill 目录、.opencode/.claude 兼容、远程拉取、hash 缓存
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import yaml


@dataclass
class SkillInfo:
    name: str
    description: str
    location: Path
    content: str

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SkillInfo name={self.name!r} at {self.location}>"


_FRONTMATTER_RE = re.compile(
    r"^---\s*\n(?P<front>.*?)\n---\s*\n?(?P<body>.*)$",
    re.DOTALL,
)


def _parse_skill_md(path: Path) -> Optional[SkillInfo]:
    text = path.read_text(encoding="utf-8", errors="replace")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return None
    try:
        front = yaml.safe_load(m.group("front")) or {}
    except yaml.YAMLError:
        return None
    if not isinstance(front, dict):
        return None
    name = front.get("name")
    description = front.get("description", "")
    if not isinstance(name, str) or not name:
        return None
    if not isinstance(description, str):
        description = ""
    return SkillInfo(
        name=name,
        description=description,
        location=path,
        content=m.group("body").strip(),
    )


class SkillLoader:
    """从一组 skills 目录扫描并解析 skill。

    用法：
        loader = SkillLoader([Path(".minicode/skills"), Path("~/.minicode/skills")])
        loader.scan()
        for s in loader.all():
            print(s.name, s.description)
    """

    def __init__(self, dirs: List[Path]):
        self._dirs: List[Path] = [d for d in dirs if d is not None]
        self._skills: Dict[str, SkillInfo] = {}

    def scan(self) -> None:
        """重新扫描所有目录，重新填充 _skills。"""
        self._skills.clear()
        for root in self._dirs:
            if not root.is_dir():
                continue
            # 支持两种布局：
            #   <root>/<name>/SKILL.md
            #   <root>/SKILL.md
            for skill_md in root.rglob("SKILL.md"):
                info = _parse_skill_md(skill_md)
                if info is None:
                    continue
                # 后扫描到的同名 skill 覆盖前面的（项目级 > 全局级）
                self._skills[info.name] = info

    def get(self, name: str) -> Optional[SkillInfo]:
        return self._skills.get(name)

    def all(self) -> List[SkillInfo]:
        return sorted(self._skills.values(), key=lambda s: s.name)

"""
自定义命令加载器。

约定（参照 mimo code 的 commands 格式）：

    .minicode/commands/
    ├── review.md
    ├── fix.md
    └── deploy.md

每个 .md 文件定义一个命令，使用 YAML frontmatter + Markdown 内容：

    ---
    description: 代码审查命令
    ---

    请审查以下代码改动，给出改进建议。
    用户输入：$ARGUMENTS

frontmatter 字段：
- description  可选，命令描述（用于 /help 和补全提示）

命令名 = 文件名（不含 .md 后缀），例如 review.md → /review
内容支持 $ARGUMENTS 占位符，运行时替换为用户在命令后输入的参数。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import yaml


@dataclass
class CommandInfo:
    """一个自定义命令的元数据。"""
    name: str               # 命令名（不含 / 前缀），如 "review"
    description: str        # 简短描述
    location: Path          # 源文件路径
    content: str            # prompt 模板（含 $ARGUMENTS 占位符）

    @property
    def slash_name(self) -> str:
        return f"/{self.name}"


_FRONTMATTER_RE = re.compile(
    r"^---\s*\n(?P<front>.*?)\n---\s*\n?(?P<body>.*)$",
    re.DOTALL,
)


def _parse_command_md(path: Path) -> Optional[CommandInfo]:
    """解析一个 .md 命令文件，返回 CommandInfo 或 None。"""
    text = path.read_text(encoding="utf-8", errors="replace")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        # 没有 frontmatter 也可以：整个文件内容就是 prompt 模板
        return CommandInfo(
            name=path.stem,
            description="",
            location=path,
            content=text.strip(),
        )
    try:
        front = yaml.safe_load(m.group("front")) or {}
    except yaml.YAMLError:
        front = {}
    if not isinstance(front, dict):
        front = {}
    description = front.get("description", "")
    if not isinstance(description, str):
        description = ""
    return CommandInfo(
        name=path.stem,
        description=description,
        location=path,
        content=m.group("body").strip(),
    )


class CommandLoader:
    """从一组 commands 目录扫描并解析自定义命令。

    用法：
        loader = CommandLoader([Path(".minicode/commands"), Path("~/.minicode/commands")])
        loader.scan()
        for cmd in loader.all():
            print(cmd.slash_name, cmd.description)
    """

    def __init__(self, dirs: List[Path]):
        self._dirs: List[Path] = [d for d in dirs if d is not None]
        self._commands: Dict[str, CommandInfo] = {}

    def scan(self) -> None:
        """重新扫描所有目录。"""
        self._commands.clear()
        for root in self._dirs:
            if not root.is_dir():
                continue
            for md_file in sorted(root.glob("*.md")):
                info = _parse_command_md(md_file)
                if info is None:
                    continue
                # 后扫描到的覆盖前面的（项目级 > 全局级）
                self._commands[info.name] = info

    def get(self, name: str) -> Optional[CommandInfo]:
        return self._commands.get(name)

    def all(self) -> List[CommandInfo]:
        return sorted(self._commands.values(), key=lambda c: c.name)

    def render(self, name: str, arguments: str = "") -> Optional[str]:
        """渲染命令模板：把 $ARGUMENTS 替换为用户输入，返回完整 prompt。"""
        cmd = self._commands.get(name)
        if cmd is None:
            return None
        return cmd.content.replace("$ARGUMENTS", arguments)
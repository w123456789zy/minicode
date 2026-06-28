"""
Subagent 加载器。

约定（与 skill 格式对齐）：

    .minicode/agents/
    ├── code-reviewer.md
    │   ---
    │   name: code-reviewer
    │   description: 严格审查代码改动并给出改进建议
    │   ---
    │
    │   # 系统提示
    │   你是 code reviewer ...
    └── explorer.md
        ...

frontmatter 字段：
- name        必填，subagent 唯一名（也是 LLM 调用时的参数）
- description 必填，列在 /agents 时显示 + 作为 tool description

与 skill 的关键区别：
- skill 是"加载文档内容"返回给 LLM，让 LLM 接下来按文档做事
- subagent 是"启动一个独立 LLM 上下文"做任务，结果返回给父 LLM
- subagent 的 body 是**系统提示**（告诉 subagent 自己是谁、怎么做），不是给父 LLM 看的文档
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import yaml


@dataclass
class SubagentInfo:
    name: str
    description: str
    location: Path
    system_prompt: str   # body 部分，subagent 启动时作为 system message

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SubagentInfo name={self.name!r} at {self.location}>"


_FRONTMATTER_RE = re.compile(
    r"^---\s*\n(?P<front>.*?)\n---\s*\n?(?P<body>.*)$",
    re.DOTALL,
)


def _parse_agent_md(path: Path) -> Optional[SubagentInfo]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
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
    body = m.group("body").strip()
    if not body:
        # body 空 → 这个 subagent 没灵魂。仍然加载（用户可能故意只占位），
        # 但 system prompt 退化为 description，让 LLM 至少知道这是干啥的
        body = f"You are a specialized assistant. {description}"
    return SubagentInfo(
        name=name,
        description=description,
        location=path,
        system_prompt=body,
    )


class SubagentLoader:
    """从一组 agents 目录扫描并解析 subagent。"""

    def __init__(self, dirs: List[Path]):
        self._dirs: List[Path] = [d for d in dirs if d is not None]
        self._agents: Dict[str, SubagentInfo] = {}

    def scan(self) -> None:
        """重新扫描所有目录，重新填充 _agents。

        约定：每个 agents 目录下放一堆 `*.md`（**平铺**，不像 skill 要嵌套目录）。
        同名冲突：项目级 > 全局级（项目级后扫，但优先保留）。
        """
        self._agents.clear()
        for root in self._dirs:
            if not root.is_dir():
                continue
            for md in root.glob("*.md"):
                if not md.is_file():
                    continue
                info = _parse_agent_md(md)
                if info is None:
                    continue
                # 后扫描到同名 → 覆盖（项目级目录通常在全局之后）
                self._agents[info.name] = info

    def get(self, name: str) -> Optional[SubagentInfo]:
        return self._agents.get(name)

    def all(self) -> List[SubagentInfo]:
        return sorted(self._agents.values(), key=lambda a: a.name)


def load_subagents(dirs: List[Path]) -> SubagentLoader:
    """便捷函数：构造 loader + scan + 返回。"""
    loader = SubagentLoader(dirs)
    loader.scan()
    return loader

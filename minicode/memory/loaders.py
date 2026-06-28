"""
记忆加载器。

来源（按你的设计）：
1. `.minicode/AGENTS.md`           项目级核心诉求（系统身份 / 业务背景）
2. `.minicode/rules/*.md`          项目级行为规则（编码风格 / 不许做的事）

不读取：
- 全局 ~/.minicode/AGENTS.md    （v0 范围外）
- 项目根目录 AGENTS.md            （v0 范围外；用项目内 .minicode 收敛）

设计要点：
- 全部不存在 → 返回空，不报错
- 文件存在但读失败 / 内容不是字符串 → 跳过 + 在 stderr 打 warning
- 按文件名排序加载，保证可复现
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


@dataclass
class AgentsDoc:
    """AGENTS.md 的内容。"""
    path: Path
    content: str

    def is_empty(self) -> bool:
        return not self.content.strip()


@dataclass
class RuleFile:
    """一条 rule 文件。"""
    name: str             # 文件名（不含 .md），用于展示
    path: Path
    content: str

    def is_empty(self) -> bool:
        return not self.content.strip()


def _read_text_safely(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        print(f"[memory] warn: failed to read {path}: {e}", file=sys.stderr)
        return None


# ─────────────────────────────────────────────────────────────
# AGENTS.md
# ─────────────────────────────────────────────────────────────


def load_agents_md(project_dir: Path) -> Optional[AgentsDoc]:
    """读 `<project_dir>/AGENTS.md`，不存在返回 None。"""
    p = project_dir / "AGENTS.md"
    if not p.is_file():
        return None
    content = _read_text_safely(p)
    if content is None:
        return None
    return AgentsDoc(path=p, content=content)


# ─────────────────────────────────────────────────────────────
# rules/*.md
# ─────────────────────────────────────────────────────────────


def load_rules(project_dir: Path) -> List[RuleFile]:
    """读 `<project_dir>/rules/*.md`，按文件名排序。空目录返回 []。

    空文件（只有空白）会被跳过——加载空 rule 没意义。
    """
    rules_dir = project_dir / "rules"
    if not rules_dir.is_dir():
        return []
    out: List[RuleFile] = []
    for p in sorted(rules_dir.glob("*.md")):
        if not p.is_file():
            continue
        content = _read_text_safely(p)
        if content is None:
            continue
        if not content.strip():
            # 空 rule 文件：跳过（避免污染 system prompt）
            continue
        out.append(RuleFile(
            name=p.stem,
            path=p,
            content=content,
        ))
    return out

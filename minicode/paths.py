"""
.minicode 路径解析。

约定：
- `.minicode/` 放在项目根目录（cwd 所在目录）下。
  - `.minicode/skills/<name>/SKILL.md`   skill 文件
  - `.minicode/agents/<name>.md`         subagent 定义
  - `.minicode/hooks/*.py|*.sh`          hook 脚本（自动发现）
  - `.minicode/mcp.json`                 MCP 服务器配置
  - `.minicode/commands/*.md`           自定义命令
  - `.minicode/config.yaml`              LLM provider 配置
- 也支持 `~/.minicode/` 作为全局兜底。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


@dataclass(frozen=True)
class MinicodePaths:
    """一组解析好的路径。

    多个候选目录按"项目 > 全局"顺序合并：
    - 读取时：依次扫描，返回首个存在的目录
    - 写入时：默认写到项目级目录
    """

    project_root: Path             # 启动时所在的目录
    project_dir: Path              # <project_root>/.minicode
    global_dir: Path               # ~/.minicode
    skills_dirs: List[Path]        # 扫描 skill 的所有目录（项目级 + 全局级）
    agents_dirs: List[Path]        # 扫描 subagent 的所有目录
    hooks_dirs: List[Path]         # 扫描 hook 的所有目录
    commands_dirs: List[Path]      # 扫描自定义命令的所有目录（项目级 + 全局级）
    mcp_config: Path               # 项目级 mcp.json（不存在不代表错）
    global_mcp_config: Path        # 全局 mcp.json
    config_yaml: Path              # 项目级 config.yaml（LLM provider 配置）
    global_config_yaml: Path       # 全局 config.yaml
    history_dir: Path              # 项目级 history/（会话持久化）

    @staticmethod
    def discover(start: Optional[Path] = None) -> "MinicodePaths":
        """从 start（默认 cwd）开始找 .minicode/，找不到就用 cwd 自己建。"""
        start = Path(start or os.getcwd()).resolve()

        # 向上找最近的 .minicode（最远 3 层），找不到就 fallback 到 cwd
        cur = start
        project_dir: Optional[Path] = None
        for _ in range(3):
            candidate = cur / ".minicode"
            if candidate.is_dir():
                project_dir = candidate
                break
            if cur.parent == cur:
                break
            cur = cur.parent
        if project_dir is None:
            project_dir = start / ".minicode"

        project_root = project_dir.parent
        global_dir = Path.home() / ".minicode"

        skills_dirs: List[Path] = []
        for d in (project_dir, global_dir):
            skills = d / "skills"
            if skills.is_dir():
                skills_dirs.append(skills)

        agents_dirs: List[Path] = []
        for d in (project_dir, global_dir):
            agents = d / "agents"
            if agents.is_dir():
                agents_dirs.append(agents)

        hooks_dirs: List[Path] = []
        for d in (project_dir, global_dir):
            hooks = d / "hooks"
            if hooks.is_dir():
                hooks_dirs.append(hooks)

        commands_dirs: List[Path] = []
        for d in (project_dir, global_dir):
            commands = d / "commands"
            if commands.is_dir():
                commands_dirs.append(commands)

        return MinicodePaths(
            project_root=project_root,
            project_dir=project_dir,
            global_dir=global_dir,
            skills_dirs=skills_dirs,
            agents_dirs=agents_dirs,
            hooks_dirs=hooks_dirs,
            commands_dirs=commands_dirs,
            mcp_config=project_dir / "mcp.json",
            global_mcp_config=global_dir / "mcp.json",
            config_yaml=project_dir / "config.yaml",
            global_config_yaml=global_dir / "config.yaml",
            history_dir=project_dir / "history",
        )

    def ensure_project_dir(self) -> None:
        """如果项目级 .minicode 不存在则创建（用于首次运行）。"""
        self.project_dir.mkdir(parents=True, exist_ok=True)
        (self.project_dir / "skills").mkdir(parents=True, exist_ok=True)
        (self.project_dir / "agents").mkdir(parents=True, exist_ok=True)
        (self.project_dir / "hooks").mkdir(parents=True, exist_ok=True)
        (self.project_dir / "commands").mkdir(parents=True, exist_ok=True)
        (self.project_dir / "history").mkdir(parents=True, exist_ok=True)

    def all_mcp_configs(self) -> List[Path]:
        """返回所有存在的 mcp.json（项目级优先）。"""
        return [p for p in (self.mcp_config, self.global_mcp_config) if p.is_file()]

    def all_config_yamls(self) -> List[Path]:
        """返回所有存在的 config.yaml（项目级优先）。"""
        return [p for p in (self.config_yaml, self.global_config_yaml) if p.is_file()]

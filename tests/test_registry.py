"""ToolRegistry 端到端单测。"""
import asyncio
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from minicode.paths import MinicodePaths
from minicode.tool.base import ToolContext, ToolKind
from minicode.tool.registry import ToolRegistry


def _make_paths(tmp_path: Path) -> MinicodePaths:
    """构造一个只指向 tmp_path 的 MinicodePaths。"""
    project_dir = tmp_path / ".minicode"
    project_dir.mkdir()
    (project_dir / "skills").mkdir()
    (project_dir / "agents").mkdir()
    # 写一个 mcp.json（空）
    (project_dir / "mcp.json").write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    # 写一个 skill
    skill = project_dir / "skills" / "demo" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: demo\ndescription: demo skill\n---\n\nbody\n", encoding="utf-8"
    )
    return MinicodePaths(
        project_root=tmp_path,
        project_dir=project_dir,
        global_dir=tmp_path / "global_dummy",   # 不存在，OK
        skills_dirs=[project_dir / "skills"],
        agents_dirs=[project_dir / "agents"],
        hooks_dirs=[project_dir / "hooks"],
        commands_dirs=[project_dir / "commands"],
        mcp_config=project_dir / "mcp.json",
        global_mcp_config=tmp_path / "global_dummy" / "mcp.json",
        config_yaml=project_dir / "config.yaml",
        global_config_yaml=tmp_path / "global_dummy" / "config.yaml",
        history_dir=project_dir / "history",
    )


@pytest.mark.asyncio
async def test_registry_builds_builtin(tmp_path: Path):
    paths = _make_paths(tmp_path)
    reg = ToolRegistry(paths)
    summary = await reg.build()
    assert summary.builtin_count == 8  # bash/read/write/edit/glob/grep/skill/subagent
    assert summary.skill_count == 1
    assert summary.subagent_count == 0
    assert summary.mcp_servers == 0
    assert summary.mcp_connected == 0
    await reg.aclose()


@pytest.mark.asyncio
async def test_registry_skill_loaded(tmp_path: Path):
    paths = _make_paths(tmp_path)
    reg = ToolRegistry(paths)
    await reg.build()
    skills = reg.skills()
    assert len(skills) == 1
    assert skills[0].name == "demo"
    await reg.aclose()


@pytest.mark.asyncio
async def test_registry_execute_bash(tmp_path: Path):
    paths = _make_paths(tmp_path)
    reg = ToolRegistry(paths)
    await reg.build()
    ctx = ToolContext(cwd=tmp_path)
    # python -c "print('ok')" 跨平台
    py = sys.executable
    result = await reg.execute("bash", {"command": f'"{py}" -c "print(123)"', "description": "echo"}, ctx)
    assert "123" in result.output
    assert result.metadata["exit"] == 0
    await reg.aclose()


@pytest.mark.asyncio
async def test_registry_execute_skill_via_builtin(tmp_path: Path):
    paths = _make_paths(tmp_path)
    reg = ToolRegistry(paths)
    await reg.build()
    ctx = ToolContext(cwd=tmp_path)
    result = await reg.execute("skill", {"name": "demo"}, ctx)
    assert "loaded skill: demo" in result.title
    assert "<skill_content" in result.output
    await reg.aclose()


@pytest.mark.asyncio
async def test_registry_unknown_tool(tmp_path: Path):
    paths = _make_paths(tmp_path)
    reg = ToolRegistry(paths)
    await reg.build()
    ctx = ToolContext(cwd=tmp_path)
    result = await reg.execute("does_not_exist", {}, ctx)
    assert result.metadata.get("error")
    await reg.aclose()


@pytest.mark.asyncio
async def test_registry_invalid_args(tmp_path: Path):
    paths = _make_paths(tmp_path)
    reg = ToolRegistry(paths)
    await reg.build()
    ctx = ToolContext(cwd=tmp_path)
    # bash 缺 description 字段
    result = await reg.execute("bash", {"command": "echo"}, ctx)
    assert result.metadata.get("validation")
    await reg.aclose()

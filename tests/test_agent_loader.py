"""agent.loader 单测。"""
from pathlib import Path

from minicode.agent.loader import SubagentLoader, load_subagents


def test_load_agents_no_dir(tmp_path: Path):
    loader = SubagentLoader([tmp_path])
    loader.scan()
    assert loader.all() == []


def test_load_agents_empty_dir(tmp_path: Path):
    (tmp_path / "agents").mkdir()
    loader = SubagentLoader([tmp_path / "agents"])
    loader.scan()
    assert loader.all() == []


def test_load_agents_one(tmp_path: Path):
    d = tmp_path / "agents"
    d.mkdir()
    (d / "reviewer.md").write_text(
        "---\n"
        "name: reviewer\n"
        "description: review code\n"
        "---\n\n"
        "You are a reviewer.\n",
        encoding="utf-8",
    )
    loader = SubagentLoader([d])
    loader.scan()
    a = loader.get("reviewer")
    assert a is not None
    assert a.name == "reviewer"
    assert a.description == "review code"
    assert "reviewer" in a.system_prompt


def test_load_agents_multiple_sorted(tmp_path: Path):
    d = tmp_path / "agents"
    d.mkdir()
    for name in ("zulu", "alpha", "mike"):
        (d / f"{name}.md").write_text(
            f"---\nname: {name}\ndescription: d-{name}\n---\nbody-{name}\n",
            encoding="utf-8",
        )
    # 非 .md 不应被加载
    (d / "ignore.txt").write_text("nope", encoding="utf-8")
    loader = SubagentLoader([d])
    loader.scan()
    assert [a.name for a in loader.all()] == ["alpha", "mike", "zulu"]


def test_load_agents_missing_frontmatter(tmp_path: Path):
    d = tmp_path / "agents"
    d.mkdir()
    (d / "broken.md").write_text("no frontmatter at all\n", encoding="utf-8")
    loader = SubagentLoader([d])
    loader.scan()
    assert loader.all() == []


def test_load_agents_missing_name(tmp_path: Path):
    d = tmp_path / "agents"
    d.mkdir()
    (d / "x.md").write_text("---\ndescription: d\n---\nbody\n", encoding="utf-8")
    loader = SubagentLoader([d])
    loader.scan()
    assert loader.all() == []


def test_load_agents_empty_body_uses_description(tmp_path: Path):
    d = tmp_path / "agents"
    d.mkdir()
    (d / "x.md").write_text(
        "---\nname: x\ndescription: do X\n---\n\n   \n",
        encoding="utf-8",
    )
    loader = SubagentLoader([d])
    loader.scan()
    a = loader.get("x")
    assert a is not None
    assert "do X" in a.system_prompt  # 退化到 description


def test_load_agents_override_with_project_first(tmp_path: Path):
    """同名：项目级后扫 → 覆盖全局级。"""
    proj = tmp_path / "project"
    proj.mkdir()
    (proj / "a.md").write_text("---\nname: a\ndescription: project-a\n---\nPROJ\n", encoding="utf-8")
    glob = tmp_path / "global"
    glob.mkdir()
    (glob / "a.md").write_text("---\nname: a\ndescription: global-a\n---\nGLOB\n", encoding="utf-8")

    # 项目级先扫 → 全局后扫 → 全局覆盖
    loader = SubagentLoader([proj, glob])
    loader.scan()
    assert loader.get("a").system_prompt.strip() == "GLOB"

    # 全局先扫 → 项目级后扫 → 项目覆盖
    loader2 = SubagentLoader([glob, proj])
    loader2.scan()
    assert loader2.get("a").system_prompt.strip() == "PROJ"


def test_load_subagents_helper(tmp_path: Path):
    d = tmp_path / "agents"
    d.mkdir()
    (d / "x.md").write_text("---\nname: x\ndescription: d\n---\nbody\n", encoding="utf-8")
    loader = load_subagents([d])
    assert loader.get("x") is not None

"""SkillLoader 单测。"""
import pytest
from pathlib import Path

from minicode.tool.skill import SkillLoader, _parse_skill_md


def _write_skill(root: Path, name: str, description: str = "desc"):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# body of {name}\n",
        encoding="utf-8",
    )


def test_parse_skill_md(tmp_path: Path):
    _write_skill(tmp_path, "foo")
    p = tmp_path / "foo" / "SKILL.md"
    info = _parse_skill_md(p)
    assert info is not None
    assert info.name == "foo"
    assert info.description == "desc"
    assert "body of foo" in info.content


def test_parse_skill_md_no_frontmatter(tmp_path: Path):
    p = tmp_path / "SKILL.md"
    p.write_text("# no frontmatter", encoding="utf-8")
    assert _parse_skill_md(p) is None


def test_parse_skill_md_missing_name(tmp_path: Path):
    d = tmp_path / "x"
    d.mkdir()
    (d / "SKILL.md").write_text("---\ndescription: x\n---\nbody\n", encoding="utf-8")
    assert _parse_skill_md(d / "SKILL.md") is None


def test_loader_scan_finds_all(tmp_path: Path):
    _write_skill(tmp_path, "a")
    _write_skill(tmp_path, "b", "second skill")
    _write_skill(tmp_path, "c")

    loader = SkillLoader([tmp_path])
    loader.scan()
    all_skills = loader.all()
    names = {s.name for s in all_skills}
    assert names == {"a", "b", "c"}
    assert loader.get("b").description == "second skill"


def test_loader_missing_dir_ignored(tmp_path: Path):
    loader = SkillLoader([tmp_path / "nope"])
    loader.scan()
    assert loader.all() == []


def test_loader_dedup_name(tmp_path: Path):
    """同名 skill：后扫描的不覆盖先扫描的（实现：if exists, continue）。"""
    _write_skill(tmp_path, "foo", "first")
    other = tmp_path / "other"
    other.mkdir()
    (other / "SKILL.md").write_text(
        "---\nname: foo\ndescription: second\n---\n\nbody\n", encoding="utf-8"
    )
    # 注意：第二个文件直接在 other/SKILL.md 而非 other/foo/SKILL.md
    # 我们的 rglob("SKILL.md") 会扫到。
    loader = SkillLoader([tmp_path, other])
    loader.scan()
    assert len(loader.all()) == 1
    assert loader.get("foo").description == "first"

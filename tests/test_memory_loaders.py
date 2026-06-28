"""memory.loaders 单测。"""
from pathlib import Path

from minicode.memory.loaders import load_agents_md, load_rules


def test_load_agents_md_missing(tmp_path: Path):
    assert load_agents_md(tmp_path) is None


def test_load_agents_md_present(tmp_path: Path):
    p = tmp_path / "AGENTS.md"
    p.write_text("# Project\n\nWe are building X.\n", encoding="utf-8")
    agents = load_agents_md(tmp_path)
    assert agents is not None
    assert "building X" in agents.content
    assert agents.path == p


def test_load_agents_md_empty(tmp_path: Path):
    (tmp_path / "AGENTS.md").write_text("   \n\n", encoding="utf-8")
    agents = load_agents_md(tmp_path)
    assert agents is not None
    assert agents.is_empty()


def test_load_rules_no_dir(tmp_path: Path):
    assert load_rules(tmp_path) == []


def test_load_rules_empty_dir(tmp_path: Path):
    (tmp_path / "rules").mkdir()
    assert load_rules(tmp_path) == []


def test_load_rules_sorted(tmp_path: Path):
    rd = tmp_path / "rules"
    rd.mkdir()
    (rd / "z.md").write_text("z", encoding="utf-8")
    (rd / "a.md").write_text("a", encoding="utf-8")
    (rd / "m.md").write_text("m", encoding="utf-8")
    # 非 .md 不应被加载
    (rd / "ignore.txt").write_text("txt", encoding="utf-8")
    rules = load_rules(tmp_path)
    assert [r.name for r in rules] == ["a", "m", "z"]


def test_load_rules_skip_empty(tmp_path: Path):
    rd = tmp_path / "rules"
    rd.mkdir()
    (rd / "ok.md").write_text("content", encoding="utf-8")
    (rd / "blank.md").write_text("   \n", encoding="utf-8")
    rules = load_rules(tmp_path)
    assert [r.name for r in rules] == ["ok"]

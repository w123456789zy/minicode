"""memory.context 单测。"""
from pathlib import Path

from minicode.memory.context import assemble_system
from minicode.memory.loaders import AgentsDoc, RuleFile


def _agents(content: str, tmp_path: Path) -> AgentsDoc:
    p = tmp_path / "AGENTS.md"
    p.write_text(content, encoding="utf-8")
    return AgentsDoc(path=p, content=content)


def _rule(name: str, content: str, tmp_path: Path) -> RuleFile:
    p = tmp_path / "rules" / f"{name}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return RuleFile(name=name, path=p, content=content)


def test_assemble_base_only(tmp_path: Path):
    out = assemble_system(None, [], tmp_path)
    assert "minicode" in out
    assert "AGENTS.md" not in out
    assert "Project Rules" not in out


def test_assemble_with_agents(tmp_path: Path):
    agents = _agents("We are building X.", tmp_path)
    out = assemble_system(agents, [], tmp_path)
    assert "We are building X" in out
    assert "AGENTS.md" in out


def test_assemble_with_rules(tmp_path: Path):
    rules = [
        _rule("code-style", "Use 4 spaces.", tmp_path),
        _rule("behavior", "No auto-commit.", tmp_path),
    ]
    out = assemble_system(None, rules, tmp_path)
    assert "Project Rules" in out
    assert "code-style" in out
    assert "behavior" in out
    assert "4 spaces" in out
    assert "No auto-commit" in out


def test_assemble_empty_files_omitted(tmp_path: Path):
    """空 agents + 空 rules → 仍是 base only。"""
    agents = _agents("   \n", tmp_path)
    rules = [_rule("empty", "  \n", tmp_path)]
    out = assemble_system(agents, rules, tmp_path)
    assert "AGENTS.md" not in out
    assert "Project Rules" not in out


def test_assemble_agents_before_rules(tmp_path: Path):
    agents = _agents("AGENTS_HERE", tmp_path)
    rules = [_rule("r1", "RULE_HERE", tmp_path)]
    out = assemble_system(agents, rules, tmp_path)
    ai = out.index("AGENTS_HERE")
    ri = out.index("RULE_HERE")
    assert ai < ri  # agents 在前

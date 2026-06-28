"""测试 cli/input.py 的命令过滤和补全逻辑。"""

from minicode.cli.input import (
    BUILTIN_SLASH_COMMANDS,
    _filter_commands,
    _longest_common_prefix,
    _render_suggestions,
)


def test_filter_commands_empty_prefix():
    """空前缀返回全部命令。"""
    result = _filter_commands("")
    assert len(result) == len(BUILTIN_SLASH_COMMANDS)
    assert result[0][0] == "/tools"


def test_filter_commands_single_char():
    """单字符前缀过滤。"""
    result = _filter_commands("/m")
    names = [c for c, _ in result]
    assert "/model" in names
    assert "/memory" in names
    assert "/mcp" in names
    assert "/tools" not in names


def test_filter_commands_full_command():
    """完整命令只返回自己。"""
    result = _filter_commands("/help")
    assert len(result) == 1
    assert result[0][0] == "/help"


def test_filter_commands_no_match():
    """无匹配返回空列表。"""
    result = _filter_commands("/xyz")
    assert result == []


def test_longest_common_prefix_single():
    """单个命令的 LCP 是它自己。"""
    assert _longest_common_prefix(["/help"]) == "/help"


def test_longest_common_prefix_multiple():
    """多个命令的 LCP。"""
    assert _longest_common_prefix(["/model", "/memory"]) == "/m"
    assert _longest_common_prefix(["/exit", "/quit"]) == "/"


def test_longest_common_prefix_empty():
    """空列表的 LCP 是空串。"""
    assert _longest_common_prefix([]) == ""


def test_render_suggestions_has_commands():
    """渲染建议包含命令名。"""
    lines = _render_suggestions("/m")
    assert len(lines) > 0
    text = "\n".join(lines)
    assert "/model" in text
    assert "/memory" in text
    assert "/mcp" in text


def test_render_suggestions_no_match():
    """无匹配时返回空列表。"""
    lines = _render_suggestions("/xyz")
    assert lines == []


def test_render_suggestions_all_commands():
    """空前缀显示所有命令（前 8 条 + more 提示）。"""
    lines = _render_suggestions("/")
    assert len(lines) > 0
    text = "\n".join(lines)
    assert "/tools" in text
    assert "more" in text  # 超过 8 条会有 +N more


def test_render_suggestions_max_show():
    """超过 max_show 时显示 +N more。"""
    lines = _render_suggestions("/", max_show=3)
    text = "\n".join(lines)
    assert "more" in text

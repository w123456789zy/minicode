"""
测试 minicode.display：4 类展示（thinking / model-input / tool-call / code-change）。
"""

from __future__ import annotations

from minicode.display import (
    CodeChange,
    ModelInputView,
    ThinkingBlock,
    ToolCallView,
    format_args,
    render_code_change,
    render_code_change_header,
    render_model_input,
    render_thinking,
    render_tool_call,
    truncate,
)


# ── 工具函数 ─────────────────────────


class TestTruncate:
    def test_short(self):
        assert truncate("hello", max_len=100) == "hello"

    def test_empty(self):
        assert truncate("", max_len=10) == ""

    def test_long(self):
        s = "x" * 1000
        out = truncate(s, max_len=200)
        assert "truncated" in out
        # 头/尾都得有
        assert out.startswith("x" * 100)
        assert out.rstrip().endswith("x" * 10)

    def test_max_len_zero(self):
        # max_len <= 0 → 原样返回
        assert truncate("hello", max_len=0) == "hello"
        assert truncate("hello", max_len=-1) == "hello"

    def test_non_string_coerced(self):
        out = truncate(12345, max_len=100)  # type: ignore[arg-type]
        assert out == "12345"


class TestFormatArgs:
    def test_none(self):
        assert format_args(None) == "(no args)"

    def test_dict(self):
        out = format_args({"command": "ls -la"})
        assert '"command"' in out
        assert "ls -la" in out

    def test_list(self):
        out = format_args([1, 2, 3])
        assert "[1, 2, 3]" in out

    def test_long_truncated(self):
        out = format_args({"x": "y" * 1000}, max_len=80)
        assert "truncated" in out


# ── thinking ─────────────────────────


class TestRenderThinking:
    def test_basic(self):
        out = render_thinking(ThinkingBlock(content="hello world"))
        assert "thinking" in out
        assert "hello world" in out

    def test_with_model_and_duration(self):
        out = render_thinking(
            ThinkingBlock(content="thinking...", model="gpt-x", duration_ms=230)
        )
        assert "gpt-x" in out
        assert "230ms" in out
        assert "lines" in out

    def test_long_truncated(self):
        out = render_thinking(ThinkingBlock(content="x" * 5000), max_len=200)
        assert "truncated" in out

    def test_empty_content(self):
        out = render_thinking(ThinkingBlock(content=""))
        assert "(empty)" in out


# ── model input ─────────────────────────


class TestRenderModelInput:
    def test_basic(self):
        out = render_model_input(ModelInputView(
            system="sys prompt",
            messages=[{"role": "user", "content": "hi"}],
        ))
        assert "model input" in out
        assert "sys prompt" in out
        assert "user" in out
        assert "hi" in out

    def test_with_model_and_tools(self):
        out = render_model_input(ModelInputView(
            system="",
            messages=[],
            model="gpt-x",
            tools=[{"name": "bash"}, {"name": "read"}],
        ))
        assert "gpt-x" in out
        assert "2 schema" in out
        assert "bash, read" in out

    def test_with_tool_call_in_message(self):
        out = render_model_input(ModelInputView(
            messages=[
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"name": "bash", "arguments": {"command": "ls"}},
                    ],
                },
            ],
        ))
        assert "tool_calls" in out
        assert "bash" in out

    def test_tool_message_with_call_id(self):
        out = render_model_input(ModelInputView(
            messages=[
                {
                    "role": "tool",
                    "content": "out",
                    "tool_call_id": "call_01",
                },
            ],
        ))
        assert "call_01" in out

    def test_empty(self):
        out = render_model_input(ModelInputView())
        assert "model input" in out
        assert "(none)" in out

    def test_many_tools_truncated_in_preview(self):
        tools = [{"name": f"t{i}"} for i in range(20)]
        out = render_model_input(ModelInputView(tools=tools))
        assert "+12 more" in out


# ── tool call ─────────────────────────


class TestRenderToolCall:
    def test_basic(self):
        out = render_tool_call(ToolCallView(
            name="bash",
            args={"command": "ls -la"},
            call_id="abc",
            source="model",
        ))
        assert "tool call" in out
        assert "bash" in out
        assert "abc" in out
        assert "model" in out
        assert "ls -la" in out

    def test_no_call_id_no_source(self):
        out = render_tool_call(ToolCallView(name="read", args={"path": "x"}))
        assert "id" not in out  # 没 id 字段
        assert "source" not in out  # 没 source 字段
        assert "read" in out
        assert '"path"' in out

    def test_does_not_show_result(self):
        """用户明确要求：不展示 tool call 结果。"""
        out = render_tool_call(ToolCallView(name="bash", args={}))
        assert "result" not in out.lower()
        assert "output" not in out.lower()


# ── code change ─────────────────────────


class TestRenderCodeChangeHeader:
    def test_empty(self):
        out = render_code_change_header([])
        assert "0 file" in out

    def test_basic(self):
        c1 = CodeChange(path="a.py", old_text="x", new_text="x\ny", added=5, removed=2)
        c2 = CodeChange(path="b.py", old_text="x\ny", new_text="", added=0, removed=3)
        out = render_code_change_header([c1, c2])
        assert "2 file" in out
        assert "a.py" in out
        assert "+5" in out
        assert "-2" in out
        assert "(delete)" in out  # c2 是删除

    def test_markers(self):
        changes = [
            CodeChange(path="new.py", added=10, removed=0),  # new
            CodeChange(path="del.py", old_text="x", new_text="", added=0, removed=5),  # delete
            CodeChange(path="noop.py", old_text="x", new_text="x", added=0, removed=0),  # no-op
        ]
        out = render_code_change_header(changes)
        assert "(new)" in out
        assert "(delete)" in out
        assert "(no-op)" in out


class TestRenderCodeChange:
    def test_basic_diff(self):
        c = CodeChange(
            path="a.py",
            old_text="line1\nline2\nline3\n",
            new_text="line1\nLINE2\nline3\n",
            added=1,
            removed=1,
        )
        out = render_code_change(c)
        assert "a.py" in out
        assert "LINE2" in out
        assert "line2" in out  # 旧行也要有
        assert "+1" in out
        assert "-1" in out

    def test_new_file(self):
        c = CodeChange(
            path="new.py",
            new_text="print('hello')\n",
            added=1,
            removed=0,
        )
        out = render_code_change(c)
        assert "new.py" in out
        assert "print" in out

    def test_delete_file(self):
        c = CodeChange(
            path="old.py",
            old_text="print('bye')\n",
            new_text="",
            added=0,
            removed=1,
        )
        out = render_code_change(c)
        assert "old.py" in out
        assert "print" in out

    def test_noop(self):
        c = CodeChange(
            path="x.py",
            old_text="same\n",
            new_text="same\n",
            added=0,
            removed=0,
        )
        out = render_code_change(c)
        assert "(no-op)" in out

    def test_empty(self):
        c = CodeChange(path="x.py")
        out = render_code_change(c)
        assert "(empty change)" in out

    def test_long_diff_truncated(self):
        old = "\n".join(f"old{i}" for i in range(200))
        new = "\n".join(f"new{i}" for i in range(200))
        c = CodeChange(path="big.py", old_text=old, new_text=new, added=200, removed=200)
        out = render_code_change(c, max_total=300)
        assert "truncated" in out

    def test_note_appears(self):
        c = CodeChange(path="a.py", added=1, removed=0, note="from edit tool")
        out = render_code_change(c)
        assert "from edit tool" in out


# ── 端到端：4 类同时输出不互相覆盖 ─────────────────────────


class TestAllFourTogether:
    def test_all_render(self, capsys):
        # 简单 sanity：四类都能渲染
        s1 = render_thinking(ThinkingBlock(content="think"))
        s2 = render_model_input(ModelInputView(messages=[{"role": "user", "content": "x"}]))
        s3 = render_tool_call(ToolCallView(name="read", args={"path": "x"}))
        s4 = render_code_change(CodeChange(path="a.py", added=1, removed=0))
        all_text = "\n".join([s1, s2, s3, s4])
        # 关键关键字都在
        assert "thinking" in all_text
        assert "model input" in all_text
        assert "tool call" in all_text
        assert "code change" in all_text

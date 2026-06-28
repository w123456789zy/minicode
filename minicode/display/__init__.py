"""
minicode.display：把模型/工具的中间过程结构化展示给用户。

设计目标：
- 5 类展示：thinking / model-input / tool-call / tool-result / code-change
- 每类一个 dataclass 作为输入 + 一个 render_*() 字符串函数
- 一律 plain text + 缩进/box-drawing 字符，可被直接 print 到终端
- 不依赖 rich / colorama 等（保持依赖最小）
- 截断策略：长内容 → 头 + 尾，省略中间
- 可被 CLI / chat bridge / 日志共用
"""

from minicode.display.formatter import (
    CodeChange,
    ModelInputView,
    ThinkingBlock,
    ToolCallView,
    ToolResultView,
    render_code_change,
    render_code_change_header,
    render_model_input,
    render_thinking,
    render_tool_call,
    render_tool_result,
    format_args,
    truncate,
)

__all__ = [
    "ThinkingBlock",
    "ModelInputView",
    "ToolCallView",
    "ToolResultView",
    "CodeChange",
    "render_thinking",
    "render_model_input",
    "render_tool_call",
    "render_tool_result",
    "render_code_change",
    "render_code_change_header",
    "format_args",
    "truncate",
]

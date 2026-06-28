"""
memory 子包入口。

公开：
- load_agents_md / load_rules
- estimate_tokens / ContextBudget
- truncate_messages / soft_trim_tool_results / hard_trim_tool_results
- compact_messages
- assemble_system
- format_status
"""

from minicode.memory.loaders import (
    load_agents_md,
    load_rules,
    AgentsDoc,
    RuleFile,
)
from minicode.memory.budget import (
    estimate_tokens,
    estimate_message_tokens,
    ContextBudget,
)
from minicode.memory.truncation import (
    truncate_messages,
    soft_trim_tool_results,
    hard_trim_tool_results,
)
from minicode.memory.compact import compact_messages
from minicode.memory.context import assemble_system
from minicode.memory.context_view import (
    compute_breakdown,
    format_context_box,
    ContextBreakdown,
)
from minicode.memory.history import (
    save_history,
    load_history,
    load_latest,
    list_sessions,
    format_session_list,
    SessionMeta,
)
from minicode.memory.status import format_status

__all__ = [
    "load_agents_md", "load_rules", "AgentsDoc", "RuleFile",
    "estimate_tokens", "estimate_message_tokens", "ContextBudget",
    "truncate_messages", "soft_trim_tool_results", "hard_trim_tool_results",
    "compact_messages",
    "assemble_system",
    "format_status",
    "compute_breakdown", "format_context_box", "ContextBreakdown",
    "save_history", "load_history", "load_latest",
    "list_sessions", "format_session_list", "SessionMeta",
]

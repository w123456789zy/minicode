"""
agent 子包入口：subagent 加载 + 运行。

公开：
- SubagentInfo / SubagentLoader / load_subagents
- run_subagent
"""

from minicode.agent.loader import (
    SubagentInfo,
    SubagentLoader,
    load_subagents,
)
from minicode.agent.runtime import (
    run_agent,
    run_subagent,
    AgentEvent,
    SubagentResult,
)

__all__ = [
    "SubagentInfo", "SubagentLoader", "load_subagents",
    "run_subagent", "SubagentResult",
    "run_agent", "AgentEvent",
]

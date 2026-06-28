"""
hooks 子包入口。

公开：
- EventName / Action / HookEvent / HookResponse / HookContext
- HookDispatcher / PythonHook / ShellHook
- load_hooks
"""

from minicode.hooks.types import (
    EventName,
    Action,
    HookEvent,
    HookResponse,
    HookContext,
    HookError,
    ALL_EVENTS,
    BLOCKING_EVENTS,
    MODIFIABLE_EVENTS,
    parse_action,
    parse_response,
)
from minicode.hooks.dispatcher import (
    HookDispatcher,
    HookInfo,
    DispatchResult,
)
from minicode.hooks.python import PythonHook
from minicode.hooks.shell import ShellHook

__all__ = [
    "EventName", "Action",
    "HookEvent", "HookResponse", "HookContext", "HookError",
    "ALL_EVENTS", "BLOCKING_EVENTS", "MODIFIABLE_EVENTS",
    "parse_action", "parse_response",
    "HookDispatcher", "HookInfo", "DispatchResult",
    "PythonHook", "ShellHook",
]

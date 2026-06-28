"""
Tool 抽象基类 + 上下文 + 结果类型。

设计要点（参考 mimo code / opencode 工具层，做了 Python 化简化）：

1. 所有工具实现同一个 Tool 接口
   - id / description / parameters / execute
   - parameters 是 Pydantic BaseModel 子类（用 schema 生成 JSON Schema 给 LLM）
   - execute 接收已校验的 BaseModel 实例 + ToolContext，返回 ToolResult
2. ToolContext 由调用方（CLI / 后续的 ReAct 循环）注入
   - 包含 cwd、session_id、extra 等运行期信息
   - 包含 ask() 回调（向用户询问权限）
3. ToolResult 是统一返回类型
   - title：给人看的简短标题
   - output：主要文本输出
   - metadata：附加信息（exit code、truncated 标志、文件路径 等）

为了便于以后扩展，预留：
- ToolKind 枚举：标记工具来源（builtin / skill / mcp / custom）
- ToolDef：聚合一个工具的完整信息（id、description、parameters、kind、source）
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional, Type
from pydantic import BaseModel, Field, ConfigDict


# ─────────────────────────────────────────────────────────────
# 基础数据结构
# ─────────────────────────────────────────────────────────────


class ToolKind(str, Enum):
    """工具来源。Registry 用它来做分组、过滤、显示。"""
    BUILTIN = "builtin"      # 代码内置（bash/read/edit/...）
    SKILL = "skill"          # .minicode/skills/<name>/SKILL.md 转成的工具
    MCP = "mcp"              # 来自 .minicode/mcp.json 中 MCP server 的工具
    CUSTOM = "custom"        # 用户自己继承 Tool 写的（预留）


class ToolContext(BaseModel):
    """工具执行上下文，由调用方注入。

    不放业务状态，状态由 registry 维护。"""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    session_id: str = "default"
    cwd: Path = Field(default_factory=Path.cwd)
    abort: Any = None                          # asyncio.Event 之类
    extra: Dict[str, Any] = Field(default_factory=dict)

    # 权限询问回调：tool 可选地调用 ctx.ask(...)
    ask: Optional[Callable[["AskRequest"], Awaitable[None]]] = None

    def sub(self, **overrides: Any) -> "ToolContext":
        """派生子 ctx（不修改自身）。"""
        data = self.model_dump()
        data.update(overrides)
        return ToolContext(**data)


class AskRequest(BaseModel):
    """权限询问的载荷。"""
    permission: str
    patterns: list[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    """工具执行结果。所有工具都返回这个（或 raise）。"""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    title: str
    output: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ─────────────────────────────────────────────────────────────
# Tool 抽象
# ─────────────────────────────────────────────────────────────


class Tool(ABC):
    """工具抽象基类。

    子类只需实现 id / description / parameters / execute。
    """

    kind: ToolKind = ToolKind.BUILTIN

    @property
    @abstractmethod
    def id(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @property
    @abstractmethod
    def parameters(self) -> Type[BaseModel]: ...

    @abstractmethod
    async def execute(self, args: BaseModel, ctx: ToolContext) -> ToolResult: ...

    # ── 便捷方法 ──

    def to_def(self) -> "ToolDef":
        return ToolDef(
            id=self.id,
            description=self.description,
            parameters=self.parameters,
            kind=self.kind,
            tool=self,
        )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{type(self).__name__} id={self.id!r} kind={self.kind.value}>"


# ─────────────────────────────────────────────────────────────
# ToolDef：注册到 Registry 的实体
# ─────────────────────────────────────────────────────────────


class ToolDef(BaseModel):
    """一个完整工具的注册形态。

    Registry 里存的全是 ToolDef。它把 Tool 本身包了一层，加上
    来源（source）信息，方便 /tools、/mcp、/skills 分组展示。
    """
    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: str
    description: str
    parameters: Type[BaseModel]
    kind: ToolKind
    tool: Tool                                # 实际可执行的对象
    source: Optional[str] = None              # 来自哪个文件/服务器，便于排查
    tags: list[str] = Field(default_factory=list)

    def json_schema(self) -> Dict[str, Any]:
        """给 LLM 用的 parameters JSON Schema（Pydantic v2 自带）。"""
        return self.parameters.model_json_schema()

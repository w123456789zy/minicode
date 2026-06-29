"""
minicode CLI 主入口（v1 工具层 + 模型层 + 记忆层 + hooks 版本）。

启动流程（仿 ClaudeCode 设计）：
1. 解析 `.minicode/` 路径
2. 加载 + 校验 `config.yaml` → 不完整直接报错退出
3. 加载记忆（AGENTS.md + rules/*.md）→ 拼成 system prompt
4. build tool registry（含 hooks dispatcher） + model registry
5. 触发 session_start hook
6. 进入 REPL（带 ctx 状态栏 + 自动裁剪 + /compact）
7. 触发 session_end hook

用法：
    minicode                # 启动 REPL（配置必须完整）
    minicode -resume        # 恢复最近一次会话
    minicode -resume list   # 列出所有已保存的会话
    minicode -resume <id>   # 恢复指定会话（支持 id 前缀）
    minicode --version      # 打印版本
    minicode --paths        # 打印解析到的 .minicode 路径
    minicode --check-config # 仅校验配置并退出（CI / 排错用）
    minicode --print-memory # 打印加载的 AGENTS.md + rules 内容并退出
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import List, Optional

import minicode
from minicode._ansi import _is_tty
from minicode.bus import Bus
from minicode.chatbridge import ChatBridgeManager
from minicode.chatbridge.adapters import builtin_stdio_adapter, builtin_webhook_adapter
from minicode.config import render_config_errors
from minicode.goal import (
    GoalService,
    judge as judge_goal,
    render_verdict,
)
from minicode.hooks import (
    EventName,
    HookContext,
    HookDispatcher,
    HookEvent,
)
from minicode.memory import (
    ContextBudget,
    assemble_system,
    compact_messages,
    estimate_tokens,
    format_status,
    load_agents_md,
    load_rules,
    soft_trim_tool_results,
    hard_trim_tool_results,
    truncate_messages,
)
from minicode.model import Message, ModelRegistry
from minicode.model.message import ToolSchema
from minicode.paths import MinicodePaths
from minicode.permission import (
    PermissionService,
    default_prompt,
)
from minicode.display import (
    CodeChange,
    ModelInputView,
    ThinkingBlock,
    ToolCallView,
    render_code_change,
    render_code_change_header,
    render_model_input,
    render_thinking,
    render_tool_call,
)
from minicode.tool.base import ToolKind
from minicode.tool.builtin.subagent import SubagentTool
from minicode.tool.registry import ToolRegistry
from minicode.agent import run_agent, run_subagent
from minicode.agent.runtime import AgentEvent
from minicode.command import CommandLoader


# Windows 终端默认 GBK，强制 utf-8 让 box-drawing 字符不报错
def _setup_io_encoding() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


BANNER = """
  ┌─┐┌┐┌┌─┐┌─┐┌─┐┌┬┐  ┌─┐┌─┐┌┬┐
  ├┤ ││││ ┬│ │├─┘ │   │ │├┤  ││
  └─┘┘└┘└─┘└─┘┴   ┴   └─┘└─┘─┴┘
  Python terminal-native AI coding assistant  v{minicode_version}
"""


HELP_TEXT = """\
可用命令：
  /tools                 列出所有工具（builtin + skill + mcp），按 kind 分组
  /skills                列出 .minicode/skills/ 下的所有 skill
  /agents                列出 .minicode/agents/ 下的所有 subagent
  /hooks                 列出已加载的所有 hook (python + shell)
  /mcp                   列出 .minicode/mcp.json 里的 MCP 服务和状态
  /model                 显示当前激活的 model
  /model test            发送 "ping" 测试当前 model（流式打印）
  /memory                显示当前加载的 AGENTS.md + rules 概览
  /context               显示当前上下文窗口占用详情（system + tools + history）
  /history               列出已保存的历史会话
  /compact               手动压缩历史对话（旧消息 → LLM 摘要）
  /goal <condition>      设置停止条件，调一次 judge 看 transcript 是否满足
  /goal clear            清除当前 goal
  /goal status           显示当前 goal 状态
  /goal                  等同 /goal status
  /chat list             列出已启动的 chat adapter
  /chat status           显示 chat bridge 详细状态（in/out/err 计数）
  /chat start webhook [--port 8765]  启动 webhook 桥接
  /chat start stdio      启动 stdin 桥接
  /chat stop [name|all]  停止 adapter（默认 all）
  /chat help             /chat 子命令帮助
  /permission            显示当前 per-session 的 always-allow / always-deny
  /permission allow <id> 把 <id> 加入 always-allow
  /permission deny <id>  把 <id> 加入 always-deny
  /permission clear [id] 清除 always 状态（不给 id = 全部清除）
  /display demo          渲染一份 demo（thinking / model-input / tool-call / code-change）
  /paths                 打印当前 .minicode 路径解析结果
  /call <id> <json>      手动调用一个工具（调试用）
  /reload                重新 build registry（要先修好 config.yaml）
  /help                  打印本帮助
  /commands              列出自定义命令（.minicode/commands/ 下的 .md 文件）
  /exit / /quit          退出

也可以直接输入非斜杠开头的文字作为自由输入（v0 暂不接 LLM，只回显）。
"""


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="minicode",
        description="Python terminal-native AI coding assistant",
    )
    p.add_argument("--version", action="store_true", help="print version and exit")
    p.add_argument("--paths", action="store_true", help="print resolved .minicode paths and exit")
    p.add_argument("--init", action="store_true", help="create .minicode/ folder with config.yaml template in current directory and exit")
    p.add_argument("--print-tools", action="store_true", help="build tool registry and print tool table, then exit")
    p.add_argument("--check-config", action="store_true", help="validate config.yaml and exit")
    p.add_argument("--print-memory", action="store_true", help="print loaded AGENTS.md + rules content and exit")
    p.add_argument(
        "-resume", "--resume", nargs="?", const="latest", default=None,
        help="resume a saved session: '-resume' for latest, '-resume list' to list, '-resume <id>' for specific session",
    )
    return p.parse_args()


def main() -> None:
    _setup_io_encoding()
    args = _parse_args()
    paths = MinicodePaths.discover()

    if args.version:
        print(f"minicode {minicode.__version__}")
        return

    if args.paths:
        print(_format_paths(paths))
        return

    if args.init:
        _init_project_and_exit()
        return

    if args.check_config:
        _check_config_and_exit(paths)
        return

    if args.print_memory:
        _print_memory_and_exit(paths)
        return

    if args.print_tools:
        asyncio.run(_print_tools_and_exit(paths))
        return

    # 默认：进入 REPL（前提是 config 完整）
    from minicode.config import load_config
    _, errs = load_config(paths.config_yaml)
    if errs:
        print(render_config_errors(paths.config_yaml, errs), file=sys.stderr)
        sys.exit(2)

    # -resume list：列出历史会话后退出
    if args.resume == "list":
        from minicode.memory import list_sessions, format_session_list
        sessions = list_sessions(paths.history_dir)
        print("Saved sessions (newest first):")
        print(format_session_list(sessions))
        return

    try:
        asyncio.run(_repl(paths, resume=args.resume))
    except KeyboardInterrupt:
        print("\nbye.")


# ─────────────────────────────────────────────────────────────
# 配置校验
# ─────────────────────────────────────────────────────────────


def _check_config_and_exit(paths: MinicodePaths) -> None:
    from minicode.config import load_config
    _, errs = load_config(paths.config_yaml)
    if errs:
        print(render_config_errors(paths.config_yaml, errs), file=sys.stderr)
        sys.exit(2)
    print(f"[ok] {paths.config_yaml} 校验通过")


# ─────────────────────────────────────────────────────────────
# --init：初始化项目
# ─────────────────────────────────────────────────────────────


_CONFIG_TEMPLATE = """\
# minicode 配置文件
# 参考: https://docs.minicode.dev/config (占位)

# LLM provider：当前支持 openai / anthropic
provider: openai

# API Key。推荐用环境变量：${OPENAI_API_KEY}
api_key: ${OPENAI_API_KEY}

# API base URL
base_url: https://api.openai.com/v1

# 模型名称
model: gpt-4o

# 上下文窗口大小（可选）。支持 128K / 1M / 128000 等写法
context_window: 128K

# 透传给 provider 的额外参数（可选）。
# 整数字段（如 max_tokens）也支持 K/M 后缀，会自动展开成整数。
extra:
  temperature: 0.7
  max_tokens: 8000
"""


def _init_project_and_exit() -> None:
    """在当前目录创建 .minicode/ 文件夹及 config.yaml 模板。"""
    project_dir = Path.cwd() / ".minicode"
    config_path = project_dir / "config.yaml"

    if project_dir.exists() and not project_dir.is_dir():
        print(f"[error] {project_dir} exists but is not a directory", file=sys.stderr)
        sys.exit(2)

    project_dir.mkdir(parents=True, exist_ok=True)

    # 同时创建 commands/、skills/、agents/、hooks/、history/ 等空目录
    for sub in ("commands", "skills", "agents", "hooks", "history"):
        (project_dir / sub).mkdir(exist_ok=True)

    if config_path.exists():
        print(f"[init] {config_path} already exists, skipped")
    else:
        config_path.write_text(_CONFIG_TEMPLATE, encoding="utf-8")
        print(f"[init] created {config_path}")

    print(f"[init] project initialized at {project_dir}")
    print("       next step: set your API key, then run `minicode --check-config`")


# ─────────────────────────────────────────────────────────────
# 一次性快照
# ─────────────────────────────────────────────────────────────


async def _print_tools_and_exit(paths: MinicodePaths) -> None:
    paths.ensure_project_dir()
    reg = ToolRegistry(paths)
    await reg.build()
    print(_format_tools(reg))
    await reg.aclose()


def _print_memory_and_exit(paths: MinicodePaths) -> None:
    paths.ensure_project_dir()
    agents = load_agents_md(paths.project_dir)
    rules = load_rules(paths.project_dir)
    sys_prompt = assemble_system(agents, rules, paths.project_root)
    print(f"── system prompt ({estimate_tokens(sys_prompt)} tokens estimated) ──")
    print(sys_prompt)
    print("── end ──")
    print(f"\n[memory] agents: {'loaded' if agents else 'none'}  rules: {len(rules)} files")


def _format_paths(p: MinicodePaths) -> str:
    lines = [
        f"project_root       : {p.project_root}",
        f"project .minicode/ : {p.project_dir}",
        f"global .minicode/  : {p.global_dir}",
        f"skill dirs (exist) : {[str(d) for d in p.skills_dirs] or '(none)'}",
        f"agents dirs (exist): {[str(d) for d in p.agents_dirs] or '(none)'}",
        f"mcp configs (exist): {[str(x) for x in p.all_mcp_configs()] or '(none)'}",
        f"config.yaml (exist): {[str(x) for x in p.all_config_yamls()] or '(none)'}",
    ]
    return "\n".join(lines)


def _format_tools(reg: ToolRegistry) -> str:
    summary = reg.summary()
    lines = []
    if summary is not None:
        lines.append(
            f"Loaded: {summary.builtin_count} builtin + "
            f"{summary.mcp_connected}/{summary.mcp_servers} mcp servers + "
            f"{summary.skill_count} skills + "
            f"{summary.subagent_count} subagents"
        )
        lines.append("")

    for kind in (ToolKind.BUILTIN, ToolKind.MCP, ToolKind.SKILL, ToolKind.CUSTOM):
        items = reg.by_kind(kind)
        if not items:
            continue
        lines.append(f"── {kind.value} ({len(items)}) ──")
        for d in items:
            src = f"  [{d.source}]" if d.source else ""
            lines.append(f"  - {d.id:30s}  {d.description[:60]}{src}")
        lines.append("")

    return "\n".join(lines) if lines else "(no tools loaded)"


def _format_subagents(reg: ToolRegistry) -> str:
    subagents = reg.subagents()
    if not subagents:
        return "(no subagents found in .minicode/agents/*.md)"
    lines = [f"── subagents ({len(subagents)}) ──"]
    for a in subagents:
        lines.append(f"  - {a.name:24s}  {a.description[:60]}")
        lines.append(f"      {_rel_path(a.location, reg)}  ({len(a.system_prompt)} chars prompt)")
    return "\n".join(lines)


def _format_memory_overview(agents, rules) -> str:
    lines = ["── memory ──"]
    if agents is None:
        lines.append("  AGENTS.md  : (none)")
    else:
        lines.append(f"  AGENTS.md  : {len(agents.content)} chars  ({agents.path})")
    if not rules:
        lines.append("  rules/*.md : (none)")
    else:
        for r in rules:
            lines.append(f"  rules/{r.name}.md  : {len(r.content)} chars")
    return "\n".join(lines)


def _print_system_prompt_overview(agents, rules, system_prompt: str, reg) -> None:
    """打印系统提示词构成概览，帮助用户理解 Agent 的"记忆"来源。"""
    from minicode._ansi import _CYAN, _DIM, _GREEN, _GREY, _RESET, _c

    lines = [
        f"{_c('┌─', _DIM)} {_c('System Prompt Construction', _CYAN)} {_c('─' * 40, _DIM)}",
    ]

    sys_tokens = estimate_tokens(system_prompt)
    lines.append(f"{_c('│', _DIM)} {_c('system prompt total', _GREEN)}: {sys_tokens:,} tokens ({len(system_prompt):,} chars)")

    # AGENTS.md
    if agents is not None:
        a_tokens = estimate_tokens(agents.content)
        lines.append(f"{_c('│', _DIM)}   ├─ {_c('AGENTS.md', _GREEN)}: {a_tokens:,} tokens ({len(agents.content):,} chars)")
        # 打印前 3 行预览
        for i, line in enumerate(agents.content.strip().splitlines()[:3]):
            preview = line[:80] + ("..." if len(line) > 80 else "")
            lines.append(f"{_c('│', _DIM)}   │  {_c(preview, _GREY)}")
        if len(agents.content.strip().splitlines()) > 3:
            lines.append(f"{_c('│', _DIM)}   │  {_c('...', _GREY)}")
    else:
        lines.append(f"{_c('│', _DIM)}   ├─ {_c('AGENTS.md', _DIM)}: (none)")

    # rules
    if rules:
        lines.append(f"{_c('│', _DIM)}   ├─ {_c('rules/*.md', _GREEN)}: {len(rules)} file(s)")
        for r in rules:
            r_tokens = estimate_tokens(r.content)
            lines.append(f"{_c('│', _DIM)}   │  {r.name}.md: {r_tokens:,} tokens ({len(r.content):,} chars)")
    else:
        lines.append(f"{_c('│', _DIM)}   ├─ {_c('rules/*.md', _DIM)}: (none)")

    # tools schema
    s = reg.summary()
    if s is not None:
        lines.append(f"{_c('│', _DIM)}   └─ {_c('tools schema', _GREEN)}: {s.builtin_count} builtin + {s.mcp_connected}/{s.mcp_servers} mcp + {s.skill_count} skills + {s.subagent_count} subagents")

    lines.append(f"{_c('└', _DIM)}{_c('─' * 60, _DIM)}")
    print("\n".join(lines))
    print()


def _format_hooks(reg: ToolRegistry) -> str:
    h = reg.hooks_dispatcher()
    if h is None or not h.hooks():
        return "(no hooks loaded from .minicode/hooks/)"
    lines = [f"── hooks ({len(h.hooks())}) ──"]
    for info in h.infos():
        lines.append(f"  - [{info.kind:6s}] {info.name:24s}  {info.description[:50]}")
        lines.append(f"      {_rel_path(info.path, reg)}")
    return "\n".join(lines)


def _rel_path(path: Any, reg: Optional[ToolRegistry] = None) -> str:
    """将路径转为相对于项目根的显示。"""
    try:
        root = reg._paths.project_root if reg is not None else Path.cwd()  # type: ignore[attr-defined]
        return str(Path(str(path)).relative_to(root))
    except (ValueError, AttributeError):
        return str(path)


# ─────────────────────────────────────────────────────────────
# 预算压力预处理（在跑 agent 前调用）
# ─────────────────────────────────────────────────────────────


def _replace_history(history: List[Message], new_history: List[Message]) -> None:
    """就地替换 history 内容，保持列表引用不变。"""
    history.clear()
    history.extend(new_history)


async def _pre_agent_budget_triage(
    history: List[Message],
    system_prompt: str,
    budget: ContextBudget,
    mreg: ModelRegistry,
) -> ContextBudget:
    """按压力等级在 agent 运行前预处理 history：soft-trim / hard-trim / compact。"""
    budget = budget.measure(system_prompt, history)
    level = budget.pressure_level

    if level >= 1:
        before = len(history)
        new_history = soft_trim_tool_results(history)
        if len(new_history) < before:
            _replace_history(history, new_history)
            budget = budget.measure(system_prompt, history)
            print(f"[auto-soft-trim] pressure={level}, trimmed old tool results")

    if budget.pressure_level >= 2:
        before = len(history)
        new_history = hard_trim_tool_results(history)
        if len(new_history) < before:
            _replace_history(history, new_history)
            budget = budget.measure(system_prompt, history)
            print(f"[auto-hard-trim] pressure={budget.pressure_level}, cleared old tool results")

    if budget.should_compact:
        m = mreg.current()
        if m is not None:
            print("[auto-compact] pressure=3, compacting before agent run...")
            try:
                new_history, summary = await compact_messages(
                    m, history, keep_turns=budget.history_window,
                )
                if summary:
                    _replace_history(history, new_history)
                    budget = budget.measure(system_prompt, history)
                    print(f"[auto-compact] done, saved tokens, summary: {summary[:80]}...")
            except Exception as e:
                history = truncate_messages(history, keep_turns=budget.history_window)
                budget = budget.measure(system_prompt, history)
                print(f"[auto-compact failed: {e}, fallback to truncate]")

    return budget


# ─────────────────────────────────────────────────────────────
# REPL
# ─────────────────────────────────────────────────────────────


async def _repl(paths: MinicodePaths, resume: Optional[str] = None) -> None:
    paths.ensure_project_dir()
    print(BANNER.format(minicode_version=minicode.__version__))
    print(f"project: {paths.project_root}")
    print(f"config : {paths.project_dir}")
    print()
    print("Type /help for commands.")
    print()

    reg = ToolRegistry(paths)
    mreg = ModelRegistry(paths.config_yaml)
    session_id = reg.session_id()
    session_start_ts = time.monotonic()

    # per-session permission 服务（默认走阻塞式 default_prompt）
    # —— CLI 子命令可以替换 / 调整
    permission_service = PermissionService(prompt_fn=default_prompt, session_id=session_id)
    reg.set_permission_service(permission_service)

    try:
        await reg.build()
    except Exception as e:
        print(f"[fatal] failed to build tool registry: {e}", file=sys.stderr)
        return

    mreg.build()

    # 配置自动修正的告警（base_url 缺协议头等）打 stderr
    from minicode.config import drain_auto_fix_warnings
    for w in drain_auto_fix_warnings():
        print(f"[warn] {w}", file=sys.stderr)

    # 加载记忆
    agents = load_agents_md(paths.project_dir)
    rules = load_rules(paths.project_dir)
    system_prompt = assemble_system(agents, rules, paths.project_root)

    s = reg.summary()
    if s is not None:
        print(
            f"[tools] {s.builtin_count} builtin · "
            f"{s.mcp_connected}/{s.mcp_servers} mcp · "
            f"{s.skill_count} skills · "
            f"{s.subagent_count} subagents · "
            f"{s.hook_count} hooks"
        )
    m = mreg.current()
    if m is not None:
        ctx_w = mreg.context_window()
        ctx_str = f"  ctx_window={ctx_w}" if ctx_w else "  ctx_window=(default 8000)"
        print(f"[model] {m.info.id}  type={m.info.type}  model={m.info.model}  base={m.info.base_url}{ctx_str}")
    mem_status = "loaded" if (agents or rules) else "(empty)"
    print(f"[memory] {mem_status}  sys={estimate_tokens(system_prompt)} tok  (AGENTS={'y' if agents else 'n'} rules={len(rules)})")
    print(f"[session] id={session_id}")
    print()

    # 展示系统提示词构成概览（教育用途：让用户理解 Agent 的"记忆"从哪来）
    _print_system_prompt_overview(agents, rules, system_prompt, reg)

    # 加载自定义命令（.minicode/commands/*.md）
    cmd_loader = CommandLoader(paths.commands_dirs)
    cmd_loader.scan()
    custom_cmds = [(c.slash_name, c.description) for c in cmd_loader.all()]
    if custom_cmds:
        print(f"[commands] {len(custom_cmds)} custom commands loaded")

    # 注入 subagent 依赖：把 model + tool_registry 注入到 SubagentTool
    # 防递归：subagent 看到的 tool 列表要排除 delegate_to_subagent 自身
    subagent_tool = reg.get("delegate_to_subagent")
    if subagent_tool is not None and isinstance(subagent_tool.tool, SubagentTool):
        subagent_tool.tool.set_tool_registry(reg)
        if m is not None:
            subagent_tool.tool.set_model(m)

    # 运行时状态
    history: List[Message] = []

    # -resume：加载历史会话
    resumed_session_id: Optional[str] = None
    if resume is not None:
        from minicode.memory import load_history, load_latest, list_sessions, format_session_list
        if resume == "latest":
            result = load_latest(paths.history_dir)
            if result is not None:
                resumed_session_id, loaded_msgs = result
                history.extend(loaded_msgs)
                print(f"[resume] loaded session {resumed_session_id[:8]} ({len(loaded_msgs)} messages)")
            else:
                print("[resume] no saved sessions found, starting fresh")
        else:
            # resume 是指定的 session_id（支持前缀匹配）
            loaded = load_history(paths.history_dir, resume)
            if loaded is None:
                # 尝试前缀匹配
                sessions = list_sessions(paths.history_dir)
                match = [s for s in sessions if s.session_id.startswith(resume)]
                if match:
                    resumed_session_id = match[0].session_id
                    loaded = load_history(paths.history_dir, resumed_session_id)
                else:
                    print(f"[resume] session '{resume}' not found")
                    print("Available sessions:")
                    print(format_session_list(sessions))
                    return
            if loaded is not None:
                if resumed_session_id is None:
                    resumed_session_id = resume
                history.extend(loaded)
                print(f"[resume] loaded session {resumed_session_id[:8]} ({len(loaded)} messages)")

    # 上下文窗口：优先用 config.yaml 里的 context_window；没配就用默认 8000
    ctx_window = mreg.context_window()
    budget = ContextBudget(
        system_tokens=estimate_tokens(system_prompt),
        history_tokens=0,
        limit=ctx_window if ctx_window >= 1000 else 8000,
    )
    # 如果有恢复的历史，重算 budget
    if history:
        budget = budget.measure(system_prompt, history)

    # /goal 服务（per-session 状态机）
    goal_service = GoalService()

    # bus + chat bridge（外部聊天软件接入）
    bus = Bus()
    chat_bridge: Optional[ChatBridgeManager] = None
    # 默认 runner：如果有 model → 调 model；否则 echo
    async def _bridge_runner(hist: List[Message]) -> str:
        m = mreg.current()
        if m is None:
            if not hist:
                return ""
            return f"[bridge echo] {hist[-1].text()[:300]}"
        try:
            resp = await m.complete(hist, system=system_prompt)
            return resp.message.text() or ""
        except Exception as e:
            return f"[bridge model error] {e}"

    chat_bridge = ChatBridgeManager(
        bus=bus, history=history,
        model_runner=_bridge_runner, session_id=session_id,
    )

    # 构造 hook context
    hook_ctx = HookContext(
        cwd=paths.project_root,
        project_root=paths.project_root,
        minicode_version=minicode.__version__,
        env=dict(os.environ),
    )
    hooks = reg.hooks_dispatcher()

    # session_start hook
    if hooks is not None and hooks.hooks():
        try:
            await hooks.emit(
                EventName.SESSION_START, session_id, hook_ctx,
                cwd=str(paths.project_root),
                model=m.info.model if m else None,
            )
        except Exception as e:
            print(f"[hooks] session_start failed: {e}", file=sys.stderr)

    message_count = 0

    try:
        while True:
            try:
                # ctx 状态栏 + 输入提示（TTY 下支持斜杠命令补全）
                # 确保上次输出与本次提示之间有换行
                print()
                if _is_tty():
                    prompt = format_status(budget)
                    from minicode.cli.input import read_line_with_completion
                    line = read_line_with_completion(prompt, extra_commands=custom_cmds)
                else:
                    line = input("minicode> ")
            except EOFError:
                print()
                break
            except KeyboardInterrupt:
                print("\n(中断，输入 /exit 退出)")
                continue

            line = line.rstrip()
            if not line:
                continue

            if line.startswith("/"):
                # 让 hook 看到"用户想发什么命令"——v0: user_prompt_submit 暂不触发
                # （只对自由输入触发，避免把 /help 这种内部命令也汇报出去）
                await _handle_command(
                    line, reg, mreg, paths, history, budget, system_prompt,
                    agents, rules, hooks, session_id, hook_ctx,
                    start_ts=session_start_ts, message_count=message_count,
                    goal_service=goal_service,
                    chat_bridge=chat_bridge, bus=bus,
                    permission_service=permission_service,
                    cmd_loader=cmd_loader,
                )
                if line.startswith("/exit") or line.startswith("/quit"):
                    break
            else:
                # 自由输入 → 触发 user_prompt_submit hook
                if hooks is not None and hooks.hooks():
                    ev = HookEvent.make(
                        EventName.USER_PROMPT_SUBMIT, session_id,
                        prompt=line,
                    )
                    res = await hooks.dispatch(ev, hook_ctx)
                    if res.denied:
                        print(f"[hook denied] {res.reason or '(no reason)'}")
                        continue
                    if res.action.value == "modify" and isinstance(res.data, dict):
                        new_prompt = res.data.get("prompt")
                        if isinstance(new_prompt, str):
                            line = new_prompt

                # 把 user 消息加进去
                history.append(Message.user(line))
                message_count += 1
                budget = await _pre_agent_budget_triage(history, system_prompt, budget, mreg)

                # 调 LLM 走 ReAct 循环
                m = mreg.current()
                if m is None:
                    print("[agent] no model loaded — falling back to echo")
                    print(f"(echo) {line}")
                else:
                    from minicode.tool.base import ToolContext
                    tool_ctx = ToolContext(
                        session_id=session_id,
                        cwd=paths.project_root,
                        abort=None,
                        extra={},
                    )
                    schemas = _build_main_tool_schemas(reg)
                    await _stream_agent_run(
                        model=m,
                        system_prompt=system_prompt,
                        history=history,
                        tool_registry=reg,
                        ctx=tool_ctx,
                        tool_schemas=schemas,
                        hooks=hooks,
                        session_id=session_id,
                        hook_ctx=hook_ctx,
                        budget=budget,
                    )

                # 刷新 budget（assistant + tool 消息已经 append 进 history）
                budget = budget.measure(system_prompt, history)
    finally:
        # 保存会话历史（退出时自动持久化）
        if history:
            from minicode.memory import save_history
            save_id = resumed_session_id or session_id
            try:
                saved = save_history(
                    paths.history_dir, save_id, history,
                    cwd=str(paths.project_root),
                )
                if saved is not None:
                    print(f"[history] saved {len(history)} messages to {saved.name}")
            except Exception as e:
                print(f"[history] save failed: {e}", file=sys.stderr)

        # session_end hook
        if hooks is not None and hooks.hooks():
            try:
                await hooks.emit(
                    EventName.SESSION_END, session_id, hook_ctx,
                    duration_s=time.monotonic() - session_start_ts,
                    message_count=message_count,
                )
            except Exception:
                pass

        # 停掉所有 chat adapter
        if chat_bridge is not None:
            try:
                await chat_bridge.stop_all()
            except Exception:
                pass
        await reg.aclose()


async def _handle_command(
    line: str,
    reg: ToolRegistry,
    mreg: ModelRegistry,
    paths: MinicodePaths,
    history: List[Message],
    budget: ContextBudget,
    system_prompt: str,
    agents,
    rules,
    hooks: Optional[HookDispatcher] = None,
    session_id: str = "",
    hook_ctx: Optional[HookContext] = None,
    start_ts: float = 0.0,
    message_count: int = 0,
    goal_service: Optional["GoalService"] = None,
    chat_bridge: Optional["ChatBridgeManager"] = None,
    bus: Optional["Bus"] = None,
    permission_service: Optional["PermissionService"] = None,
    cmd_loader: Optional[CommandLoader] = None,
) -> None:
    parts = line.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if cmd in ("/exit", "/quit", "/q"):
        # 触发 stop hook（可拒绝）
        if hooks is not None and hooks.hooks() and hook_ctx is not None:
            ev = HookEvent.make(EventName.STOP, session_id, reason="user_exit")
            try:
                res = await hooks.dispatch(ev, hook_ctx)
                if res.denied:
                    print(f"[hook denied stop] {res.reason or '(no reason)'}")
                    return
            except Exception:
                pass
        print("bye.")
        sys.exit(0)

    if cmd == "/help":
        print(HELP_TEXT)
        return

    if cmd == "/tools":
        print(_format_tools(reg))
        return

    if cmd == "/skills":
        print(_format_skills(reg))
        return

    if cmd == "/mcp":
        print(_format_mcp(reg))
        return

    if cmd == "/agents":
        print(_format_subagents(reg))
        return

    if cmd == "/hooks":
        print(_format_hooks(reg))
        return

    if cmd == "/model":
        await _cmd_model(arg, mreg, reg)
        return

    if cmd == "/memory":
        print(_format_memory_overview(agents, rules))
        return

    if cmd == "/context":
        from minicode.memory import compute_breakdown, format_context_box
        ctx_limit = mreg.context_window() if mreg.context_window() >= 1000 else 8000
        tool_schemas = _build_main_tool_schemas(reg)
        bd = compute_breakdown(system_prompt, tool_schemas, history, limit=ctx_limit)
        print(format_context_box(bd))
        return

    if cmd == "/history":
        from minicode.memory import list_sessions, format_session_list
        sessions = list_sessions(paths.history_dir)
        print("Saved sessions (newest first):")
        print(format_session_list(sessions))
        return

    if cmd == "/compact":
        await _cmd_compact(
            mreg, history, budget, system_prompt,
            hooks=hooks, session_id=session_id, hook_ctx=hook_ctx,
        )
        # 注意：_cmd_compact 会就地修改 history 列表
        return

    if cmd == "/goal":
        await _cmd_goal(
            arg, mreg, goal_service, session_id, history, system_prompt,
        )
        return

    if cmd == "/paths":
        print(_format_paths(paths))
        return

    if cmd == "/permission":
        await _cmd_permission(arg, permission_service)
        return

    if cmd == "/display":
        _cmd_display(arg, reg, mreg, system_prompt)
        return

    if cmd == "/call":
        await _cmd_call(arg, reg, paths)
        return

    if cmd == "/commands":
        if cmd_loader is not None:
            print(_format_commands(cmd_loader))
        else:
            print("(no custom commands loaded)")
        return

    if cmd == "/reload":
        # 重新 build（用户修了 config.yaml 后用）
        await reg.aclose()
        await reg.build()
        # 重新扫描自定义命令
        if cmd_loader is not None:
            cmd_loader.scan()
        errs = mreg.build()
        if errs:
            print("[reload] model config 仍然无效：", file=sys.stderr)
            for e in errs:
                print(f"  {e}", file=sys.stderr)
        s = reg.summary()
        if s is not None:
            print(
                f"[reload] tools: {s.builtin_count} builtin · "
                f"{s.mcp_connected}/{s.mcp_servers} mcp · "
                f"{s.skill_count} skills · "
                f"{s.subagent_count} subagents · "
                f"{s.hook_count} hooks"
            )
        m = mreg.current()
        if m is not None:
            print(f"[reload] model: {m.info.id}  model={m.info.model}")
        return

    # 自定义命令：检查 cmd_loader 中是否有匹配
    if cmd_loader is not None and cmd.startswith("/"):
        cmd_name = cmd[1:]  # 去掉 /
        cmd_info = cmd_loader.get(cmd_name)
        if cmd_info is not None:
            await _run_custom_command(cmd_info, arg, mreg, history, system_prompt, budget, reg, session_id, hook_ctx, hooks)
            return

    print(f"unknown command: {cmd} (try /help)")


async def _cmd_model(arg: str, mreg: ModelRegistry, treg: ToolRegistry) -> None:
    """`/model`  /  `/model test`"""
    if not arg:
        m = mreg.current()
        if m is None:
            print("(no model active)")
            return
        print(f"current: {m.info.id}  type={m.info.type}  model={m.info.model}")
        print(f"base_url: {m.info.base_url}")
        print(f"api_key : {'set' if m.api_key else '(empty)'}")
        if m.extra:
            print(f"extra   : {m.extra}")
        return

    head = arg.split(maxsplit=1)[0]
    if head == "test":
        await _cmd_model_test(mreg, treg)
        return

    print(f"unknown subcommand: {head} (try `/model` or `/model test`)")


async def _cmd_model_test(mreg: ModelRegistry, treg: ToolRegistry) -> None:
    m = mreg.current()
    if m is None:
        print("(no model active)")
        return
    msgs = [Message.user("ping")]
    print(f"[model.test] sending 1 message to {m.info.id} ({m.info.model}) ...")
    print("---- stream begin ----")
    text_chunks: List[str] = []
    tool_calls: dict = {}
    usage = None
    finish = ""
    err = ""
    try:
        async for ev in m.stream(msgs):
            if ev.type == "text_delta":
                sys.stdout.write(ev.text)
                sys.stdout.flush()
                text_chunks.append(ev.text)
            elif ev.type == "tool_call_delta":
                tool_calls[ev.tool_call_id] = ev.tool_name
                sys.stdout.write(f"\n[tool_call] {ev.tool_name}({ev.tool_args_delta})\n")
                sys.stdout.flush()
            elif ev.type == "usage" and ev.usage:
                usage = ev.usage
            elif ev.type == "finish":
                finish = ev.finish_reason
            elif ev.type == "error":
                err = ev.error
    except Exception as e:
        err = f"exception: {e}"
    print()
    print("---- stream end ----")
    if err:
        print(f"error : {err}")
    print(f"finish: {finish or '(none)'}")
    if usage:
        print(f"usage : in={usage.input_tokens}  out={usage.output_tokens}")


async def _cmd_compact(
    mreg: ModelRegistry,
    history: List[Message],
    budget: ContextBudget,
    system_prompt: str,
    hooks: Optional[HookDispatcher] = None,
    session_id: str = "",
    hook_ctx: Optional[HookContext] = None,
) -> None:
    """`/compact`：调 LLM 压缩旧消息。"""
    m = mreg.current()
    if m is None:
        print("(no model active)")
        return
    if not history:
        print("(no history to compact)")
        return
    print(f"[compact] before: {len(history)} msgs, {budget.history_tokens} hist-tokens")
    old_count = len(history)
    try:
        new_history, summary = await compact_messages(m, history, keep_turns=budget.history_window)
    except Exception as e:
        print(f"[compact] failed: {e}", file=sys.stderr)
        return
    if not summary:
        print("[compact] nothing to compress (old segment empty)")
        return
    _replace_history(history, new_history)
    new_budget = budget.measure(system_prompt, history)
    print(f"[compact] after:  {len(history)} msgs, {new_budget.history_tokens} hist-tokens")
    print(f"[compact] saved:  {budget.history_tokens - new_budget.history_tokens} hist-tokens")
    print(f"[compact] summary preview: {summary[:120]}{'...' if len(summary) > 120 else ''}")

    # 触发 compact hook
    if hooks is not None and hooks.hooks() and hook_ctx is not None:
        try:
            await hooks.emit(
                EventName.COMPACT, session_id, hook_ctx,
                old_count=old_count,
                new_count=len(history),
                summary_len=len(summary),
            )
        except Exception:
            pass


def _print_goal_status(goal_service: GoalService, session_id: str) -> None:
    """打印 goal 状态（供 /goal 和 /goal status 共用）。"""
    goal = goal_service.get(session_id)
    if goal is None:
        print("[goal] (no active goal)")
        print("       usage: /goal <condition>")
        return
    print(f"[goal] condition : {goal.condition}")
    print(f"        react     : {goal.react}")
    if goal.last_verdict is not None:
        print(f"        verdict   : {render_verdict(goal.last_verdict)}")
    else:
        print("        verdict   : (not judged yet)")


async def _cmd_goal(
    arg: str,
    mreg: ModelRegistry,
    goal_service: Optional[GoalService],
    session_id: str,
    history: List[Message],
    system_prompt: str,
) -> None:
    """`/goal [condition | clear | status]`。

    行为：
    - 无参数 / `status`  → 显示当前 goal 状态
    - `clear`            → 清除当前 goal
    - `<text>`           → 设置条件，调一次 judge 看 transcript 是否满足

    v0 简化：每次 set 都立即调一次 judge，给出 verdict。
    """
    if goal_service is None:
        print("[goal] GoalService 未初始化（bug）")
        return

    arg = (arg or "").strip()
    arg_lower = arg.lower()

    # 分支：clear
    if arg_lower == "clear":
        if goal_service.clear(session_id):
            print("[goal] cleared")
        else:
            print("[goal] no active goal to clear")
        return

    # 分支：status / 无参
    if arg_lower in ("", "status"):
        _print_goal_status(goal_service, session_id)
        return

    # 分支：help
    if arg_lower in ("help", "?"):
        print(
            "/goal <condition>  设置条件并立即 judge\n"
            "/goal clear        清除当前 goal\n"
            "/goal status       显示当前 goal 状态\n"
            "/goal              同 /goal status"
        )
        return

    # 分支：set + judge
    if not arg:
        # 空白 condition 走 status 分支（防御性，正常不会到这里）
        _print_goal_status(goal_service, session_id)
        return
    try:
        goal = goal_service.set(session_id, arg)
    except ValueError as e:
        print(f"[goal] invalid: {e}")
        return
    print(f"[goal] set: {goal.condition}")

    m = mreg.current()
    if m is None:
        print("[goal] (no model → skip judge; transcript 评估跳过)")
        return

    attempt = goal_service.next_attempt(session_id)
    # 准备 transcript：system + history
    judge_msgs: List[Message] = []
    if system_prompt:
        judge_msgs.append(Message.system(system_prompt))
    judge_msgs.extend(history)
    try:
        verdict = await judge_goal(m, goal.condition, judge_msgs, attempt=attempt)
    except Exception as e:
        print(f"[goal] judge exception: {e}")
        return

    goal_service.record_verdict(session_id, verdict)
    print(f"[goal] {render_verdict(verdict)}")
    if verdict.satisfied:
        # ok 或 impossible → 自动 clear，避免 stale 状态
        goal_service.clear(session_id)
        print("[goal] cleared (satisfied/impossible)")


# ─────────────────────────────────────────────────────────────
# 自定义命令
# ─────────────────────────────────────────────────────────────


def _format_commands(loader: CommandLoader) -> str:
    """格式化自定义命令列表。"""
    cmds = loader.all()
    if not cmds:
        return "(no custom commands found in .minicode/commands/)"
    lines = [f"── custom commands ({len(cmds)}) ──"]
    for c in cmds:
        desc = c.description or "(no description)"
        location = c.location
        try:
            # 显示相对路径
            cwd = Path.cwd()
            rel = location.relative_to(cwd)
            loc_str = str(rel)
        except ValueError:
            loc_str = str(location)
        lines.append(f"  /{c.name:24s}  {desc}")
        lines.append(f"      {loc_str}")
    return "\n".join(lines)


async def _run_custom_command(
    cmd_info,
    arg: str,
    mreg: ModelRegistry,
    history: List[Message],
    system_prompt: str,
    budget: ContextBudget,
    reg: ToolRegistry,
    session_id: str,
    hook_ctx: Optional[HookContext],
    hooks: Optional[HookDispatcher],
) -> None:
    """执行自定义命令：渲染模板，作为用户消息发送给 agent。"""
    m = mreg.current()
    if m is None:
        print(f"[command] no model loaded — cannot run /{cmd_info.name}")
        return

    # 渲染模板：$ARGUMENTS → 用户参数
    prompt_text = cmd_info.content.replace("$ARGUMENTS", arg)
    print(f"[command] running /{cmd_info.name} ...")
    print(f"[command] prompt: {prompt_text[:200]}{'...' if len(prompt_text) > 200 else ''}")

    # 触发 user_prompt_submit hook
    if hooks is not None and hooks.hooks() and hook_ctx is not None:
        ev = HookEvent.make(
            EventName.USER_PROMPT_SUBMIT, session_id,
            prompt=prompt_text,
        )
        res = await hooks.dispatch(ev, hook_ctx)
        if res.denied:
            print(f"[hook denied] {res.reason or '(no reason)'}")
            return
        if res.action.value == "modify" and isinstance(res.data, dict):
            new_prompt = res.data.get("prompt")
            if isinstance(new_prompt, str):
                prompt_text = new_prompt

    history.append(Message.user(prompt_text))
    budget = await _pre_agent_budget_triage(history, system_prompt, budget, mreg)

    from minicode.tool.base import ToolContext
    tool_ctx = ToolContext(
        session_id=session_id,
        cwd=Path.cwd(),
        abort=None,
        extra={},
    )
    schemas = _build_main_tool_schemas(reg)
    await _stream_agent_run(
        model=m,
        system_prompt=system_prompt,
        history=history,
        tool_registry=reg,
        ctx=tool_ctx,
        tool_schemas=schemas,
        hooks=hooks,
        session_id=session_id,
        hook_ctx=hook_ctx,
        budget=budget,
    )


async def _cmd_chat(
    arg: str,
    chat_bridge: Optional[ChatBridgeManager],
    bus: Optional[Bus],
    paths,
    history: List[Message],
    system_prompt: str,
    mreg,
) -> None:
    """`/chat [subcmd]` 管理 chat bridge。

    子命令：
    - list                          列出 adapter
    - status                        详细 status
    - start webhook [--port 8765]   启动 webhook
    - start stdio                   启动 stdio
    - stop [name|all]               停止（默认 all）
    - help                          帮助
    """
    if chat_bridge is None or bus is None:
        print("[chat] ChatBridge 未初始化（bug）")
        return

    parts = arg.split()
    sub = parts[0].lower() if parts else "status"
    rest = parts[1:]

    if sub in ("help", "?", ""):
        print(
            "/chat list                       列出 adapter\n"
            "/chat status                     详细 status\n"
            "/chat start webhook [--port N]   启动 webhook (default 8765)\n"
            "/chat start stdio                启动 stdin/stdout 桥接\n"
            "/chat stop [name|all]            停止 (default all)\n"
            "  注: webhook 出站消息写入 chat-outbox.jsonl"
        )
        return

    if sub == "list":
        adapters = chat_bridge.list_adapters()
        if not adapters:
            print("[chat] (no active adapter)")
            return
        for a in adapters:
            mark = "●" if a["running"] else "○"
            print(f"  {mark} {a['name']:12s} {a['type']:18s} running={a['running']}")
        return

    if sub == "status":
        st = chat_bridge.status()
        print(f"[chat] session   : {st['session_id']}")
        print(f"        uptime    : {st['uptime_s']:.1f}s")
        print(f"        in/out/err: {st['incoming']} / {st['outgoing']} / {st['errors']}")
        print(f"        history   : {st['history_len']} messages")
        print(f"        threads   : {len(st['threads'])}")
        for a in st["adapters"]:
            mark = "●" if a["running"] else "○"
            print(f"        {mark} {a['name']:12s} {a['type']}")
        return

    if sub == "start":
        if not rest:
            print("[chat] start <webhook|stdio> [...]")
            return
        name = rest[0].lower()
        if name == "webhook":
            port = 8765
            if "--port" in rest:
                try:
                    i = rest.index("--port")
                    port = int(rest[i + 1])
                except (ValueError, IndexError):
                    print("[chat] bad --port")
                    return
            outbound = str(paths.project_root / "chat-outbox.jsonl")
            adp = builtin_webhook_adapter(port=port, host="127.0.0.1", outbound_path=outbound)
            try:
                await chat_bridge.register(adp)
            except OSError as e:
                print(f"[chat] failed to start webhook on port {port}: {e}")
                return
            print(f"[chat] webhook started on http://127.0.0.1:{port}/chat")
            print(f"        outbound → {outbound}")
            print(f"        try: curl -X POST http://127.0.0.1:{port}/chat -H 'Content-Type: application/json' \\")
            print("                -d '{\"user\":\"alice\",\"channel\":\"#test\",\"thread\":\"t1\",\"text\":\"hi\"}'")
            return
        if name == "stdio":
            adp = builtin_stdio_adapter()
            try:
                await chat_bridge.register(adp)
            except Exception as e:
                print(f"[chat] failed to start stdio: {e}")
                return
            print("[chat] stdio started — type messages after the prompt; 'exit' to stop")
            return
        print(f"[chat] unknown adapter: {name} (available: webhook, stdio)")
        return

    if sub == "stop":
        target = rest[0].lower() if rest else "all"
        if target in ("all", "*"):
            n = await chat_bridge.stop_all()
            print(f"[chat] stopped {n} adapter(s)")
            return
        if await chat_bridge.unregister(target):
            print(f"[chat] stopped: {target}")
        else:
            print(f"[chat] no such adapter: {target}")
        return

    print(f"[chat] unknown subcommand: {sub} (try /chat help)")


def _format_skills(reg: ToolRegistry) -> str:
    skills = reg.skills()
    if not skills:
        return "(no skills found in .minicode/skills/)"
    lines = [f"── skills ({len(skills)}) ──"]
    for s in skills:
        loc = s.location
        try:
            rel = loc.relative_to(reg._paths.project_root)  # type: ignore[attr-defined]
            loc_str = str(rel)
        except ValueError:
            loc_str = str(loc)
        lines.append(f"  - {s.name:24s}  {s.description[:60]}")
        lines.append(f"      {loc_str}")
    return "\n".join(lines)


def _format_mcp(reg: ToolRegistry) -> str:
    statuses = reg.mcp_statuses()
    if not statuses:
        return "(no MCP servers in .minicode/mcp.json)"
    lines = [f"── mcp servers ({len(statuses)}) ──"]
    for s in statuses:
        marker = "✓" if s.connected else "✗"
        cfg = s.config
        if cfg.type == "stdio":
            target = " ".join([cfg.command] + cfg.args)
            extra = f"  cmd=`{target}`"
        else:
            extra = f"  url={cfg.url}"
        err = f"  err={s.error}" if s.error else ""
        lines.append(f"  {marker} {s.name:16s}  type={cfg.type}{extra}{err}")
        for td in s.tools:
            lines.append(f"      - {td.name} : {td.description[:60]}")
    return "\n".join(lines)


async def _cmd_call(arg: str, reg: ToolRegistry, paths: MinicodePaths) -> None:
    """`/call <id> {json}`：手动调用一个工具。"""
    if not arg:
        print("usage: /call <tool_id> [json-args]")
        return
    parts = arg.split(maxsplit=1)
    tool_id = parts[0]
    raw = parts[1] if len(parts) > 1 else "{}"
    try:
        args = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"invalid json: {e}")
        return
    from minicode.tool.base import ToolContext
    ctx = ToolContext(cwd=paths.project_root)
    result = await reg.execute(tool_id, args, ctx)
    print("── result ──")
    print(f"title   : {result.title}")
    print("output  :")
    for line in (result.output or "").splitlines():
        print(f"  {line}")
    if result.metadata:
        print(f"metadata: {json.dumps(result.metadata, ensure_ascii=False, default=str)}")


# ─────────────────────────────────────────────────────────────
# /permission  / /display
# ─────────────────────────────────────────────────────────────


async def _cmd_permission(arg: str, svc: Optional[PermissionService]) -> None:
    """`/permission` 子命令。

    - 无参 / status      → 显示当前 always-allow / always-deny + 计数
    - allow <id>         → 把 <id> 加进 always-allow
    - deny  <id>         → 把 <id> 加进 always-deny
    - clear [id]         → 清空 always 状态（不给 id = 清全部）
    - help               → 打印本帮助
    """
    if svc is None:
        print("[permission] PermissionService 未初始化（bug）")
        return

    arg = (arg or "").strip()
    parts = arg.split()
    sub = parts[0].lower() if parts else "status"
    rest = parts[1:]

    if sub in ("help", "?", "h"):
        print(
            "/permission                  显示当前 always 状态\n"
            "/permission allow <id>       把 <id> 加入 always-allow\n"
            "/permission deny  <id>       把 <id> 加入 always-deny\n"
            "/permission clear [id]       清空 always 状态（无 id = 全部）"
        )
        return

    if sub in ("status", ""):
        st = svc.status()
        allow = st["always_allow"]
        deny = st["always_deny"]
        print(f"[permission] session  : {st['session_id']}")
        print(f"             allow    : {allow or '(none)'}")
        print(f"             deny     : {deny or '(none)'}")
        print(
            f"             stats    : allow={st['stats']['allow']}  "
            f"allow_always={st['stats']['allow_always']}  "
            f"deny={st['stats']['deny']}  "
            f"skipped={st['stats']['skipped']}"
        )
        return

    if sub == "allow":
        if not rest:
            print("usage: /permission allow <tool_id>")
            return
        tool_id = rest[0]
        svc.always_allow(tool_id)
        print(f"[permission] always-allow: {tool_id}")
        return

    if sub == "deny":
        if not rest:
            print("usage: /permission deny <tool_id>")
            return
        tool_id = rest[0]
        svc.always_deny(tool_id)
        print(f"[permission] always-deny : {tool_id}")
        return

    if sub == "clear":
        if not rest:
            n = svc.clear()
            print(f"[permission] cleared ({n} entries)")
        else:
            tool_id = rest[0]
            n = svc.clear(tool_id)
            print(f"[permission] cleared {tool_id!r} ({n} entries)")
        return

    print(f"[permission] unknown subcommand: {sub} (try `/permission help`)")


def _cmd_display(
    arg: str,
    reg: ToolRegistry,
    mreg: ModelRegistry,
    system_prompt: str,
) -> None:
    """`/display demo`：渲染 demo（thinking / model-input / tool-call / code-change）。

    demo 用真实数据：取 system_prompt（如果非空）、registry 的 tool schemas、几个示例 change。
    """
    arg = (arg or "").strip()
    sub = arg.split()[0].lower() if arg else "demo"

    if sub in ("help", "?", "h"):
        print(
            "/display demo   渲染一份 demo（thinking / model-input / tool-call / code-change）"
        )
        return

    if sub != "demo":
        print(f"[display] unknown subcommand: {sub} (try `/display demo`)")
        return

    # 1) thinking
    sample_thinking = (
        "用户让我实现 permission 系统。\n"
        "我需要：\n"
        "  1. 三选项 prompt：Yes / Yes-and-always / No\n"
        "  2. per-session always 集合\n"
        "  3. 异步友好（await）\n"
        "  4. 集成到 ToolRegistry.execute\n"
    )
    print(render_thinking(
        ThinkingBlock(content=sample_thinking, model="demo", duration_ms=120),
    ))
    print()

    # 2) model input
    msgs = [
        {"role": "system", "content": system_prompt or "(empty)"},
        {"role": "user", "content": "在 src/auth/login.py 里把 bcrypt 替换成 argon2"},
        {"role": "assistant", "content": "好的，我先 grep 一下哪些地方用 bcrypt。"},
        {"role": "tool", "content": "src/auth/login.py:18: from bcrypt import ...\nsrc/auth/login.py:42: bcrypt.hashpw(...)", "tool_call_id": "call_01"},
    ]
    tools_preview = None
    if reg is not None:
        try:
            tools_preview = [
                {"name": d.id, "description": d.description}
                for d in reg.all()[:6]
            ]
        except Exception:
            tools_preview = None
    m = mreg.current() if mreg is not None else None
    print(render_model_input(ModelInputView(
        system=system_prompt or "",
        messages=msgs,
        model=getattr(m.info, "model", None) if m is not None else None,
        tools=tools_preview,
    )))
    print()

    # 3) tool call
    print(render_tool_call(ToolCallView(
        name="read",
        call_id="call_01",
        source="model",
        args={"path": "src/auth/login.py", "limit": 80},
    )))
    print()

    # 4) code change (header + one)
    sample_change = CodeChange(
        path="src/auth/login.py",
        old_text=(
            "from bcrypt import hashpw, gensalt\n"
            "\n"
            "def hash_password(pw: str) -> bytes:\n"
            "    return hashpw(pw.encode(), gensalt())\n"
        ),
        new_text=(
            "from argon2 import PasswordHasher\n"
            "\n"
            "_ph = PasswordHasher()\n"
            "\n"
            "def hash_password(pw: str) -> str:\n"
            "    return _ph.hash(pw)\n"
        ),
        added=4,
        removed=3,
        note="demo: bcrypt → argon2",
    )
    sample_change2 = CodeChange(
        path="requirements.txt",
        old_text="bcrypt==4.1.2\n",
        new_text="argon2-cffi==23.1.0\n",
        added=1,
        removed=1,
    )
    print(render_code_change_header([sample_change, sample_change2]))
    print()
    print(render_code_change(sample_change, context=2))


# ─────────────────────────────────────────────────────────────
# Main agent：CLI 主回路用
# ─────────────────────────────────────────────────────────────


def _to_schema(defn) -> ToolSchema:
    """将 ToolDef 转为 LLM 视角的 ToolSchema。"""
    return ToolSchema(
        name=defn.id,
        description=defn.description,
        parameters=defn.json_schema(),
    )


def _build_main_tool_schemas(reg: ToolRegistry) -> List[ToolSchema]:
    """给主 LLM 用的 tool 列表。

    主 LLM 不应该看到 delegate_to_subagent 吗？这里**保留**——它本身就是一个
    用户视角的工具，主 LLM 想让 subagent 干活时应该能调。
    """
    return [_to_schema(d) for d in reg.all()]


async def _stream_agent_run(
    model,
    system_prompt: str,
    history: List[Message],
    tool_registry,
    ctx,
    tool_schemas: List[ToolSchema],
    hooks: Optional["HookDispatcher"] = None,
    session_id: str = "",
    hook_ctx: Optional["HookContext"] = None,
    budget: Optional[ContextBudget] = None,
) -> None:
    """主 agent 的 ReAct 流式跑法（CLI 用）。

    - 收到 text_delta → 直接 stdout.print(..., end="", flush=True) 实现 streaming
    - 收到 thinking_delta → 攒起来，等本轮结束用 render_thinking 打印
    - 收到 tool_call → 用 render_tool_call 打印
    - 收到 tool_result → 用 render_tool_result 打印结果（让小白理解 Agent 怎么用工具）
    - 收到 usage → 记录，finish 时打印每轮 token 用量
    - 收到 budget → 打印预算变化（auto-compact / auto-trim 通知）
    - 每轮 LLM 调用前 → 打印 model input（完整展示发给 LLM 的 prompt 构成）
    """
    from minicode.display import (
        CodeChange,
        ModelInputView,
        ThinkingBlock,
        ToolCallView,
        ToolResultView,
        build_model_input_messages,
        render_code_change_header,
        render_model_input,
        render_thinking,
        render_tool_call,
        render_tool_result,
    )
    from minicode.display.formatter import _c, _DIM, _CYAN, _GREEN, _YELLOW, _RED, _GREY

    # 给 code_change 累加（tool 返回 metadata.code_change 的话）
    pending_changes: List[CodeChange] = []
    # 思考内容攒起来：本轮 text 打完才打印（避免打扰流）
    pending_thinking: List[str] = []
    # 本轮 token 用量
    pending_usage: Optional[Any] = None
    # 本轮迭代号
    current_iteration = 0
    # 本轮思考开始时间
    thinking_start: Optional[float] = None

    # 渲染 model input（每轮都展示，让小白看清每次发给 LLM 的内容）
    def _show_model_input() -> None:
        try:
            view = ModelInputView(
                system=system_prompt,
                messages=build_model_input_messages(history),
                model=getattr(model.info, "model", None) if model else None,
                tools=[
                    {"name": s.name, "description": s.description}
                    for s in tool_schemas
                ] or None,
            )
            sys.stdout.write("\n" + render_model_input(view) + "\n")
            sys.stdout.flush()
        except Exception:
            pass

    def _flush_thinking() -> None:
        """把本轮累积的 thinking 一次性打出（合并显示）。

        一轮内可能出现多次 thinking（reasoning_content），必须合并成 1 个 block
        输出 —— 不能按 token 拆成多个 think 模块。
        """
        nonlocal pending_thinking
        if not pending_thinking:
            return
        duration_ms = None
        if thinking_start is not None:
            duration_ms = int((time.monotonic() - thinking_start) * 1000)
        block = ThinkingBlock(
            content="".join(pending_thinking),
            duration_ms=duration_ms,
        )
        sys.stdout.write("\n" + render_thinking(block) + "\n")
        sys.stdout.flush()
        pending_thinking = []

    async def on_event(ev: AgentEvent) -> None:
        nonlocal pending_thinking, pending_usage, current_iteration, thinking_start
        if ev.type == "iteration_start":
            current_iteration = ev.iteration
            thinking_start = time.monotonic()
            sys.stdout.write(
                f"\n{_c('══', _DIM)} {_c(f'Round {current_iteration}', _CYAN)} "
                f"{_c('═' * 50, _DIM)}\n"
            )
            sys.stdout.flush()
            _show_model_input()
            pending_thinking = []
            pending_usage = None
        elif ev.type == "text_delta":
            sys.stdout.write(ev.text)
            sys.stdout.flush()
        elif ev.type == "thinking_delta":
            # 兼容旧版 runtime（仍 emit thinking_delta 的情况），仅累积不 flush
            pending_thinking.append(ev.text)
        elif ev.type == "thinking_done":
            # 本轮所有 thinking 已由 runtime 合并为 1 个事件，直接渲染
            if ev.text:
                duration_ms = None
                if thinking_start is not None:
                    duration_ms = int((time.monotonic() - thinking_start) * 1000)
                block = ThinkingBlock(content=ev.text, duration_ms=duration_ms)
                sys.stdout.write("\n" + render_thinking(block) + "\n")
                sys.stdout.flush()
        elif ev.type == "tool_call":
            _flush_thinking()
            sys.stdout.write("\n" + render_tool_call(ToolCallView(
                name=ev.tool_name,
                args=ev.tool_args,
                call_id=ev.tool_call_id,
                source="model",
            )) + "\n")
            sys.stdout.flush()
        elif ev.type == "tool_result":
            # 展示工具执行结果（让小白理解 Agent 怎么用工具的）
            meta = ev.tool_result_metadata or {}
            exit_code = meta.get("exit_code")
            sys.stdout.write("\n" + render_tool_result(ToolResultView(
                name=ev.tool_name,
                call_id=ev.tool_call_id,
                content=ev.tool_result_content,
                is_error=ev.tool_result_is_error,
                exit_code=exit_code,
                metadata=meta,
            )) + "\n")
            sys.stdout.flush()
            # 累积 code_change
            cc = meta.get("code_change")
            if isinstance(cc, dict):
                try:
                    pending_changes.append(CodeChange(
                        path=cc.get("path", "?"),
                        old_text=cc.get("old_text"),
                        new_text=cc.get("new_text"),
                        added=int(cc.get("added", 0)),
                        removed=int(cc.get("removed", 0)),
                        note=cc.get("note"),
                    ))
                except Exception:
                    pass
        elif ev.type == "usage":
            pending_usage = ev.usage
        elif ev.type == "finish":
            # 本轮结束前 flush 思考
            _flush_thinking()
            # 展示本轮 token 用量
            if pending_usage is not None:
                usage = pending_usage
                sys.stdout.write(
                    f"\n{_c('──', _DIM)} {_c('token usage', _GREEN)} "
                    f"{_c(f'in={usage.input_tokens:,}  out={usage.output_tokens:,}  '
                          f'total={usage.total_tokens:,}', _DIM)} "
                    f"{_c('──', _DIM)}\n"
                )
                sys.stdout.flush()
            # 本轮结束分隔
            sys.stdout.write(
                f"{_c('══', _DIM)} {_c(f'Round {current_iteration} end', _CYAN)} "
                f"{_c(f'(finish_reason={ev.finish_reason})', _GREY)} "
                f"{_c('═' * 40, _DIM)}\n\n"
            )
            sys.stdout.flush()
        elif ev.type == "error":
            sys.stdout.write(f"\n{_c('[agent error]', _RED)} {ev.error}\n")
            sys.stdout.flush()
        elif ev.type == "budget":
            sys.stdout.write(f"\n{_c('[budget]', _YELLOW)} {ev.budget_msg}\n")
            sys.stdout.flush()

    try:
        await run_agent(
            model=model,
            system_prompt=system_prompt,
            history=history,
            tool_registry=tool_registry,
            ctx=ctx,
            tool_schemas=tool_schemas,
            on_event=on_event,
            max_iterations=20,
            budget=budget,
        )
    except Exception as e:
        sys.stdout.write(f"\n[agent fatal] {e}\n")
        sys.stdout.flush()
        return

    # 跑完后：一次性展示 code changes
    if pending_changes:
        from minicode.display import render_code_change_header
        sys.stdout.write("\n" + render_code_change_header(pending_changes) + "\n")
        sys.stdout.flush()

    # flush 残留 thinking
    if pending_thinking:
        from minicode.display import ThinkingBlock, render_thinking
        sys.stdout.write("\n" + render_thinking(ThinkingBlock(content="".join(pending_thinking))) + "\n")
        sys.stdout.flush()

    # 跑完没任何产出 → 提示一下
    last_assistant = None
    for m in reversed(history):
        if m.role.value == "assistant":
            last_assistant = m
            break
    if last_assistant is not None and not last_assistant.text() and not last_assistant.tool_calls():
        sys.stdout.write(
            "\n[no-output] 模型本轮没产出 text / tool_call。\n"
            "            可能原因：\n"
            "              - 模型只产出了 reasoning（已打印在上面的 thinking）\n"
            "              - 模型拒答（content_filter）\n"
            "              - 模型在最后一轮 finish_reason=length，输出被截断\n"
            "            试试 /model test ping 单独验证 model 是否能正常输出。\n"
        )
        sys.stdout.flush()
    if hooks is not None and hooks.hooks():
        try:
            # 取最后一条 assistant 的 text
            text = ""
            for m in reversed(history):
                if m.role.value == "assistant":
                    text = m.text()
                    break
            await hooks.emit(
                EventName.ASSISTANT_MESSAGE, session_id, hook_ctx,
                text=text, tool_calls=[],
            )
        except Exception:
            pass


if __name__ == "__main__":
    main()

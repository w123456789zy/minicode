"""
Subagent + Main agent 运行时：ReAct 循环。

关键设计（参考 mimo code 的 runLoop + processor）：
- 主 agent 每轮迭代后检查 token 预算，按压力等级分级处理：
  · level 1 (50-70%)：软裁剪旧 tool result（head+tail）
  · level 2 (70-85%)：硬裁剪旧 tool result（清空内容）
  · level 3 (85%+)：自动 compact（调 LLM 压缩历史）
- tool 输出截断：错误感知 head+tail（保留错误信息）
- doom loop 检测：连续相同 tool+args 3 次自动终止
- subagent 有基础预算保护，防止上下文溢出

调用方：
- cli/app.py 调 run_agent() 跑主 ReAct
- SubagentTool.execute() 调 run_subagent() 跑嵌套 ReAct
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from minicode.memory.budget import ContextBudget, estimate_message_tokens, estimate_tokens
from minicode.model.base import Model, ModelUsage, _PartialToolCall as _PartialTC
from minicode.model.message import (
    Message,
    Role,
    TextPart,
    ToolCallPart,
    ToolResultPart,
    ToolSchema,
)
from minicode.tool.base import ToolContext


# ─────────────────────────────────────────────────────────────
# tool 输出截断：错误感知 head+tail
# ─────────────────────────────────────────────────────────────

# 单条 tool 输出上限（字符数）
_MAX_TOOL_OUT = 8000
# head+tail 截断时各保留多少
_HEAD_LEN = 3000
_TAIL_LEN = 3000


def _truncate_tool_output(content: str, max_chars: int = _MAX_TOOL_OUT) -> str:
    """截断 tool 输出：超长时用 head+tail，错误感知。

    - 未超 max_chars → 原样返回
    - 超长 → 保留 head + tail，中间省略
    - 错误感知：扫尾部找 error/exception 等特征，有则多留 tail
    """
    if len(content) <= max_chars:
        return content

    # 错误感知：尾部 2048 字符有错误特征时，多留 tail
    tail_check = content[-2048:]
    has_error = any(
        kw in tail_check.lower()
        for kw in ("error", "exception", "failed", "fatal", "traceback", "panic", "exit code")
    )
    if has_error:
        head_len = _HEAD_LEN
        tail_len = _TAIL_LEN
    else:
        # 无错误：head 多留一点，tail 少留
        head_len = _HEAD_LEN + 1000
        tail_len = _TAIL_LEN - 1000

    head = content[:head_len]
    tail = content[-tail_len:] if tail_len > 0 else ""
    return (
        head
        + f"\n\n[... truncated, original {len(content)} chars, kept first {head_len} + last {tail_len} ...]\n\n"
        + tail
    )


# ─────────────────────────────────────────────────────────────
# doom loop 检测
# ─────────────────────────────────────────────────────────────

_DOOM_LOOP_THRESHOLD = 3  # 连续相同 tool+args 多少次触发


def _tool_call_signature(name: str, args: Dict[str, Any]) -> str:
    """生成 tool call 的签名（name + args 的稳定字符串），用于 doom loop 检测。"""
    try:
        return f"{name}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
    except (TypeError, ValueError):
        return f"{name}:{str(args)}"


def _check_doom_loop(recent_signatures: List[str], tc_name: str, tc_args: Dict[str, Any]) -> Optional[str]:
    """检测 doom loop：追加签名并检查最近 N 次是否全是同一签名。

    返回触发 doom loop 的 tool name，未触发返回 None。
    """
    sig = _tool_call_signature(tc_name, tc_args)
    recent_signatures.append(sig)
    if len(recent_signatures) >= _DOOM_LOOP_THRESHOLD:
        last_n = recent_signatures[-_DOOM_LOOP_THRESHOLD:]
        if len(set(last_n)) == 1:
            return tc_name
    return None


# ─────────────────────────────────────────────────────────────
# 公共：执行工具并返回 tool_result 消息
# ─────────────────────────────────────────────────────────────


async def _execute_tool_and_make_message(
    tool_registry,
    tc_name: str,
    tc_args: Dict[str, Any],
    tc_id: str,
    ctx: ToolContext,
) -> tuple[Message, Dict[str, Any]]:
    """执行一个工具调用，截断输出，返回 (Message(role=TOOL, ...), metadata)。"""
    metadata: Dict[str, Any] = {}
    try:
        result = await tool_registry.execute(tc_name, tc_args, ctx)
        content = result.output if isinstance(result.output, str) else str(result.output)
        content = _truncate_tool_output(content)
        is_error = bool(result.metadata and result.metadata.get("error"))
        metadata = dict(result.metadata) if result.metadata else {}
    except Exception as e:
        content = f"tool execution failed: {e}"
        is_error = True
    return Message(
        role=Role.TOOL,
        parts=[ToolResultPart(
            tool_call_id=tc_id,
            content=content,
            is_error=is_error,
        )],
    ), metadata


# ─────────────────────────────────────────────────────────────
# Subagent：一次性返回的嵌套 ReAct
# ─────────────────────────────────────────────────────────────


@dataclass
class SubagentResult:
    """run_subagent() 的返回值。"""
    text: str                       # subagent 最后输出的文本
    iterations: int = 0             # ReAct 循环了几轮
    tool_calls_made: List[str] = field(default_factory=list)  # 调过哪些工具
    usage_input: int = 0
    usage_output: int = 0
    error: Optional[str] = None


async def run_subagent(
    model: Model,
    subagent_system_prompt: str,
    task: str,
    tool_registry,                    # 注入：minicode.tool.registry.ToolRegistry
    ctx: ToolContext,
    tool_schemas: List[ToolSchema],   # 注入：父 LLM 看到的 tool 列表
    max_iterations: int = 10,
    on_progress: Optional[Any] = None,  # 可选：每轮迭代回调（CLI 用来打日志）
    context_limit: int = 0,           # 可选：上下文窗口上限（0 = 用默认 8000）
) -> SubagentResult:
    """跑一次 subagent。

    返回 SubagentResult；error 不为 None 时代表执行失败（被 catch 后包成文本给父 LLM）。

    基础预算保护：每轮检查 history token 估算，超 85% 时提前终止
    （subagent 不做 compact，因为它的 history 不需要跨轮保留）。
    """
    messages: List[Message] = [Message.user(task)]
    usage_in = 0
    usage_out = 0
    tool_calls_made: List[str] = []
    final_text = ""
    err: Optional[str] = None

    # 预算：subagent 用自己的 budget（不共享父 agent 的）
    limit = context_limit if context_limit >= 1000 else 8000
    budget = ContextBudget(
        system_tokens=estimate_tokens(subagent_system_prompt),
        history_tokens=estimate_message_tokens(messages[0]),
        limit=limit,
    )

    # doom loop 检测
    recent_signatures: List[str] = []

    iterations_done = 0
    for it in range(max_iterations):
        iterations_done = it + 1
        # 预算保护：超 85% 就停（subagent 不做 compact）
        if budget.should_compact:
            err = f"subagent context overflow (>{budget.compact_threshold} tokens), stopping early"
            break

        # 调一次 LLM
        try:
            resp = await model.complete(
                messages,
                tools=tool_schemas or None,
                system=subagent_system_prompt,
            )
        except Exception as e:
            err = f"model call failed: {e}"
            break

        if resp.usage:
            usage_in += resp.usage.input_tokens
            usage_out += resp.usage.output_tokens

        assistant_msg = resp.message
        tool_calls = assistant_msg.tool_calls()
        text = assistant_msg.text()

        if text:
            final_text = text  # 始终保留最近一次 text 作为结果

        if on_progress is not None:
            try:
                on_progress(it, assistant_msg)
            except Exception:
                pass

        # 没 tool_call → 这一轮就结束
        if not tool_calls:
            break

        # 把 assistant message 加进去（带 tool_call）
        messages.append(assistant_msg)
        budget = budget.with_added_tokens(estimate_message_tokens(assistant_msg))

        # doom loop 检测
        for tc in tool_calls:
            loop_name = _check_doom_loop(recent_signatures, tc.name, tc.arguments)
            if loop_name:
                err = f"doom loop detected: {loop_name} called {_DOOM_LOOP_THRESHOLD} times with same args"
                break
        if err:
            break

        # 执行所有 tool_call
        for tc in tool_calls:
            tool_calls_made.append(tc.name)
            tool_msg, _ = await _execute_tool_and_make_message(
                tool_registry, tc.name, tc.arguments, tc.id, ctx,
            )
            messages.append(tool_msg)
            budget = budget.with_added_tokens(estimate_message_tokens(tool_msg))

        if on_progress is not None:
            try:
                on_progress(it, None, post_tool=True)
            except Exception:
                pass
    else:
        # for-else：正常结束没 break → 说明达到 max_iterations
        err = f"subagent reached max iterations ({max_iterations})"

    return SubagentResult(
        text=final_text,
        iterations=iterations_done,
        tool_calls_made=tool_calls_made,
        usage_input=usage_in,
        usage_output=usage_out,
        error=err,
    )


# ─────────────────────────────────────────────────────────────
# Main agent：v2 ReAct 循环（CLI 主回路用）
# ─────────────────────────────────────────────────────────────


@dataclass
class AgentEvent:
    """run_agent 在每一步产出的事件，给调用方（CLI）做展示。"""
    # 类型：
    #   "iteration_start"  进入新一轮 LLM 调用
    #   "text_delta"       文本 token
    #   "thinking_delta"   思考 token（reasoning content，已废弃，改用 thinking_done）
    #   "thinking_done"    本轮所有 thinking 合并为 1 个事件（CLI 直接渲染）
    #   "tool_call"        攒齐了一次 tool_call（id + name + args）
    #   "tool_result"      一次 tool 执行完（name + content + is_error + result）
    #   "usage"            token 用量
    #   "finish"           一轮 LLM 结束（finish_reason）
    #   "error"            错误
    #   "budget"           预算变化（pressure level / auto-compact / auto-trim）
    #   "done"             整个 agent 跑完（final_text 填充）
    type: str

    # iteration_start
    iteration: int = 0
    # text_delta / thinking_delta
    text: str = ""
    # tool_call
    tool_call_id: str = ""
    tool_name: str = ""
    tool_args: Dict[str, Any] = field(default_factory=dict)
    # tool_result
    tool_result_content: str = ""
    tool_result_is_error: bool = False
    tool_result_metadata: Dict[str, Any] = field(default_factory=dict)
    # usage
    usage: Optional[ModelUsage] = None
    # finish
    finish_reason: str = ""
    # error
    error: str = ""
    # budget
    budget_msg: str = ""
    # done
    final_text: str = ""
    iterations: int = 0


AgentCallback = Callable[[AgentEvent], Awaitable[None]]


async def _emit(cb: Optional[AgentCallback], ev: AgentEvent) -> None:
    if cb is None:
        return
    try:
        await cb(ev)
    except Exception:
        # 回调异常不能拖死主循环
        pass


async def _maybe_auto_compact(
    model: Model,
    history: List[Message],
    budget: ContextBudget,
    system_prompt: str,
    on_event: Optional[AgentCallback],
) -> ContextBudget:
    """根据压力等级自动 compact / trim history。

    返回更新后的 budget。
    就地修改 history 列表（clear + extend，保持引用）。
    """
    from minicode.memory.truncation import (
        soft_trim_tool_results,
        hard_trim_tool_results,
    )
    from minicode.memory.compact import compact_messages

    def _replace_and_remeasure(new_history: List[Message]) -> ContextBudget:
        nonlocal budget
        history.clear()
        history.extend(new_history)
        after_tokens = sum(estimate_message_tokens(m) for m in history)
        budget = budget.with_added_tokens(after_tokens - before_tokens)
        return budget

    level = budget.pressure_level

    # level 1：软裁剪旧 tool result
    if level >= 1:
        before_tokens = budget.history_tokens
        new_history = soft_trim_tool_results(history)
        if len(new_history) != len(history) or any(
            new is not old for new, old in zip(new_history, history)
        ):
            budget = _replace_and_remeasure(new_history)
            await _emit(on_event, AgentEvent(
                type="budget",
                budget_msg=f"[auto-soft-trim] pressure={level}, saved {before_tokens - budget.history_tokens} tokens",
            ))

    # level 2：硬裁剪旧 tool result
    if budget.pressure_level >= 2:
        before_tokens = budget.history_tokens
        new_history = hard_trim_tool_results(history)
        if len(new_history) != len(history) or any(
            new is not old for new, old in zip(new_history, history)
        ):
            budget = _replace_and_remeasure(new_history)
            await _emit(on_event, AgentEvent(
                type="budget",
                budget_msg=f"[auto-hard-trim] pressure={budget.pressure_level}, saved {before_tokens - budget.history_tokens} tokens",
            ))

    # level 3：自动 compact（调 LLM 压缩）
    if budget.should_compact:
        before_tokens = budget.history_tokens
        try:
            new_history, summary = await compact_messages(
                model, history, keep_turns=budget.history_window,
            )
            if summary:
                budget = _replace_and_remeasure(new_history)
                await _emit(on_event, AgentEvent(
                    type="budget",
                    budget_msg=f"[auto-compact] saved {before_tokens - budget.history_tokens} tokens, summary: {summary[:80]}...",
                ))
        except Exception as e:
            # compact 失败不致命：降级到 truncate
            from minicode.memory.truncation import truncate_messages
            new_history = truncate_messages(history, keep_turns=budget.history_window)
            budget = _replace_and_remeasure(new_history)
            await _emit(on_event, AgentEvent(
                type="budget",
                budget_msg=f"[auto-compact failed: {e}, fallback to truncate] saved {before_tokens - budget.history_tokens} tokens",
            ))

    return budget


async def run_agent(
    model: Model,
    system_prompt: str,
    history: List[Message],
    tool_registry,
    ctx: ToolContext,
    tool_schemas: List[ToolSchema],
    on_event: Optional[AgentCallback] = None,
    max_iterations: int = 20,
    budget: Optional[ContextBudget] = None,
) -> str:
    """主 ReAct 循环：跑 model.stream，收到 tool_call 就执行，再灌回去。

    返回 final_text（assistant 最后一次输出的 text）。

    设计：
    - 流式：on_event 会被高频回调，调用方用 stream 输出
    - history 是 in-out：本函数会 append assistant/tool messages 进去
    - 硬限制：max_iterations 防死循环
    - 出错 → 把 error 当事件给 UI，直接 return
    - 预算管理：如果传入 budget，每轮迭代后检查压力等级，自动 compact/trim
    - doom loop 检测：连续相同 tool+args 3 次自动终止
    """
    final_text = ""
    iterations_done = 0

    # doom loop 检测
    recent_signatures: List[str] = []

    for it in range(max_iterations):
        iterations_done = it + 1
        await _emit(on_event, AgentEvent(type="iteration_start", iteration=it))

        # 一次流式调用
        text_parts: List[str] = []
        thinking_parts: List[str] = []
        tool_calls: Dict[str, "_PartialTC"] = {}
        usage: Optional[ModelUsage] = None
        finish = "stop"
        err: Optional[str] = None

        try:
            async for ev in model.stream(
                history, tools=tool_schemas or None, system=system_prompt,
            ):
                if ev.type == "text_delta":
                    text_parts.append(ev.text)
                    await _emit(on_event, AgentEvent(type="text_delta", text=ev.text))
                elif ev.type == "thinking_delta":
                    thinking_parts.append(ev.text)
                    # 不 emit 离散的 thinking_delta；stream 结束后统一 emit thinking_done
                    # 这样 CLI 侧就不可能把 thinking 拆成多个 block
                elif ev.type == "tool_call_delta":
                    if not ev.tool_call_id:
                        if tool_calls:
                            last = next(reversed(tool_calls.values()))
                            last.args_delta += ev.tool_args_delta
                            if ev.tool_name:
                                last.name = ev.tool_name
                        continue
                    if ev.tool_call_id not in tool_calls:
                        tool_calls[ev.tool_call_id] = _PartialTC(
                            id=ev.tool_call_id,
                            name=ev.tool_name,
                            args_delta=ev.tool_args_delta,
                        )
                    else:
                        p = tool_calls[ev.tool_call_id]
                        p.args_delta += ev.tool_args_delta
                        if ev.tool_name and not p.name:
                            p.name = ev.tool_name
                elif ev.type == "usage" and ev.usage:
                    usage = ev.usage
                    await _emit(on_event, AgentEvent(type="usage", usage=ev.usage))
                elif ev.type == "finish":
                    if ev.finish_reason:
                        finish = ev.finish_reason
                elif ev.type == "error":
                    err = ev.error
        except Exception as e:
            err = f"model stream failed: {e}"

        # 构造 assistant message
        parts: List[Any] = []
        if text_parts:
            parts.append(TextPart(text="".join(text_parts)))
        final_text = "".join(text_parts)  # 始终记最新一次

        tc_objs: List[ToolCallPart] = []
        for tc in tool_calls.values():
            try:
                args = json.loads(tc.args_delta) if tc.args_delta else {}
            except json.JSONDecodeError:
                args = {"_raw": tc.args_delta}
            tc_objs.append(ToolCallPart(id=tc.id, name=tc.name, arguments=args))
            await _emit(on_event, AgentEvent(
                type="tool_call",
                tool_call_id=tc.id,
                tool_name=tc.name,
                tool_args=args,
            ))
        if tc_objs:
            parts.extend(tc_objs)

        assistant_msg = Message(role=Role.ASSISTANT, parts=parts)
        history.append(assistant_msg)
        if budget is not None:
            budget = budget.with_added_tokens(estimate_message_tokens(assistant_msg))

        # 本轮所有 thinking 合并为 1 个事件（保证 CLI 只渲染 1 个 block）
        if thinking_parts:
            await _emit(on_event, AgentEvent(type="thinking_done", text="".join(thinking_parts)))

        await _emit(on_event, AgentEvent(type="finish", finish_reason=finish, usage=usage))

        # 错误处理：error → 不再继续，但保留 final_text（如果有部分输出）
        if err is not None:
            err_msg = f"[model error] {err}"
            await _emit(on_event, AgentEvent(type="error", error=err_msg))
            return final_text or err_msg

        # 没 tool_call → 收敛
        if not tc_objs:
            break

        # 执行 tool_calls
        for tc in tc_objs:
            tool_msg, metadata = await _execute_tool_and_make_message(
                tool_registry, tc.name, tc.arguments, tc.id, ctx,
            )
            # 提取 content 和 is_error 用于 emit
            tp = tool_msg.parts[0] if tool_msg.parts else None
            content = tp.content if tp else ""
            is_error = tp.is_error if tp else False

            await _emit(on_event, AgentEvent(
                type="tool_result",
                tool_call_id=tc.id,
                tool_name=tc.name,
                tool_result_content=content,
                tool_result_is_error=is_error,
                tool_result_metadata=metadata,
            ))

            history.append(tool_msg)
            if budget is not None:
                budget = budget.with_added_tokens(estimate_message_tokens(tool_msg))

        # doom loop 检测：在执行完 tool 后检查，避免阻止当前轮的执行
        doom_triggered = False
        for tc in tc_objs:
            loop_name = _check_doom_loop(recent_signatures, tc.name, tc.arguments)
            if loop_name:
                await _emit(on_event, AgentEvent(
                    type="error",
                    error=f"doom loop detected: {loop_name} called {_DOOM_LOOP_THRESHOLD} times with same args, stopping",
                ))
                doom_triggered = True
                break
        if doom_triggered:
            break

        # 每轮结束后：预算管理（自动 compact / trim）
        if budget is not None:
            budget = await _maybe_auto_compact(
                model, history, budget, system_prompt, on_event,
            )
    else:
        # for-else：达到 max_iterations 没收敛
        await _emit(on_event, AgentEvent(
            type="error",
            error=f"agent reached max iterations ({max_iterations})",
        ))

    await _emit(on_event, AgentEvent(
        type="done", final_text=final_text, iterations=iterations_done,
    ))
    return final_text

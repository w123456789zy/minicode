# minicode

> 终端原生的 AI 编程助手 · Terminal-native AI coding assistant (Python)

[![PyPI](https://img.shields.io/pypi/v/pyminicode?style=flat-square)](https://pypi.org/project/pyminicode/)
[![Python](https://img.shields.io/pypi/pyversions/pyminicode?style=flat-square)](https://pypi.org/project/pyminicode/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](LICENSE)
[![Status](https://img.shields.io/badge/status-v2%20stable-blue?style=flat-square)](#项目状态)

`minicode` 是一个用 Python 实现的**终端原生 AI 编程助手**，仿照 ClaudeCode 的设计思路，自研了一套完整的 Agent 内核：**ReAct 循环**、**分级记忆管理**、**可插拔工具 / Skill / Hook / Subagent**、**斜杠命令补全**。最大的特点是**完整展示 Agent 内部每一步的运行状态**——每一次输入提示词的构成、每一次工具调用的参数、中间思考过程，全部可见，非常适合用来学习代码 Agent 的内部机制。

---

## 📑 目录

- [特性](#特性)
- [快速开始](#快速开始)
  - [从 PyPI 安装（推荐）](#从-pypi-安装推荐)
  - [从 GitHub 源码安装](#从-github-源码安装)
  - [首次运行](#首次运行)
- [项目状态](#项目状态)
- [架构总览](#架构总览)
- [目录结构](#目录结构)
- [配置说明](#配置说明)
- [CLI 命令](#cli-命令)
- [工具 / Skill / Hook / Subagent](#工具--skill--hook--subagent)
- [记忆系统](#记忆系统)
- [权限系统](#权限系统)
- [与 ClaudeCode / mimo-code 的差异](#与-claudecode--mimo-code-的差异)
- [开发与测试](#开发与测试)
- [路线图](#路线图)
- [许可证](#许可证)

---

## 特性

- 🤖 **完整的 ReAct 循环** — user → LLM → tool_call → execute → result → LLM，标准 Agent 模式
- 🧠 **三级预算管理** — 软裁剪 → 硬裁剪 → 自动 compact，按压力等级自动保护上下文
- 🔧 **可插拔工具系统** — 8 个内置工具 + Skill + MCP + Subagent，统一接口
- 💬 **斜杠命令补全** — TTY 环境下输入 `/` 自动提示，Tab 补全到最长公共前缀
- 🪝 **双类型 Hook** — 支持 Python 和 Shell 两种 hook，事件粒度细化到 tool_call
- 🎯 **Goal + Judge** — 设置停止条件，judge LLM 独立评估任务完成度
- 🔐 **细粒度权限** — per-session always-allow / always-deny + 阻塞式询问
- 🌐 **多端 Chat Bridge** — stdio / webhook adapter，让外部聊天工具接入 agent
- 📝 **自定义命令** — `.minicode/commands/*.md` 文件即命令，支持 `$ARGUMENTS` 占位
- 🪟 **上下文可视化** — `/context` 命令打印 system / tools / history 的占比
- 🧪 **442 个测试用例** — 单元测试 + 端到端测试，全量通过

---

## 快速开始

### 从 PyPI 安装（推荐）

> - PyPI 包名（pip 用的名字）：**`pyminicode`**
> - Python 导入名（`import` 用的名字）：**`minicode`**
> - 命令行入口（安装后多出来的命令）：**`minicode`**

```bash
pip install pyminicode
```

升级到最新版：

```bash
pip install --upgrade pyminicode
```

指定版本：

```bash
pip install pyminicode==0.2.0
```

带开发依赖安装（如果要参与开发或跑测试）：

```bash
pip install "pyminicode[dev]"
```

### 从 GitHub 源码安装

```bash
git clone https://github.com/<your-username>/minicode.git
cd minicode
pip install -e .
```

或使用 `pip` 直接装 GitHub 仓库：

```bash
pip install git+https://github.com/<your-username>/minicode.git
```

### 首次运行

安装完成后：

```bash
# 1. 在当前项目目录初始化 .minicode/（含 config.yaml 模板 + skills/agents/hooks/commands 子目录）
minicode --init

# 2. 编辑 .minicode/config.yaml，填入你的 API Key
#    （推荐用环境变量：${OPENAI_API_KEY}）

# 3. 启动 REPL
minicode
```

环境变量模板：

```bash
# OpenAI / OpenAI 兼容服务
export OPENAI_API_KEY=sk-...

# Anthropic
export ANTHROPIC_API_KEY=sk-ant-...
```

> 第一次启动会自动加载 `.minicode/config.yaml`、构建 tool registry、加载 memory/hooks/agents/skills，然后进入 REPL。

如果你想在 Python 代码里调用（不是通过 REPL）：

```python
import minicode
print(minicode.__version__)        # '0.2.0'
from minicode.cli.app import main  # 等价于命令行 `minicode`
from minicode.model import ModelRegistry
from minicode.tool.registry import ToolRegistry
```

### 一次性命令

```bash
minicode --version          # 打印版本
minicode --paths            # 打印 .minicode 路径解析结果
minicode --init             # 在当前目录创建 .minicode/ + config.yaml 模板
minicode --check-config     # 校验配置文件
minicode --print-tools      # 列出所有工具
minicode --print-memory     # 打印加载的 AGENTS.md + rules
```

---

## 项目状态

| 版本 | 内容 |
| --- | --- |
| **v0** | 工具层（Tool / Registry / 8 个内置工具 + Skill + MCP） |
| **v1** | 模型层（OpenAI 兼容 / Anthropic / Demo provider） |
| **v2** ✅ 当前 | ReAct 循环 + 记忆系统（预算/裁剪/压缩/持久化/可视化）+ 权限 + Hook + Goal + Chat Bridge + 斜杠命令补全 + 自定义命令 + 代码简化 |

下一版（v3）计划：
- Google Gemini provider
- 远程 skill / agent 拉取（从 git / URL）
- 超过阈值的输出自动转临时文件 + 占位符
- chatbridge 多 session 隔离

---

## 架构总览

```
┌──────────────────────────────────────────────────────────────────────┐
│                           CLI (cli/app.py)                           │
│  REPL / 命令处理 / 斜杠补全 / 状态栏 / 渲染                           │
└──────────────────────────┬───────────────────────────────────────────┘
                           │
          ┌────────────────┼────────────────┐
          │                │                │
          ▼                ▼                ▼
   ┌────────────┐  ┌────────────┐  ┌────────────────┐
   │  ToolRegistry│  │ModelRegistry│  │ CommandLoader  │
   │  (all tools) │  │ (providers) │  │ (custom cmds)  │
   └──────┬─────┘  └──────┬─────┘  └────────────────┘
          │                │
          │                │
          ▼                ▼
   ┌──────────────────────────────────────────┐
   │              ReAct 循环 (agent/runtime.py) │
   │  user → LLM → tool_call → execute → result │
   │       ↑                          │        │
   │       └──────────────────────────┘        │
   │                                          │
   │  预算管理：soft/hard trim → compact       │
   │  doom loop 检测                           │
   │  tool 输出截断（错误感知 head+tail）       │
   └──────────────────────────────────────────┘
          │
          ├──────────────┬──────────────┬──────────────┐
          ▼              ▼              ▼              ▼
   ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
   │  builtin │  │  skill   │  │   mcp    │  │subagent  │
   │  tools   │  │  tools   │  │  tools   │  │ (nested) │
   └──────────┘  └──────────┘  └──────────┘  └──────────┘

   ┌──────────────────────────────────────────────────┐
   │              记忆系统 (memory/)                    │
   │  budget → truncation → compact → context_view     │
   │  history (持久化) / status (输入框前)             │
   └──────────────────────────────────────────────────┘

   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
   │  permission  │  │    hooks     │  │    goal      │
   │  (per-session)│  │ (python/shell)│  │  + judge    │
   └──────────────┘  └──────────────┘  └──────────────┘

   ┌──────────────────────────────────────────────────┐
   │              chatbridge (多端桥接)                  │
   │  stdio / webhook adapter → bus → model → history  │
   └──────────────────────────────────────────────────┘
```

---

## 目录结构

```
minicode/
├── pyproject.toml
├── README.md
├── LICENSE                             # MIT
├── .gitignore
├── .minicode/                          # per-project config dir
│   ├── skills/                         # skill files
│   │   ├── code-review/SKILL.md
│   │   ├── refactor/SKILL.md
│   │   └── test-gen/SKILL.md
│   ├── agents/                         # subagent definitions
│   │   ├── code-reviewer.md
│   │   ├── explorer.md
│   │   └── test-writer.md
│   ├── commands/                       # custom slash commands
│   │   ├── review.md
│   │   └── fix.md
│   ├── hooks/                          # python/shell hooks
│   ├── rules/                          # memory rules
│   ├── mcp.json                        # MCP server config (stdio / http)
│   ├── AGENTS.md                       # agent memory root
│   └── config.yaml                     # LLM provider 配置
├── minicode/                           # source
│   ├── __init__.py
│   ├── __main__.py
│   ├── _ansi.py                        # ANSI 颜色常量 + TTY 探测（公共模块）
│   ├── paths.py                        # .minicode 路径解析
│   ├── config.py                       # config.yaml 加载
│   ├── command.py                      # custom command loader
│   ├── registry.py                     # 全局 registry
│   ├── tool/                           # 工具层
│   │   ├── base.py                     # Tool 抽象基类 + ToolContext + ToolResult
│   │   ├── registry.py                 # ToolRegistry
│   │   ├── skill.py                    # SkillLoader
│   │   ├── mcp.py                      # McpClient + McpToolAdapter
│   │   └── builtin/                    # 8 个内置工具
│   │       ├── bash.py
│   │       ├── edit.py
│   │       ├── glob_tool.py
│   │       ├── grep_tool.py
│   │       ├── read.py
│   │       ├── write.py
│   │       ├── skill.py
│   │       └── subagent.py             # delegate_to_subagent
│   ├── model/                          # 模型层
│   │   ├── base.py                     # Model 抽象基类
│   │   ├── message.py                  # Message / Part / ToolSchema
│   │   ├── openai_compat.py            # OpenAI Chat Completions
│   │   ├── anthropic.py                # Anthropic Messages API
│   │   ├── demo.py                     # 假 provider（echo）
│   │   └── registry.py                 # ModelRegistry
│   ├── agent/                          # ReAct 循环 + subagent
│   │   ├── loader.py                   # SubagentLoader
│   │   └── runtime.py                  # run_agent / run_subagent + 预算管理
│   ├── memory/                         # 记忆系统
│   │   ├── budget.py                   # ContextBudget + token 估算
│   │   ├── truncation.py               # 分级裁剪
│   │   ├── compact.py                  # /compact 手动压缩
│   │   ├── context.py                  # AGENTS.md + rules → system prompt
│   │   ├── context_view.py             # /context 可视化
│   │   ├── history.py                  # 会话历史持久化
│   │   ├── loaders.py                  # AGENTS.md / rules 加载
│   │   └── status.py                   # 输入框前 ctx 状态栏
│   ├── permission/                     # 工具调用权限
│   ├── hooks/                          # Hook 系统
│   ├── goal/                           # 停止条件 + judge
│   ├── display/                        # 结构化渲染
│   ├── chatbridge/                     # 多端聊天桥接
│   └── cli/
│       ├── app.py                      # REPL + 命令处理
│       └── input.py                    # 斜杠命令补全
└── tests/                              # 442 个测试用例
    └── ...
```

---

## 配置说明

`.minicode/config.yaml`（由 `minicode --init` 自动生成）：

```yaml
# LLM provider：当前支持 openai / anthropic / demo
provider: openai

# API Key。推荐用环境变量：${OPENAI_API_KEY}
api_key: ${OPENAI_API_KEY}

# API base URL
base_url: https://api.openai.com/v1

# 模型名称
model: gpt-4o

# 上下文窗口大小（可选）。支持 128K / 1M / 128000 等写法
context_window: 128K

# 透传给 provider 的额外参数（可选）
extra:
  temperature: 0.7
  max_tokens: 8K
```

`provider` 可选值：

| 值 | 说明 |
| --- | --- |
| `openai` | 任何实现 OpenAI Chat Completions 的服务（OpenAI / DeepSeek / Moonshot / ollama / vllm / ...） |
| `anthropic` | Anthropic Messages API |
| `demo` | 进程内回显，无需网络（开箱即用） |

`extra` 字段透传给 provider 实现（OpenAI：`temperature` / `top_p` / `presence_penalty`；Anthropic：`max_tokens` 必填 / `temperature` / `top_k`）。

---

## CLI 命令

在 REPL 中输入 `/` 触发自动补全（Tab 键补全到最长公共前缀）。

| 命令 | 作用 |
| --- | --- |
| `/tools` | 列出所有工具（builtin + skill + mcp） |
| `/skills` | 列出 skill |
| `/agents` | 列出 subagent |
| `/hooks` | 列出已加载的 hook |
| `/mcp` | 列出 MCP 服务和状态 |
| `/model` | 显示当前 model 详情 |
| `/model test` | 流式发 "ping" 测试当前 model 的连通性 |
| `/memory` | 显示 AGENTS.md + rules |
| `/context` | 显示上下文窗口占用（进度条 + 分项明细） |
| `/history` | 列出历史会话 |
| `/compact` | 手动压缩历史对话（调 LLM 摘要） |
| `/goal` | 设置/查看停止条件 |
| `/goal judge` | 调 judge 评估是否完成 |
| `/chat` | chat bridge 管理 |
| `/permission` | 权限管理（always allow/deny/clear） |
| `/display` | 渲染 demo（thinking / tool-call / code-change） |
| `/paths` | 打印路径解析结果 |
| `/call <id> [json]` | 手动调用一个工具 |
| `/reload` | 重新 build registry |
| `/commands` | 列出自定义命令 |
| `/help` | 打印帮助 |
| `/exit` / `/quit` | 退出 |

---

## 工具 / Skill / Hook / Subagent

### 工具协议

所有工具实现同一接口（[minicode/tool/base.py](minicode/tool/base.py)）：

```python
class Tool(ABC):
    kind: ToolKind = ToolKind.BUILTIN

    @property
    def id(self) -> str: ...

    @property
    def description(self) -> str: ...

    @property
    def parameters(self) -> type[BaseModel]: ...

    async def execute(self, args: Parameters, ctx: ToolContext) -> ToolResult: ...
```

`ToolContext` 携带：`session_id` / `cwd` / `project_root` / `tool_registry` / `permission_service` / `hook_dispatcher` / `bus` / `model_registry` / `config` / `history` / `context_budget`。

`ToolResult` 字段：`title`（短标题）/ `output`（内容，str 或 list[Part]）/ `metadata`（dict）。

### Skill（`.minicode/skills/<name>/SKILL.md`）

```markdown
---
name: code-review
description: 严格审查代码改动并给出改进建议
---

你是 code reviewer ...
```

通过内置的 `skill` 工具动态加载。

### Hook（`.minicode/hooks/`）

支持 Python 和 Shell 两种 hook：

```python
# .minicode/hooks/my_hook.py
def run(event, context):
    if event.event == "tool_call_before":
        if event.data["tool"] == "bash":
            return HookResponse.deny("no bash allowed")
    return HookResponse.allow()
```

```bash
# .minicode/hooks/my_hook.sh
#!/bin/bash
EVENT=$(cat)
if echo "$EVENT" | jq -e '.event == "tool_call_before" and .data.tool == "bash"' > /dev/null; then
    echo '{"action":"deny","reason":"no bash"}'
    exit 0
fi
echo '{"action":"allow"}'
```

事件：`session_start`, `session_end`, `user_prompt_submit`, `assistant_message`, `tool_call_before`, `tool_call_after`, `error`, `stop`, `compact`

聚合规则：并行执行，任一 deny → 整体 deny，多个 modify → 串联合并。

### Subagent（`.minicode/agents/<name>.md`）

```markdown
---
name: code-reviewer
description: 严格审查代码改动并给出改进建议
---

你是 code reviewer ...
```

`delegate_to_subagent` 工具让父 LLM 把任务委派给 subagent，防递归（subagent 看不到此工具）。

### 自定义命令（`.minicode/commands/<name>.md`）

```markdown
---
description: 代码审查命令
---

请审查以下代码改动，给出改进建议。
用户输入：$ARGUMENTS
```

运行时 `$ARGUMENTS` 替换为用户输入。

### Chat Bridge（chatbridge/）

```
> /chat
> /chat register webhook https://example.com/webhook
> /chat status
```

支持 stdio / webhook adapter，让外部聊天工具接入 agent。

---

## 记忆系统

### Token 预算（memory/budget.py）

不引入 tiktoken，用 `chars / 3` 粗估。`ContextBudget` 跟踪 system + history 的 token，按压力等级分级：

| 等级 | 阈值 | 动作 |
| --- | --- | --- |
| 0 | < 50% | 无 |
| 1 | 50-70% | 软裁剪旧 tool result（head+tail） |
| 2 | 70-85% | 硬裁剪旧 tool result（清空内容） |
| 3 | ≥ 85% | 自动 compact（调 LLM 压缩历史） |

### 分级裁剪（memory/truncation.py）

- `soft_trim_tool_results` — 旧 tool result → head+tail（保留结构，压缩体积）
- `hard_trim_tool_results` — 旧 tool result → 清空标记（保留 tool_call 配对）
- `truncate_messages` — 丢弃整轮旧消息（最后的兜底）

保护最近 N 轮不动（避免裁到当前上下文）。

### `/compact` 手动压缩（memory/compact.py）

把旧消息喂给 LLM 生成摘要，替换成一条 assistant summary message。

### 上下文可视化（memory/context_view.py）

`/context` 命令展示：

```
┌──────────────────────────────────────────┐
│ context window  6500/8000  81.3%          │
│ [████████░░]                              │
│                                          │
│ breakdown                                │
│   system prompt   1200 (15.0%)           │
│   tools schema     800 (10.0%)           │
│   history         4500 (56.3%)           │
│     user text     1200                   │
│     assistant     1500                   │
│     tool calls     800                   │
│     tool results  1000                   │
│                                          │
│   output reserve  4000 (50.0%)           │
│   remaining       1500 (18.8%)           │
│                                          │
│   pressure: high (level 3)               │
└──────────────────────────────────────────┘
```

### 状态栏（memory/status.py）

输入框前显示：`minicode> [ctx 6500/8000 ████████░░] $_`

颜色：绿 (< 60%) / 黄 (60-85%) / 红 (> 85%)。

### 历史持久化（memory/history.py）

会话退出时保存到 `.minicode/history/{session_id}.json`，支持 `/history` 列出和恢复。

---

## 权限系统（permission/）

工具调用前询问用户：

```
[permission] tool 'bash' wants to run
            args: {"command": "ls -la"}
            [1] Yes
            [2] Yes, and always (allow this tool for the rest of the session)
            [3] No  [default: 1]
```

- `always_allow` / `always_deny` — per-session 状态
- `/permission` 管理：`allow <tool>` / `deny <tool>` / `clear`

---

## Goal + Judge（goal/）

设置停止条件，让 judge 独立 LLM 调用评估是否完成：

```
> /goal tests pass
Goal set: "tests pass"

> /goal judge
[judge] evaluating...
[goal not yet] test suite has 3 failures
```

Verdict：`ok`（满足）、`impossible`（不可达）、`error`（judge 失败，fail-open）。

---

## 斜杠命令补全（cli/input.py）

TTY 环境下输入 `/` 时在下方显示命令列表，继续输入实时过滤，Tab 补全到最长公共前缀，Enter 确认，Ctrl+C / Ctrl+D 中断。非 TTY 降级为 `input()`。

跨平台实现：Windows 用 `msvcrt.getwch()` 逐字符读取，POSIX 用 `termios` + `sys.stdin.read(1)`，不依赖 prompt_toolkit / rich。

---

## 与 ClaudeCode / mimo-code 的差异

| 维度 | mimo-code (TS) | minicode (Py) |
| --- | --- | --- |
| 语言 | TypeScript + Effect | Python 3.10+ + Pydantic |
| 异步模型 | Effect.gen | async/await |
| 工具 schema | zod | Pydantic BaseModel |
| MCP SDK | 官方 @modelcontextprotocol/sdk | 自研最小 JSON-RPC over stdio/http |
| 内置工具数 | 21 | 8（保留核心 + subagent 委派） |
| Skill 协议 | 完整（嵌套 + 外部目录 + 远程拉取） | 简化（`<name>/SKILL.md` + frontmatter） |
| Model 协议 | Vercel AI SDK | 自研（`stream()` + `complete()`） |
| provider | 11 种 | 3 种（openai-compat + anthropic + demo） |
| LLM 循环 | 接好 | **已接**（v2，ReAct + 预算管理） |
| 记忆系统 | 无 | 完整（预算/裁剪/压缩/持久化/可视化） |
| 权限 | 无 | per-session always + 阻塞 prompt |
| Hook | 无 | python/shell 双类型 |
| Goal/Judge | 无 | 独立 LLM 调用评估 |
| Chat Bridge | 无 | stdio/webhook adapter |
| 自定义命令 | 无 | .minicode/commands/*.md |
| 斜杠补全 | 无 | TTY 逐字符读取 + 实时过滤 |
| **可观测性** | 一般 | **突出** — 完整展示每一步 Agent 运行状态、提示词构成、tool_call 参数 |

> **核心特色**：本项目的最大亮点是**完整可观测性**——适合想要学习代码 Agent 内部运行机制的开发者。

---

## 开发与测试

克隆后开发模式安装：

```bash
git clone https://github.com/<your-username>/minicode.git
cd minicode
pip install -e ".[dev]"
```

跑测试：

```bash
pytest tests/ -v
```

代码风格检查（可选）：

```bash
ruff check minicode/
```

---

## 路线图

1. **Google provider**（v3）— Gemini generateContent
2. **远程 skill/agent 拉取** — 从 git repo / URL 拉取
3. **truncate 服务** — 超过阈值的输出写入临时文件，原始位置放占位
4. **多 session 隔离** — chatbridge 的 thread_key → session_id 映射
5. **测试覆盖** — 当前 442 个测试，目标 500+

---

## 已知问题

- judge 依赖 LLM 的 JSON 输出能力，弱模型可能解析失败（fail-open 处理）。
- chatbridge 的 webhook adapter 需要外部服务配合测试。

---

## 许可证

本项目使用 [MIT 许可证](LICENSE) 开源。

```
MIT License

Copyright (c) 2026 minicode authors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the above copyright notice and this permission
notice appearing in the Software.
```

---

## Star History

如果这个项目对你有帮助，欢迎在 GitHub 上点 ⭐ Star！

## 贡献

欢迎 PR / Issue。在提交 PR 前请：
1. 跑通 `pytest tests/ -v`
2. 保持 `ruff check` 通过
3. 保持现有功能不变

---

<div align="center">

**用 Python 重新实现的代码 Agent · Made for learning how agents work**

</div>

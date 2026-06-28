# minicode Project Context

> 用户核心诉求与项目身份描述。
> LLM 在每次对话开始时都会读这份文件，作为项目的"身份卡"。

## 项目目标
minicode 是一个 Python 写的 terminal-native AI coding assistant。
灵感来自 ClaudeCode，但要做成：纯 Python、可定制、易扩展。

## 核心原则
- **简洁优于功能** — 不堆 feature
- **协议兼容** — 至少支持 OpenAI Chat Completions + Anthropic Messages 两种协议
- **离线优先** — 工具层不依赖网络也能跑（demo provider、builtin tools）

## 当前进度
- v0: 工具层（builtin + skill + MCP）✅
- v1: 模型层（多 provider 抽象 + 流式）+ 记忆层（AGENTS.md / rules）✅
- v2: ReAct 循环（计划）

## 沟通风格
- 用户偏好直接、不要"good question"这种客套
- 写代码前先简短说明意图
- 中文沟通，代码注释用中文

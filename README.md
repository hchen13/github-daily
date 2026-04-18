# GitHub Daily

中文名：辛哥的开源风向

GitHub Daily 是一个 agent-independent 的开源技术刊物项目。

目标：每天产出一份面向 developer 的技术早报，覆盖两类信号：

1. 深度跟踪的标杆项目
   - anthropics/claude-code
   - openai/codex
   - openclaw/openclaw
   - NousResearch/hermes-agent

2. GitHub 社区热点
   - Daily Trending Top 3
   - Weekly Trending Top 10

## Core principles

- Agent-independent：采集、分析、出刊、发布四层解耦
- Source-first：优先直接从 GitHub 页面与 GitHub CLI 获取原始信号
- Publishable artifact：项目本身产出刊物，任意 agent 都只负责“取刊”和“分发”
- No harness lock-in：不绑定 OpenClaw、Hermes、Claude Code 中任何一个运行时

## Proposed architecture

- `collectors/` — collect raw inputs from GitHub repos and Trending pages
- `renderers/` — turn normalized data into publication-ready markdown/json
- `publishers/` — optional adapters for Feishu / email / webhook / file export
- `schedules/` — cron entrypoints or task definitions
- `docs/` — editorial rules, scope, and implementation plans

## Output shape

The publication should have two sections:

1. AI Agent 标杆项目动态
2. GitHub 开源社区风向

The output should be usable by any downstream runtime:
- local markdown file
- JSON payload
- webhook payload
- chat-ready message

## Data acquisition

- Repo tracking: `gh` CLI
- Trending discovery: fetch GitHub Trending HTML, parse repo slugs, then hydrate with `gh`

## Scheduling direction

Default daily run target:
- 08:00 local time

The scheduler should only generate artifacts.
Distribution should be a separate step.

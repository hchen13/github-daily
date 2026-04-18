# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Status

Scaffold only. `collectors/`, `renderers/`, `publishers/`, `schedules/` are empty directories — no build, lint, or test commands exist yet. Only `README.md` and `docs/项目定位.CN.md` carry real content.

When adding the first implementation, also establish the toolchain (language choice, dependency manifest, test runner) and record the actual commands here.

## Architecture intent

GitHub Daily ("辛哥的开源风向") is a **publication project**, not an agent plugin. It produces one artifact per day: a developer-facing technical briefing with two fixed sections:

1. **AI Agent 标杆项目动态** — tracking `anthropics/claude-code`, `openai/codex`, `openclaw/openclaw`, `NousResearch/hermes-agent`. Answers: what problems users hit, what the community is patching, what direction maintainers are pushing.
2. **GitHub 开源社区风向** — Daily Trending Top 3 and Weekly Trending Top 10. Answers: what's hot now, and which heat is a trend vs. noise.

The pipeline is four decoupled layers, each a top-level directory:

- `collectors/` — pull raw signals. Repo tracking uses the `gh` CLI; trending discovery fetches GitHub Trending HTML, parses repo slugs, then hydrates via `gh`.
- `renderers/` — normalize collected data into Markdown + JSON. Markdown + JSON are the canonical artifacts; chat/webhook payloads are re-wrappings of the same output.
- `publishers/` — optional adapters (Feishu, email, webhook, file export). Distribution is a separate step from artifact generation.
- `schedules/` — cron entrypoints. Default daily run is 08:00 local time. Schedulers only trigger artifact generation; they do not distribute.
- `docs/` — editorial rules, scope, implementation plans.

## Load-bearing constraints

These shape design choices and should not be violated without discussion:

- **Agent-independent.** No lock-in to OpenClaw, Hermes, or Claude Code as a runtime. The analysis layer must expose a stable interface so the backing model/agent can be swapped (Claude Code CLI today, API or other models later) without touching collectors or renderers.
- **Source-first.** Prefer direct GitHub pages and the `gh` CLI over third-party aggregators.
- **Publishable artifact.** The repo itself produces the publication. Downstream agents only fetch and distribute — they must not be required to do rendering.

## Language

User-facing content (the publication, `README.md`, `docs/`) is primarily Chinese. Code identifiers and file names stay in English.

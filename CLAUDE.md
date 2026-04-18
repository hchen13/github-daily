# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup

```
python3 -m venv .venv
.venv/bin/pip install -e .
```

Requires `gh` CLI on PATH and authenticated (`gh auth login`).

## Commands

- `python -m collectors.trending` — fetch GitHub Trending Daily Top 1 + Weekly Top 10, write `data/trending/YYYY-MM-DD.json`.
- `python -m collectors.repos` — fetch issues / PRs / commits (all branches) / releases for every enabled repo in `config.yaml`, write to SQLite at `data/github-daily.db`. Pass `--repo owner/name` to collect a single repo.

Both scripts accept `--verbose`. Output paths are controlled by `config.yaml` → `storage`.

No test suite yet.

## Architecture intent

GitHub Daily ("辛哥的开源风向") is a **publication project**, not an agent plugin. It produces one artifact per day: a developer-facing technical briefing with two fixed sections:

1. **AI Agent 标杆项目动态** — tracks the four repos listed in `config.yaml` under `repos:` (default: `anthropics/claude-code`, `openai/codex`, `openclaw/openclaw`, `NousResearch/hermes-agent`). For each: issues / PRs / commits (all branches) / releases.
2. **GitHub 开源社区风向** — Daily Trending **Top 1** + Weekly Trending Top 10, deduped.

The pipeline is four decoupled layers:

- `collectors/` — `trending.py` writes JSON per day; `repos.py` writes to SQLite. Repo tracking uses `gh` CLI; trending scrapes HTML then hydrates via `gh`.
- `renderers/` — (not built yet) normalize collected data into Markdown + JSON publication artifacts.
- `publishers/` — (not built yet) optional adapters for PDF/JPEG render, Feishu, webhook, email.
- `schedules/` — (not built yet) cron entrypoints. Default publish time from `config.yaml` is 08:00 local (Asia/Shanghai).

`config.py` is the single source of truth for runtime configuration; all other modules import `RepoConfig`, `CollectionConfig`, etc. from it.

## Storage split

- **Trending data**: JSON file per day under `data/trending/`. No SQLite — trending is publication-oriented and file-first fits the "publishable artifact" principle.
- **Tracked repo data**: SQLite at `data/github-daily.db`. Schema in `db/models.py`. Tables: `repos`, `issues`, `pull_requests`, `commits`, `releases`, `reports`, `fetch_log`, `analysis_steps`. Full schema is created up front to avoid future migrations.

## Load-bearing constraints

- **Agent-independent.** No lock-in to OpenClaw, Hermes, or Claude Code as a runtime. The (future) analysis layer must expose a stable interface so the backing model/agent can be swapped without touching collectors or renderers.
- **Source-first.** Prefer direct GitHub pages and the `gh` CLI over third-party aggregators.
- **Publishable artifact.** The repo itself produces the publication. Downstream agents only fetch and distribute — they must not be required to do rendering.

## Language

User-facing content (the publication, `README.md`, `docs/`) is primarily Chinese. Code identifiers and file names stay in English.

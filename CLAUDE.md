# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup

```
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/playwright install chromium     # one-time, ~150 MB
```

Requires `gh` CLI on PATH and authenticated (`gh auth login`), plus the `claude` CLI logged in (defaults to `/Users/claire/.local/bin/claude`; override in `config.yaml` → `analysis.claude_bin`).

## Commands

End-to-end:
- `python -m run` — full daily pipeline. Stages: trending → repos → narrator → reviewer → editor → render. Use `--only <stage>` or `--skip <stage>` (repeatable) to subset.
- `python -m run --workers 8 --verbose` — propagate parallelism + logging into the analyst stages.

Single stages (each accepts `--verbose`):
- `python -m collectors.trending` — Daily Top 1 + Weekly Top 10 → `data/trending/YYYY-MM-DD.json`.
- `python -m collectors.repos` — `gh`-based collection of issues/PRs/commits (all branches)/releases for every enabled repo in `config.yaml` → SQLite at `data/github-daily.db`. Pass `--repo owner/name` to scope to one.
- `python -m analysts.narrator` — 1a: per-repo 24h integrated narrative (sonnet), persisted to `analysis_steps`. Parallel via `--workers`.
- `python -m analysts.trending_reviewer` — 1b: opus reviews each trending repo's code (shallow clone + Read/Grep/Glob), cache-aware. Cache files at `data/trending_reviews/{owner}-{repo}.json`. `--force` to bypass cache.
- `python -m analysts.editor` — 2: assemble `data/publications/YYYY-MM-DD.md`. `--dry-run` to inspect the prompt that would be sent.
- `python -m renderers.publication` — render MD to `data/renders/YYYY-MM-DD/{publication.html,.pdf,.jpeg}` via Playwright. `--no-pdf` / `--no-jpeg` to subset.

Maintenance + serving:
- `python -m cleanup` — dry-run; lists trending review caches for repos off all lists for >7 days. `--apply` to delete.
- `python -m web` — FastAPI/uvicorn web UI on `127.0.0.1:8765`. Routes: `/` (latest), `/d/<date>`, `/d/<date>/{pdf,jpeg}`, `/archive`, `/settings`, `/api/publications`, `/api/config`.
- `python -m schedules.install {install,uninstall,status,render}` — manage the macOS launchd plist that runs `python -m run` at the configured `publish_time` daily.

No test suite yet.

## Architecture intent

GitHub Daily ("辛哥的开源风向") is a **publication project**, not an agent plugin. It produces one artifact per day: a developer-facing technical briefing with two fixed sections:

1. **AI Agent 标杆项目动态** — the four repos in `config.yaml` (default: `anthropics/claude-code`, `openai/codex`, `openclaw/openclaw`, `NousResearch/hermes-agent`). Issues / PRs / commits (all branches) / releases.
2. **GitHub 开源社区风向** — Daily Trending **Top 1** + Weekly Trending Top 10, deduped.

Pipeline layers:

- `collectors/` — `trending.py` (HTML scrape + `gh` hydrate, JSON output) and `repos.py` (`gh` CLI, SQLite output).
- `analysts/` — three writing roles, each backed by a `claude` CLI subprocess: `narrator.py` (1a, sonnet), `trending_reviewer.py` (1b, opus 4.7, gets shallow clone + Read/Grep/Glob), `editor.py` (2, sonnet, assembles publication MD).
- `renderers/publication.py` — MD → HTML (markdown-it-py) → PDF + JPEG (Playwright headless Chromium).
- `web/` — FastAPI app for browsing/downloading artifacts.
- `schedules/install.py` — launchd plist generator from `config.yaml`.
- `prompts/` — system prompts: `SOUL.md` (publication voice, prepended to every analyst), `narrator.md`, `trending_reviewer.md`, `editor.md`.
- `assets/themes/` — `apple.css` (lifted/adapted from getdesign.md/apple, MIT) + `shell.html`.

`config.py` is the single source of truth for runtime configuration; all other modules import from it.

## Storage split

- **Trending data**: JSON file per day at `data/trending/<date>.json`. File-first ("publishable artifact" principle).
- **Tracked repo data**: SQLite at `data/github-daily.db`. Schema in `db/models.py` — full schema created up front (incl. `reports`, `analysis_steps`, `fetch_log`) to avoid migrations.
- **Trending reviews**: JSON cache per repo at `data/trending_reviews/<owner>-<repo>.json`. Cache key is `(owner, repo)` only — no SHA check; while a repo stays on any trending list it's reused, after 7 days off-list `cleanup.py` purges.
- **Publications**: Markdown at `data/publications/<date>.md` (also stored in `reports` table).
- **Renders**: `data/renders/<date>/publication.{html,pdf,jpeg}`.

## Voice layer (`SOUL.md`)

`prompts/SOUL.md` defines the publication's persona — distilled from xinge's IDENTITY/SOUL but stripped of personal details. `analysts.build_system_prompt()` prepends it to every role-specific prompt before calling `claude`. To change the publication's tone, edit only this one file. To disable voice injection entirely, delete `SOUL.md` — the helper falls back to the role prompt alone.

Key voice rules: judgment-first, data-before-guess, plain language, acknowledge dull days, no over-extrapolation. Banned: emoji, "赋能/闭环/抓手/值得关注/重磅" and similar filler. Encouraged: explicit "看不准" / "不知道" / "直接跳过".

## Load-bearing constraints

- **Agent-independent.** No lock-in to OpenClaw, Hermes, or Claude Code as a runtime. The analysis layer must keep a stable interface so the backing model/agent can be swapped without touching collectors or renderers.
- **Source-first.** Prefer direct GitHub pages and the `gh` CLI over third-party aggregators.
- **Publishable artifact.** The repo itself produces the publication. Downstream agents only fetch and distribute — they must not be required to do rendering.

## Language

User-facing content (publication, `README.md`, `docs/`) is primarily Chinese. Code identifiers and file names stay in English.

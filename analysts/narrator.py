"""Narrator (1a): per-repo 24h integrated narrative.

For each tracked repo, pulls the last 24h of issues / PRs / commits / releases
from SQLite, writes them as JSON files to a working dir, then invokes claude
CLI with the narrator system prompt and the JSON file paths. The model is
expected to return one paragraph of prose (<=150 zh chars).

Run:
    python -m analysts.narrator                        # all enabled repos
    python -m analysts.narrator --repo anthropics/claude-code
    python -m analysts.narrator --anchor 2026-04-19T08:00:00+08:00
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from analysts import build_system_prompt
from analysts.repo_wiki import load_wiki
from config import RepoConfig, load_config
from db.models import get_db, init_db

logger = logging.getLogger("narrator")

WORK_ROOT = Path("/tmp/github-daily")
WINDOW_HOURS = 24


def _slug(full_name: str) -> str:
    return full_name.replace("/", "-")


def _iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def collect_window(repo: RepoConfig, db_path: str, cutoff_iso: str) -> dict:
    """Return dict of lists: issues, prs, commits, releases within the window."""
    full_name = repo.full_name
    out = {"issues": [], "prs": [], "commits": [], "releases": []}

    with get_db(db_path) as conn:
        rows = conn.execute("""
            SELECT issue_number, title, body, state, author, labels,
                   created_at, updated_at, closed_at, comments, url
            FROM issues
            WHERE repo_full_name = ?
              AND (updated_at >= ? OR created_at >= ? OR closed_at >= ?)
            ORDER BY updated_at DESC
        """, (full_name, cutoff_iso, cutoff_iso, cutoff_iso)).fetchall()
        out["issues"] = [dict(r) for r in rows]

        rows = conn.execute("""
            SELECT pr_number, title, body, state, author, labels,
                   base_branch, head_branch, created_at, updated_at, merged_at, url
            FROM pull_requests
            WHERE repo_full_name = ?
              AND (updated_at >= ? OR created_at >= ? OR merged_at >= ?)
            ORDER BY updated_at DESC
        """, (full_name, cutoff_iso, cutoff_iso, cutoff_iso)).fetchall()
        out["prs"] = [dict(r) for r in rows]

        rows = conn.execute("""
            SELECT branch, sha, author, message, committed_at, url
            FROM commits
            WHERE repo_full_name = ?
              AND committed_at >= ?
            ORDER BY committed_at DESC
        """, (full_name, cutoff_iso)).fetchall()
        out["commits"] = [dict(r) for r in rows]

        rows = conn.execute("""
            SELECT tag_name, name, body, is_prerelease, published_at, url
            FROM releases
            WHERE repo_full_name = ?
              AND published_at >= ?
            ORDER BY published_at DESC
        """, (full_name, cutoff_iso)).fetchall()
        out["releases"] = [dict(r) for r in rows]

    return out


def _write_dimension_files(workdir: Path, data: dict) -> dict[str, Path]:
    workdir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for dim, items in data.items():
        p = workdir / f"{dim}.json"
        p.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        paths[dim] = p
    return paths


def _build_user_prompt(repo: RepoConfig, paths: dict[str, Path],
                      counts: dict[str, int], anchor: datetime) -> str:
    window_start = anchor - timedelta(hours=WINDOW_HOURS)
    lines = [
        f"Repo: {repo.display_name} ({repo.full_name})",
        f"Window: {_iso_utc(window_start)} → {_iso_utc(anchor)} (24h)",
    ]

    wiki = load_wiki(repo.full_name)
    if wiki:
        lines += [
            "",
            "## 项目背景 wiki（你唯一的代码理解来源——遇到陌生术语先在这里查）",
            "",
            wiki,
        ]
    else:
        lines += [
            "",
            "（本 repo 暂无 wiki。你只能基于 issue/PR/commit 文本推断背景，"
            '不确定的地方直说"看不准"。）',
        ]

    lines += [
        "",
        "## 今日 24h 原始数据（用 Read 工具按需打开）",
        f"- {paths['issues']}  ({counts['issues']} issues)",
        f"- {paths['prs']}     ({counts['prs']} PRs)",
        f"- {paths['commits']} ({counts['commits']} commits)",
        f"- {paths['releases']} ({counts['releases']} releases)",
        "",
        "开始写。",
    ]
    return "\n".join(lines)


def run_narrator(repo: RepoConfig, db_path: str, claude_bin: str,
                 model: str, anchor: datetime) -> Optional[str]:
    cutoff_iso = _iso_utc(anchor - timedelta(hours=WINDOW_HOURS))
    data = collect_window(repo, db_path, cutoff_iso)
    counts = {k: len(v) for k, v in data.items()}
    logger.info("[%s] window counts: %s", repo.full_name, counts)

    workdir = WORK_ROOT / anchor.strftime("%Y-%m-%d") / _slug(repo.full_name)
    paths = _write_dimension_files(workdir, data)

    system_prompt = build_system_prompt("narrator.md")
    user_prompt = _build_user_prompt(repo, paths, counts, anchor)

    cmd = [
        claude_bin,
        "--print",
        "--model", model,
        "--allowedTools", "Read",
        "--system-prompt", system_prompt,
    ]

    logger.debug("[%s] invoking %s", repo.full_name, " ".join(cmd[:5]))
    t0 = time.time()
    try:
        result = subprocess.run(
            cmd,
            input=user_prompt,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        logger.error("[%s] claude CLI timed out", repo.full_name)
        return None
    duration = time.time() - t0

    if result.returncode != 0:
        logger.error("[%s] claude CLI failed: %s", repo.full_name, result.stderr.strip()[:300])
        return None

    narrative = result.stdout.strip()
    logger.info("[%s] narrator done in %.1fs, %d chars", repo.full_name, duration, len(narrative))

    # Persist the step. report_date uses local date (publication-day intent),
    # not UTC date — anchor may be UTC but the publication is keyed on local date.
    report_date = anchor.astimezone().date().isoformat()
    with get_db(db_path) as conn:
        conn.execute("""
            INSERT OR REPLACE INTO analysis_steps
            (report_date, repo_full_name, step_name, analyst, model, content, duration_s)
            VALUES (?, ?, 'narrative_24h', 'narrator', ?, ?, ?)
        """, (report_date, repo.full_name, model, narrative, duration))

    return narrative


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run the narrator (1a) for tracked repos.")
    parser.add_argument("--repo", help="Only run for this repo (owner/name)")
    parser.add_argument(
        "--anchor",
        help="ISO datetime, the right edge of the 24h window (default: now).",
    )
    parser.add_argument("--config", help="Path to config.yaml")
    parser.add_argument("--workers", type=int, default=4, help="Parallel workers (default: 4)")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cfg = load_config(args.config)
    init_db(cfg.storage.db_path)

    if args.anchor:
        anchor = datetime.fromisoformat(args.anchor)
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=timezone.utc)
    else:
        anchor = datetime.now(timezone.utc)

    repos = cfg.enabled_repos
    if args.repo:
        repos = [r for r in repos if r.full_name == args.repo]
        if not repos:
            logger.error("repo %s not found in config", args.repo)
            return 1

    model = cfg.analysis.model_for("narrator")
    logger.info("Anchor: %s | Model: %s | Repos: %d | Workers: %d",
                _iso_utc(anchor), model, len(repos), args.workers)

    narratives: dict[str, Optional[str]] = {}
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {
            pool.submit(run_narrator, repo, cfg.storage.db_path,
                        cfg.analysis.claude_bin, model, anchor): repo
            for repo in repos
        }
        for fut in as_completed(futures):
            repo = futures[fut]
            try:
                narratives[repo.full_name] = fut.result()
            except Exception as e:
                logger.exception("[%s] worker crashed: %s", repo.full_name, e)
                narratives[repo.full_name] = None

    for repo in repos:
        print()
        print("=" * 72)
        print(f"{repo.display_name} ({repo.full_name})")
        print("=" * 72)
        print(narratives.get(repo.full_name) or "(no output)")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())

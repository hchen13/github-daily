"""Editor (2): assemble the daily publication.

Pulls together:
- Tracked-repo narratives (1a) from analysis_steps where step_name='narrative_24h'
- Trending reviews (1b) from data/trending_reviews/
- Today's trending JSON for ranking + star metadata

Invokes claude CLI (sonnet) with the editor system prompt and an inlined user
prompt containing all materials. Writes Markdown to data/publications/<date>.md
and persists to the reports table (report_type='global').

Run:
    python -m analysts.editor                # today
    python -m analysts.editor --date 2026-04-19
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from analysts import build_system_prompt
from config import Config, RepoConfig, load_config
from db.models import get_db, init_db

logger = logging.getLogger("editor")

PUBLICATIONS_DIR = Path("data/publications")


def _slug(full_name: str) -> str:
    return full_name.replace("/", "-")


def _format_stars(n: int) -> str:
    if n >= 100_000:
        return f"{n / 1000:.0f}k"
    if n >= 10_000:
        return f"{n / 1000:.1f}k"
    return str(n)


def load_narratives(db_path: str, repos: list[RepoConfig], target_date: date) -> dict[str, str]:
    """Load today's 1a narratives for each tracked repo."""
    out: dict[str, str] = {}
    with get_db(db_path) as conn:
        for repo in repos:
            row = conn.execute("""
                SELECT content FROM analysis_steps
                WHERE report_date = ? AND repo_full_name = ? AND step_name = 'narrative_24h'
                ORDER BY id DESC LIMIT 1
            """, (target_date.isoformat(), repo.full_name)).fetchone()
            if row:
                out[repo.full_name] = row["content"]
            else:
                logger.warning("[%s] no narrative for %s — section will be marked missing",
                               repo.full_name, target_date)
    return out


def load_trending_reviews(reviews_dir: Path, trending_repos: list[dict]) -> dict[str, dict]:
    """Load cached reviews for the repos appearing in today's trending JSON."""
    out: dict[str, dict] = {}
    for repo in trending_repos:
        full_name = repo["full_name"]
        path = reviews_dir / f"{_slug(full_name)}.json"
        if path.exists():
            try:
                out[full_name] = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                logger.warning("[%s] corrupt review JSON: %s", full_name, e)
    return out


def load_trending(trending_dir: Path, target_date: date) -> dict:
    path = trending_dir / f"{target_date.isoformat()}.json"
    if not path.exists():
        raise FileNotFoundError(f"Trending file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


COMPARE_MIN_DAYS = 7
COMPARE_MAX_DAYS = 30


def find_comparison_trending(trending_dir: Path,
                             target_date: date) -> Optional[tuple[int, dict]]:
    """Find the nearest historical trending JSON at least 7 days old.

    Walks backwards from target_date - 7 days up to target_date - 30 days.
    Returns (days_ago, trending_dict) for the first hit, or None if nothing
    in that window exists.
    """
    for days in range(COMPARE_MIN_DAYS, COMPARE_MAX_DAYS + 1):
        day = target_date - timedelta(days=days)
        path = trending_dir / f"{day.isoformat()}.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return days, data
            except json.JSONDecodeError:
                continue
    return None


def relative_time_phrase(days_ago: int) -> str:
    """Map a day count (>= 7) to a human phrase in Chinese.

    The editor will reference time in prose — we want it to say "一周前"
    when comparing a 7-day gap, "近两周前" for 11-13 days, etc., rather than
    always repeating "一周前" even when the snapshot is two weeks old.
    """
    if days_ago <= 8:
        return "一周前"
    if days_ago <= 10:
        return "一周多前"
    if days_ago <= 13:
        return "近两周前"
    if days_ago == 14:
        return "两周前"
    if days_ago <= 17:
        return "两周多前"
    if days_ago <= 20:
        return "近三周前"
    if days_ago == 21:
        return "三周前"
    if days_ago <= 27:
        return "三周多前"
    return "近一个月前"


def build_user_prompt(target_date: date, repos: list[RepoConfig],
                      narratives: dict[str, str], trending: dict,
                      reviews: dict[str, dict],
                      comparison: Optional[tuple[int, dict]] = None) -> str:
    parts: list[str] = [f"Date: {target_date.isoformat()}", ""]

    parts.append("=" * 72)
    parts.append("跟踪 repo 叙事 (1a 工作稿，可改写、压缩、重组)")
    parts.append("=" * 72)
    parts.append("")
    for repo in repos:
        narrative = narratives.get(repo.full_name)
        parts.append(f"### {repo.display_name} ({repo.full_name})")
        if narrative:
            parts.append(narrative)
        else:
            parts.append('(没有今日叙事——这个 repo 段落写一句"今日无叙事数据"即可)')
        parts.append("")

    parts.append("=" * 72)
    parts.append("Trending 元数据 + 评价 (1b 评价稿)")
    parts.append("=" * 72)
    parts.append("")

    repos_in_trending = trending.get("repos", [])
    daily_top = [r for r in repos_in_trending if "daily_top1" in r.get("lists", [])]
    weekly_top = [r for r in repos_in_trending if "weekly_top10" in r.get("lists", [])]
    weekly_top.sort(key=lambda r: r.get("rank", {}).get("weekly_top10", 999))

    if daily_top:
        r = daily_top[0]
        review = reviews.get(r["full_name"], {})
        parts.append(f"[Daily Top 1] {r['full_name']}  ★{_format_stars(r['stars'])}  "
                     f"daily +★ {r.get('stars_gained_daily') or '?'}  "
                     f"语言 {r.get('language') or '-'}")
        if r.get("description"):
            parts.append(f"GitHub 描述: {r['description']}")
        if review:
            parts.append(f"INTRO: {review.get('intro', '')}")
            parts.append(f"TECH_STACK: {', '.join(review.get('tech_stack', []))}")
            parts.append(f"SCALE: {review.get('scale', '')}")
            parts.append(f"EVALUATION: {review.get('evaluation', '')}")
        parts.append("")

    parts.append("--- Weekly Top 10 ---")
    for r in weekly_top:
        rank = r.get("rank", {}).get("weekly_top10", "?")
        review = reviews.get(r["full_name"], {})
        parts.append("")
        parts.append(f"[Weekly #{rank}] {r['full_name']}  ★{_format_stars(r['stars'])}  "
                     f"weekly +★ {r.get('stars_gained_weekly') or '?'}  "
                     f"语言 {r.get('language') or '-'}")
        if r.get("description"):
            parts.append(f"GitHub 描述: {r['description']}")
        if review:
            parts.append(f"INTRO: {review.get('intro', '')}")
            parts.append(f"EVALUATION: {review.get('evaluation', '')}")

    parts.append("")

    if comparison:
        days_ago, past = comparison
        phrase = relative_time_phrase(days_ago)
        past_date = (target_date - timedelta(days=days_ago)).isoformat()
        past_weekly = [r for r in past.get("repos", [])
                       if "weekly_top10" in r.get("lists", [])]
        past_weekly.sort(key=lambda r: r.get("rank", {}).get("weekly_top10", 999))

        parts.append("=" * 72)
        parts.append(f"历史对比快照 ({phrase} / {past_date} / 距今 {days_ago} 天)")
        parts.append("=" * 72)
        parts.append("")
        parts.append(f'在「一览」段落里讲到社区风向那一块时，用"{phrase}"这个说法，'
                     "带出对比——提炼整体共性的变化，看社区注意力有没有在转移。")
        parts.append("不要逐个复述旧榜单项目。")
        parts.append("")
        parts.append(f"{phrase}（{past_date}）Weekly Top 10：")
        for r in past_weekly:
            rank = r.get("rank", {}).get("weekly_top10", "?")
            desc = r.get("description") or "(no description)"
            lang = r.get("language") or "-"
            parts.append(f"  #{rank} {r['full_name']} [{lang}] — {desc}")
        parts.append("")
    else:
        parts.append("=" * 72)
        parts.append("历史对比快照：暂无")
        parts.append("=" * 72)
        parts.append("")
        parts.append("（最近 7-30 天内没有足够旧的 trending 快照做对比——"
                     "「一览」里讲社区风向时就只聊今天的共性和注意力方向，不要强行引入对比。）")
        parts.append("")

    parts.append("=" * 72)
    parts.append("任务")
    parts.append("=" * 72)
    parts.append(
        "按 system prompt 给的模板输出今日刊物 Markdown。"
        "记住：判断先行、不要 issue/PR 编号、允许平淡。"
    )

    return "\n".join(parts)


def run_editor(claude_bin: str, model: str, user_prompt: str) -> Optional[str]:
    system_prompt = build_system_prompt("editor.md")
    cmd = [
        claude_bin,
        "--print",
        "--model", model,
        "--system-prompt", system_prompt,
    ]
    logger.info("invoking editor (model=%s, prompt %d chars)", model, len(user_prompt))
    t0 = time.time()
    try:
        result = subprocess.run(
            cmd,
            input=user_prompt,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        logger.error("editor timed out")
        return None
    duration = time.time() - t0
    if result.returncode != 0:
        logger.error("editor failed: %s", result.stderr.strip()[:300])
        return None
    out = result.stdout.strip()
    logger.info("editor done in %.1fs, %d chars", duration, len(out))
    return out


def save_publication(target_date: date, markdown: str, db_path: str, model: str) -> Path:
    PUBLICATIONS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = PUBLICATIONS_DIR / f"{target_date.isoformat()}.md"
    out_path.write_text(markdown, encoding="utf-8")
    with get_db(db_path) as conn:
        conn.execute("""
            INSERT OR REPLACE INTO reports
            (report_date, repo_full_name, report_type, content, created_at)
            VALUES (?, '', 'global', ?, datetime('now'))
        """, (target_date.isoformat(), markdown))
        conn.execute("""
            INSERT OR REPLACE INTO analysis_steps
            (report_date, repo_full_name, step_name, analyst, model, content, duration_s)
            VALUES (?, '', 'publication', 'editor', ?, ?, NULL)
        """, (target_date.isoformat(), model, markdown))
    return out_path


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run the editor (2) and write today's publication.")
    parser.add_argument("--date", type=lambda s: date.fromisoformat(s),
                        default=date.today(), help="Publication date (default: today)")
    parser.add_argument("--config", help="Path to config.yaml")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the user prompt that would be sent and exit")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cfg = load_config(args.config)
    init_db(cfg.storage.db_path)

    narratives = load_narratives(cfg.storage.db_path, cfg.enabled_repos, args.date)
    trending = load_trending(Path(cfg.storage.trending_dir), args.date)
    reviews = load_trending_reviews(Path("data/trending_reviews"), trending.get("repos", []))
    comparison = find_comparison_trending(Path(cfg.storage.trending_dir), args.date)

    logger.info("Loaded: %d narratives, %d trending repos, %d reviews, comparison=%s",
                len(narratives), len(trending.get("repos", [])), len(reviews),
                f"{comparison[0]}d ago" if comparison else "none")

    user_prompt = build_user_prompt(args.date, cfg.enabled_repos, narratives,
                                    trending, reviews, comparison)

    if args.dry_run:
        print(user_prompt)
        return 0

    model = cfg.analysis.model_for("editor")
    markdown = run_editor(cfg.analysis.claude_bin, model, user_prompt)
    if not markdown:
        return 1

    out_path = save_publication(args.date, markdown, cfg.storage.db_path, model)
    logger.info("Wrote publication to %s", out_path)

    print()
    print(markdown)
    return 0


if __name__ == "__main__":
    sys.exit(main())

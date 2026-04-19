"""Weekly Trending Top 10 block renderer.

The editor intentionally leaves the Weekly Trending Top 10 section empty.
This module fills it in by combining:
- ``data/trending/<date>.json``  for rank + metadata (stars, weekly delta,
  language)
- ``data/trending_reviews/<slug>.json`` for opus-produced INTRO + VERDICT
  (these are shown verbatim; sonnet does not re-summarize)

The output is raw HTML injected into the rendered article so we get full
control over layout (chip pills, verdict pull-quotes, etc.) without
fighting Markdown table widths.
"""
from __future__ import annotations

import json
import re
from datetime import date
from html import escape
from pathlib import Path
from typing import Optional

REVIEWS_DIR = Path("data/trending_reviews")


def _slug(full_name: str) -> str:
    return full_name.replace("/", "-")


def _format_stars(n: int) -> str:
    if n >= 100_000:
        return f"{n / 1000:.0f}k"
    if n >= 10_000:
        return f"{n / 1000:.1f}k"
    if n >= 1_000:
        return f"{n / 1000:.1f}k"
    return str(n)


def _extract_weekly_delta(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    # "42,267 stars this week" → "+42,267"
    m = re.match(r"([\d,]+)", raw)
    return f"+{m.group(1)}" if m else raw


def load_trending(trending_dir: Path, target_date: date) -> Optional[dict]:
    path = trending_dir / f"{target_date.isoformat()}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_review(full_name: str, reviews_dir: Path = REVIEWS_DIR) -> Optional[dict]:
    path = reviews_dir / f"{_slug(full_name)}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _render_card(repo: dict, review: dict, rank_label: str,
                 delta_type: str = "weekly") -> str:
    """One trending card (shared by daily + weekly).

    ``delta_type`` is 'weekly' or 'daily' — picks which stars-gained field
    to read and how to label the delta chip.
    """
    full_name = repo["full_name"]
    stars = repo.get("stars") or 0
    language = repo.get("language") or "—"

    if delta_type == "daily":
        raw = repo.get("stars_gained_daily")
        delta_suffix = "/ 日"
    else:
        raw = repo.get("stars_gained_weekly")
        delta_suffix = "/ 周"
    delta = _extract_weekly_delta(raw)

    intro = review.get("intro") or repo.get("description") or ""
    verdict = review.get("verdict") or ""

    chips_html = (
        f'<span class="chip">{escape(language)}</span>'
        f'<span class="chip">★ {_format_stars(stars)}</span>'
    )
    if delta:
        chips_html += f'<span class="chip delta">{escape(delta)} {delta_suffix}</span>'

    owner, _, name = full_name.partition("/")
    name_html = (
        f'<a class="trending-name" '
        f'href="https://github.com/{escape(full_name, quote=True)}">'
        f'<span class="owner">{escape(owner)}/</span>'
        f'<span class="repo">{escape(name)}</span>'
        f'</a>'
    )

    verdict_html = (
        f'<div class="trending-verdict">'
        f'<span class="verdict-label">一句话</span>'
        f'<span class="verdict-text">{escape(verdict)}</span>'
        f'</div>'
    ) if verdict else ""

    return (
        f'<div class="trending-entry">'
        f'<div class="trending-header">'
        f'<span class="trending-rank">{escape(rank_label)}</span>'
        f'{name_html}'
        f'<span class="trending-meta">{chips_html}</span>'
        f'</div>'
        f'<p class="trending-intro">{escape(intro)}</p>'
        f'{verdict_html}'
        f'</div>'
    )


def render_daily_top1_html(target_date: date, trending_dir: Path,
                           reviews_dir: Path = REVIEWS_DIR) -> Optional[str]:
    trending = load_trending(trending_dir, target_date)
    if not trending:
        return None
    daily = [
        r for r in trending.get("repos", [])
        if "daily_top1" in (r.get("lists") or [])
    ]
    if not daily:
        return None
    repo = daily[0]
    review = load_review(repo["full_name"], reviews_dir) or {}
    card = _render_card(repo, review, rank_label="Daily #1", delta_type="daily")
    return '<div class="trending-list">' + card + '</div>'


def render_weekly_top10_html(target_date: date, trending_dir: Path,
                             reviews_dir: Path = REVIEWS_DIR) -> Optional[str]:
    trending = load_trending(trending_dir, target_date)
    if not trending:
        return None

    weekly = [
        r for r in trending.get("repos", [])
        if "weekly_top10" in (r.get("lists") or [])
    ]
    weekly.sort(key=lambda r: (r.get("rank") or {}).get("weekly_top10", 999))
    if not weekly:
        return None

    entries: list[str] = []
    for repo in weekly:
        rank = (repo.get("rank") or {}).get("weekly_top10", "?")
        review = load_review(repo["full_name"], reviews_dir) or {}
        entries.append(_render_card(repo, review, rank_label=f"#{rank}",
                                    delta_type="weekly"))
    return '<div class="trending-list">' + "".join(entries) + '</div>'


H3_WEEKLY_RE = re.compile(
    r'(<h3>[^<]*Weekly Trending Top 10[^<]*</h3>)'
    r'(.*?)'
    r'(?=<h[23]>|$)',
    re.DOTALL | re.IGNORECASE,
)
H3_DAILY_RE = re.compile(
    r'(<h3>[^<]*Daily Trending Top 1[^<]*</h3>)'
    r'(.*?)'
    r'(?=<h[23]>|$)',
    re.DOTALL | re.IGNORECASE,
)


def inject_weekly_top10(html: str, target_date: date, trending_dir: Path,
                        reviews_dir: Path = REVIEWS_DIR) -> str:
    block = render_weekly_top10_html(target_date, trending_dir, reviews_dir)
    if not block:
        return html

    def _repl(m: re.Match) -> str:
        return m.group(1) + "\n" + block + "\n"

    new_html, count = H3_WEEKLY_RE.subn(_repl, html, count=1)
    return new_html if count else html


def inject_daily_top1(html: str, target_date: date, trending_dir: Path,
                      reviews_dir: Path = REVIEWS_DIR) -> str:
    block = render_daily_top1_html(target_date, trending_dir, reviews_dir)
    if not block:
        return html

    def _repl(m: re.Match) -> str:
        return m.group(1) + "\n" + block + "\n"

    new_html, count = H3_DAILY_RE.subn(_repl, html, count=1)
    return new_html if count else html

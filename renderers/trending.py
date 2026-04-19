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
        full_name = repo["full_name"]
        rank = (repo.get("rank") or {}).get("weekly_top10", "?")
        stars = repo.get("stars") or 0
        language = repo.get("language") or "—"
        weekly_delta = _extract_weekly_delta(repo.get("stars_gained_weekly"))
        review = load_review(full_name, reviews_dir) or {}

        intro = review.get("intro") or repo.get("description") or ""
        verdict = review.get("verdict") or ""

        chips_html = (
            f'<span class="chip">{escape(language)}</span>'
            f'<span class="chip">★ {_format_stars(stars)}</span>'
        )
        if weekly_delta:
            chips_html += f'<span class="chip delta">{escape(weekly_delta)} / 周</span>'

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

        entries.append(
            f'<article class="trending-entry">'
            f'<header class="trending-header">'
            f'<span class="trending-rank">#{rank}</span>'
            f'{name_html}'
            f'<span class="trending-meta">{chips_html}</span>'
            f'</header>'
            f'<p class="trending-intro">{escape(intro)}</p>'
            f'{verdict_html}'
            f'</article>'
        )

    return '<div class="trending-list">' + "".join(entries) + '</div>'


H3_WEEKLY_RE = re.compile(
    r'(<h3>[^<]*Weekly Trending Top 10[^<]*</h3>)'
    r'(.*?)'
    r'(?=<h[23]>|$)',
    re.DOTALL | re.IGNORECASE,
)


def inject_weekly_top10(html: str, target_date: date, trending_dir: Path,
                        reviews_dir: Path = REVIEWS_DIR) -> str:
    """Replace whatever is between the Weekly Top 10 H3 and the next H2/H3
    with the programmatic list."""
    block = render_weekly_top10_html(target_date, trending_dir, reviews_dir)
    if not block:
        return html

    def _repl(m: re.Match) -> str:
        return m.group(1) + "\n" + block + "\n"

    new_html, count = H3_WEEKLY_RE.subn(_repl, html, count=1)
    return new_html if count else html

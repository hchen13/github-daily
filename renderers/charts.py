"""Repo-stat charts (7-day sparklines) for the publication.

For each tracked repo, queries SQLite for the last 7 days of:
- issues created    (issues.created_at)
- PRs opened        (pull_requests.created_at)
- commits           (commits.committed_at, all branches)
- PRs merged        (pull_requests.merged_at)

Renders a single inline SVG block per repo (no JS, no images, no extra
network). The block is then injected after the corresponding ``<h3>`` in
the publication HTML.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable

from db.models import get_db


METRICS: list[tuple[str, str]] = [
    ("issues", "Issues"),
    ("prs", "PRs"),
    ("commits", "Commits"),
    ("merged", "PRs merged"),
]


def fetch_7d_counts(repo_full_name: str, end_date: date, db_path: str) -> dict[str, list[int]]:
    """Return ``{metric: [c_d-6, ..., c_d0]}`` for each of the four metrics."""
    days = [(end_date - timedelta(days=n)).isoformat() for n in range(6, -1, -1)]
    out: dict[str, list[int]] = {m: [0] * 7 for m, _ in METRICS}
    out["_days"] = days  # type: ignore[assignment]

    queries = {
        "issues": "SELECT COUNT(*) FROM issues WHERE repo_full_name=? AND date(created_at)=?",
        "prs": "SELECT COUNT(*) FROM pull_requests WHERE repo_full_name=? AND date(created_at)=?",
        "commits": "SELECT COUNT(*) FROM commits WHERE repo_full_name=? AND date(committed_at)=?",
        "merged": "SELECT COUNT(*) FROM pull_requests WHERE repo_full_name=? AND date(merged_at)=?",
    }

    with get_db(db_path) as conn:
        for metric, sql in queries.items():
            for i, day in enumerate(days):
                row = conn.execute(sql, (repo_full_name, day)).fetchone()
                out[metric][i] = row[0] if row else 0
    return out


def sparkline_svg(values: list[int], width: int = 132, height: int = 32) -> str:
    """Inline SVG sparkline. Polyline + filled area; apple-blue stroke."""
    n = len(values)
    if n < 2:
        return f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" class="sparkline"></svg>'

    max_v = max(values) or 1
    pad = 3
    inner_h = height - pad * 2
    step = (width - pad * 2) / (n - 1)

    pts: list[str] = []
    for i, v in enumerate(values):
        x = pad + i * step
        y = pad + inner_h - (v / max_v) * inner_h
        pts.append(f"{x:.1f},{y:.1f}")

    line = " ".join(pts)
    # Area path: start bottom-left, line over points, close to bottom-right
    area_pts = [f"{pad:.1f},{height - pad:.1f}"] + pts + [f"{pad + (n - 1) * step:.1f},{height - pad:.1f}"]
    area = " ".join(area_pts)

    # End-point dot: highlight the most recent value
    last_x = pad + (n - 1) * step
    last_y = pad + inner_h - (values[-1] / max_v) * inner_h
    dot = f'<circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="2.5" fill="#0071e3"/>'

    if max(values) == 0:
        # Flat baseline rather than a dead line at top
        baseline = height - pad
        return (
            f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" class="sparkline">'
            f'<line x1="{pad}" y1="{baseline}" x2="{width - pad}" y2="{baseline}" '
            f'stroke="#d2d2d7" stroke-width="1" stroke-dasharray="3 3"/>'
            f'</svg>'
        )

    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" class="sparkline">'
        f'<polygon points="{area}" fill="rgba(0,113,227,0.10)"/>'
        f'<polyline points="{line}" fill="none" stroke="#0071e3" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"/>'
        f'{dot}'
        f'</svg>'
    )


def render_repo_stats_html(repo_full_name: str, end_date: date, db_path: str) -> str:
    counts = fetch_7d_counts(repo_full_name, end_date, db_path)
    cards: list[str] = []
    for key, label in METRICS:
        series = counts[key]
        total = sum(series)
        cards.append(
            '<div class="stat-card">'
            f'<div class="stat-label">{label}</div>'
            '<div class="stat-row">'
            f'<div class="stat-value">{total}</div>'
            f'{sparkline_svg(series)}'
            '</div>'
            '<div class="stat-sub">7 日</div>'
            '</div>'
        )
    return '<div class="repo-stats">' + "".join(cards) + '</div>'


def inject_charts(html: str, end_date: date, db_path: str,
                  repos: Iterable) -> str:
    """Insert each repo's stat block right after its ``<h3>...</h3>`` heading.

    Match strategy: H3 inner text equals the repo's display_name (case- and
    whitespace-insensitive). Unknown H3s are left alone.
    """
    import re

    by_name: dict[str, str] = {}
    for r in repos:
        by_name[r.display_name.strip()] = r.full_name

    pattern = re.compile(r"(<h3>)([^<]+)(</h3>)")

    def repl(m: re.Match) -> str:
        original = m.group(0)
        text = m.group(2).strip()
        full_name = by_name.get(text)
        if not full_name:
            return original
        try:
            block = render_repo_stats_html(full_name, end_date, db_path)
        except Exception:
            return original
        return original + "\n" + block

    return pattern.sub(repl, html)

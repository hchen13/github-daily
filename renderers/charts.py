"""Activity panel: 4 line charts (one per metric), 4 lines per chart
(one per tracked repo).

Injected right after the ``## AI Agent 标杆项目动态`` section header so
readers get the comparative overview before diving into per-repo prose.

Four metrics, 7-day window, one SVG each:
- Issues created       (issues.created_at)
- PRs opened           (pull_requests.created_at)
- Commits              (commits.committed_at, across all branches)
- PRs merged           (pull_requests.merged_at)

Colors: Apple system palette assigned by repo order in config.yaml.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Iterable, Sequence

from db.models import get_db


# (key, display title, SQL)
METRICS: list[tuple[str, str, str]] = [
    ("issues", "Issues 新增",
     "SELECT COUNT(*) FROM issues WHERE repo_full_name=? AND date(created_at)=?"),
    ("prs", "PRs 新开",
     "SELECT COUNT(*) FROM pull_requests WHERE repo_full_name=? AND date(created_at)=?"),
    ("commits", "Commits",
     "SELECT COUNT(*) FROM commits WHERE repo_full_name=? AND date(committed_at)=?"),
    ("merged", "PRs Merged",
     "SELECT COUNT(*) FROM pull_requests WHERE repo_full_name=? AND date(merged_at)=?"),
]


# Apple system colors (light mode). Blue first because Claude Code is
# typically slot 0; the rest rotate through pleasant, distinguishable hues.
REPO_COLORS: list[str] = [
    "#0071e3",  # apple blue
    "#ff9500",  # apple orange
    "#30b852",  # apple green (slightly darker than system green for print)
    "#bf5af2",  # apple purple
    "#ff375f",  # apple red
    "#5e5ce6",  # apple indigo
]


def fetch_all_series(repos: Sequence, end_date: date, db_path: str) -> tuple[dict, list[str]]:
    """Return ({metric: {repo_full_name: [c_d-7..c_d-1]}}, [7 day labels]).

    Window is the 7 full days ending **yesterday**, not the 7 days ending
    today. Today's bucket is always partial — data is in UTC while
    ``end_date`` is local, and the pipeline usually runs before today's
    UTC day has even started, so the rightmost column would otherwise
    show ~0 for everything.
    """
    days = [(end_date - timedelta(days=n)).isoformat() for n in range(7, 0, -1)]
    out: dict[str, dict[str, list[int]]] = {m: {} for m, *_ in METRICS}
    with get_db(db_path) as conn:
        for repo in repos:
            for key, _title, sql in METRICS:
                series: list[int] = []
                for day in days:
                    row = conn.execute(sql, (repo.full_name, day)).fetchone()
                    series.append(row[0] if row else 0)
                out[key][repo.full_name] = series
    return out, days


def _line_chart_svg(
    series_by_repo: dict[str, list[int]],
    repos: Sequence,
    days: list[str],
    width: int = 340,
    height: int = 140,
    margin_top: int = 14,
    margin_right: int = 16,
    margin_bottom: int = 22,
    margin_left: int = 18,
) -> str:
    """One chart with up to N polylines, coloured by repo slot."""
    plot_w = width - margin_left - margin_right
    plot_h = height - margin_top - margin_bottom

    all_values = [v for s in series_by_repo.values() for v in s]
    max_v = max(all_values) if all_values else 0

    baseline_y = margin_top + plot_h
    n = len(days)
    step = plot_w / max(n - 1, 1)

    parts: list[str] = []

    # Baseline
    parts.append(
        f'<line x1="{margin_left}" y1="{baseline_y}" '
        f'x2="{width - margin_right}" y2="{baseline_y}" '
        f'stroke="#ededf2" stroke-width="1"/>'
    )
    # Max-value grid line (only if there's any data)
    if max_v > 0:
        parts.append(
            f'<line x1="{margin_left}" y1="{margin_top}" '
            f'x2="{width - margin_right}" y2="{margin_top}" '
            f'stroke="#f5f5f7" stroke-width="1" stroke-dasharray="2 3"/>'
        )
        parts.append(
            f'<text x="{width - margin_right}" y="{margin_top - 3}" '
            f'text-anchor="end" font-family="ui-monospace, Menlo, monospace" '
            f'font-size="9" fill="rgba(0,0,0,0.48)">{max_v}</text>'
        )

    # Day tick labels (first + last day, MM-DD)
    def _label(iso: str) -> str:
        return iso[5:]  # MM-DD
    parts.append(
        f'<text x="{margin_left}" y="{height - 6}" text-anchor="start" '
        f'font-family="ui-monospace, Menlo, monospace" font-size="9" '
        f'fill="rgba(0,0,0,0.48)">{_label(days[0])}</text>'
    )
    parts.append(
        f'<text x="{width - margin_right}" y="{height - 6}" text-anchor="end" '
        f'font-family="ui-monospace, Menlo, monospace" font-size="9" '
        f'fill="rgba(0,0,0,0.48)">{_label(days[-1])}</text>'
    )

    scale = max(max_v, 1)
    for idx, repo in enumerate(repos):
        full_name = repo.full_name
        series = series_by_repo.get(full_name, [0] * n)
        color = REPO_COLORS[idx % len(REPO_COLORS)]
        # Compute points once, render polyline then dots.
        xy: list[tuple[float, float]] = []
        for i, v in enumerate(series):
            x = margin_left + i * step
            y = margin_top + plot_h - (v / scale) * plot_h
            xy.append((x, y))
        pts_str = " ".join(f"{x:.1f},{y:.1f}" for x, y in xy)
        parts.append(
            f'<polyline points="{pts_str}" fill="none" stroke="{color}" '
            f'stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round"/>'
        )
        # Hover-target dot per data point with a native SVG <title> tooltip.
        for i, ((x, y), v) in enumerate(zip(xy, series)):
            parts.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{color}" '
                f'fill-opacity="{1.0 if i == len(xy) - 1 else 0.85}">'
                f'<title>{repo.display_name} · {days[i][5:]} · {v}</title>'
                f'</circle>'
            )

    return (
        f'<svg viewBox="0 0 {width} {height}" preserveAspectRatio="xMidYMid meet" '
        f'class="activity-chart">'
        + "".join(parts)
        + '</svg>'
    )


def render_activity_panel_html(repos: Sequence, end_date: date, db_path: str) -> str:
    data, days = fetch_all_series(repos, end_date, db_path)

    legend_items = "".join(
        f'<span class="legend-item">'
        f'<span class="swatch" style="background:{REPO_COLORS[i % len(REPO_COLORS)]}"></span>'
        f'{r.display_name}</span>'
        for i, r in enumerate(repos)
    )

    cards: list[str] = []
    for key, title, _sql in METRICS:
        svg = _line_chart_svg(data[key], repos, days)
        cards.append(
            f'<div class="chart-card">'
            f'<div class="chart-title">{title}</div>'
            f'{svg}'
            f'</div>'
        )

    return (
        '<section class="activity-panel">'
        f'<div class="activity-legend">{legend_items}</div>'
        f'<div class="activity-grid">{"".join(cards)}</div>'
        '</section>'
    )


H2_RE = re.compile(r'(<h2>[^<]*AI Agent[^<]*</h2>)', re.IGNORECASE)


def inject_activity_panel(html: str, end_date: date, db_path: str, repos: Iterable) -> str:
    """Insert the activity panel right after the tracked-repos section H2."""
    repos_list = list(repos)
    if not repos_list:
        return html
    panel = render_activity_panel_html(repos_list, end_date, db_path)
    if not H2_RE.search(html):
        return html
    return H2_RE.sub(r"\1\n" + panel, html, count=1)

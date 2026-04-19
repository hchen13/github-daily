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

import json
import re
from datetime import date, timedelta
from html import escape
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


def _catmull_rom_path(points: list[tuple[float, float]]) -> str:
    """Convert a list of (x,y) points to an SVG path ``d`` attribute using
    Catmull-Rom → cubic-Bezier interpolation (tension 0.5).

    Endpoints are duplicated so the curve starts/ends exactly at the first and
    last point with horizontal-ish tangents. Produces much more natural-looking
    lines than a raw polyline.
    """
    if not points:
        return ""
    if len(points) == 1:
        x, y = points[0]
        return f"M {x:.2f},{y:.2f}"
    if len(points) == 2:
        (x0, y0), (x1, y1) = points
        return f"M {x0:.2f},{y0:.2f} L {x1:.2f},{y1:.2f}"

    padded = [points[0]] + list(points) + [points[-1]]
    x0, y0 = points[0]
    out = [f"M {x0:.2f},{y0:.2f}"]
    for i in range(1, len(points)):
        p0 = padded[i - 1]
        p1 = padded[i]
        p2 = padded[i + 1]
        p3 = padded[i + 2]
        c1x = p1[0] + (p2[0] - p0[0]) / 6.0
        c1y = p1[1] + (p2[1] - p0[1]) / 6.0
        c2x = p2[0] - (p3[0] - p1[0]) / 6.0
        c2y = p2[1] - (p3[1] - p1[1]) / 6.0
        out.append(
            f"C {c1x:.2f},{c1y:.2f} {c2x:.2f},{c2y:.2f} {p2[0]:.2f},{p2[1]:.2f}"
        )
    return " ".join(out)


def _line_chart_svg(
    series_by_repo: dict[str, list[int]],
    repos: Sequence,
    days: list[str],
    width: int = 340,
    height: int = 150,
    margin_top: int = 18,
    margin_right: int = 18,
    margin_bottom: int = 24,
    margin_left: int = 22,
) -> str:
    """One chart, smooth Bezier curves, per-point hover hit-areas with tooltips."""
    plot_w = width - margin_left - margin_right
    plot_h = height - margin_top - margin_bottom

    all_values = [v for s in series_by_repo.values() for v in s]
    max_v = max(all_values) if all_values else 0

    baseline_y = margin_top + plot_h
    n = len(days)
    step = plot_w / max(n - 1, 1)

    parts: list[str] = []

    # Gridlines: baseline (solid), midpoint (dashed), max (dashed).
    parts.append(
        f'<line x1="{margin_left}" y1="{baseline_y}" '
        f'x2="{width - margin_right}" y2="{baseline_y}" '
        f'stroke="#e5e5ea" stroke-width="1"/>'
    )
    if max_v > 0:
        mid_y = margin_top + plot_h / 2
        for y, val in [(margin_top, max_v), (mid_y, max_v / 2)]:
            parts.append(
                f'<line x1="{margin_left}" y1="{y:.2f}" '
                f'x2="{width - margin_right}" y2="{y:.2f}" '
                f'stroke="#f0f0f3" stroke-width="1" stroke-dasharray="2 4"/>'
            )
        parts.append(
            f'<text x="{margin_left - 4}" y="{margin_top + 3}" text-anchor="end" '
            f'font-family="ui-monospace, Menlo, monospace" font-size="9" '
            f'fill="rgba(0,0,0,0.36)">{max_v}</text>'
        )
        parts.append(
            f'<text x="{margin_left - 4}" y="{baseline_y + 3}" text-anchor="end" '
            f'font-family="ui-monospace, Menlo, monospace" font-size="9" '
            f'fill="rgba(0,0,0,0.36)">0</text>'
        )

    # X-axis tick labels: first, middle, last (MM-DD).
    def _mmdd(iso: str) -> str:
        return iso[5:]
    label_y = height - 6
    tick_style = ('font-family="ui-monospace, Menlo, monospace" font-size="9" '
                  'fill="rgba(0,0,0,0.48)"')
    parts.append(
        f'<text x="{margin_left}" y="{label_y}" text-anchor="start" {tick_style}>'
        f'{_mmdd(days[0])}</text>'
    )
    if n >= 3:
        mid_x = margin_left + (n // 2) * step
        parts.append(
            f'<text x="{mid_x:.2f}" y="{label_y}" text-anchor="middle" {tick_style}>'
            f'{_mmdd(days[n // 2])}</text>'
        )
    parts.append(
        f'<text x="{width - margin_right}" y="{label_y}" text-anchor="end" {tick_style}>'
        f'{_mmdd(days[-1])}</text>'
    )

    scale = max(max_v, 1)
    # Per-day records for JS tooltip: [{date, items:[{name,value,color}]}, ...]
    day_records: list[dict] = []
    for i, day in enumerate(days):
        items = []
        for idx, repo in enumerate(repos):
            full_name = repo.full_name
            series = series_by_repo.get(full_name, [0] * n)
            items.append({
                "name": repo.display_name,
                "value": int(series[i]),
                "color": REPO_COLORS[idx % len(REPO_COLORS)],
            })
        day_records.append({"date": _mmdd(day), "items": items})

    # Vertical guide line (moved by JS on hover)
    parts.append(
        f'<line class="day-guide" x1="0" y1="{margin_top:.2f}" x2="0" '
        f'y2="{baseline_y:.2f}" stroke="rgba(0,0,0,0.14)" stroke-width="1" '
        f'visibility="hidden"/>'
    )

    # Each series: smooth curve + dots + endpoint halo
    for idx, repo in enumerate(repos):
        full_name = repo.full_name
        series = series_by_repo.get(full_name, [0] * n)
        color = REPO_COLORS[idx % len(REPO_COLORS)]

        xy: list[tuple[float, float]] = []
        for i, v in enumerate(series):
            x = margin_left + i * step
            y = margin_top + plot_h - (v / scale) * plot_h
            xy.append((x, y))

        path_d = _catmull_rom_path(xy)
        parts.append(
            f'<path d="{path_d}" fill="none" stroke="{color}" stroke-width="2" '
            f'stroke-linejoin="round" stroke-linecap="round" '
            f'style="filter:drop-shadow(0 1px 1.5px rgba(0,0,0,0.08))"/>'
        )

        for i, ((x, y), v) in enumerate(zip(xy, series)):
            is_last = i == len(xy) - 1
            if is_last:
                parts.append(
                    f'<circle cx="{x:.2f}" cy="{y:.2f}" r="4" fill="{color}" '
                    f'fill-opacity="0.18"/>'
                )
                parts.append(
                    f'<circle cx="{x:.2f}" cy="{y:.2f}" r="2.5" fill="{color}"/>'
                )
            else:
                parts.append(
                    f'<circle cx="{x:.2f}" cy="{y:.2f}" r="1.8" fill="{color}" '
                    f'fill-opacity="0.75"/>'
                )

    # Day-column hit rectangles (transparent, on top). Each triggers the
    # JS tooltip for its column. Width = step, centered on the day's x.
    col_w = step
    for i in range(n):
        cx = margin_left + i * step
        left = max(0.0, cx - col_w / 2)
        parts.append(
            f'<rect class="day-hit" x="{left:.2f}" y="{margin_top:.2f}" '
            f'width="{col_w:.2f}" height="{plot_h:.2f}" '
            f'fill="transparent" data-day-idx="{i}" data-cx="{cx:.2f}"/>'
        )

    data_attr = escape(json.dumps(day_records, ensure_ascii=False), quote=True)
    return (
        f'<svg viewBox="0 0 {width} {height}" preserveAspectRatio="xMidYMid meet" '
        f'class="activity-chart" data-series="{data_attr}">'
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
            f'<div class="chart-wrapper">{svg}<div class="chart-tooltip" hidden></div></div>'
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

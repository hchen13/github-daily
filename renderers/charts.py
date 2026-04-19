"""Activity panel: momentum chart + signal brief + KPI row.

Layout (injected after ``## AI Agent 标杆项目动态``):

  1. **Momentum chart**: one large line chart of cumulative 7-day activity
     (issues + PRs + commits) per tracked repo. Lines are drawn on a
     log2 scale to keep disparate projects visually comparable. No legend
     and no y-axis; the right edge of each line shows an avatar + label +
     current cumulative value.

  2. **Signal Brief**: four computed narrative cards with Chinese labels —
     最快上升 / 合并最多 / Issue 领跑 / 提交最稳. Each auto-picks the
     winning repo for that rule and prints a one-line explanation.

  3. **KPI row**: four metric cards (新增 Issue / 新开 PR / 合并 PR /
     Commits), each showing the 7-day total across all tracked repos,
     the delta vs the previous day, and a filled mini-sparkline.

Data is computed here from SQLite; avatars and CSS belong to the article
bundle so the block works offline in PDF/JPEG renders.
"""
from __future__ import annotations

import base64
import math
import re
import statistics
from datetime import date, timedelta
from html import escape
from pathlib import Path
from typing import Sequence

from db.models import get_db


ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
AVATARS_DIR = ASSETS_DIR / "avatars"


# Series color palette — Apple system colors.
REPO_COLORS: list[str] = [
    "#0071e3",  # apple blue
    "#ff9500",  # apple orange
    "#30b852",  # apple green
    "#bf5af2",  # apple purple
    "#ff375f",  # apple red
    "#5e5ce6",  # apple indigo
]


# ────────────────────────────────────────────────────────────────────────────
# Data access
# ────────────────────────────────────────────────────────────────────────────

# key → (display-name, SQL where predicate on date column)
_COUNT_QUERIES: dict[str, tuple[str, str]] = {
    "issues":  ("新增 Issue", "SELECT COUNT(*) FROM issues WHERE repo_full_name=? AND date(created_at)=?"),
    "prs":     ("新开 PR",   "SELECT COUNT(*) FROM pull_requests WHERE repo_full_name=? AND date(created_at)=?"),
    "merged":  ("合并 PR",   "SELECT COUNT(*) FROM pull_requests WHERE repo_full_name=? AND date(merged_at)=?"),
    "commits": ("Commits",   "SELECT COUNT(*) FROM commits WHERE repo_full_name=? AND date(committed_at)=?"),
}


def _fetch_series(db_path: str, repos: Sequence, days: list[str]) -> dict:
    """Fetch counts for every (repo × metric × day). Returns nested dict:
    ``out[metric][repo_full_name] = [count_d0, count_d1, ..., count_dN]``."""
    out: dict[str, dict[str, list[int]]] = {
        key: {} for key in _COUNT_QUERIES
    }
    with get_db(db_path) as conn:
        for repo in repos:
            for key, (_title, sql) in _COUNT_QUERIES.items():
                series = []
                for day in days:
                    row = conn.execute(sql, (repo.full_name, day)).fetchone()
                    series.append(int(row[0]) if row else 0)
                out[key][repo.full_name] = series
    return out


# ────────────────────────────────────────────────────────────────────────────
# Avatar cache (base64 data URLs for offline PDF/JPEG rendering)
# ────────────────────────────────────────────────────────────────────────────

def _avatar_data_uri(owner: str) -> str:
    """Return base64 data URI for the owner's avatar, empty string if missing."""
    for ext, mime in (("png", "image/png"), ("jpg", "image/jpeg"), ("jpeg", "image/jpeg")):
        path = AVATARS_DIR / f"{owner}.{ext}"
        if path.exists():
            raw = path.read_bytes()
            # file command showed openclaw.png is actually JPEG — detect by magic bytes
            if raw[:3] == b"\xff\xd8\xff":
                mime = "image/jpeg"
            elif raw[:8] == b"\x89PNG\r\n\x1a\n":
                mime = "image/png"
            b64 = base64.b64encode(raw).decode("ascii")
            return f"data:{mime};base64,{b64}"
    return ""


# ────────────────────────────────────────────────────────────────────────────
# Main momentum chart (single large chart, cumulative, log2)
# ────────────────────────────────────────────────────────────────────────────

def _catmull_rom(points: list[tuple[float, float]]) -> str:
    """Cubic-Bezier approximation of a Catmull-Rom spline."""
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
        p0 = padded[i - 1]; p1 = padded[i]; p2 = padded[i + 1]; p3 = padded[i + 2]
        c1x = p1[0] + (p2[0] - p0[0]) / 6.0
        c1y = p1[1] + (p2[1] - p0[1]) / 6.0
        c2x = p2[0] - (p3[0] - p1[0]) / 6.0
        c2y = p2[1] - (p3[1] - p1[1]) / 6.0
        out.append(f"C {c1x:.2f},{c1y:.2f} {c2x:.2f},{c2y:.2f} {p2[0]:.2f},{p2[1]:.2f}")
    return " ".join(out)


def _cumulative(series: list[int]) -> list[int]:
    out, total = [], 0
    for v in series:
        total += v
        out.append(total)
    return out


def _log2_scale(v: int) -> float:
    """log2(v+1) so that 0 maps to 0 and the curve is smooth near zero."""
    return math.log2(v + 1)


def _render_momentum_chart(repos: Sequence, daily: dict, days: list[str],
                           width: int = 520, height: int = 320) -> str:
    """Daily activity chart. Each point = that day's issues + PRs + commits.
    No y-axis (log2 scale); line-end avatar + label + 7-day total."""
    daily_sum_by_repo: dict[str, list[int]] = {}
    for repo in repos:
        daily_sum_by_repo[repo.full_name] = [
            daily["issues"][repo.full_name][i]
            + daily["prs"][repo.full_name][i]
            + daily["commits"][repo.full_name][i]
            for i in range(len(days))
        ]

    # Layout — leave room on the right for avatar + label + value
    pad_top, pad_right, pad_bottom, pad_left = 20, 110, 32, 14
    plot_w = width - pad_left - pad_right
    plot_h = height - pad_top - pad_bottom
    n = len(days)
    step = plot_w / max(n - 1, 1)

    # Shared log2 scale across all repos (daily values)
    all_vals = [v for s in daily_sum_by_repo.values() for v in s]
    max_log = max((_log2_scale(v) for v in all_vals), default=0)
    max_log = max(max_log, 1)

    parts: list[str] = []

    # Subtle baseline
    baseline_y = pad_top + plot_h
    parts.append(
        f'<line x1="{pad_left}" y1="{baseline_y:.2f}" '
        f'x2="{pad_left + plot_w}" y2="{baseline_y:.2f}" '
        f'stroke="#e5e5ea" stroke-width="1"/>'
    )

    # X-axis tick labels (start, mid, end)
    def _mmdd(iso: str) -> str:
        return iso[5:]
    label_y = height - 10
    tick_style = ('font-family="ui-monospace, Menlo, monospace" font-size="10" '
                  'fill="rgba(0,0,0,0.4)"')
    parts.append(
        f'<text x="{pad_left}" y="{label_y}" text-anchor="start" {tick_style}>'
        f'{_mmdd(days[0])}</text>'
    )
    if n >= 3:
        mid_x = pad_left + (n // 2) * step
        parts.append(
            f'<text x="{mid_x:.2f}" y="{label_y}" text-anchor="middle" {tick_style}>'
            f'{_mmdd(days[n // 2])}</text>'
        )
    parts.append(
        f'<text x="{pad_left + plot_w}" y="{label_y}" text-anchor="end" {tick_style}>'
        f'{_mmdd(days[-1])}</text>'
    )

    # Lines
    end_label_positions: list[tuple[float, object, str, int]] = []
    for idx, repo in enumerate(repos):
        series = daily_sum_by_repo[repo.full_name]
        color = REPO_COLORS[idx % len(REPO_COLORS)]
        xy: list[tuple[float, float]] = []
        for i, v in enumerate(series):
            x = pad_left + i * step
            y = pad_top + plot_h - (_log2_scale(v) / max_log) * plot_h
            xy.append((x, y))
        parts.append(
            f'<path d="{_catmull_rom(xy)}" fill="none" stroke="{color}" '
            f'stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round" '
            f'style="filter:drop-shadow(0 1px 1.5px rgba(0,0,0,0.08))"/>'
        )
        # End dot
        end_x, end_y = xy[-1]
        parts.append(
            f'<circle cx="{end_x:.2f}" cy="{end_y:.2f}" r="4.5" fill="{color}" fill-opacity="0.18"/>'
        )
        parts.append(
            f'<circle cx="{end_x:.2f}" cy="{end_y:.2f}" r="3" fill="{color}"/>'
        )
        # Label value = 7-day total for this repo (more meaningful at a glance
        # than the latest day alone)
        total_7d = sum(series)
        end_label_positions.append((end_y, repo, color, total_7d))

    # Resolve vertical collisions between labels.
    end_label_positions.sort(key=lambda t: t[0])
    row_h = 48
    label_min_gap = row_h
    placed_y: list[float] = []
    for i, (y, repo, color, value) in enumerate(end_label_positions):
        target = y
        if placed_y and target - placed_y[-1] < label_min_gap:
            target = placed_y[-1] + label_min_gap
        # Keep labels inside the chart
        target = max(pad_top + 10, min(target, pad_top + plot_h - 10))
        placed_y.append(target)
        # Render label html-as-foreignObject is heavy; use pure SVG instead.
        # avatar (<image>) + repo label + value
        avatar_uri = _avatar_data_uri(repo.owner)
        label_x = pad_left + plot_w + 10
        # Connector line from endpoint to label
        parts.append(
            f'<line x1="{pad_left + plot_w:.2f}" y1="{y:.2f}" '
            f'x2="{label_x:.2f}" y2="{target:.2f}" '
            f'stroke="{color}" stroke-opacity="0.35" stroke-width="1" stroke-dasharray="2 3"/>'
        )
        avatar_size = 22
        if avatar_uri:
            # Inline CSS clip-path works in Chromium SVG; the attribute-form
            # `clip-path="circle(...)"` is NOT the same syntax and silently
            # fails, leaving squares.
            parts.append(
                f'<image href="{avatar_uri}" x="{label_x:.2f}" '
                f'y="{target - avatar_size/2:.2f}" '
                f'width="{avatar_size}" height="{avatar_size}" '
                f'preserveAspectRatio="xMidYMid slice" '
                f'style="clip-path: circle(50%);"/>'
            )
        text_x = label_x + avatar_size + 6
        parts.append(
            f'<text x="{text_x:.2f}" y="{target - 2:.2f}" '
            f'font-family="var(--font-display, Inter, sans-serif)" '
            f'font-size="13" font-weight="600" fill="{color}">'
            f'{escape(repo.label)}</text>'
        )
        parts.append(
            f'<text x="{text_x:.2f}" y="{target + 12:.2f}" '
            f'font-family="ui-monospace, Menlo, monospace" '
            f'font-size="12" font-weight="500" fill="{color}" fill-opacity="0.85">'
            f'{value}</text>'
        )

    svg = (
        f'<svg viewBox="0 0 {width} {height}" preserveAspectRatio="xMidYMid meet" '
        f'class="momentum-chart">'
        + "".join(parts)
        + '</svg>'
    )
    return (
        '<div class="momentum-card">'
        '<div class="momentum-title">每日活跃度 <span class="momentum-sub">'
        'issues + PRs + commits · 线右数字为 7 日合计</span></div>'
        f'{svg}'
        '</div>'
    )


# ────────────────────────────────────────────────────────────────────────────
# Signal Brief (4 computed narrative cards)
# ────────────────────────────────────────────────────────────────────────────

# Lucide-style SVG icons (stroke-width 1.8, rounded)
_ICON_TREND_UP = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
    'stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M3 17l6-6 4 4 8-8"/><path d="M14 7h7v7"/>'
    '</svg>'
)
_ICON_GIT_MERGE = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
    'stroke-linecap="round" stroke-linejoin="round">'
    '<circle cx="18" cy="18" r="3"/><circle cx="6" cy="6" r="3"/>'
    '<path d="M6 21V9a9 9 0 0 0 9 9"/>'
    '</svg>'
)
_ICON_FILE_TEXT = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
    'stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>'
    '<polyline points="14 2 14 8 20 8"/>'
    '<line x1="8" y1="13" x2="16" y2="13"/>'
    '<line x1="8" y1="17" x2="16" y2="17"/>'
    '</svg>'
)
_ICON_CODE = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
    'stroke-linecap="round" stroke-linejoin="round">'
    '<polyline points="16 18 22 12 16 6"/>'
    '<polyline points="8 6 2 12 8 18"/>'
    '</svg>'
)


def _safe_ratio(cur: float, prev: float) -> float | None:
    if prev == 0:
        return None if cur == 0 else float("inf")
    return (cur - prev) / prev


def _signal_cards(repos: Sequence, daily: dict, days: list[str]) -> list[dict]:
    """Compute 4 signal cards. Each returns a dict with icon/title/repo/detail."""
    cards: list[dict] = []

    # 1. 最快上升 — 昨天相对前天总活跃度（issues+prs+commits）增长率最大
    if len(days) >= 2:
        best = None
        for repo in repos:
            cur = sum(daily[k][repo.full_name][-1] for k in ("issues", "prs", "commits"))
            prev = sum(daily[k][repo.full_name][-2] for k in ("issues", "prs", "commits"))
            ratio = _safe_ratio(cur, prev)
            if ratio is None:
                continue
            if best is None or ratio > best[0]:
                best = (ratio, repo, cur, prev)
        if best:
            ratio, repo, cur, prev = best
            if ratio == float("inf"):
                detail = f"昨日新增活动 {cur} 项，前日为 0 — 冷启动爆发。"
            else:
                pct = int(round(ratio * 100))
                detail = f"昨日新增活动 {cur} 项，较前日 {prev} 项激增 {pct:+d}%。"
            cards.append({
                "icon": _ICON_TREND_UP, "tone": "purple",
                "title": "最快上升", "repo": repo, "detail": detail,
            })

    # 2. 合并最多 — 7 日 merged PR 总数最高
    best_m = max(repos, key=lambda r: sum(daily["merged"][r.full_name]), default=None)
    if best_m:
        total = sum(daily["merged"][best_m.full_name])
        cards.append({
            "icon": _ICON_GIT_MERGE, "tone": "orange",
            "title": "合并最多", "repo": best_m,
            "detail": f"本周合并 {total} 个 PR，吞吐最高。",
        })

    # 3. Issue 领跑 — 7 日新增 issue 最多
    best_i = max(repos, key=lambda r: sum(daily["issues"][r.full_name]), default=None)
    if best_i:
        total = sum(daily["issues"][best_i.full_name])
        cards.append({
            "icon": _ICON_FILE_TEXT, "tone": "blue",
            "title": "Issue 领跑", "repo": best_i,
            "detail": f"本周新增 {total} 条 issue，用户反馈最集中。",
        })

    # 4. 提交最稳 — 7 日内有 commit 的天数最多；打平时看标准差最小
    def commit_streak_score(repo) -> tuple[int, float]:
        s = daily["commits"][repo.full_name]
        active_days = sum(1 for v in s if v > 0)
        stdev = statistics.pstdev(s) if len(s) > 1 else 0
        # Higher active_days first, then lower stdev (more even distribution)
        return (active_days, -stdev)

    if repos:
        best_c = max(repos, key=commit_streak_score)
        s = daily["commits"][best_c.full_name]
        active_days = sum(1 for v in s if v > 0)
        total = sum(s)
        cards.append({
            "icon": _ICON_CODE, "tone": "green",
            "title": "提交最稳", "repo": best_c,
            "detail": f"7 日内有 {active_days} 天有提交，共 {total} 次，节奏最稳。",
        })

    return cards


def _render_signal_brief(cards: list[dict]) -> str:
    if not cards:
        return ""
    items: list[str] = []
    for c in cards:
        repo = c["repo"]
        items.append(
            f'<div class="signal-card signal-{c["tone"]}">'
            f'<div class="signal-icon">{c["icon"]}</div>'
            f'<div class="signal-body">'
            f'<div class="signal-title">{escape(c["title"])}</div>'
            f'<div class="signal-repo">{escape(repo.label)}</div>'
            f'<div class="signal-detail">{escape(c["detail"])}</div>'
            f'</div>'
            f'</div>'
        )
    return '<div class="signal-brief">' + "".join(items) + '</div>'


# ────────────────────────────────────────────────────────────────────────────
# KPI row (4 metric cards)
# ────────────────────────────────────────────────────────────────────────────

_KPI_ORDER = [
    ("issues",  "新增 Issue", _ICON_FILE_TEXT, "blue"),
    ("prs",     "新开 PR",   _ICON_GIT_MERGE, "orange"),
    ("merged",  "合并 PR",   _ICON_GIT_MERGE, "green"),
    ("commits", "Commits",   _ICON_CODE,      "purple"),
]


def _render_kpi_card(key: str, title: str, icon_svg: str, tone: str,
                     aggregate_series: list[int]) -> str:
    total = sum(aggregate_series)
    delta = aggregate_series[-1] - aggregate_series[-2] if len(aggregate_series) >= 2 else 0
    arrow = "↑" if delta > 0 else ("↓" if delta < 0 else "·")
    delta_cls = "up" if delta > 0 else ("down" if delta < 0 else "flat")

    # Sparkline
    w, h = 160, 40
    pad = 2
    inner_h = h - pad * 2
    if aggregate_series:
        max_v = max(aggregate_series) or 1
        n = len(aggregate_series)
        step = (w - pad * 2) / max(n - 1, 1)
        xy = [
            (pad + i * step, pad + inner_h - (v / max_v) * inner_h)
            for i, v in enumerate(aggregate_series)
        ]
        path_d = _catmull_rom(xy)
        # Area polygon
        area = (
            f'{pad:.2f},{h - pad:.2f} '
            + " ".join(f"{x:.2f},{y:.2f}" for x, y in xy)
            + f' {pad + (n-1)*step:.2f},{h - pad:.2f}'
        )
        spark = (
            f'<svg viewBox="0 0 {w} {h}" class="kpi-spark" preserveAspectRatio="none">'
            f'<polygon points="{area}" class="spark-area"/>'
            f'<path d="{path_d}" class="spark-line"/>'
            f'</svg>'
        )
    else:
        spark = ""

    return (
        f'<div class="kpi-card kpi-{tone}">'
        f'<div class="kpi-head">'
        f'<div class="kpi-icon">{icon_svg}</div>'
        f'<div class="kpi-title">{escape(title)}</div>'
        f'</div>'
        f'<div class="kpi-body">'
        f'<div class="kpi-value">{total}</div>'
        f'<div class="kpi-delta-wrap">'
        f'<div class="kpi-delta kpi-delta-{delta_cls}">'
        f'<span class="arrow">{arrow}</span>{abs(delta)}'
        f'</div>'
        f'<div class="kpi-delta-sub">vs 昨日</div>'
        f'</div>'
        f'</div>'
        f'{spark}'
        f'</div>'
    )


def _render_kpi_row(daily: dict) -> str:
    cards: list[str] = []
    for key, title, icon, tone in _KPI_ORDER:
        # Aggregate across all repos per day
        per_day: list[int] = []
        series = daily[key]
        if not series:
            continue
        n = len(next(iter(series.values())))
        for i in range(n):
            per_day.append(sum(s[i] for s in series.values()))
        cards.append(_render_kpi_card(key, title, icon, tone, per_day))
    return '<div class="kpi-row">' + "".join(cards) + '</div>'


# ────────────────────────────────────────────────────────────────────────────
# Public API — compose + inject
# ────────────────────────────────────────────────────────────────────────────

def render_activity_panel_html(repos: Sequence, end_date: date, db_path: str) -> str:
    # Window: [end_date - 7, end_date - 1]  (7 full UTC days, ending yesterday)
    days = [(end_date - timedelta(days=n)).isoformat() for n in range(7, 0, -1)]
    daily = _fetch_series(db_path, repos, days)
    momentum = _render_momentum_chart(repos, daily, days)
    signals = _render_signal_brief(_signal_cards(repos, daily, days))
    kpis = _render_kpi_row(daily)
    return '<section class="activity-panel">' + momentum + signals + kpis + '</section>'


H2_RE = re.compile(r'(<h2>[^<]*AI Agent[^<]*</h2>)', re.IGNORECASE)


def inject_activity_panel(html: str, end_date: date, db_path: str, repos) -> str:
    repos_list = list(repos)
    if not repos_list:
        return html
    panel = render_activity_panel_html(repos_list, end_date, db_path)
    if not H2_RE.search(html):
        return html
    return H2_RE.sub(r"\1\n" + panel, html, count=1)

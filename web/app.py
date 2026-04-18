"""GitHub Daily web UI.

Serves the current and historical publications, plus a read-only view of
the active config. Designed to share the apple-styled CSS with the
publication renderer so the whole site feels consistent.

Run:
    python -m web                       # bind 127.0.0.1:8765
    python -m web --host 0.0.0.0
    python -m web --port 8000
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from config import load_config

logger = logging.getLogger("web")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = PROJECT_ROOT / "assets"
RENDERS_DIR = PROJECT_ROOT / "data" / "renders"
PUBLICATIONS_DIR = PROJECT_ROOT / "data" / "publications"

app = FastAPI(title="GitHub Daily", description="辛哥的开源风向")
app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")


# ── helpers ─────────────────────────────────────────────────────────────────

def list_publication_dates() -> list[str]:
    if not PUBLICATIONS_DIR.exists():
        return []
    out: list[str] = []
    for p in PUBLICATIONS_DIR.glob("*.md"):
        try:
            date.fromisoformat(p.stem)
            out.append(p.stem)
        except ValueError:
            continue
    return sorted(out, reverse=True)


def extract_article(html: str) -> str:
    """Pull just the ``<article class="publication">…</article>`` block.

    Lets us re-wrap the publication in the web-UI chrome (nav + footer)
    without nesting full HTML documents.
    """
    start_marker = '<article class="publication">'
    end_marker = '</article>'
    start = html.find(start_marker)
    if start == -1:
        return html
    end = html.find(end_marker, start)
    if end == -1:
        return html[start:]
    return html[start:end + len(end_marker)]


def render_chrome(title: str, body: str, current_date: Optional[str] = None) -> str:
    """Wrap body in the web-UI chrome: shared apple CSS + sticky nav."""
    css_href = "/assets/themes/apple.css"
    nav_extra = f' · <code>{current_date}</code>' if current_date else ""
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap">
  <link rel="stylesheet" href="{css_href}">
  <style>
    .web-nav {{
      position: sticky; top: 0; z-index: 50;
      background: rgba(255,255,255,0.85);
      backdrop-filter: saturate(180%) blur(20px);
      -webkit-backdrop-filter: saturate(180%) blur(20px);
      border-bottom: 1px solid #d2d2d7;
      padding: 12px 32px;
      font-family: 'Inter', -apple-system, sans-serif;
      font-size: 13px;
      letter-spacing: -0.13px;
      display: flex; align-items: center; justify-content: space-between;
    }}
    .web-nav .brand {{ font-weight: 600; color: #1d1d1f; }}
    .web-nav .brand small {{ color: rgba(0,0,0,0.48); font-weight: 400; margin-left: 6px; }}
    .web-nav .links a {{
      color: rgba(0,0,0,0.8); text-decoration: none; margin-left: 18px;
    }}
    .web-nav .links a:hover {{ color: #0066cc; }}
    .web-nav code {{
      font-family: ui-monospace, 'SF Mono', Menlo, monospace;
      background: #f5f5f7; padding: 2px 6px; border-radius: 4px;
      font-size: 12px;
    }}
  </style>
</head>
<body>
  <nav class="web-nav">
    <div class="brand">GitHub Daily<small>辛哥的开源风向{nav_extra}</small></div>
    <div class="links">
      <a href="/">今日</a>
      <a href="/archive">历史</a>
      <a href="/settings">设置</a>
    </div>
  </nav>
{body}
</body>
</html>
"""


# ── routes ──────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def home():
    dates = list_publication_dates()
    if not dates:
        return HTMLResponse(render_chrome(
            "GitHub Daily",
            '<article class="publication"><h1>暂无刊物</h1><p>运行 <code>python -m run</code> 生成今日刊物。</p></article>',
        ))
    return _serve_publication_html(dates[0])


@app.get("/d/{day}", response_class=HTMLResponse)
def serve_day(day: str):
    _validate_date(day)
    return _serve_publication_html(day)


@app.get("/d/{day}/pdf")
def serve_pdf(day: str):
    _validate_date(day)
    path = RENDERS_DIR / day / "publication.pdf"
    if not path.exists():
        raise HTTPException(404, f"No PDF for {day}")
    return FileResponse(path, media_type="application/pdf",
                        filename=f"github-daily-{day}.pdf")


@app.get("/d/{day}/jpeg")
def serve_jpeg(day: str):
    _validate_date(day)
    path = RENDERS_DIR / day / "publication.jpeg"
    if not path.exists():
        raise HTTPException(404, f"No JPEG for {day}")
    return FileResponse(path, media_type="image/jpeg",
                        filename=f"github-daily-{day}.jpg")


@app.get("/archive", response_class=HTMLResponse)
def archive():
    dates = list_publication_dates()
    items = "\n".join(
        f'<li><a href="/d/{d}">{d}</a></li>' for d in dates
    ) or "<li>暂无</li>"
    body = f"""
<article class="publication">
  <h1>历史刊物</h1>
  <blockquote><p>共 {len(dates)} 期</p></blockquote>
  <ul>{items}</ul>
</article>
"""
    return HTMLResponse(render_chrome("历史 · GitHub Daily", body))


@app.get("/settings", response_class=HTMLResponse)
def settings():
    cfg = load_config()
    repos_html = "\n".join(
        f"<li><strong>{r.display_name}</strong> <code>{r.full_name}</code> "
        f"{'(enabled)' if r.enabled else '(disabled)'}</li>"
        for r in cfg.repos
    )
    body = f"""
<article class="publication">
  <h1>设置</h1>
  <blockquote><p>当前为只读视图；编辑请改 <code>config.yaml</code> 后重启服务。</p></blockquote>

  <h2>出刊时间</h2>
  <p><strong>每天 {cfg.schedule.publish_time}</strong> · 时区 <code>{cfg.schedule.timezone}</code></p>

  <h2>跟踪 Repo（{len(cfg.enabled_repos)}/{len(cfg.repos)} 启用）</h2>
  <ul>{repos_html}</ul>

  <h2>采集上限</h2>
  <ul>
    <li>Issues: {cfg.collection.max_issues}</li>
    <li>PRs: {cfg.collection.max_prs}</li>
    <li>Commits / branch: {cfg.collection.max_commits_per_branch}</li>
    <li>Releases: {cfg.collection.max_releases}</li>
  </ul>

  <h2>分析模型</h2>
  <ul>
    <li>叙事师 (1a): <code>{cfg.analysis.model_for('narrator')}</code></li>
    <li>评价师 (1b): <code>{cfg.analysis.model_for('trending_reviewer')}</code></li>
    <li>总编 (2):  <code>{cfg.analysis.model_for('editor')}</code></li>
  </ul>
  <p>Claude CLI: <code>{cfg.analysis.claude_bin}</code></p>

  <h2>存储</h2>
  <ul>
    <li>SQLite: <code>{cfg.storage.db_path}</code></li>
    <li>Trending JSON: <code>{cfg.storage.trending_dir}</code></li>
  </ul>
</article>
"""
    return HTMLResponse(render_chrome("设置 · GitHub Daily", body))


@app.get("/api/publications")
def api_publications():
    return JSONResponse({"dates": list_publication_dates()})


@app.get("/api/config")
def api_config():
    cfg = load_config()
    return JSONResponse({
        "schedule": {"publish_time": cfg.schedule.publish_time, "timezone": cfg.schedule.timezone},
        "repos": [{"full_name": r.full_name, "display_name": r.display_name, "enabled": r.enabled}
                  for r in cfg.repos],
        "collection": {
            "max_issues": cfg.collection.max_issues,
            "max_prs": cfg.collection.max_prs,
            "max_commits_per_branch": cfg.collection.max_commits_per_branch,
            "max_releases": cfg.collection.max_releases,
        },
        "analysis": {
            "claude_bin": cfg.analysis.claude_bin,
            "models": cfg.analysis.models,
        },
    })


# ── internal ────────────────────────────────────────────────────────────────

def _validate_date(day: str) -> None:
    try:
        date.fromisoformat(day)
    except ValueError:
        raise HTTPException(400, "Invalid date format. Use YYYY-MM-DD.")


def _serve_publication_html(day: str) -> HTMLResponse:
    rendered = RENDERS_DIR / day / "publication.html"
    if rendered.exists():
        article = extract_article(rendered.read_text(encoding="utf-8"))
        return HTMLResponse(render_chrome(f"{day} · GitHub Daily", article, current_date=day))
    md_path = PUBLICATIONS_DIR / f"{day}.md"
    if md_path.exists():
        body = (
            '<article class="publication">'
            f'<h1>{day}</h1>'
            '<blockquote><p>渲染未生成</p></blockquote>'
            f'<p>Markdown 已在 <code>{md_path}</code>。运行 '
            f'<code>python -m run --only render --date {day}</code> 后刷新。</p>'
            '</article>'
        )
        return HTMLResponse(render_chrome(f"{day} · 渲染 pending", body))
    raise HTTPException(404, f"No publication for {day}")

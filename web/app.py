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

import json
import logging
import os
from datetime import date
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
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


def render_chrome(title: str, body: str, current_date: Optional[str] = None, root: str = "") -> str:
    """Wrap body in the web-UI chrome: shared apple CSS + sticky nav."""
    css_href = f"{root}/assets/themes/apple.css"
    nav_extra = f' · <code>{current_date}</code>' if current_date else ""
    float_html = ""
    if current_date:
        float_html = (
            f'  <div class="float-actions">\n'
            f'    <a class="float-btn" href="{root}/d/{current_date}/pdf" target="_blank" title="查看 PDF">'
            f'<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
            f'<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>'
            f'<polyline points="14 2 14 8 20 8"/>'
            f'<line x1="9" y1="13" x2="15" y2="13"/><line x1="9" y1="17" x2="13" y2="17"/>'
            f'</svg></a>\n'
            f'    <a class="float-btn" href="{root}/d/{current_date}/jpeg" target="_blank" title="查看图片">'
            f'<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
            f'<rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>'
            f'<circle cx="8.5" cy="8.5" r="1.5"/>'
            f'<polyline points="21 15 16 10 5 21"/>'
            f'</svg></a>\n'
            f'  </div>\n'
        )
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
    .float-actions {{
      position: fixed; bottom: 28px; right: 28px;
      display: flex; flex-direction: column; gap: 10px; z-index: 100;
    }}
    .float-btn {{
      width: 44px; height: 44px; border-radius: 50%;
      background: rgba(255,255,255,0.92);
      backdrop-filter: saturate(180%) blur(16px);
      -webkit-backdrop-filter: saturate(180%) blur(16px);
      border: 1px solid rgba(0,0,0,0.12);
      box-shadow: 0 2px 12px rgba(0,0,0,0.12);
      display: flex; align-items: center; justify-content: center;
      color: #1d1d1f; text-decoration: none; transition: background 0.15s, color 0.15s;
    }}
    .float-btn:hover {{ background: #f0f4ff; color: #0066cc; }}
  </style>
</head>
<body>
  <nav class="web-nav">
    <div class="brand">GitHub Daily<small>辛哥的开源风向{nav_extra}</small></div>
    <div class="links">
      <a href="{root}/">今日</a>
      <a href="{root}/archive">历史</a>
      <a href="{root}/settings">设置</a>
    </div>
  </nav>
{body}
{float_html}  <script src="{root}/assets/themes/chart-tooltip.js"></script>
</body>
</html>
"""


# ── routes ──────────────────────────────────────────────────────────────────

def _root(request: Request) -> str:
    return request.scope.get("root_path", "").rstrip("/")


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    root = _root(request)
    dates = list_publication_dates()
    if not dates:
        return HTMLResponse(render_chrome(
            "GitHub Daily",
            '<article class="publication"><h1>暂无刊物</h1><p>运行 <code>python -m run</code> 生成今日刊物。</p></article>',
            root=root,
        ))
    return _serve_publication_html(dates[0], root)


@app.get("/d/{day}", response_class=HTMLResponse)
def serve_day(request: Request, day: str):
    _validate_date(day)
    return _serve_publication_html(day, _root(request))


@app.get("/d/{day}/pdf", response_class=HTMLResponse)
def serve_pdf(request: Request, day: str):
    _validate_date(day)
    root = _root(request)
    path = RENDERS_DIR / day / "publication.pdf"
    if not path.exists():
        raise HTTPException(404, f"No PDF for {day}")
    return HTMLResponse(f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PDF · {day}</title>
<style>*{{margin:0;padding:0;box-sizing:border-box}}html,body{{height:100%;background:#525659}}</style>
</head><body>
<embed src="{root}/d/{day}/pdf-file" type="application/pdf" width="100%" height="100%">
</body></html>""")


@app.get("/d/{day}/pdf-file")
def serve_pdf_file(day: str):
    _validate_date(day)
    path = RENDERS_DIR / day / "publication.pdf"
    if not path.exists():
        raise HTTPException(404, f"No PDF for {day}")
    return FileResponse(path, media_type="application/pdf",
                        headers={"Content-Disposition": f'inline; filename="github-daily-{day}.pdf"'})


@app.get("/d/{day}/jpeg", response_class=HTMLResponse)
def serve_jpeg(request: Request, day: str):
    _validate_date(day)
    root = _root(request)
    path = RENDERS_DIR / day / "publication.jpeg"
    if not path.exists():
        raise HTTPException(404, f"No JPEG for {day}")
    return HTMLResponse(f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>图片 · {day}</title>
<style>*{{margin:0;padding:0;box-sizing:border-box}}body{{background:#1d1d1f}}</style>
</head><body>
<img src="{root}/d/{day}/jpeg-file" style="display:block;width:100%;height:auto">
</body></html>""")


@app.get("/d/{day}/jpeg-file")
def serve_jpeg_file(day: str):
    _validate_date(day)
    path = RENDERS_DIR / day / "publication.jpeg"
    if not path.exists():
        raise HTTPException(404, f"No JPEG for {day}")
    return FileResponse(path, media_type="image/jpeg",
                        headers={"Content-Disposition": f'inline; filename="github-daily-{day}.jpg"'})


@app.get("/archive", response_class=HTMLResponse)
def archive(request: Request):
    root = _root(request)
    dates = list_publication_dates()
    dates_json = json.dumps(dates)
    body = f"""
<article class="publication">
  <h1>历史刊物</h1>
  <blockquote><p>共 {len(dates)} 期</p></blockquote>
  <div id="cal-wrap">
    <div class="cal-header">
      <button class="cal-nav" onclick="calPrev()">&#8249;</button>
      <span id="cal-title" class="cal-title"></span>
      <button class="cal-nav" onclick="calNext()">&#8250;</button>
    </div>
    <div class="cal-grid" id="cal-grid"></div>
  </div>
</article>
<style>
  #cal-wrap {{ max-width: 340px; margin: 0 auto; font-family: 'Inter', -apple-system, sans-serif; }}
  .cal-header {{ display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }}
  .cal-nav {{ background: none; border: 1px solid #d2d2d7; border-radius: 6px; width: 32px; height: 32px; cursor: pointer; font-size: 18px; color: #1d1d1f; line-height: 1; }}
  .cal-nav:hover {{ background: #f5f5f7; }}
  .cal-title {{ font-size: 16px; font-weight: 600; color: #1d1d1f; }}
  .cal-grid {{ display: grid; grid-template-columns: repeat(7, 1fr); gap: 2px; }}
  .cal-dow {{ text-align: center; font-size: 11px; font-weight: 500; color: rgba(0,0,0,0.38); padding: 4px 0 10px; }}
  .cal-day {{ display: flex; flex-direction: column; align-items: center; padding: 6px 0; border-radius: 8px; min-height: 42px; justify-content: flex-start; padding-top: 7px; }}
  .cal-day.has-pub {{ cursor: pointer; }}
  .cal-day.has-pub:hover {{ background: #f0f4ff; }}
  .cal-day .dn {{ font-size: 13px; color: #1d1d1f; line-height: 1; }}
  .cal-day.blank .dn {{ color: transparent; }}
  .cal-day.today .dn {{ background: #0066cc; color: #fff; width: 22px; height: 22px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 12px; }}
  .cal-day .dot {{ width: 5px; height: 5px; border-radius: 50%; background: #0066cc; margin-top: 4px; }}
</style>
<script>
(function() {{
  const DATES = new Set({dates_json});
  const MZ = ['1月','2月','3月','4月','5月','6月','7月','8月','9月','10月','11月','12月'];
  const DZ = ['日','一','二','三','四','五','六'];
  const td = new Date();
  const todayStr = td.getFullYear()+'-'+p2(td.getMonth()+1)+'-'+p2(td.getDate());
  const sorted = Array.from(DATES).sort().reverse();
  let yr, mo;
  if (sorted.length) {{
    const latest = sorted[0].split('-').map(Number);
    yr = latest[0]; mo = latest[1] - 1;
  }} else {{
    yr = td.getFullYear(); mo = td.getMonth();
  }}
  function p2(n) {{ return String(n).padStart(2,'0'); }}
  function render() {{
    document.getElementById('cal-title').textContent = yr+'年'+MZ[mo];
    const grid = document.getElementById('cal-grid');
    grid.innerHTML = '';
    DZ.forEach(d => {{
      const el = document.createElement('div');
      el.className = 'cal-dow'; el.textContent = d; grid.appendChild(el);
    }});
    const firstDow = new Date(yr, mo, 1).getDay();
    const lastD = new Date(yr, mo+1, 0).getDate();
    for (let i = 0; i < firstDow; i++) {{
      const el = document.createElement('div');
      el.className = 'cal-day blank';
      el.innerHTML = '<span class="dn">·</span>';
      grid.appendChild(el);
    }}
    for (let d = 1; d <= lastD; d++) {{
      const ds = yr+'-'+p2(mo+1)+'-'+p2(d);
      const el = document.createElement('div');
      el.className = 'cal-day' + (ds === todayStr ? ' today' : '');
      el.innerHTML = '<span class="dn">'+d+'</span>';
      if (DATES.has(ds)) {{
        el.classList.add('has-pub');
        el.innerHTML += '<span class="dot"></span>';
        el.addEventListener('click', function() {{ window.location.href='{root}/d/'+ds; }});
      }}
      grid.appendChild(el);
    }}
  }}
  window.calPrev = function() {{ mo--; if(mo<0){{mo=11;yr--;}} render(); }};
  window.calNext = function() {{ mo++; if(mo>11){{mo=0;yr++;}} render(); }};
  render();
}})();
</script>
"""
    return HTMLResponse(render_chrome("历史 · GitHub Daily", body, root=root))


@app.get("/settings", response_class=HTMLResponse)
def settings(request: Request):
    root = _root(request)
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
    return HTMLResponse(render_chrome("设置 · GitHub Daily", body, root=root))


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


def _serve_publication_html(day: str, root: str = "") -> HTMLResponse:
    rendered = RENDERS_DIR / day / "publication.html"
    if rendered.exists():
        article = extract_article(rendered.read_text(encoding="utf-8"))
        return HTMLResponse(render_chrome(f"{day} · GitHub Daily", article, current_date=day, root=root))
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
        return HTMLResponse(render_chrome(f"{day} · 渲染 pending", body, root=root))
    raise HTTPException(404, f"No publication for {day}")

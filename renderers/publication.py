"""Renderer: Markdown publication → HTML + PDF + JPEG.

Reads ``data/publications/<date>.md``, wraps the rendered HTML body in the
apple-styled shell, then uses Playwright headless Chromium to emit:

- ``data/renders/<date>/publication.html``  (the styled standalone page)
- ``data/renders/<date>/publication.pdf``   (A4 print)
- ``data/renders/<date>/publication.jpeg``  (full-page screenshot for IM)

Run:
    python -m renderers.publication
    python -m renderers.publication --date 2026-04-19
    python -m renderers.publication --no-pdf      # skip PDF
    python -m renderers.publication --no-jpeg     # skip JPEG
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path
from typing import Optional

from markdown_it import MarkdownIt

from config import load_config
from renderers.charts import inject_activity_panel

logger = logging.getLogger("renderer")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = PROJECT_ROOT / "assets" / "themes"
PUBLICATIONS_DIR = PROJECT_ROOT / "data" / "publications"
RENDERS_DIR = PROJECT_ROOT / "data" / "renders"


def md_to_html_body(md_text: str) -> str:
    md = (
        MarkdownIt("commonmark", {"html": False, "breaks": False, "linkify": True})
        .enable("table")
        .enable("strikethrough")
    )
    return md.render(md_text)


def build_html(md_text: str, title: str, target_date: date) -> str:
    body = md_to_html_body(md_text)
    cfg = load_config()
    body = inject_activity_panel(body, target_date, cfg.storage.db_path, cfg.enabled_repos)
    css = (ASSETS_DIR / "apple.css").read_text(encoding="utf-8")
    js = (ASSETS_DIR / "chart-tooltip.js").read_text(encoding="utf-8")
    shell = (ASSETS_DIR / "shell.html").read_text(encoding="utf-8")
    return (
        shell
        .replace("{{TITLE}}", title)
        .replace("{{CSS}}", css)
        .replace("{{BODY}}", body)
        .replace("{{CHART_SCRIPT}}", js)
    )


def render(target_date: date, want_pdf: bool, want_jpeg: bool) -> dict[str, Path]:
    md_path = PUBLICATIONS_DIR / f"{target_date.isoformat()}.md"
    if not md_path.exists():
        raise FileNotFoundError(f"Publication markdown not found: {md_path}")

    md_text = md_path.read_text(encoding="utf-8")
    html = build_html(md_text, title=f"GitHub Daily · {target_date.isoformat()}", target_date=target_date)

    out_dir = RENDERS_DIR / target_date.isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)

    written: dict[str, Path] = {}

    html_path = out_dir / "publication.html"
    html_path.write_text(html, encoding="utf-8")
    written["html"] = html_path
    logger.info("wrote %s", html_path)

    if not (want_pdf or want_jpeg):
        return written

    # Lazy-import Playwright so the HTML-only path doesn't require it.
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 980, "height": 1400})
            # file:// URL so relative resources (Google Fonts) resolve normally.
            page.goto(html_path.as_uri(), wait_until="networkidle")

            if want_pdf:
                pdf_path = out_dir / "publication.pdf"
                page.emulate_media(media="print")
                page.pdf(
                    path=str(pdf_path),
                    format="A4",
                    print_background=True,
                    margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
                )
                written["pdf"] = pdf_path
                logger.info("wrote %s", pdf_path)
                # Reset to screen media for the JPEG screenshot.
                page.emulate_media(media="screen")
                page.goto(html_path.as_uri(), wait_until="networkidle")

            if want_jpeg:
                jpeg_path = out_dir / "publication.jpeg"
                page.screenshot(
                    path=str(jpeg_path),
                    full_page=True,
                    type="jpeg",
                    quality=92,
                )
                written["jpeg"] = jpeg_path
                logger.info("wrote %s", jpeg_path)
        finally:
            browser.close()

    return written


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Render today's publication to HTML / PDF / JPEG.")
    parser.add_argument("--date", type=lambda s: date.fromisoformat(s),
                        default=date.today(), help="Publication date (default: today)")
    parser.add_argument("--no-pdf", action="store_true", help="Skip PDF output")
    parser.add_argument("--no-jpeg", action="store_true", help="Skip JPEG output")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        outputs = render(args.date, want_pdf=not args.no_pdf, want_jpeg=not args.no_jpeg)
    except FileNotFoundError as e:
        logger.error("%s", e)
        return 1

    for kind, path in outputs.items():
        print(f"{kind}: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

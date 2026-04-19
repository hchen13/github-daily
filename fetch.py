"""CLI for downstream agents: look up a rendered publication by date.

Prints an absolute path (one per line), exits 0 on success. Agents can
``cp``/``cat`` the path or read it directly.

Usage:
    python -m fetch latest                    # path to latest PDF
    python -m fetch latest --format jpeg      # latest JPEG
    python -m fetch --date 2026-04-19         # specific date, PDF
    python -m fetch --date 2026-04-19 --format md
    python -m fetch list                      # list all available dates
    python -m fetch list --format jpeg        # list dates that have a JPEG

Formats: pdf (default), jpeg, md, html.

Exit codes:
    0  ok
    1  not found
    2  usage error
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent
RENDERS_DIR = PROJECT_ROOT / "data" / "renders"
PUBLICATIONS_DIR = PROJECT_ROOT / "data" / "publications"

FORMAT_TO_FILENAME = {
    "pdf": "publication.pdf",
    "jpeg": "publication.jpeg",
    "jpg": "publication.jpeg",
    "html": "publication.html",
    "md": None,  # sourced from PUBLICATIONS_DIR instead of RENDERS_DIR
}


def _path_for(target_date: date, fmt: str) -> Path:
    fmt = fmt.lower()
    if fmt not in FORMAT_TO_FILENAME:
        raise SystemExit(f"Unknown format: {fmt!r}. Choose from: pdf / jpeg / md / html.")
    if fmt == "md":
        return PUBLICATIONS_DIR / f"{target_date.isoformat()}.md"
    return RENDERS_DIR / target_date.isoformat() / FORMAT_TO_FILENAME[fmt]


def _all_dates(fmt: str) -> list[str]:
    """Dates that have a file for the given format, newest first."""
    fmt = fmt.lower()
    if fmt == "md":
        if not PUBLICATIONS_DIR.exists():
            return []
        stems: list[str] = []
        for p in PUBLICATIONS_DIR.glob("*.md"):
            try:
                date.fromisoformat(p.stem)
                stems.append(p.stem)
            except ValueError:
                continue
        return sorted(stems, reverse=True)

    if not RENDERS_DIR.exists():
        return []
    filename = FORMAT_TO_FILENAME[fmt]
    out: list[str] = []
    for child in RENDERS_DIR.iterdir():
        if not child.is_dir():
            continue
        try:
            date.fromisoformat(child.name)
        except ValueError:
            continue
        if (child / filename).exists():
            out.append(child.name)
    return sorted(out, reverse=True)


def cmd_latest(fmt: str) -> int:
    dates = _all_dates(fmt)
    if not dates:
        print(f"No publication found for format {fmt!r}.", file=sys.stderr)
        return 1
    path = _path_for(date.fromisoformat(dates[0]), fmt)
    print(path)
    return 0


def cmd_date(target_date: date, fmt: str) -> int:
    path = _path_for(target_date, fmt)
    if not path.exists():
        print(f"Not found: {path}", file=sys.stderr)
        return 1
    print(path)
    return 0


def cmd_list(fmt: str) -> int:
    for d in _all_dates(fmt):
        print(d)
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch a rendered publication path by date.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "action",
        nargs="?",
        default="latest",
        choices=["latest", "list"],
        help="`latest` (default) or `list`. Omit when passing --date.",
    )
    parser.add_argument("--date", type=lambda s: date.fromisoformat(s),
                        help="Specific date (YYYY-MM-DD). Overrides action.")
    parser.add_argument("--format", default="pdf",
                        choices=["pdf", "jpeg", "jpg", "md", "html"],
                        help="Output format (default: pdf).")
    args = parser.parse_args(argv)

    if args.date:
        return cmd_date(args.date, args.format)
    if args.action == "latest":
        return cmd_latest(args.format)
    if args.action == "list":
        return cmd_list(args.format)
    parser.error("unreachable")
    return 2


if __name__ == "__main__":
    sys.exit(main())

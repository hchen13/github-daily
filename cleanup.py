"""Maintenance: prune trending review cache for repos that have been off
all trending lists for more than N days (default: 7).

Strategy: scan the last N days of ``data/trending/<date>.json`` files, build
the set of full_names that appeared on any list, and delete every cached
review whose ``full_name`` is not in that set.

Run:
    python -m cleanup                    # dry-run, prints what would be removed
    python -m cleanup --apply            # actually delete
    python -m cleanup --days 14          # change retention window
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from config import load_config

logger = logging.getLogger("cleanup")

REVIEWS_DIR = Path("data/trending_reviews")


def keep_slugs(trending_dir: Path, today: date, days: int) -> set[str]:
    """Slugs (owner-repo) of every repo seen on any trending list in the window."""
    slugs: set[str] = set()
    for n in range(days):
        d = today - timedelta(days=n)
        path = trending_dir / f"{d.isoformat()}.json"
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            logger.warning("skipping corrupt %s: %s", path, e)
            continue
        for repo in data.get("repos", []):
            full_name = repo.get("full_name")
            if full_name:
                slugs.add(full_name.replace("/", "-"))
    return slugs


def cleanup(reviews_dir: Path, keep: set[str], apply: bool) -> list[Path]:
    if not reviews_dir.exists():
        logger.info("no reviews directory at %s, nothing to do", reviews_dir)
        return []
    removed: list[Path] = []
    for path in sorted(reviews_dir.glob("*.json")):
        if path.stem in keep:
            continue
        removed.append(path)
        if apply:
            path.unlink()
    return removed


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=7,
                        help="Retention window in days (default: 7)")
    parser.add_argument("--date", type=lambda s: date.fromisoformat(s),
                        default=date.today(), help="Reference date (default: today)")
    parser.add_argument("--apply", action="store_true",
                        help="Actually delete files. Without this, prints a dry-run list.")
    parser.add_argument("--config", help="Path to config.yaml")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cfg = load_config(args.config)
    keep = keep_slugs(Path(cfg.storage.trending_dir), args.date, args.days)
    logger.info("Keeping %d unique repos seen in last %d days", len(keep), args.days)

    removed = cleanup(REVIEWS_DIR, keep, args.apply)
    if not removed:
        logger.info("Nothing to remove. (cache is clean)")
        return 0

    action = "Removed" if args.apply else "Would remove"
    logger.info("%s %d cached review(s):", action, len(removed))
    for p in removed:
        print(f"  {p}")

    if not args.apply:
        logger.info("Dry-run; pass --apply to actually delete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

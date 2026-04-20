"""Orchestrator: run the full GitHub Daily pipeline end-to-end.

Stages, in order:
    trending  → fetch GitHub Trending Daily Top 1 + Weekly Top 10 → JSON
    repos     → fetch tracked repos' issues/PRs/commits/releases → SQLite
    wiki      → pull local clones, rebuild per-repo wiki if HEAD sha changed
    narrator  → 1a, per-repo 24h integrated narrative (reads wiki)
    reviewer  → 1b, opus reviews each trending repo's code (cache-aware)
    editor    → 2, assemble the daily publication Markdown
    render    → MD → HTML + PDF + JPEG via Playwright

Usage:
    python -m run                            # full pipeline, today
    python -m run --date 2026-04-19          # specific date
    python -m run --only editor --only render
    python -m run --skip trending --skip repos
    python -m run --workers 8                # propagated to narrator + reviewer
    python -m run --verbose
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date
from typing import Optional

from analysts.editor import main as editor_main
from analysts.narrator import main as narrator_main
from analysts.repo_wiki import main as wiki_main
from analysts.trending_reviewer import main as reviewer_main
from collectors.repos import main as repos_main
from collectors.trending import main as trending_main
from renderers.publication import main as render_main

STEPS = ["trending", "repos", "wiki", "narrator", "reviewer", "editor", "render"]


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", type=lambda s: date.fromisoformat(s),
                        default=date.today(), help="Publication date (default: today)")
    parser.add_argument("--skip", action="append", default=[], choices=STEPS,
                        help="Skip a stage. Repeat to skip multiple.")
    parser.add_argument("--only", action="append", default=[], choices=STEPS,
                        help="Run only the listed stages. Repeat for multiple.")
    parser.add_argument("--workers", type=int, default=4,
                        help="Parallelism for narrator + reviewer (default: 4)")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger = logging.getLogger("run")

    date_arg = args.date.isoformat()
    workers_arg = ["--workers", str(args.workers)]
    verbose_arg = ["--verbose"] if args.verbose else []

    invocations: dict[str, tuple] = {
        "trending": (trending_main, ["--date", date_arg] + verbose_arg),
        "repos":    (repos_main,    verbose_arg),
        "wiki":     (wiki_main,     verbose_arg),
        "narrator": (narrator_main, workers_arg + verbose_arg),
        "reviewer": (reviewer_main, ["--date", date_arg] + workers_arg + verbose_arg),
        "editor":   (editor_main,   ["--date", date_arg] + verbose_arg),
        "render":   (render_main,   ["--date", date_arg] + verbose_arg),
    }

    plan = list(args.only) if args.only else list(STEPS)
    plan = [s for s in plan if s not in args.skip]
    if not plan:
        logger.error("Nothing to run (after --only/--skip filters)")
        return 1

    logger.info("Plan: %s | date=%s | workers=%d", " → ".join(plan), date_arg, args.workers)

    pipeline_t0 = time.time()
    for step in plan:
        fn, step_argv = invocations[step]
        logger.info("───── [%s] starting (argv=%s)", step, step_argv)
        t0 = time.time()
        try:
            rc = fn(step_argv)
        except SystemExit as e:
            rc = int(e.code) if e.code is not None else 0
        except Exception as e:
            logger.exception("[%s] crashed: %s", step, e)
            rc = 99
        elapsed = time.time() - t0
        if rc != 0:
            logger.error("───── [%s] FAILED (rc=%d) after %.1fs", step, rc, elapsed)
            return rc
        logger.info("───── [%s] done in %.1fs", step, elapsed)

    total = time.time() - pipeline_t0
    logger.info("Pipeline complete in %.1fs (%d/%d stages)", total, len(plan), len(STEPS))
    return 0


if __name__ == "__main__":
    sys.exit(main())

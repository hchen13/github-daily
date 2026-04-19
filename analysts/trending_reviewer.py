"""Trending reviewer (1b): opus reads a repo and writes a review.

For each repo in today's trending JSON that doesn't have a cached review yet,
shallow-clone it, invoke claude CLI with the reviewer system prompt, parse
the returned JSON, and cache under ``data/trending_reviews/{slug}.json``.

Cache policy: cache by (owner/repo), no SHA check. As long as the repo stays
on a trending list, yesterday's review is reused. A separate cleanup task
will remove reviews for repos that have dropped off the list for >7 days.

Run:
    python -m analysts.trending_reviewer              # today, cache-aware
    python -m analysts.trending_reviewer --date 2026-04-19
    python -m analysts.trending_reviewer --repo owner/name
    python -m analysts.trending_reviewer --force       # bypass cache
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from analysts import build_system_prompt
from config import load_config

logger = logging.getLogger("trending_reviewer")

CLONE_ROOT = Path("/tmp/github-daily/clones")
REVIEWS_DIR = Path("data/trending_reviews")


def _slug(full_name: str) -> str:
    return full_name.replace("/", "-")


def _iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_trending(trending_dir: Path, target_date: date) -> list[dict]:
    path = trending_dir / f"{target_date.isoformat()}.json"
    if not path.exists():
        raise FileNotFoundError(f"Trending file not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("repos", [])


def cache_path(full_name: str) -> Path:
    return REVIEWS_DIR / f"{_slug(full_name)}.json"


def load_cached(full_name: str) -> Optional[dict]:
    p = cache_path(full_name)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("corrupt cache for %s, ignoring", full_name)
        return None


def save_review(full_name: str, review: dict) -> Path:
    REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    p = cache_path(full_name)
    p.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def clone_repo(full_name: str) -> Optional[Path]:
    CLONE_ROOT.mkdir(parents=True, exist_ok=True)
    dest = CLONE_ROOT / _slug(full_name)
    if dest.exists():
        shutil.rmtree(dest)
    url = f"https://github.com/{full_name}.git"
    logger.info("[%s] cloning", full_name)
    t0 = time.time()
    try:
        result = subprocess.run(
            ["git", "clone", "--depth", "1", "--single-branch", url, str(dest)],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        logger.error("[%s] git clone timeout", full_name)
        return None
    if result.returncode != 0:
        logger.error("[%s] git clone failed: %s", full_name, result.stderr.strip()[:300])
        return None
    logger.debug("[%s] cloned in %.1fs", full_name, time.time() - t0)
    return dest


SECTION_RE = re.compile(
    r"^(INTRO|TECH_STACK|SCALE|SCALE_TAG|TECH_TAGS|VERDICT|EVALUATION)\s*:\s*$",
    re.MULTILINE,
)

REQUIRED_SECTIONS = ("INTRO", "TECH_STACK", "SCALE", "EVALUATION")


def parse_review(text: str) -> Optional[dict]:
    """Parse the section format. VERDICT is optional for back-compat with
    older cached reviews; everything else is required."""
    text = text.strip()
    # Strip optional code fences
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)

    matches = list(SECTION_RE.finditer(text))
    if len(matches) < len(REQUIRED_SECTIONS):
        return None

    sections: dict[str, str] = {}
    for i, m in enumerate(matches):
        name = m.group(1)
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections[name] = text[body_start:body_end].strip()

    if not all(k in sections for k in REQUIRED_SECTIONS):
        return None

    def _parse_bullets(body: str) -> list[str]:
        items: list[str] = []
        for line in body.splitlines():
            s = line.strip()
            if s.startswith(("-", "*", "•")):
                s = s.lstrip("-*• ").strip()
            if s:
                items.append(s)
        return items

    tech_stack = _parse_bullets(sections["TECH_STACK"])
    tech_tags = _parse_bullets(sections.get("TECH_TAGS", ""))

    return {
        "intro": sections["INTRO"].strip(),
        "tech_stack": tech_stack,
        "scale": sections["SCALE"].strip(),
        "scale_tag": sections.get("SCALE_TAG", "").strip(),
        "tech_tags": tech_tags,
        "verdict": sections.get("VERDICT", "").strip(),
        "evaluation": sections["EVALUATION"].strip(),
    }


def run_reviewer(full_name: str, clone_path: Path, claude_bin: str, model: str) -> Optional[dict]:
    system_prompt = build_system_prompt("trending_reviewer.md")
    user_prompt = (
        f"目标仓库：{full_name}\n"
        f"本地克隆路径：{clone_path}\n\n"
        "开始翻。按四个 section 输出：INTRO / TECH_STACK / SCALE / EVALUATION。"
    )

    cmd = [
        claude_bin,
        "--print",
        "--model", model,
        "--allowedTools", "Read,Grep,Glob",
        "--add-dir", str(clone_path),
        "--system-prompt", system_prompt,
    ]

    logger.info("[%s] running reviewer (model=%s)", full_name, model)
    t0 = time.time()
    try:
        result = subprocess.run(
            cmd,
            input=user_prompt,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        logger.error("[%s] claude CLI timeout", full_name)
        return None
    duration = time.time() - t0

    if result.returncode != 0:
        logger.error("[%s] claude CLI failed: %s", full_name, result.stderr.strip()[:300])
        return None

    raw = result.stdout.strip()
    parsed = parse_review(raw)
    if not parsed:
        debug_path = Path("/tmp/github-daily/failed") / f"{_slug(full_name)}.txt"
        debug_path.parent.mkdir(parents=True, exist_ok=True)
        debug_path.write_text(raw, encoding="utf-8")
        logger.error("[%s] failed to parse review. Full raw output saved to %s (head: %r)",
                     full_name, debug_path, raw[:200])
        return None

    review = {
        "full_name": full_name,
        "reviewed_at": _iso_utc(datetime.now(timezone.utc)),
        "model": model,
        "duration_s": round(duration, 1),
        **parsed,
    }
    logger.info("[%s] reviewer done in %.1fs", full_name, duration)
    return review


def process_repo(full_name: str, claude_bin: str, model: str, force: bool = False) -> Optional[dict]:
    if not force:
        cached = load_cached(full_name)
        if cached:
            logger.info("[%s] cache hit, skipping (reviewed_at=%s)",
                        full_name, cached.get("reviewed_at", "?"))
            return cached

    clone_path = clone_repo(full_name)
    if not clone_path:
        return None
    try:
        review = run_reviewer(full_name, clone_path, claude_bin, model)
        if review:
            save_review(full_name, review)
        return review
    finally:
        # Clone is disposable — review JSON is the artifact.
        try:
            shutil.rmtree(clone_path)
        except Exception as e:
            logger.warning("[%s] failed to clean clone: %s", full_name, e)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run the trending reviewer (1b) on today's trending repos.")
    parser.add_argument("--date", type=lambda s: date.fromisoformat(s),
                        default=date.today(), help="Trending file date (default: today)")
    parser.add_argument("--repo", help="Review only this repo (owner/name)")
    parser.add_argument("--force", action="store_true", help="Bypass cache, re-review")
    parser.add_argument("--workers", type=int, default=4, help="Parallel workers (default: 4)")
    parser.add_argument("--config", help="Path to config.yaml")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cfg = load_config(args.config)

    if args.repo:
        targets = [args.repo]
    else:
        repos = load_trending(Path(cfg.storage.trending_dir), args.date)
        targets = [r["full_name"] for r in repos]

    model = cfg.analysis.model_for("trending_reviewer")
    logger.info("Reviewing %d repos with %s (workers=%d)", len(targets), model, args.workers)

    reviews: dict[str, Optional[dict]] = {}
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {
            pool.submit(process_repo, full_name, cfg.analysis.claude_bin, model, args.force): full_name
            for full_name in targets
        }
        for fut in as_completed(futures):
            full_name = futures[fut]
            try:
                reviews[full_name] = fut.result()
            except Exception as e:
                logger.exception("[%s] worker crashed: %s", full_name, e)
                reviews[full_name] = None

    ok = 0
    for full_name in targets:  # preserve original order for printing
        review = reviews.get(full_name)
        if review:
            ok += 1
            print()
            print("=" * 72)
            print(full_name)
            print("=" * 72)
            print(json.dumps(review, ensure_ascii=False, indent=2))

    logger.info("Done. %d/%d reviewed (rest skipped or failed)", ok, len(targets))
    return 0


if __name__ == "__main__":
    sys.exit(main())

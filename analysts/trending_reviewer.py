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

from config import load_config

logger = logging.getLogger("trending_reviewer")

CLONE_ROOT = Path("/tmp/github-daily/clones")
REVIEWS_DIR = Path("data/trending_reviews")
PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "trending_reviewer.md"


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


def extract_json(text: str) -> Optional[dict]:
    """Pull the first balanced ``{...}`` block out of a possibly noisy stdout."""
    text = text.strip()
    # Strip ``` fences if present
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    # Try straight parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Find first balanced object
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                candidate = text[start:i + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    continue
    return None


def run_reviewer(full_name: str, clone_path: Path, claude_bin: str, model: str) -> Optional[dict]:
    system_prompt = PROMPT_PATH.read_text(encoding="utf-8")
    user_prompt = (
        f"目标仓库：{full_name}\n"
        f"本地克隆路径：{clone_path}\n\n"
        "开始翻。记住：输出单个 JSON 对象，无 markdown 包裹。"
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
    parsed = extract_json(raw)
    if not parsed:
        logger.error("[%s] failed to parse JSON from output (first 300 chars): %s",
                     full_name, raw[:300])
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

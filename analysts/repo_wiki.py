"""Repo wiki builder (1a-prelude): compile a structured understanding of each tracked repo.

The narrator doesn't read source code — it only looks at 24h JSON feeds. This
module produces a repo-level "wiki" (architecture overview, concept glossary,
reader persona, tracked focus) so the narrator can translate technical PRs
into plain-language prose that readers without project background can follow.

Wiki output: one markdown per repo at ``data/repo_wikis/<owner>-<repo>.md``.
Input: the repo already cloned locally under ``~/research/repos/<name>/``.

Run:
    python -m analysts.repo_wiki                        # all enabled repos with local clones
    python -m analysts.repo_wiki --repo openai/codex    # one repo
    python -m analysts.repo_wiki --force                # rebuild even if wiki already up to date
"""
from __future__ import annotations

import argparse
import logging
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from analysts import build_system_prompt
from config import RepoConfig, load_config

logger = logging.getLogger("repo_wiki")

WIKIS_DIR = Path("data/repo_wikis")
LOCAL_REPOS_ROOT = Path.home() / "research" / "repos"


def _slug(full_name: str) -> str:
    return full_name.replace("/", "-")


def _iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def local_clone_path(repo: RepoConfig) -> Optional[Path]:
    """Map a config repo to its local clone. Returns None if not found."""
    candidate = LOCAL_REPOS_ROOT / repo.name
    if candidate.exists() and (candidate / ".git").exists():
        return candidate
    return None


def current_head_sha(clone_path: Path) -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=clone_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception as e:
        logger.warning("[%s] git rev-parse failed: %s", clone_path.name, e)
    return None


def pull_clone(clone_path: Path, label: str) -> None:
    """Fast-forward pull the clone. Best-effort — failures are logged, not raised.

    We only fast-forward to avoid touching any in-progress user work in the clone.
    If the user has diverged local commits, the pull fails and we proceed with
    whatever HEAD is currently at (existing wiki stays valid).
    """
    try:
        t0 = time.time()
        result = subprocess.run(
            ["git", "pull", "--ff-only"],
            cwd=clone_path,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            logger.info("[%s] git pull --ff-only ok (%.1fs)", label, time.time() - t0)
        else:
            logger.warning("[%s] git pull failed (rc=%d): %s",
                           label, result.returncode, result.stderr.strip()[:200])
    except Exception as e:
        logger.warning("[%s] git pull crashed: %s", label, e)


FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def wiki_path(full_name: str) -> Path:
    return WIKIS_DIR / f"{_slug(full_name)}.md"


def existing_wiki_sha(full_name: str) -> Optional[str]:
    p = wiki_path(full_name)
    if not p.exists():
        return None
    text = p.read_text(encoding="utf-8")
    m = FRONTMATTER_RE.match(text)
    if not m:
        return None
    for line in m.group(1).splitlines():
        if line.startswith("last_sha:"):
            return line.split(":", 1)[1].strip()
    return None


def run_wiki_builder(repo: RepoConfig, clone_path: Path,
                     claude_bin: str, model: str) -> Optional[str]:
    system_prompt = build_system_prompt("repo_wiki.md")
    user_prompt = (
        f"目标项目：{repo.display_name}（{repo.full_name}）\n"
        f"本地克隆绝对路径：{clone_path}\n\n"
        "开始读。按五个章节输出 markdown："
        "## 概览 / ## 架构 / ## 概念词典 / ## 读者画像 / ## 跟踪焦点。"
    )
    cmd = [
        claude_bin,
        "--print",
        "--model", model,
        "--allowedTools", "Read,Grep,Glob",
        "--add-dir", str(clone_path),
        "--system-prompt", system_prompt,
    ]

    logger.info("[%s] building wiki (model=%s)", repo.full_name, model)
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
        logger.error("[%s] claude CLI timeout", repo.full_name)
        return None
    duration = time.time() - t0

    if result.returncode != 0:
        logger.error("[%s] claude CLI failed: %s", repo.full_name, result.stderr.strip()[:300])
        return None

    body = result.stdout.strip()
    if "## 概览" not in body:
        logger.error("[%s] wiki output missing '## 概览' header, head=%r", repo.full_name, body[:200])
        return None

    logger.info("[%s] wiki built in %.1fs, %d chars", repo.full_name, duration, len(body))
    return body


def save_wiki(repo: RepoConfig, body: str, sha: str, model: str) -> Path:
    WIKIS_DIR.mkdir(parents=True, exist_ok=True)
    p = wiki_path(repo.full_name)
    frontmatter = (
        "---\n"
        f"repo: {repo.full_name}\n"
        f"display_name: {repo.display_name}\n"
        f"last_sha: {sha}\n"
        f"last_built: {_iso_utc(datetime.now(timezone.utc))}\n"
        f"model: {model}\n"
        "---\n\n"
    )
    p.write_text(frontmatter + body + "\n", encoding="utf-8")
    return p


def build_one(repo: RepoConfig, claude_bin: str, model: str, force: bool) -> Optional[Path]:
    clone_path = local_clone_path(repo)
    if not clone_path:
        logger.warning("[%s] no local clone at %s/%s — skipping",
                       repo.full_name, LOCAL_REPOS_ROOT, repo.name)
        return None

    pull_clone(clone_path, repo.full_name)

    sha = current_head_sha(clone_path)
    if not sha:
        logger.warning("[%s] could not read HEAD sha — skipping", repo.full_name)
        return None

    if not force:
        existing_sha = existing_wiki_sha(repo.full_name)
        if existing_sha == sha:
            logger.info("[%s] wiki already up to date (sha=%s) — skipping",
                        repo.full_name, sha[:12])
            return wiki_path(repo.full_name)

    body = run_wiki_builder(repo, clone_path, claude_bin, model)
    if not body:
        return None
    return save_wiki(repo, body, sha, model)


def load_wiki(full_name: str) -> Optional[str]:
    """Narrator helper: return wiki body (without frontmatter), or None."""
    p = wiki_path(full_name)
    if not p.exists():
        return None
    text = p.read_text(encoding="utf-8")
    m = FRONTMATTER_RE.match(text)
    return text[m.end():].strip() if m else text.strip()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build per-repo wiki for narrator context.")
    parser.add_argument("--repo", help="Only build for this repo (owner/name)")
    parser.add_argument("--force", action="store_true",
                        help="Rebuild even if wiki sha matches current HEAD")
    parser.add_argument("--config", help="Path to config.yaml")
    parser.add_argument("--workers", type=int, default=2,
                        help="Parallel workers (default: 2)")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cfg = load_config(args.config)
    repos = cfg.enabled_repos
    if args.repo:
        repos = [r for r in repos if r.full_name == args.repo]
        if not repos:
            logger.error("repo %s not found in config", args.repo)
            return 1

    model = cfg.analysis.model_for("repo_wiki")
    logger.info("Building wikis for %d repos with %s (workers=%d)",
                len(repos), model, args.workers)

    results: dict[str, Optional[Path]] = {}
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {
            pool.submit(build_one, repo, cfg.analysis.claude_bin, model, args.force): repo
            for repo in repos
        }
        for fut in as_completed(futures):
            repo = futures[fut]
            try:
                results[repo.full_name] = fut.result()
            except Exception as e:
                logger.exception("[%s] worker crashed: %s", repo.full_name, e)
                results[repo.full_name] = None

    ok = sum(1 for p in results.values() if p)
    logger.info("Done. %d/%d wikis available", ok, len(repos))
    for repo in repos:
        p = results.get(repo.full_name)
        status = str(p) if p else "(skipped / failed)"
        print(f"  {repo.full_name}: {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

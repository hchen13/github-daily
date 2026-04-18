"""GitHub Trending collector.

Fetches Daily Top 1 + Weekly Top 10, dedupes, hydrates metadata via `gh` CLI,
writes ``data/trending/YYYY-MM-DD.json``.

Run:
    python -m collectors.trending
    python -m collectors.trending --date 2026-04-19 --verbose
"""
from __future__ import annotations

import argparse
import base64
import json
import logging
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup

logger = logging.getLogger("trending")

TRENDING_URL = "https://github.com/trending"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


@dataclass
class TrendingEntry:
    full_name: str
    owner: str
    name: str
    url: str = ""
    description: Optional[str] = None
    language: Optional[str] = None
    stars: int = 0
    stars_gained_daily: Optional[str] = None
    stars_gained_weekly: Optional[str] = None
    created_at: Optional[str] = None
    pushed_at: Optional[str] = None
    topics: list[str] = field(default_factory=list)
    homepage: Optional[str] = None
    readme_excerpt: Optional[str] = None
    lists: list[str] = field(default_factory=list)
    rank: dict[str, int] = field(default_factory=dict)


def fetch_trending_html(since: str) -> str:
    url = f"{TRENDING_URL}?{urllib.parse.urlencode({'since': since})}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def parse_trending(html: str) -> list[tuple[str, str, Optional[str]]]:
    """Return ``[(owner, name, stars_gained_text)]`` in rank order."""
    soup = BeautifulSoup(html, "html.parser")
    results: list[tuple[str, str, Optional[str]]] = []
    for article in soup.select("article.Box-row"):
        link = article.select_one("h2 a, h1 a, h3 a")
        if not link:
            continue
        href = (link.get("href") or "").strip().strip("/")
        if "/" not in href:
            continue
        owner, _, remainder = href.partition("/")
        name = remainder.split("/", 1)[0]
        if not owner or not name:
            continue
        gained_text: Optional[str] = None
        for span in article.select("span.d-inline-block.float-sm-right"):
            text = span.get_text(" ", strip=True)
            if "star" in text.lower():
                gained_text = text
                break
        results.append((owner, name, gained_text))
    return results


def _run_gh(args: list[str], timeout: int = 30) -> Optional[str]:
    try:
        result = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        logger.error("gh CLI not found on PATH. Install from https://cli.github.com/")
        return None
    except subprocess.TimeoutExpired:
        logger.warning("gh timeout: %s", " ".join(args))
        return None
    if result.returncode != 0:
        logger.warning("gh %s failed: %s", args[0], result.stderr.strip()[:200])
        return None
    return result.stdout


def gh_repo_view(full_name: str) -> Optional[dict]:
    fields = ",".join([
        "name", "nameWithOwner", "owner", "description",
        "primaryLanguage", "stargazerCount",
        "createdAt", "updatedAt", "pushedAt",
        "repositoryTopics", "homepageUrl", "url",
    ])
    stdout = _run_gh(["repo", "view", full_name, "--json", fields])
    if not stdout:
        return None
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as e:
        logger.warning("repo view JSON decode failed for %s: %s", full_name, e)
        return None


def gh_readme(full_name: str) -> Optional[str]:
    stdout = _run_gh(["api", f"repos/{full_name}/readme"])
    if not stdout:
        return None
    try:
        data = json.loads(stdout)
        return base64.b64decode(data.get("content", "")).decode("utf-8", errors="replace")
    except Exception as e:
        logger.warning("readme decode failed for %s: %s", full_name, e)
        return None


def first_paragraph(text: str, max_chars: int = 500) -> str:
    """Extract the first substantive paragraph from a README.

    Skips headings, badges, images, and lone HTML tags. Best-effort — the real
    code review happens later when opus reads the repo directly.
    """
    buf: list[str] = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            if buf:
                break
            continue
        if s.startswith("#"):
            continue
        if s.startswith("![") or s.startswith("<img"):
            continue
        if s.startswith("[![") or s.startswith("[<"):
            continue
        if s.startswith("<!--"):
            continue
        if s.startswith("<") or s.endswith(">"):
            continue
        if set(s) <= set("-=*_ "):
            continue
        buf.append(s)
    joined = " ".join(buf).strip()
    if len(joined) > max_chars:
        joined = joined[:max_chars].rsplit(" ", 1)[0] + "…"
    return joined


def hydrate(entry: TrendingEntry) -> TrendingEntry:
    meta = gh_repo_view(entry.full_name)
    if meta:
        entry.description = meta.get("description") or None
        lang = meta.get("primaryLanguage")
        entry.language = lang.get("name") if isinstance(lang, dict) else None
        entry.stars = int(meta.get("stargazerCount") or 0)
        entry.created_at = meta.get("createdAt")
        entry.pushed_at = meta.get("pushedAt")
        topics = meta.get("repositoryTopics") or []
        entry.topics = [t.get("name") for t in topics if isinstance(t, dict) and t.get("name")]
        entry.homepage = meta.get("homepageUrl") or None
        entry.url = meta.get("url") or f"https://github.com/{entry.full_name}"
    else:
        entry.url = f"https://github.com/{entry.full_name}"
    readme = gh_readme(entry.full_name)
    if readme:
        entry.readme_excerpt = first_paragraph(readme) or None
    return entry


def collect(output_dir: Path, target_date: date) -> Path:
    logger.info("Fetching daily trending")
    daily_html = fetch_trending_html("daily")
    logger.info("Fetching weekly trending")
    weekly_html = fetch_trending_html("weekly")

    daily = parse_trending(daily_html)[:1]
    weekly = parse_trending(weekly_html)[:10]

    by_full_name: dict[str, TrendingEntry] = {}

    for rank, (owner, name, gained) in enumerate(daily, start=1):
        full_name = f"{owner}/{name}"
        entry = by_full_name.setdefault(
            full_name,
            TrendingEntry(full_name=full_name, owner=owner, name=name),
        )
        entry.lists.append("daily_top1")
        entry.rank["daily_top1"] = rank
        if gained and not entry.stars_gained_daily:
            entry.stars_gained_daily = gained

    for rank, (owner, name, gained) in enumerate(weekly, start=1):
        full_name = f"{owner}/{name}"
        entry = by_full_name.setdefault(
            full_name,
            TrendingEntry(full_name=full_name, owner=owner, name=name),
        )
        entry.lists.append("weekly_top10")
        entry.rank["weekly_top10"] = rank
        if gained and not entry.stars_gained_weekly:
            entry.stars_gained_weekly = gained

    if not by_full_name:
        raise RuntimeError(
            "No trending repos parsed. GitHub page markup may have changed."
        )

    logger.info("Hydrating %d unique repos via gh CLI", len(by_full_name))
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(hydrate, e): e.full_name for e in by_full_name.values()}
        for fut in as_completed(futures):
            try:
                fut.result()
            except Exception as e:
                logger.warning("hydrate failed for %s: %s", futures[fut], e)

    entries_sorted = sorted(
        by_full_name.values(),
        key=lambda e: (
            e.rank.get("daily_top1", 10**6),
            e.rank.get("weekly_top10", 10**6),
            e.full_name,
        ),
    )

    payload = {
        "date": target_date.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "source": "github.com/trending",
        "counts": {
            "daily_top": 1,
            "weekly_top": 10,
            "unique_repos": len(entries_sorted),
        },
        "repos": [asdict(e) for e in entries_sorted],
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{target_date.isoformat()}.json"
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("Wrote %s (%d repos)", out_path, len(entries_sorted))
    return out_path


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Collect GitHub Trending (Daily Top 1 + Weekly Top 10) into JSON.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/trending"),
        help="Output directory (default: data/trending)",
    )
    parser.add_argument(
        "--date",
        type=lambda s: date.fromisoformat(s),
        default=date.today(),
        help="Date label for the output file (default: today)",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        collect(args.output_dir, args.date)
    except urllib.error.HTTPError as e:
        logger.error("HTTP error: %s", e)
        return 2
    except urllib.error.URLError as e:
        logger.error("Network error: %s", e)
        return 2
    except Exception as e:
        logger.exception("Fatal: %s", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

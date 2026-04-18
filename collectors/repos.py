"""Tracked-repo collector.

Ported from pulse/collectors/github.py. For each tracked repo fetches:
- open issues + recently closed issues
- open PRs + recently merged PRs
- commits on every branch (auto-discovered)
- recent releases

All data lands in SQLite (``db/github-daily.db`` by default).

Run:
    python -m collectors.repos                 # all enabled repos
    python -m collectors.repos --repo owner/name
    python -m collectors.repos --verbose
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

from config import CollectionConfig, RepoConfig, load_config
from db.models import get_db, init_db

logger = logging.getLogger("repos")


def _run_gh(args: List[str], timeout: int = 60) -> Optional[Any]:
    """Run gh CLI, return parsed JSON (or [] if empty)."""
    cmd = ["gh", *args]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        logger.error("gh CLI not found on PATH")
        return None
    except subprocess.TimeoutExpired:
        logger.warning("gh timeout: %s", " ".join(args))
        return None

    if result.returncode != 0:
        logger.warning("gh failed (%s): %s", " ".join(args[:3]), result.stderr.strip()[:200])
        return None
    if not result.stdout.strip():
        return []
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        logger.warning("JSON decode error for %s: %s", " ".join(args[:3]), e)
        return None


class RepoCollector:
    def __init__(self, config: CollectionConfig, db_path: str):
        self.config = config
        self.db_path = db_path

    def fetch_all(self, repo: RepoConfig) -> Dict[str, int]:
        return {
            "issues": self.fetch_issues(repo),
            "prs": self.fetch_prs(repo),
            "commits": self.fetch_commits(repo),
            "releases": self.fetch_releases(repo),
        }

    def fetch_issues(self, repo: RepoConfig) -> int:
        full_name = repo.full_name
        logger.info("[%s] fetching issues", full_name)

        fields = "number,title,body,state,author,labels,createdAt,updatedAt,closedAt,comments,url"
        open_data = _run_gh([
            "issue", "list", "--repo", full_name, "--state", "open",
            "--limit", str(self.config.max_issues), "--json", fields,
        ]) or []
        closed_data = _run_gh([
            "issue", "list", "--repo", full_name, "--state", "closed",
            "--limit", str(self.config.max_issues // 2), "--json", fields,
        ]) or []

        count = 0
        with get_db(self.db_path) as conn:
            for issue in open_data + closed_data:
                try:
                    labels = json.dumps([l.get("name", "") for l in issue.get("labels", [])])
                    author = issue.get("author") or {}
                    author_login = author.get("login", "") if isinstance(author, dict) else str(author)
                    comments_val = issue.get("comments", 0)
                    if isinstance(comments_val, list):
                        comments_val = len(comments_val)
                    conn.execute("""
                        INSERT OR REPLACE INTO issues
                        (repo_full_name, issue_number, title, body, state, author, labels,
                         created_at, updated_at, closed_at, comments, url, fetched_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                    """, (
                        full_name, issue["number"], issue.get("title", ""),
                        (issue.get("body") or "")[:5000],
                        issue.get("state", ""), author_login, labels,
                        issue.get("createdAt", ""), issue.get("updatedAt", ""),
                        issue.get("closedAt", ""), comments_val, issue.get("url", ""),
                    ))
                    count += 1
                except Exception as e:
                    logger.warning("skip issue #%s: %s", issue.get("number"), e)
            self._log_fetch(conn, full_name, "issues", "success", count)
        logger.info("[%s] issues: %d", full_name, count)
        return count

    def fetch_prs(self, repo: RepoConfig) -> int:
        full_name = repo.full_name
        logger.info("[%s] fetching PRs", full_name)

        fields = "number,title,body,state,author,labels,baseRefName,headRefName,createdAt,updatedAt,mergedAt,url"
        open_data = _run_gh([
            "pr", "list", "--repo", full_name, "--state", "open",
            "--limit", str(self.config.max_prs), "--json", fields,
        ]) or []
        merged_data = _run_gh([
            "pr", "list", "--repo", full_name, "--state", "merged",
            "--limit", str(self.config.max_prs // 2), "--json", fields,
        ]) or []

        count = 0
        with get_db(self.db_path) as conn:
            for pr in open_data + merged_data:
                try:
                    labels = json.dumps([l.get("name", "") for l in pr.get("labels", [])])
                    author = pr.get("author") or {}
                    author_login = author.get("login", "") if isinstance(author, dict) else str(author)
                    conn.execute("""
                        INSERT OR REPLACE INTO pull_requests
                        (repo_full_name, pr_number, title, body, state, author, labels,
                         base_branch, head_branch, created_at, updated_at, merged_at, url, fetched_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                    """, (
                        full_name, pr["number"], pr.get("title", ""),
                        (pr.get("body") or "")[:5000],
                        pr.get("state", ""), author_login, labels,
                        pr.get("baseRefName", ""), pr.get("headRefName", ""),
                        pr.get("createdAt", ""), pr.get("updatedAt", ""),
                        pr.get("mergedAt", ""), pr.get("url", ""),
                    ))
                    count += 1
                except Exception as e:
                    logger.warning("skip PR #%s: %s", pr.get("number"), e)
            self._log_fetch(conn, full_name, "prs", "success", count)
        logger.info("[%s] PRs: %d", full_name, count)
        return count

    def fetch_commits(self, repo: RepoConfig) -> int:
        """Auto-discover all branches, fetch recent commits per branch in parallel."""
        full_name = repo.full_name
        logger.info("[%s] fetching commits across all branches", full_name)

        branches_data = _run_gh([
            "api", f"repos/{full_name}/branches",
            "--method", "GET", "--field", "per_page=100",
        ])

        if branches_data and isinstance(branches_data, list):
            branches = [b["name"] for b in branches_data if isinstance(b, dict) and "name" in b]
            logger.debug("[%s] discovered %d branches", full_name, len(branches))
        else:
            info = _run_gh(["repo", "view", full_name, "--json", "defaultBranchRef"])
            default = info.get("defaultBranchRef", {}).get("name", "main") if info else "main"
            branches = [default]

        def _fetch_branch(branch: str):
            data = _run_gh([
                "api", f"repos/{full_name}/commits",
                "--method", "GET",
                "--field", f"sha={branch}",
                "--field", f"per_page={self.config.max_commits_per_branch}",
            ])
            return branch, data or []

        all_branch_data: list[tuple[str, list]] = []
        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = {pool.submit(_fetch_branch, b): b for b in branches}
            for fut in as_completed(futures):
                try:
                    all_branch_data.append(fut.result())
                except Exception as e:
                    logger.warning("branch fetch failed for %s: %s", futures[fut], e)

        count = 0
        with get_db(self.db_path) as conn:
            for branch, data in all_branch_data:
                for commit in data:
                    try:
                        sha = commit.get("sha", "")
                        cdata = commit.get("commit", {}) or {}
                        adata = cdata.get("author", {}) or {}
                        gh_author = commit.get("author") or {}
                        conn.execute("""
                            INSERT OR IGNORE INTO commits
                            (repo_full_name, branch, sha, author, message, committed_at, url, fetched_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
                        """, (
                            full_name, branch, sha,
                            gh_author.get("login", adata.get("name", "")),
                            (cdata.get("message") or "")[:2000],
                            adata.get("date", ""),
                            commit.get("html_url", ""),
                        ))
                        count += 1
                    except Exception as e:
                        logger.warning("skip commit %s: %s", commit.get("sha", "")[:8], e)
            self._log_fetch(conn, full_name, "commits", "success", count)
        logger.info("[%s] commits: %d (across %d branches)", full_name, count, len(branches))
        return count

    def fetch_releases(self, repo: RepoConfig) -> int:
        full_name = repo.full_name
        logger.info("[%s] fetching releases", full_name)

        releases = _run_gh([
            "release", "list", "--repo", full_name,
            "--limit", str(self.config.max_releases),
            "--json", "tagName,name,isPrerelease,publishedAt",
        ]) or []

        count = 0
        with get_db(self.db_path) as conn:
            for rel in releases:
                try:
                    detail = _run_gh([
                        "release", "view", rel["tagName"],
                        "--repo", full_name, "--json", "body",
                    ])
                    body = detail.get("body", "") if detail else ""
                    url = f"https://github.com/{full_name}/releases/tag/{rel['tagName']}"
                    conn.execute("""
                        INSERT OR REPLACE INTO releases
                        (repo_full_name, tag_name, name, body, is_prerelease, published_at, url, fetched_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
                    """, (
                        full_name, rel["tagName"], rel.get("name", ""),
                        (body or "")[:5000],
                        1 if rel.get("isPrerelease") else 0,
                        rel.get("publishedAt", ""), url,
                    ))
                    count += 1
                except Exception as e:
                    logger.warning("skip release %s: %s", rel.get("tagName"), e)
            self._log_fetch(conn, full_name, "releases", "success", count)
        logger.info("[%s] releases: %d", full_name, count)
        return count

    def _log_fetch(self, conn, repo_full_name: str, fetch_type: str,
                   status: str, count: int, error_msg: Optional[str] = None):
        conn.execute("""
            INSERT INTO fetch_log (repo_full_name, fetch_type, status, items_count, error_msg)
            VALUES (?, ?, ?, ?, ?)
        """, (repo_full_name, fetch_type, status, count, error_msg))


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Collect tracked repos into SQLite.")
    parser.add_argument("--repo", help="Collect only this one repo (owner/name)")
    parser.add_argument("--config", help="Path to config.yaml (default: ./config.yaml)")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cfg = load_config(args.config)
    init_db(cfg.storage.db_path)

    repos = cfg.enabled_repos
    if args.repo:
        repos = [r for r in repos if r.full_name == args.repo]
        if not repos:
            logger.error("repo %s not found in config", args.repo)
            return 1

    collector = RepoCollector(cfg.collection, cfg.storage.db_path)
    summary: dict[str, dict[str, int]] = {}
    for repo in repos:
        try:
            summary[repo.full_name] = collector.fetch_all(repo)
        except Exception as e:
            logger.exception("[%s] fatal: %s", repo.full_name, e)
            summary[repo.full_name] = {"error": str(e)}

    logger.info("Done. Summary: %s", json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""SQLite schema and connection helpers.

Ported from pulse with minor cleanup. Full schema is initialized up front
so later phases (analysis, reports) don't require schema migrations.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional


_db_path: str = "./data/github-daily.db"


def set_db_path(path: str) -> None:
    global _db_path
    _db_path = path


def init_db(db_path: Optional[str] = None) -> None:
    if db_path:
        set_db_path(db_path)

    Path(_db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_db_path)
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS repos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner TEXT NOT NULL,
            name TEXT NOT NULL,
            display_name TEXT,
            enabled INTEGER DEFAULT 1,
            added_at TEXT DEFAULT (datetime('now')),
            UNIQUE(owner, name)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS issues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repo_full_name TEXT NOT NULL,
            issue_number INTEGER NOT NULL,
            title TEXT,
            body TEXT,
            state TEXT,
            author TEXT,
            labels TEXT,
            created_at TEXT,
            updated_at TEXT,
            closed_at TEXT,
            comments INTEGER DEFAULT 0,
            url TEXT,
            fetched_at TEXT DEFAULT (datetime('now')),
            UNIQUE(repo_full_name, issue_number)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS pull_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repo_full_name TEXT NOT NULL,
            pr_number INTEGER NOT NULL,
            title TEXT,
            body TEXT,
            state TEXT,
            author TEXT,
            labels TEXT,
            base_branch TEXT,
            head_branch TEXT,
            created_at TEXT,
            updated_at TEXT,
            merged_at TEXT,
            url TEXT,
            fetched_at TEXT DEFAULT (datetime('now')),
            UNIQUE(repo_full_name, pr_number)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS commits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repo_full_name TEXT NOT NULL,
            branch TEXT NOT NULL,
            sha TEXT NOT NULL,
            author TEXT,
            message TEXT,
            committed_at TEXT,
            url TEXT,
            fetched_at TEXT DEFAULT (datetime('now')),
            UNIQUE(repo_full_name, sha)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS releases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repo_full_name TEXT NOT NULL,
            tag_name TEXT NOT NULL,
            name TEXT,
            body TEXT,
            is_prerelease INTEGER DEFAULT 0,
            published_at TEXT,
            url TEXT,
            fetched_at TEXT DEFAULT (datetime('now')),
            UNIQUE(repo_full_name, tag_name)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_date TEXT NOT NULL,
            repo_full_name TEXT,
            report_type TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(report_date, repo_full_name, report_type)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS fetch_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repo_full_name TEXT NOT NULL,
            fetch_type TEXT NOT NULL,
            status TEXT NOT NULL,
            items_count INTEGER DEFAULT 0,
            error_msg TEXT,
            fetched_at TEXT DEFAULT (datetime('now'))
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS analysis_steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_date TEXT NOT NULL,
            repo_full_name TEXT NOT NULL,
            step_name TEXT NOT NULL,
            analyst TEXT NOT NULL,
            model TEXT,
            content TEXT NOT NULL,
            duration_s REAL,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(report_date, repo_full_name, step_name)
        )
    """)

    conn.commit()
    conn.close()


@contextmanager
def get_db(db_path: Optional[str] = None) -> Generator[sqlite3.Connection, None, None]:
    path = db_path or _db_path
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

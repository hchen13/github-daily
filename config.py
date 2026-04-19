"""Config loader for config.yaml."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import yaml


@dataclass
class RepoConfig:
    owner: str
    name: str
    display_name: str
    short_name: str = ""  # optional nickname for compact chart labels
    enabled: bool = True

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    @property
    def label(self) -> str:
        """Preferred label for compact spots (charts, badges)."""
        return self.short_name or self.display_name


@dataclass
class CollectionConfig:
    max_issues: int = 50
    max_prs: int = 30
    max_commits_per_branch: int = 20
    max_releases: int = 10


@dataclass
class ScheduleConfig:
    publish_time: str = "08:00"
    timezone: str = "Asia/Shanghai"


@dataclass
class StorageConfig:
    db_path: str = "./data/github-daily.db"
    trending_dir: str = "./data/trending"


@dataclass
class AnalysisConfig:
    claude_bin: str = "claude"
    models: dict = field(default_factory=dict)

    def model_for(self, role: str) -> str:
        return self.models.get(role) or "claude-sonnet-4-6"


@dataclass
class Config:
    repos: List[RepoConfig] = field(default_factory=list)
    collection: CollectionConfig = field(default_factory=CollectionConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)

    @property
    def enabled_repos(self) -> List[RepoConfig]:
        return [r for r in self.repos if r.enabled]


def load_config(config_path: Optional[str | Path] = None) -> Config:
    path = Path(config_path) if config_path else Path(__file__).parent / "config.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    repos = [
        RepoConfig(
            owner=r["owner"],
            name=r["name"],
            display_name=r.get("display_name", f"{r['owner']}/{r['name']}"),
            short_name=r.get("short_name", ""),
            enabled=r.get("enabled", True),
        )
        for r in raw.get("repos", [])
    ]

    col = raw.get("collection", {}) or {}
    collection = CollectionConfig(
        max_issues=col.get("max_issues", 50),
        max_prs=col.get("max_prs", 30),
        max_commits_per_branch=col.get("max_commits_per_branch", 20),
        max_releases=col.get("max_releases", 10),
    )

    sched = raw.get("schedule", {}) or {}
    schedule = ScheduleConfig(
        publish_time=sched.get("publish_time", "08:00"),
        timezone=sched.get("timezone", "Asia/Shanghai"),
    )

    stor = raw.get("storage", {}) or {}
    storage = StorageConfig(
        db_path=stor.get("db_path", "./data/github-daily.db"),
        trending_dir=stor.get("trending_dir", "./data/trending"),
    )

    anal = raw.get("analysis", {}) or {}
    analysis = AnalysisConfig(
        claude_bin=anal.get("claude_bin", "claude"),
        models=anal.get("models", {}) or {},
    )

    return Config(
        repos=repos,
        collection=collection,
        schedule=schedule,
        storage=storage,
        analysis=analysis,
    )

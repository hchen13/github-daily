"""Shared utilities for the analyst agents (1a / 1b / 2)."""
from __future__ import annotations

from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
SOUL_PATH = PROMPTS_DIR / "SOUL.md"


def build_system_prompt(role_file: str) -> str:
    """Concatenate SOUL.md (publication-wide voice) with the role-specific prompt.

    The SOUL is prepended so role-specific instructions can override or extend it.
    If SOUL.md is missing, the role prompt is returned alone — useful when a
    user wants to disable the voice layer entirely by deleting that file.
    """
    role_text = (PROMPTS_DIR / role_file).read_text(encoding="utf-8")
    if SOUL_PATH.exists():
        soul = SOUL_PATH.read_text(encoding="utf-8")
        return f"{soul}\n\n---\n\n# 角色\n\n{role_text}"
    return role_text

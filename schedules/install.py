"""Install / uninstall / inspect a macOS launchd schedule for the daily run.

Reads ``publish_time`` from ``config.yaml`` and writes a LaunchAgent plist
to ``~/Library/LaunchAgents/com.github-daily.daemon.plist`` that runs
``python -m run`` from the project root at the configured local time daily.

Usage:
    python -m schedules.install status      # show whether installed + loaded
    python -m schedules.install install     # write plist + bootstrap into launchd
    python -m schedules.install uninstall   # bootout + remove plist
    python -m schedules.install render      # print plist text without writing
"""
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from config import load_config

logger = logging.getLogger("schedule")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PLIST_LABEL = "com.github-daily.daemon"
PLIST_DIR = Path.home() / "Library" / "LaunchAgents"
PLIST_PATH = PLIST_DIR / f"{PLIST_LABEL}.plist"
LOG_DIR = PROJECT_ROOT / "data" / "logs"

PLIST_TMPL = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{python}</string>
    <string>-m</string>
    <string>run</string>
  </array>
  <key>WorkingDirectory</key>
  <string>{cwd}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>{path}</string>
  </dict>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>{hour}</integer>
    <key>Minute</key>
    <integer>{minute}</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>{stdout}</string>
  <key>StandardErrorPath</key>
  <string>{stderr}</string>
  <key>RunAtLoad</key>
  <false/>
  <key>ProcessType</key>
  <string>Background</string>
</dict>
</plist>
"""


def render_plist() -> str:
    cfg = load_config()
    try:
        hour, minute = (int(x) for x in cfg.schedule.publish_time.split(":"))
    except ValueError as e:
        raise SystemExit(f"Invalid publish_time {cfg.schedule.publish_time!r} in config.yaml: {e}")

    python = PROJECT_ROOT / ".venv" / "bin" / "python"
    if not python.exists():
        raise SystemExit(f"venv python not found at {python}; create it with `python3 -m venv .venv`.")

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    return PLIST_TMPL.format(
        label=PLIST_LABEL,
        python=str(python),
        cwd=str(PROJECT_ROOT),
        path=os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        hour=hour,
        minute=minute,
        stdout=str(LOG_DIR / "daemon.out"),
        stderr=str(LOG_DIR / "daemon.err"),
    )


def _target() -> str:
    return f"gui/{os.getuid()}"


def cmd_render() -> int:
    print(render_plist())
    return 0


def cmd_install() -> int:
    plist_text = render_plist()
    PLIST_DIR.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_text(plist_text, encoding="utf-8")
    logger.info("Wrote %s", PLIST_PATH)

    # bootout silently if previously loaded, then bootstrap fresh
    subprocess.run(["launchctl", "bootout", _target(), str(PLIST_PATH)],
                   capture_output=True, text=True)
    res = subprocess.run(["launchctl", "bootstrap", _target(), str(PLIST_PATH)],
                         capture_output=True, text=True)
    if res.returncode != 0:
        logger.error("launchctl bootstrap failed (%d):\n%s", res.returncode, res.stderr.strip())
        return res.returncode

    cfg = load_config()
    logger.info("Loaded into launchd. Will fire daily at %s local (%s).",
                cfg.schedule.publish_time, cfg.schedule.timezone)
    logger.info("Logs → %s/daemon.{out,err}", LOG_DIR)
    return 0


def cmd_uninstall() -> int:
    if not PLIST_PATH.exists():
        logger.info("Not installed (no %s).", PLIST_PATH)
        return 0
    subprocess.run(["launchctl", "bootout", _target(), str(PLIST_PATH)],
                   capture_output=True, text=True)
    PLIST_PATH.unlink()
    logger.info("Removed %s and unloaded.", PLIST_PATH)
    return 0


def cmd_status() -> int:
    if not PLIST_PATH.exists():
        print("Not installed.")
        return 0
    print(f"Plist file: {PLIST_PATH}")
    res = subprocess.run(["launchctl", "print", f"{_target()}/{PLIST_LABEL}"],
                         capture_output=True, text=True)
    if res.returncode != 0:
        print("Plist exists but not loaded into launchd. Run `install` to (re)load.")
        return 0
    print("Loaded ✓")
    for line in res.stdout.splitlines():
        s = line.strip()
        if s.startswith(("state =", "next run", "last exit code")):
            print(f"  {s}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Manage GitHub Daily launchd schedule.")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("install", help="Write plist + load into launchd")
    sub.add_parser("uninstall", help="Bootout + remove plist")
    sub.add_parser("status", help="Show installation/load status")
    sub.add_parser("render", help="Print rendered plist (does not write)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    return {
        "install": cmd_install,
        "uninstall": cmd_uninstall,
        "status": cmd_status,
        "render": cmd_render,
    }[args.action]()


if __name__ == "__main__":
    sys.exit(main())

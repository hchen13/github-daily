"""``python -m web`` entry point — boots uvicorn with the FastAPI app."""
from __future__ import annotations

import argparse
import logging
import sys

import uvicorn


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the GitHub Daily web UI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--root-path", default="",
                        help="URL prefix when behind a reverse proxy, e.g. /github-daily")
    parser.add_argument("--reload", action="store_true", help="Auto-reload on code changes")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    uvicorn.run("web.app:app", host=args.host, port=args.port,
                root_path=args.root_path, reload=args.reload, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())

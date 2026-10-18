#!/usr/bin/env python3
"""Show the latest MySQL SGD five-minute report as Markdown tables."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from typing import Any

from report_mysql_sgd_progress_every_5min import DEFAULT_LOG, markdown


def latest_report(path: Path) -> dict[str, Any] | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def identity(value: dict[str, Any]) -> tuple[Any, Any]:
    return value.get("timestamp"), value.get("sequence")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument(
        "--follow",
        action="store_true",
        help="keep running and print each newly written five-minute report",
    )
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    args = parser.parse_args()
    if args.poll_seconds <= 0:
        parser.error("--poll-seconds must be positive")

    last_identity: tuple[Any, Any] | None = None
    while True:
        value = latest_report(args.log.resolve())
        if value is not None and identity(value) != last_identity:
            print(markdown(value), flush=True)
            last_identity = identity(value)
        elif value is None and last_identity is None:
            print(f"{args.log.resolve()}", flush=True)
            if not args.follow:
                return 1
        if not args.follow:
            return 0
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())

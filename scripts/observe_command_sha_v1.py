#!/usr/bin/env python3
"""Read-only short-lived process observer for dynamic-gate race diagnosis."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import time


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--seconds", type=float, default=30.0)
    parser.add_argument("--interval", type=float, default=0.05)
    args = parser.parse_args()
    deadline = time.monotonic() + args.seconds
    seen: set[int] = set()
    while time.monotonic() < deadline:
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit() or int(entry.name) in seen:
                continue
            try:
                command = (
                    (entry / "cmdline").read_bytes().replace(b"\0", b" ")
                    .decode("utf-8", errors="replace").strip()
                )
                if hashlib.sha256(command.encode()).hexdigest() != args.sha256:
                    continue
                status = (entry / "status").read_text(
                    encoding="utf-8", errors="replace"
                )
                cgroup = (entry / "cgroup").read_text(
                    encoding="utf-8", errors="replace"
                ).strip()
                seen.add(int(entry.name))
                print(f"pid={entry.name}\ncommand={command}\n{status}\ncgroup={cgroup}", flush=True)
            except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
                continue
        time.sleep(max(0.01, args.interval))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

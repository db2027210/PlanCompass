#!/usr/bin/env python3
"""Wait for one independent C scene and repair only its proven terminal status."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import repair_mysql_algorithm_c_scoped_terminal_status_v1 as repair


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--scene", choices=sorted(repair.SCENES), required=True)
    parser.add_argument("--poll-seconds", type=float, default=15.0)
    parser.add_argument("--maximum-wait-seconds", type=float, default=259200.0)
    args = parser.parse_args()
    if args.poll_seconds <= 0 or args.maximum_wait_seconds <= 0:
        parser.error("positive wait bounds required")

    deadline = time.monotonic() + args.maximum_wait_seconds
    while time.monotonic() < deadline:
        repaired = repair.repair_if_scoped_complete(args.state, args.scene)
        state = repair.load(args.state.expanduser().resolve())
        status = state.get("status")
        if status == "COMPLETE":
            print(json.dumps({
                "status": "REPAIRED" if repaired else "ALREADY_COMPLETE",
                "scene": args.scene,
                "state": str(args.state.expanduser().resolve()),
                "accepted": int(
                    (state.get("accepted_rounds") or {}).get(args.scene) or 0
                ),
            }, sort_keys=True))
            return 0
        if status != "RUNNING":
            raise RuntimeError(
                f"independent C scene terminated without convergence: {status}"
            )
        time.sleep(args.poll_seconds)
    return 4


if __name__ == "__main__":
    raise SystemExit(main())

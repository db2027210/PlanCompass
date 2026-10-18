#!/usr/bin/env python3
"""Launch B after Outer 18 using the three-confirmation C checkpoint."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import wait_launch_mysql_algorithm_b_after_direct_c_v1 as predecessor


def run(command: list[str], timeout: int = 300):
    command = list(command)
    if "scripts.build_mysql_algorithm_c_outer_checkpoint_v4" in command:
        command[command.index(
            "scripts.build_mysql_algorithm_c_outer_checkpoint_v4"
        )] = "scripts.build_mysql_algorithm_c_outer_checkpoint_v9"
    return predecessor.subprocess.run(
        command, cwd=predecessor.ROOT, text=True,
        stdout=predecessor.subprocess.PIPE, stderr=predecessor.subprocess.PIPE,
        timeout=timeout, check=True,
    )


def main() -> int:
    predecessor.run = run
    predecessor.PRIOR_QUARANTINE = predecessor.ROOT / (
        "history/mysql_four_scene_20260809/"
        "algorithm_c_v79_replay_quarantine_v15_outer17_20260817T1427p0800/"
        "replay_quarantine.json"
    )
    return predecessor.main()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        raise

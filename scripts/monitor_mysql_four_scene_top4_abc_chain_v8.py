#!/usr/bin/env python3
"""Retry only failed internal handoffs in the independent C-to-B-to-A chain."""

from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import monitor_mysql_four_scene_top4_abc_chain_v7 as predecessor


base = predecessor.base
base.B_LAUNCHER = ROOT / "scripts/launch_mysql_four_scene_top4_algorithm_b_two_worker_scene_v8.py"
_original_start_handoff = base.start_handoff


def start_handoff(unit: str, description: str, command: list[str], stdout: Path, stderr: Path) -> None:
    shown = base.unit_state(unit)
    if shown.get("ActiveState") == "failed":
        completed = subprocess.run(
            ["systemctl", "--user", "reset-failed", unit],
            cwd=ROOT, text=True, capture_output=True, check=False, timeout=15,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"failed internal handoff unit reset failed: {unit}")
    _original_start_handoff(unit, description, command, stdout, stderr)


base.start_handoff = start_handoff


if __name__ == "__main__":
    raise SystemExit(base.main())

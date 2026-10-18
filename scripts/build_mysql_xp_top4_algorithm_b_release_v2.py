#!/usr/bin/env python3
"""Freeze the current-authority, adapter-compatible Top4 B v11 release."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_xp_top4_algorithm_b_release_v1 as predecessor


predecessor.OUTPUT = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_b_20260826_v11"
predecessor.CONTROLLER = ROOT / "scripts/run_mysql_algorithm_b_xp_top4_async_v2.py"
predecessor.COORDINATION = predecessor.OUTPUT / "coordination_contract.json"
predecessor.CONTRACT = predecessor.OUTPUT / "algorithm_b_top4_contract.json"
predecessor.BOOTSTRAP_VERSION = "v11"
predecessor.SOURCES.update({
    "controller": predecessor.CONTROLLER,
    "controller_predecessor": ROOT / "scripts/run_mysql_algorithm_b_xp_top4_async_v1.py",
    "workload_adapter": ROOT / "mysql_workload_adapter.py",
    "current_authority_refresh": ROOT / "scripts/refresh_mysql_xp4_live_authority_from_reset_v1.py",
    "scene_launcher": ROOT / "scripts/launch_mysql_xp_top4_algorithm_b_scene_v2.py",
})


def main() -> int:
    return predecessor.main()


if __name__ == "__main__":
    raise SystemExit(main())

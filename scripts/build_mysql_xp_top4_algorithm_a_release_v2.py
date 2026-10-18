#!/usr/bin/env python3
"""Freeze Top4 Algorithm A v2 for the current B successor paths."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_xp_top4_algorithm_a_release_v1 as predecessor


predecessor.OUTPUT = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_a_20260826_v2"
predecessor.CONTRACT = predecessor.OUTPUT / "algorithm_a_top4_contract.json"
predecessor.COORDINATION = predecessor.OUTPUT / "coordination_contract.json"
predecessor.SOURCES.update({
    "controller": ROOT / "scripts/run_mysql_algorithm_a_xp_top4_async_v2.py",
    "controller_predecessor": ROOT / "scripts/run_mysql_algorithm_a_xp_top4_async_v1.py",
    "scene_launcher": ROOT / "scripts/launch_mysql_algorithm_a_xp_top4_scene_v2.py",
})


def main() -> int:
    return predecessor.main()


if __name__ == "__main__":
    raise SystemExit(main())

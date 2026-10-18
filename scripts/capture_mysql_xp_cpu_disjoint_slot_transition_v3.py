#!/usr/bin/env python3
"""Capture the current-gap CPU-worker slot transition."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import capture_mysql_xp_cpu_disjoint_slot_transition_v1 as predecessor


predecessor.FREEZE = (
    ROOT / "config/formal/frozen_mysql_xp_cpu_disjoint_slots_20260826_v3"
    / "resource_successor.json"
)
predecessor.SOURCE = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v206_to_xp_cpu_compact_disjoint_transition_20260826/reset_binding.json"
)
predecessor.OUTPUT = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v210_to_xp_cpu_current_gap_transition_20260826/reset_binding.json"
)


if __name__ == "__main__":
    raise SystemExit(predecessor.main())


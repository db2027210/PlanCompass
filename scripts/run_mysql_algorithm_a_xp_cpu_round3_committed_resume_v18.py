#!/usr/bin/env python3
"""Resume CPU-A Round4 with the resilient XP8 reset successor."""

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_a_xp_cpu_round3_committed_resume_v17 as predecessor
from scripts import reset_mysql_xp_data_planes_mixed_eight_worker_scene_v5 as reset8_v5


CONTRACT_ROOT = (
    ROOT
    / "config/formal/frozen_mysql_xp_top4_algorithm_a_mixed_eight_worker_20260827_v11"
)


def _install_successor(value: Any, seen: set[int] | None = None) -> None:
    seen = seen or set()
    if id(value) in seen:
        return
    seen.add(id(value))
    if not isinstance(value, dict):
        return
    if "reset8" in value:
        value["reset8"] = reset8_v5
    if "CONTRACT_ROOT" in value:
        value["CONTRACT_ROOT"] = CONTRACT_ROOT
    if "CONTRACT" in value:
        value["CONTRACT"] = CONTRACT_ROOT / "algorithm_a_top4_contract.json"
    if "COORDINATION" in value:
        value["COORDINATION"] = CONTRACT_ROOT / "coordination_contract.json"
    for child in value.values():
        if isinstance(child, dict):
            _install_successor(child, seen)


_install_successor(predecessor.implementation)
_install_successor(predecessor.implementation["runtime"].implementation)


if __name__ == "__main__":
    raise SystemExit(predecessor.implementation["main"]())

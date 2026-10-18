#!/usr/bin/env python3
"""Resume the two failed XP-B scenes at their last committed boundaries."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v25 as gate_v25
from scripts import run_mysql_algorithm_b_four_scene_client_primary_v5 as runtime
from scripts import run_mysql_algorithm_b_top4_boundary_resume_v2 as base


HISTORY = ROOT / "history/mysql_four_scene_20260809"
CONTRACT_ROOT = ROOT / (
    "config/formal/frozen_mysql_four_scene_algorithm_b_client_primary_20260903_v71"
)
CONFIG = {
    "hybench_xp_cpu": {
        "source": HISTORY / "v1184_to_b_client_primary_xp_cleanup_recovery_20260903_xp_cpu",
        "accepted": 4, "rejected": 8, "committed": 12,
        "partial_outer": 13, "partial_ranks": (),
    },
    "hybench_xp_io": {
        "source": HISTORY / "v1184_to_b_client_primary_xp_cleanup_recovery_20260903_xp_io",
        "accepted": 3, "rejected": 4, "committed": 7,
        "partial_outer": 8, "partial_ranks": (1, 2, 3, 4),
    },
}


def argument(name: str) -> str:
    try:
        return sys.argv[sys.argv.index(name) + 1]
    except (ValueError, IndexError) as error:
        raise RuntimeError(f"required argument absent: {name}") from error


def configure(scene: str) -> None:
    row = CONFIG[scene]
    base.SOURCE = row["source"]
    base.SCENE = scene
    base.EXPECTED_ERROR = "RuntimeError: dynamic non-interference gate failed closed"
    base.EXPECTED_ACCEPTED = row["accepted"]
    base.EXPECTED_REJECTED = row["rejected"]
    base.CALIBRATION_COMPLETED = 15
    base.COMMITTED_OUTER = row["committed"]
    base.PARTIAL_OUTER = row["partial_outer"]
    base.PARTIAL_RANKS = row["partial_ranks"]
    base.COMMITTED_DECISION_ROOTS = {}
    base.VERIFIED_BASELINE_ROOT = None
    base.EXPECTED_BASELINE_WORKERS = 2
    base.RUNTIME_PREDECESSOR = runtime
    base.DYNAMIC_GATE = gate_v25
    base.CONTRACT_ROOT = CONTRACT_ROOT
    base.CONTRACT = CONTRACT_ROOT / "algorithm_b_top4_contract.json"
    base.COORDINATION = CONTRACT_ROOT / "coordination_contract.json"


def main() -> int:
    scene = argument("--scene")
    if scene not in CONFIG:
        raise RuntimeError(f"unsupported recovery scene: {scene}")
    configure(scene)
    return int(base.main())


if __name__ == "__main__":
    raise SystemExit(main())

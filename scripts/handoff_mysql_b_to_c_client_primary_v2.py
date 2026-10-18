#!/usr/bin/env python3
"""Handoff successor using the clean TPC-H client-QPS B continuation."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import handoff_mysql_b_to_c_client_primary_v1 as predecessor


HISTORY = ROOT / "history/mysql_four_scene_20260809"
predecessor.B_STATES = {
    "job_memory": HISTORY / (
        "v1178_to_b_client_primary_four_scene_20260903_job/run_state.json"
    ),
    "tpch_sf4_memory": HISTORY / (
        "v1191_to_b_tpch_client_primary_predecision_continue_20260903_tpch/"
        "run_state.json"
    ),
    "hybench_xp_cpu": HISTORY / (
        "v1184_to_b_client_primary_xp_cleanup_recovery_20260903_xp_cpu/"
        "run_state.json"
    ),
    "hybench_xp_io": HISTORY / (
        "v1184_to_b_client_primary_xp_cleanup_recovery_20260903_xp_io/"
        "run_state.json"
    ),
}
predecessor.AUTHORITY_RUN_ID = "v1192_to_c_client_primary_live_authority_20260903"
predecessor.FRESH_R0_RUN_ID = "v1193_to_c_client_primary_fresh_r0_20260903"
predecessor.C_CAMPAIGN_ID = "v1194_to_c_client_primary_four_scene_20260903"
predecessor.SUPERVISOR_ROOT = HISTORY / (
    "v1192_to_c_client_primary_handoff_20260903"
)
predecessor.MANIFEST = predecessor.SUPERVISOR_ROOT / "handoff_manifest.json"


def main() -> int:
    return int(predecessor.main())


if __name__ == "__main__":
    raise SystemExit(main())

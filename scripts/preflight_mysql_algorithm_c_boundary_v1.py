#!/usr/bin/env python3
"""Run the v15 live gate on the completed Outer-3 boundary data plane."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import preflight_mysql_four_scene_live_v8 as implementation


implementation.FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260811_v15"
    / "four_scene_effective_resource_successor.json"
)
implementation.V10_FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260811_v14"
    / "four_scene_effective_resource_successor.json"
)
implementation.V10_PREFLIGHT = (
    ROOT / "preflight/mysql_four_scene_20260809/runtime_preflight_v32.json"
)
implementation.NON_XP_PREPARED = (
    ROOT / "history/mysql_four_scene_20260809"
    / "four_scene_live_rebind_v30_20260812T010000p0800/prepared_binding.json"
)
implementation.XP_PREPARED = (
    ROOT / "history/mysql_four_scene_20260809"
    / "algorithm_c_boundary_outer5_xp_binding_v1_20260812_retry16/prepared_binding.json"
)
implementation.FREEZE_SCHEMA = (
    "mysql_disk2_four_scene_two_worker_effective_resource_successor_v15"
)
implementation.PREDECESSOR_FREEZE_SCHEMA = (
    "mysql_disk2_four_scene_two_worker_effective_resource_successor_v14"
)
implementation.OUTPUT_SCHEMA = "mysql_algorithm_c_boundary_runtime_preflight_v1"
implementation.AUTHORITY_VERSION = "v15_algorithm_c_outer3_boundary"


def _v15_change(change: dict) -> bool:
    write = change.get("xp_io_write_bps") or {}
    return (
        change.get("cpu_memory_swap_process_cpu_numa_and_read_io_fields_unchanged")
        is True
        and write.get("from") == 900000
        and write.get("to") == 524288
        and write.get("applies_to_workers") == ["xp_io_w1", "xp_io_w2"]
        and change.get("v14_safe_innodb_io_profile_unchanged") is True
        and change.get("workload_unchanged") is True
    )


implementation.successor_change_matches = _v15_change


if __name__ == "__main__":
    raise SystemExit(implementation.main())

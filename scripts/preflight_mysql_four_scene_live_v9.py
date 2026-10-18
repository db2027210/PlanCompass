#!/usr/bin/env python3
"""Run the v13 60-second live non-interference and resource gate."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import preflight_mysql_four_scene_live_v8 as implementation


implementation.FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260811_v13"
    / "four_scene_effective_resource_successor.json"
)
implementation.V10_FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260810_v12"
    / "four_scene_effective_resource_successor.json"
)
implementation.V10_PREFLIGHT = (
    ROOT / "preflight/mysql_four_scene_20260809/runtime_preflight_v23.json"
)
implementation.XP_PREPARED = (
    ROOT / "history/mysql_four_scene_20260809"
    / "xp_restore_after_failed_formal_v24_retry6_fresh_20260811T113000p0800"
    / "prepared_binding.json"
)
implementation.FREEZE_SCHEMA = (
    "mysql_disk2_four_scene_two_worker_effective_resource_successor_v13"
)
implementation.PREDECESSOR_FREEZE_SCHEMA = (
    "mysql_disk2_four_scene_two_worker_effective_resource_successor_v12"
)
implementation.OUTPUT_SCHEMA = "mysql_four_scene_live_runtime_preflight_v13"
implementation.AUTHORITY_VERSION = "v13"


def _v13_change(change: dict) -> bool:
    return (
        change.get("cpu_memory_swap_io_process_and_isolation_fields_unchanged") is True
        and change.get("xp_innodb_use_native_aio") == {"from": True, "to": False}
        and change.get("xp_innodb_flush_method") == {"from": "O_DIRECT", "to": "fsync"}
        and change.get("xp_container_init") == {"from": False, "to": True}
        and change.get("workload_unchanged") is True
    )


implementation.successor_change_matches = _v13_change


if __name__ == "__main__":
    raise SystemExit(implementation.main())

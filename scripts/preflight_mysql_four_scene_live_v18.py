#!/usr/bin/env python3
"""Run the v18 live non-interference and resource gate."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import preflight_mysql_four_scene_live_v8 as implementation


implementation.FREEZE = ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260813_v18/four_scene_effective_resource_successor.json"
implementation.V10_FREEZE = ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260813_v17/four_scene_effective_resource_successor.json"
# This is the last successful >=60 s machine-level predecessor gate.  The v17
# attempt is intentionally BLOCKED_FAIL_CLOSED and cannot serve as PASS
# authority, although it remains linked by the zero-credit pilot artifacts.
implementation.V10_PREFLIGHT = ROOT / "preflight/mysql_four_scene_20260809/runtime_preflight_auto_v14_attempt15_20260813T082901p0800.json"
implementation.XP_PREPARED = ROOT / "history/mysql_four_scene_20260809/xp_reset_v18_750k_stability_candidate_20260813T1520p0800/reset_binding.json"
implementation.FREEZE_SCHEMA = "mysql_disk2_four_scene_two_worker_effective_resource_successor_v18"
implementation.PREDECESSOR_FREEZE_SCHEMA = "mysql_disk2_four_scene_two_worker_effective_resource_successor_v17"
implementation.OUTPUT_SCHEMA = "mysql_four_scene_live_runtime_preflight_v18"
implementation.AUTHORITY_VERSION = "v18"
implementation.XP_ALLOWED_STATUSES = {"PASS"}


def _v18_change(change: dict) -> bool:
    write = change.get("xp_io_write_bps") or {}
    return (
        change.get("cpu_memory_swap_process_cpu_numa_and_read_io_fields_unchanged") is True
        and write.get("from") == 800000
        and write.get("to") == 750000
        and write.get("applies_to_workers") == ["xp_io_w1", "xp_io_w2"]
        and change.get("native_xp_workload_unchanged") is True
        and change.get("job_tpch_and_xp_cpu_envelopes_unchanged") is True
    )


implementation.successor_change_matches = _v18_change


if __name__ == "__main__":
    raise SystemExit(implementation.main())

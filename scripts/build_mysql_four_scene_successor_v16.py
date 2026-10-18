#!/usr/bin/env python3
"""Publish v16 after v15's 512 KiB/s XP/I/O cap hung InnoDB."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PREDECESSOR = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260811_v15"
    / "four_scene_effective_resource_successor.json"
)
FAILED_BATCH = (
    ROOT / "history/mysql_four_scene_20260809"
    / "four_scene_algorithm_c_outer5_pilot_nonformal_pg_ignored_serial_v13_20260813T1209p0800"
    / "outer_005/top5_batch_2/batch_result.json"
)
RECOVERY = (
    ROOT / "history/mysql_four_scene_20260809"
    / "xp_discard_reset_after_outer5_v13_batch2_timeout_v4_20260813T1241p0800"
    / "pilot_result.json"
)
OUTPUT = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260813_v16"
    / "four_scene_effective_resource_successor.json"
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"JSON object required: {path}")
    return value


def main() -> int:
    if OUTPUT.exists():
        raise SystemExit(f"refusing to replace frozen successor: {OUTPUT}")
    predecessor = load(PREDECESSOR)
    failed = load(FAILED_BATCH)
    recovery = load(RECOVERY)
    failures = failed.get("failures") or []
    timed_out = {row.get("task") for row in failures if "timed out after 900" in str(row.get("error"))}
    if (
        predecessor.get("schema_version")
        != "mysql_disk2_four_scene_two_worker_effective_resource_successor_v15"
        or failed.get("status") != "FAILED_CLOSED"
        or float(failed.get("elapsed_seconds") or 0) <= 900
        or not {"xp_cpu_w1", "xp_io_w1", "pair_1"}.issubset(timed_out)
        or recovery.get("status") != "PASS_RECOVERY_ONLY"
        or recovery.get("workload_started") is not False
        or recovery.get("quarantine_retention_policy")
        != "zero_retention_after_successful_reset_binding"
        or int(recovery.get("quarantine_deleted_bytes") or 0) <= 0
    ):
        raise SystemExit("v16 predecessor failure/recovery authority differs")

    successor = dict(predecessor)
    workers = []
    for original in predecessor["workers"]:
        worker = dict(original)
        if worker["scene"] == "hybench_xp_io":
            if int(worker["io_write_bps"]) != 524288:
                raise SystemExit("v15 XP/I/O write cap differs")
            worker["v15_io_write_bps"] = int(worker["io_write_bps"])
            worker["io_write_bps"] = 1048576
        workers.append(worker)

    successor.update({
        "schema_version": "mysql_disk2_four_scene_two_worker_effective_resource_successor_v16",
        "status": "FROZEN_RUNTIME_READBACK_PENDING",
        "predecessor": {
            "path": str(PREDECESSOR.relative_to(ROOT)),
            "sha256": sha(PREDECESSOR),
        },
        "successor_reason": (
            "v15_xp_io_512kib_write_cap_caused_repeated_innodb_600s_"
            "semaphore_crash_and_900s_full_workload_timeout"
        ),
        "successor_resource_envelope_change": {
            "cpu_memory_swap_process_cpu_numa_and_read_io_fields_unchanged": True,
            "xp_io_write_bps": {
                "from": 524288,
                "to": 1048576,
                "applies_to_workers": ["xp_io_w1", "xp_io_w2"],
            },
            "native_xp_workload_unchanged": True,
            "job_tpch_and_xp_cpu_envelopes_unchanged": True,
        },
        "predecessor_data_plane_failure": {
            "path": str(FAILED_BATCH.relative_to(ROOT)),
            "sha256": sha(FAILED_BATCH),
            "elapsed_seconds": failed["elapsed_seconds"],
            "timed_out_tasks": sorted(timed_out),
            "mysql_failure": (
                "xp_cpu_w1_and_xp_io_w1_innodb_semaphore_wait_exceeded_600s_"
                "and_mysqld_aborted"
            ),
            "accepted_state_credit": 0,
        },
        "predecessor_failure_recovery": {
            "path": str(RECOVERY.relative_to(ROOT)),
            "sha256": sha(RECOVERY),
            "status": recovery["status"],
            "quarantine_deleted_bytes": recovery["quarantine_deleted_bytes"],
            "old_datadirs_never_reused": True,
        },
        "version_transition_credit_policy": {
            "job_memory_v15_rounds_may_be_migrated_with_version_provenance": True,
            "tpch_sf4_memory_v15_rounds_may_be_migrated_with_version_provenance": True,
            "hybench_xp_cpu_v15_rounds_may_be_migrated_with_version_provenance": True,
            "hybench_xp_io_v15_rounds_may_be_migrated": False,
            "hybench_xp_io_v16_r0_required": True,
            "pre_and_post_change_xp_io_results_must_never_be_combined": True,
        },
        "workers": workers,
        "formal_launch_authorized": False,
        "launch_performed": False,
        "accepted_rounds": {
            "job_memory": 0,
            "tpch_sf4_memory": 0,
            "hybench_xp_cpu": 0,
            "hybench_xp_io": 0,
        },
    })
    OUTPUT.parent.mkdir(parents=True, exist_ok=False)
    OUTPUT.write_text(
        json.dumps(successor, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii"
    )
    print(OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

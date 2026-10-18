#!/usr/bin/env python3
"""Resume only the JOB and XP-I/O C scenes that failed in v1245.

TPC-H and XP-CPU remain independently active in v1245.  This recovery starts
the two failed scenes concurrently and records that it is a subset recovery,
not a new four-scene campaign.
"""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import launch_mysql_c_scene_resume_v109_v1245 as base


base.CAMPAIGN = base.HISTORY / "v1246_mysql_c_failed_scene_resume_v109_20260905"
base.MANIFEST = base.CAMPAIGN / "launch_manifest.json"
base.BARRIER = Path(
    "/tmp/mysql_async_outer_start/v1246_mysql_c_failed_scene_resume_v109"
)
base.SPECS = {
    scene: dict(spec)
    for scene, spec in base.SPECS.items()
    if scene in {"job_memory", "hybench_xp_io"}
}
base.SPECS["job_memory"]["run_id"] = (
    "v1246_to_c_kernel_gate_lifecycle_fix_v109_20260905_job"
)
base.SPECS["hybench_xp_io"]["run_id"] = (
    "v1246_to_c_kernel_gate_lifecycle_fix_v109_20260905_xp_io"
)

_atomic = base.atomic


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    document = dict(value)
    if path == base.MANIFEST:
        document["all_four_scenes_started_concurrently"] = False
        document["subset_failure_recovery"] = True
        document["recovery_scenes_started_concurrently"] = [
            "job_memory", "hybench_xp_io"
        ]
        document["retained_independently_running_scenes"] = [
            "tpch_sf4_memory", "hybench_xp_cpu"
        ]
        document["completed_outer_history_preserved"] = True
        document["failed_v1245_outer_credit"] = 0
    _atomic(path, document)


base.atomic = atomic


if __name__ == "__main__":
    raise SystemExit(base.main())

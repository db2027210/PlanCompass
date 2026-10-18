#!/usr/bin/env python3
"""Resume all four C scenes after the all-worker blkio repair."""

from copy import deepcopy
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import launch_mysql_c_scene_resume_v111_v1251 as prior


launch = prior.launch
launch.CAMPAIGN = (
    launch.HISTORY / "v1255_mysql_c_four_scene_resume_v111_20260906"
)
launch.MANIFEST = launch.CAMPAIGN / "launch_manifest.json"
launch.BARRIER = launch.Path(
    "/tmp/mysql_async_outer_start/v1255_mysql_c_four_scene_resume_v111"
)
launch.SPECS = deepcopy(launch.SPECS)
for scene, spec in launch.SPECS.items():
    spec["run_id"] = (
        "v1255_to_c_full_docker_path_resilience_v111_20260906_"
        + str(spec["suffix"])
    )
launch.SPECS["job_memory"].update({
    "checkpoint": "v1250_c_job_resume_checkpoint_v111_20260906",
    "quarantine": "v1250_c_job_resume_quarantine_v111_20260906",
    "resume_after_outer": 13,
})

REPAIR = launch.HISTORY / (
    "v1254_c_all_scene_blkio_repair_v111_20260906/readback.json"
)


def main() -> int:
    repair = launch.load(REPAIR)
    worker_ids = {str(row.get("worker_id")) for row in repair.get("workers") or []}
    expected = {
        "job_w1", "job_w2", "tpch_w1", "tpch_w2",
        "xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2",
    }
    if (
        repair.get("status") != "PASS"
        or repair.get("formal_scope") != "mysql_only"
        or repair.get("cross_engine_comparison_prohibited") is not True
        or worker_ids != expected
    ):
        raise RuntimeError("all-scene blkio repair evidence differs")

    result = launch.main()
    manifest = launch.load(launch.MANIFEST)
    manifest.update({
        "prelaunch_blkio_repair_path": str(REPAIR),
        "prelaunch_blkio_repair_sha256": launch.sha(REPAIR),
        "prelaunch_blkio_repair_status": "PASS",
        "prelaunch_blkio_repair_worker_count": 8,
    })
    launch.atomic(launch.MANIFEST, manifest)
    return result


if __name__ == "__main__":
    raise SystemExit(main())

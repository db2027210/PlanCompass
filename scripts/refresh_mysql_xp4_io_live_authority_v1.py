#!/usr/bin/env python3
"""Refresh IO XP4 live authority after a successful formal data-plane reset."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import execute_mysql_algorithm_c_xp4_scene_handoff_v1 as handoff
from scripts import prepare_mysql_xp4_live_authority_v1 as base
from scripts import reset_mysql_xp_data_planes_four_worker_v1 as reset4


SCENE = "hybench_xp_io"
OUTPUT = (
    base.HISTORY / "v177_to_xp4_io_live_authority_refresh_20260826" / SCENE
)
RESET_BINDING = (
    base.HISTORY / "v172_to_c_top4_io_xp4_20260826_attempt006_xp_io"
    / "xp_resets/v18_pruning_v2_formal_incumbent_baseline/reset_binding.json"
)


def main() -> int:
    result_path = OUTPUT / "live_authority.json"
    if result_path.is_file():
        result = base.load(result_path)
        if result.get("status") == "PASS":
            print(json.dumps(result, sort_keys=True))
            return 0
    if OUTPUT.exists():
        raise RuntimeError(f"fresh authority refresh root required: {OUTPUT}")
    freeze = base.load(base.FREEZE)
    reset = base.load(RESET_BINDING)
    expected = {"xp_io_w1", "xp_io_w2", "xp_io_w3", "xp_io_w4"}
    rows = {
        str(row.get("worker_id")): copy.deepcopy(dict(row))
        for row in reset.get("workers") or []
        if row.get("worker_id") in expected
    }
    if (
        reset.get("status") != "PASS"
        or reset.get("freeze_sha256") != base.sha(base.FREEZE)
        or set(rows) != expected
    ):
        raise RuntimeError("completed IO reset binding differs")
    for worker_id, row in rows.items():
        inspected = reset4._docker_json("inspect", str(row["container"]))
        defaults = Path(str(row["credential_defaults"]))
        benchmark = Path(str(row["benchmark_defaults"]))
        if (
            inspected.get("Id") != row.get("container_id")
            or (inspected.get("State") or {}).get("Running") is not True
            or not defaults.is_file() or defaults.is_symlink()
            or not benchmark.is_file() or benchmark.is_symlink()
        ):
            raise RuntimeError(f"{worker_id} refreshed live identity differs")
        sessions = reset4._host_mysql(
            row, defaults,
            "SELECT COUNT(*) FROM information_schema.processlist "
            "WHERE ID<>CONNECTION_ID() AND COMMAND<>'Daemon' "
            "AND USER<>'event_scheduler'",
            timeout=30,
        ).stdout.strip()
        identity = reset4._host_mysql(
            row, benchmark, "SELECT CURRENT_USER(),DATABASE();", timeout=30,
        ).stdout.strip().split("\t")
        if sessions != "0" or not identity or not identity[0].startswith("ra_benchmark@"):
            raise RuntimeError(f"{worker_id} session or benchmark identity differs")
        row.update({
            "ready": True,
            "container_pid": int((inspected.get("State") or {}).get("Pid") or 0),
            "benchmark_user": "ra_benchmark",
            "external_current_user": identity[0],
            "external_database": "hybench_sf10x",
            "database": "hybench_sf10x",
            "kernel_cgroup_resources": reset4.prep._kernel_resource_readback(
                inspected, row
            ),
            "blkio_authority": reset4.prep._enforce_blkio(inspected, freeze, row),
            "passwords_emitted": False,
        })
    OUTPUT.mkdir(parents=True, mode=0o700)
    artifacts = handoff.authority_artifacts(
        scene=SCENE, freeze_path=base.FREEZE, freeze=freeze,
        live_rows=rows, output=OUTPUT / "authority",
    )
    result = {
        "schema_version": "mysql_xp_four_worker_live_authority_refresh_v1",
        "status": "PASS", "created_at": base.now(), "scene": SCENE,
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "resource_freeze_path": str(base.FREEZE),
        "resource_freeze_sha256": base.sha(base.FREEZE),
        "source_reset_binding_path": str(RESET_BINDING),
        "source_reset_binding_sha256": base.sha(RESET_BINDING),
        "worker_ids": sorted(rows), "physical_replay_parallelism": 4,
        "candidate_batches": [[1, 2, 3, 4], [5]],
        "native_xp_workload_unchanged": True,
        "recalibration_performed": False,
        "accepted_state_credit_increment": 0,
        "completed_candidate_replay_increment": 0,
        "artifacts": {name: str(path) for name, path in artifacts.items()},
    }
    base.atomic(result_path, result)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Create current XP4 live authority from a completed formal reset binding."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import execute_mysql_algorithm_c_xp4_scene_handoff_v1 as handoff
from scripts import prepare_mysql_xp4_live_authority_v1 as base
from scripts import reset_mysql_xp_data_planes_four_worker_v1 as reset4


SCENE_PREFIX = {"hybench_xp_cpu": "xp_cpu", "hybench_xp_io": "xp_io"}


def safe_path(raw: str, *, file: bool) -> Path:
    path = Path(raw).expanduser().resolve()
    history = base.HISTORY.resolve()
    if path == history or history not in path.parents or path.is_symlink():
        raise argparse.ArgumentTypeError("path must be a non-symlink history child")
    if file and not path.is_file():
        raise argparse.ArgumentTypeError("reset binding must be a regular file")
    return path


def prepare(scene: str, reset_binding: Path, output: Path) -> dict[str, object]:
    result_path = output / "live_authority.json"
    if result_path.is_file():
        result = base.load(result_path)
        if result.get("status") == "PASS":
            return result
    if output.exists():
        raise RuntimeError(f"fresh authority refresh root required: {output}")
    freeze = base.load(base.FREEZE)
    reset = base.load(reset_binding)
    prefix = SCENE_PREFIX[scene]
    expected = {f"{prefix}_w{index}" for index in range(1, 5)}
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
        raise RuntimeError("completed XP4 reset binding differs")
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
            "external_database": "hybench_sf10x", "database": "hybench_sf10x",
            "kernel_cgroup_resources": reset4.prep._kernel_resource_readback(
                inspected, row
            ),
            "blkio_authority": reset4.prep._enforce_blkio(inspected, freeze, row),
            "passwords_emitted": False,
        })
    output.mkdir(parents=True, mode=0o700)
    artifacts = handoff.authority_artifacts(
        scene=scene, freeze_path=base.FREEZE, freeze=freeze,
        live_rows=rows, output=output / "authority",
    )
    result = {
        "schema_version": "mysql_xp_four_worker_live_authority_refresh_v2",
        "status": "PASS", "created_at": base.now(), "scene": scene,
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "resource_freeze_path": str(base.FREEZE),
        "resource_freeze_sha256": base.sha(base.FREEZE),
        "source_reset_binding_path": str(reset_binding),
        "source_reset_binding_sha256": base.sha(reset_binding),
        "worker_ids": sorted(rows), "physical_replay_parallelism": 4,
        "candidate_batches": [[1, 2, 3, 4], [5]],
        "native_xp_workload_unchanged": True, "recalibration_performed": False,
        "accepted_state_credit_increment": 0,
        "completed_candidate_replay_increment": 0,
        "artifacts": {name: str(path) for name, path in artifacts.items()},
    }
    base.atomic(result_path, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", choices=sorted(SCENE_PREFIX), required=True)
    parser.add_argument("--reset-binding", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.:/-]+", args.reset_binding) is None:
        parser.error("unsafe reset binding")
    reset_binding = safe_path(args.reset_binding, file=True)
    output = safe_path(args.output, file=False)
    result = prepare(args.scene, reset_binding, output)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

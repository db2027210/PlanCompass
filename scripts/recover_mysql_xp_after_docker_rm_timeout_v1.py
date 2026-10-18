#!/usr/bin/env python3
"""Recover the exact outer-001 batch-3 Docker-rm timeout, fail closed."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import provision_mysql_four_scene_benchmark_users_v5 as users
from scripts import reset_mysql_xp_data_planes_v5 as reset


FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260811_v15"
    / "four_scene_effective_resource_successor.json"
)
FAILED_ROOT = (
    ROOT / "history/mysql_four_scene_20260809"
    / "four_scene_algorithm_c_v13_v15_20260811T152400p0800"
)
FAILED_STATE = FAILED_ROOT / "run_state.json"
PREDECESSOR_XP = FAILED_ROOT / "xp_resets/outer_001_batch_2/reset_binding.json"
PARTIAL_STAGE = FAILED_ROOT / "xp_resets/outer_001_batch_3"
BATCH_1 = FAILED_ROOT / "outer_001/top5_batch_1/batch_result.json"
BATCH_2 = FAILED_ROOT / "outer_001/top5_batch_2/batch_result.json"
XP_IDS = ("xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2")
FAILED_WORKER = "xp_io_w1"
EXPECTED_ERROR = (
    "TimeoutExpired: Command '['docker', 'rm', "
    "'ra_mysql_disk2_algc_xp_io_w1_20260809']' timed out after 30 seconds"
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"regular non-symlink JSON required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def atomic_json(path: Path, document: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,128}", args.run_id) is None:
        parser.error("unsafe run-id")

    freeze = load(FREEZE)
    failed = load(FAILED_STATE)
    predecessor = load(PREDECESSOR_XP)
    batch_1 = load(BATCH_1)
    batch_2 = load(BATCH_2)
    frozen = {row["worker_id"]: row for row in freeze["workers"]}
    prior = {row["worker_id"]: row for row in predecessor["workers"]}
    if (
        freeze.get("schema_version")
        != "mysql_disk2_four_scene_two_worker_effective_resource_successor_v15"
        or failed.get("status") != "FAILED_CLOSED"
        or failed.get("stage") != "FAILED"
        or failed.get("error") != EXPECTED_ERROR
        or failed.get("freeze_sha256") != sha(FREEZE)
        or failed.get("accepted_rounds")
        != {
            "hybench_xp_cpu": 0,
            "hybench_xp_io": 0,
            "job_memory": 0,
            "tpch_sf4_memory": 0,
        }
        or predecessor.get("status") != "PASS"
        or predecessor.get("freeze_sha256") != sha(FREEZE)
        or set(prior) != set(XP_IDS)
        or batch_1.get("status") != "PASS"
        or batch_2.get("status") != "PASS"
        or batch_1.get("failures")
        or batch_2.get("failures")
    ):
        raise RuntimeError("Docker-rm timeout recovery authority differs")
    controller_pid = int(failed.get("pid") or 0)
    if controller_pid > 0 and Path(f"/proc/{controller_pid}").exists():
        raise RuntimeError("failed controller is still alive")

    output = ROOT / "history/mysql_four_scene_20260809" / args.run_id
    if output.exists():
        raise RuntimeError(f"fresh output exists: {output}")
    output.mkdir(parents=True, mode=0o700)
    quarantine = (
        Path(str(freeze["storage"]["runtime_root"]))
        / "quarantine" / FAILED_ROOT.name / "outer_001_batch_3"
    )
    runtime_root = (
        Path(str(freeze["storage"]["runtime_root"])) / FAILED_WORKER
    )
    failed_quarantine = quarantine / FAILED_WORKER
    expected_successes = set(XP_IDS) - {FAILED_WORKER}
    reset_rows: dict[str, dict] = {}
    for worker_id in sorted(expected_successes):
        row = load(PARTIAL_STAGE / worker_id / "reset_worker.json")
        inspect = reset.prep._docker_json("inspect", frozen[worker_id]["container"])
        if (
            row.get("worker_id") != worker_id
            or row.get("container_id") != inspect.get("Id")
            or (inspect.get("State") or {}).get("Running") is not True
            or not (quarantine / worker_id).is_dir()
        ):
            raise RuntimeError(f"{worker_id} partial reset identity differs")
        reset_rows[worker_id] = row

    if reset._container_id(str(frozen[FAILED_WORKER]["container"])):
        raise RuntimeError(f"{FAILED_WORKER} timed-out container still exists")
    if failed_quarantine.exists() or not runtime_root.is_dir():
        raise RuntimeError(f"{FAILED_WORKER} pre-quarantine filesystem differs")
    os.rename(runtime_root, failed_quarantine)
    runtime_root.mkdir(mode=0o700)
    (runtime_root / "datadir").mkdir(mode=0o700)
    (runtime_root / "credentials").mkdir(mode=0o700)
    reset_rows[FAILED_WORKER] = reset._prepare_fresh_xp(
        freeze,
        frozen[FAILED_WORKER],
        prior[FAILED_WORKER],
        quarantine,
        output,
        True,
        "formal_point",
    )

    provisioned: list[dict] = []
    failures: list[dict] = []
    account_root = output / "accounts"
    account_root.mkdir()
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="xp-rm-timeout-users") as pool:
        futures = {
            pool.submit(
                users._run_worker, reset_rows[worker_id], account_root, False, True,
            ): worker_id
            for worker_id in XP_IDS
        }
        for future in as_completed(futures):
            worker_id = futures[future]
            try:
                provisioned.append({**reset_rows[worker_id], **future.result()})
                print(json.dumps({
                    "worker_id": worker_id,
                    "account_ready": True,
                }), flush=True)
            except BaseException as error:
                failures.append({
                    "worker_id": worker_id,
                    "stage": "account",
                    "error": f"{type(error).__name__}: {error}",
                })
                print(json.dumps(failures[-1]), flush=True)

    provisioned.sort(key=lambda row: row["worker_id"])
    passed = len(provisioned) == len(XP_IDS) and not failures
    document = {
        "schema_version": "mysql_four_scene_xp_recovery_after_docker_rm_timeout_v1",
        "status": "PREPARED" if passed else "FAILED_CLOSED",
        "created_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "freeze_path": str(FREEZE),
        "freeze_sha256": sha(FREEZE),
        "failed_formal_path": str(FAILED_STATE),
        "failed_formal_sha256": sha(FAILED_STATE),
        "predecessor_xp_binding_path": str(PREDECESSOR_XP),
        "predecessor_xp_binding_sha256": sha(PREDECESSOR_XP),
        "valid_batch_result_paths": [str(BATCH_1), str(BATCH_2)],
        "valid_batch_result_sha256": [sha(BATCH_1), sha(BATCH_2)],
        "recovery_source_path": str(Path(__file__).resolve()),
        "recovery_source_sha256": sha(Path(__file__).resolve()),
        "reset_source_path": str(Path(reset.__file__).resolve()),
        "reset_source_sha256": sha(Path(reset.__file__).resolve()),
        "quarantine_root": str(quarantine),
        "workers": provisioned,
        "failures": failures,
        "workload_started": False,
        "fresh_r0_complete": False,
        "formal_outer_started": False,
        "accepted_rounds": {scene: 0 for scene in freeze["active_scenes"]},
    }
    binding = output / "prepared_binding.json"
    atomic_json(binding, document)
    print(json.dumps({
        "status": document["status"],
        "binding": str(binding),
        "ready": len(provisioned),
        "failed": len(failures),
    }), flush=True)
    return 0 if passed else 4


if __name__ == "__main__":
    raise SystemExit(main())

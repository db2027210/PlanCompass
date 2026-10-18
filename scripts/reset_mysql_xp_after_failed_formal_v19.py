#!/usr/bin/env python3
"""Recover zombie XP mysqld processes after formal v10 failed closed."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import provision_mysql_four_scene_benchmark_users_v5 as users
from scripts import reset_mysql_xp_after_failed_formal_v13 as common
from scripts import reset_mysql_xp_data_planes_v5 as reset


FREEZE = common.FREEZE
FAILED_STATE = (
    ROOT / "history/mysql_four_scene_20260809"
    / "four_scene_algorithm_c_contextual_formal_v10_20260811T004608p0800/run_state.json"
)
CURRENT_XP = FAILED_STATE.parent / "xp_resets/outer_001_batch_1/reset_binding.json"
XP_IDS = common.XP_IDS
sha, load, now = common.sha, common.load, common.now


def restore_verified_zombie(
    freeze: dict, worker: dict, prior: dict, quarantine: Path, output: Path,
) -> dict:
    container = str(worker["container"])
    inspect = reset.prep._docker_json("inspect", container)
    state = inspect.get("State") or {}
    labels = (inspect.get("Config") or {}).get("Labels") or {}
    if (
        inspect.get("Id") != prior.get("container_id")
        or state.get("Running") is not True
        or labels.get("ra.owner") != "mysql_four_scene_v5"
        or labels.get("ra.worker_id") != worker["worker_id"]
    ):
        raise RuntimeError(f"{worker['worker_id']} zombie container identity differs")
    top = reset.prep._run(
        ["docker", "top", container, "-eo", "stat,comm,args"], timeout=30,
    ).stdout.splitlines()[1:]
    rows = [line.strip() for line in top if line.strip()]
    if (
        len(rows) != 1
        or not rows[0].split(None, 1)[0].startswith("Z")
        or "[mysqld]" not in rows[0]
    ):
        raise RuntimeError(f"{worker['worker_id']} is not a verified zombie mysqld")
    return reset._prepare_fresh_xp(
        freeze, worker, prior, quarantine, output, False, "formal_point",
        allow_unresponsive_old_container=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,128}", args.run_id) is None:
        parser.error("unsafe run-id")

    freeze, failed, current = load(FREEZE), load(FAILED_STATE), load(CURRENT_XP)
    if (
        freeze.get("schema_version")
        != "mysql_disk2_four_scene_two_worker_effective_resource_successor_v12"
        or failed.get("status") != "FAILED_CLOSED"
        or failed.get("stage") != "FAILED"
        or not str(failed.get("error", "")).startswith(
            "TimeoutExpired: Command '['/usr/bin/hwloc-bind'"
        )
        or failed.get("freeze_sha256") != sha(FREEZE)
        or failed.get("outer_attempt") is not None
        or failed.get("accepted_rounds")
        != {
            "hybench_xp_cpu": 0,
            "hybench_xp_io": 0,
            "job_memory": 0,
            "tpch_sf4_memory": 0,
        }
        or current.get("status") != "PASS"
        or current.get("freeze_sha256") != sha(FREEZE)
        or {row.get("worker_id") for row in current.get("workers") or []}
        != set(XP_IDS)
    ):
        raise RuntimeError("v19 failed-formal XP reset authority differs")
    interrupted_pid = int(failed.get("pid", 0))
    if interrupted_pid > 0 and Path(f"/proc/{interrupted_pid}").exists():
        raise RuntimeError("v10 controller is still alive; refusing XP reset")

    output = ROOT / "history/mysql_four_scene_20260809" / args.run_id
    if output.exists():
        raise RuntimeError(f"fresh output exists: {output}")
    output.mkdir(parents=True, mode=0o700)
    quarantine = Path(str(freeze["storage"]["runtime_root"])) / "quarantine" / args.run_id
    frozen = {row["worker_id"]: row for row in freeze["workers"]}
    previous = {row["worker_id"]: row for row in current["workers"]}

    restored: list[dict] = []
    failures: list[dict] = []
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="xp-v19-restore") as pool:
        futures = {
            pool.submit(
                restore_verified_zombie, freeze, frozen[worker_id],
                previous[worker_id], quarantine, output,
            ): worker_id
            for worker_id in XP_IDS
        }
        for future in as_completed(futures):
            worker_id = futures[future]
            try:
                restored.append(future.result())
                print(json.dumps({"worker_id": worker_id, "restored": True}), flush=True)
            except BaseException as error:
                failures.append({
                    "worker_id": worker_id,
                    "stage": "reset",
                    "error": f"{type(error).__name__}: {error}",
                })
                print(json.dumps(failures[-1]), flush=True)

    provisioned: list[dict] = []
    if not failures:
        account_root = output / "accounts"
        account_root.mkdir()
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="xp-v16-users") as pool:
            futures = {
                pool.submit(users._run_worker, row, account_root, False, True): row
                for row in restored
            }
            for future in as_completed(futures):
                row = futures[future]
                try:
                    provisioned.append({**row, **future.result()})
                    print(json.dumps({
                        "worker_id": row["worker_id"], "account_ready": True,
                    }), flush=True)
                except BaseException as error:
                    failures.append({
                        "worker_id": row["worker_id"],
                        "stage": "account",
                        "error": f"{type(error).__name__}: {error}",
                    })
                    print(json.dumps(failures[-1]), flush=True)

    provisioned.sort(key=lambda row: row["worker_id"])
    passed = len(provisioned) == len(XP_IDS) and not failures
    document = {
        "schema_version": "mysql_four_scene_xp_restore_after_failed_formal_v19",
        "status": "PREPARED" if passed else "FAILED_CLOSED",
        "created_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "freeze_path": str(FREEZE),
        "freeze_sha256": sha(FREEZE),
        "failed_formal_path": str(FAILED_STATE),
        "failed_formal_sha256": sha(FAILED_STATE),
        "predecessor_xp_binding_path": str(CURRENT_XP),
        "predecessor_xp_binding_sha256": sha(CURRENT_XP),
        "reset_source_path": str(Path(__file__).resolve()),
        "reset_source_sha256": sha(Path(__file__).resolve()),
        "quarantine_root": str(quarantine),
        "workers": provisioned,
        "failures": failures,
        "workload_started": False,
        "fresh_r0_complete": False,
        "formal_outer_started": False,
        "accepted_rounds": {scene: 0 for scene in freeze["active_scenes"]},
    }
    binding = output / "prepared_binding.json"
    binding.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    binding.with_name(binding.name + ".sha256").write_text(
        f"{sha(binding)}  {binding.name}\n", encoding="ascii"
    )
    print(json.dumps({
        "status": document["status"], "binding": str(binding),
        "ready": len(provisioned), "failed": len(failures),
    }), flush=True)
    return 0 if passed else 4


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Resume v23 staged XP copies with bounded bootstrap retry and v13 I/O."""

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


FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260811_v13"
    / "four_scene_effective_resource_successor.json"
)
PREDECESSOR_FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260810_v12"
    / "four_scene_effective_resource_successor.json"
)
FAILED_STATE = (
    ROOT / "history/mysql_four_scene_20260809"
    / "four_scene_algorithm_c_contextual_formal_v11_20260811T085327p0800/run_state.json"
)
CURRENT_XP = FAILED_STATE.parent / "xp_resets/outer_001_batch_1/reset_binding.json"
FAILED_V23 = (
    ROOT / "history/mysql_four_scene_20260809"
    / "xp_restore_after_failed_formal_v23_20260811T100500p0800/prepared_binding.json"
)
XP_IDS = common.XP_IDS
sha, load, now = common.sha, common.load, common.now


def resume_verified_staging(
    freeze: dict, worker: dict, prior: dict, quarantine: Path, output: Path,
    force_fresh_copy: bool,
) -> dict:
    worker_id = str(worker["worker_id"])
    runtime_root = Path(str(freeze["storage"]["runtime_root"])) / worker_id
    if not (quarantine / worker_id).is_dir():
        raise RuntimeError(f"{worker_id} v20 quarantine source is absent")
    if not (runtime_root / "datadir").is_dir():
        raise RuntimeError(f"{worker_id} v20 staged datadir is absent")
    existing = reset.prep._run(
        ["docker", "ps", "-aq", "--filter", f"name=^{worker['container']}$"],
        timeout=30,
    ).stdout.strip()
    if existing:
        raise RuntimeError(f"{worker_id} unexpected final container already exists")
    return reset._prepare_fresh_xp(
        freeze, worker, prior, quarantine, output, True, "formal_point",
        allow_bootstrap_touched_staging=not force_fresh_copy,
        force_fresh_copy_on_resume=force_fresh_copy,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--force-fresh-copy", action="store_true",
        help="quarantine bootstrap-touched staging and recopy the sealed template",
    )
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,128}", args.run_id) is None:
        parser.error("unsafe run-id")

    freeze, failed, current, failed_v23 = (
        load(FREEZE), load(FAILED_STATE), load(CURRENT_XP), load(FAILED_V23)
    )
    if (
        freeze.get("schema_version")
        != "mysql_disk2_four_scene_two_worker_effective_resource_successor_v13"
        or (freeze.get("predecessor") or {}).get("sha256")
        != sha(PREDECESSOR_FREEZE)
        or failed.get("status") != "FAILED_CLOSED"
        or failed.get("stage") != "FAILED"
        or failed.get("error")
        != "RuntimeError: xp_cpu_w2 still has active non-system sessions"
        or failed.get("freeze_sha256") != sha(PREDECESSOR_FREEZE)
        or failed.get("outer_attempt") is not None
        or failed.get("accepted_rounds")
        != {
            "hybench_xp_cpu": 0,
            "hybench_xp_io": 0,
            "job_memory": 0,
            "tpch_sf4_memory": 0,
        }
        or current.get("status") != "PASS"
        or current.get("freeze_sha256") != sha(PREDECESSOR_FREEZE)
        or {row.get("worker_id") for row in current.get("workers") or []}
        != set(XP_IDS)
        or failed_v23.get("status") != "FAILED_CLOSED"
        or failed_v23.get("freeze_sha256") != sha(FREEZE)
        or failed_v23.get("failed_formal_sha256") != sha(FAILED_STATE)
        or {row.get("worker_id") for row in failed_v23.get("failures") or []}
        != set(XP_IDS)
        or any(
            "reset bootstrap readiness timed out" not in str(row.get("error", ""))
            for row in failed_v23.get("failures") or []
        )
    ):
        raise RuntimeError("v24 resumed XP reset authority differs")
    interrupted_pid = int(failed.get("pid", 0))
    if interrupted_pid > 0 and Path(f"/proc/{interrupted_pid}").exists():
        raise RuntimeError("v10 controller is still alive; refusing XP reset")

    output = ROOT / "history/mysql_four_scene_20260809" / args.run_id
    if output.exists():
        raise RuntimeError(f"fresh output exists: {output}")
    output.mkdir(parents=True, mode=0o700)
    quarantine = Path(str(failed_v23["quarantine_root"]))
    frozen = {row["worker_id"]: row for row in freeze["workers"]}
    previous = {row["worker_id"]: row for row in current["workers"]}

    restored: list[dict] = []
    failures: list[dict] = []
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="xp-v24-resume") as pool:
        futures = {
            pool.submit(
                resume_verified_staging, freeze, frozen[worker_id],
                previous[worker_id], quarantine, output, args.force_fresh_copy,
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
        "schema_version": "mysql_four_scene_xp_restore_after_failed_formal_v24",
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
        "failed_v23_path": str(FAILED_V23),
        "failed_v23_sha256": sha(FAILED_V23),
        "predecessor_freeze_path": str(PREDECESSOR_FREEZE),
        "predecessor_freeze_sha256": sha(PREDECESSOR_FREEZE),
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

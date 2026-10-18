#!/usr/bin/env python3
"""Rebuild the XP/IO eight-lane live authority after partial Outer12 reset."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Importing v8 installs the successor reset module throughout the dynamic
# Algorithm-B runtime dictionaries before ORIGINAL_RESET_XP is invoked.
from scripts import run_mysql_algorithm_b_xp_top4_mixed_eight_worker_v8 as runtime_v8
from scripts import reset_mysql_xp_data_planes_mixed_eight_worker_v3 as reset_v3
from scripts import refresh_mysql_xp8_live_authority_from_reset_v1 as refresh


FREEZE = ROOT / "config/formal/frozen_mysql_xp_eight_worker_20260827_v1/xp_effective_resource_successor.json"
PREPARED = ROOT / "history/mysql_four_scene_20260809/v587_to_b_io_mixed_eight_worker_v7_live_authority_20260827/prepared_binding.json"
EXPECTED = {f"xp_io_w{i}" for i in range(1, 9)}


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset-output", required=True)
    parser.add_argument("--authority-output", required=True)
    args = parser.parse_args()
    reset_output = Path(args.reset_output).resolve()
    authority_output = Path(args.authority_output).resolve()
    if reset_output.exists() or reset_output.is_symlink():
        raise RuntimeError(f"fresh reset output required: {reset_output}")
    if authority_output.exists() or authority_output.is_symlink():
        raise RuntimeError(f"fresh authority output required: {authority_output}")
    freeze = load(FREEZE)
    prepared = load(PREPARED)
    all_frozen = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in freeze.get("workers") or []
    }
    workers = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in prepared.get("workers") or []
        if row.get("worker_id") in EXPECTED
    }
    if (
        set(all_frozen) != {f"xp_{kind}_w{i}" for kind in ("cpu", "io") for i in range(1, 9)}
        or set(workers) != EXPECTED
        or prepared.get("status") != "PREPARED"
    ):
        raise RuntimeError("XP/IO unlocked recovery authority differs")
    reset_v3.install_for_workers(all_frozen)
    reset_v3.configure(freeze=FREEZE, prepared=PREPARED)
    if (
        reset_v3.predecessor.base._impl["_docker_run"]
        is not reset_v3.predecessor.base._base_docker_run
        or int(reset_v3.predecessor.base._impl["DOCKER_CONTROL_CONCURRENCY"]) != 8
    ):
        raise RuntimeError("unlocked eight-way Docker control is not active")
    # A failed parallel reset may have replaced a container after the launch
    # authority was sealed.  Refresh only the exact Docker identity used to
    # prove running/exited liveness; the subsequent full reset still rebuilds
    # and reads back every worker before producing authority.
    for worker_id in sorted(EXPECTED):
        frozen = all_frozen[worker_id]
        expected_datadir = str(workers[worker_id]["datadir"])
        inspect = reset_v3._docker_json("inspect", str(frozen["container"]))
        labels = (inspect.get("Config") or {}).get("Labels") or {}
        mounts = {
            str(row.get("Destination") or ""): str(row.get("Source") or "")
            for row in inspect.get("Mounts") or []
        }
        state = inspect.get("State") or {}
        if (
            labels.get("ra.worker_id") != worker_id
            or labels.get("ra.owner") != "mysql_four_scene_v5"
            or mounts.get("/var/lib/mysql") != expected_datadir
        ):
            raise RuntimeError(f"{worker_id} current Docker seed differs")
        workers[worker_id].update({
            "container_id": str(inspect["Id"]),
            "container_pid": int(state.get("Pid") or 0),
            "datadir": expected_datadir,
        })
    alg = runtime_v8.settle_v32.runtime.alg
    alg.FREEZE = FREEZE
    alg.reset = reset_v3
    alg.WORKER_IDS = tuple(sorted(all_frozen))
    alg.XP_IDS = tuple(sorted(all_frozen))
    alg.XP_RESET_MAX_WORKERS = 8
    original_classify = alg.classify_xp_mysql_liveness

    def classify_recovery_seed(worker: Mapping[str, Any]) -> dict[str, Any]:
        container = str(worker["container"])
        expected_id = str(worker["container_id"])
        inspect = reset_v3._docker_json("inspect", container)
        state = inspect.get("State") or {}
        labels = (inspect.get("Config") or {}).get("Labels") or {}
        cgroup = Path("/sys/fs/cgroup/blkio/docker-limited") / expected_id
        if (
            inspect.get("Id") == expected_id
            and state.get("Running") is False
            and int(state.get("Pid") or 0) == 0
            and state.get("Restarting") is False
            and state.get("Dead") is False
            and labels.get("ra.owner") == "mysql_four_scene_v5"
            and labels.get("ra.worker_id") == worker["worker_id"]
            and not cgroup.exists()
        ):
            return {
                "worker_id": worker["worker_id"],
                "expected_container": container,
                "observed_container": container,
                "expected_container_id": expected_id,
                "first_container_id": expected_id,
                "docker_running": False,
                "container_identity_stable": True,
                "mysqld_rows": [], "live_mysqld_rows": [],
                "status": "PROVEN_NO_LIVE_MYSQLD",
                "safe_to_discard_unresponsive_predecessor": True,
                "session_count_semantics": "zero_by_exact_stopped_reset_identity_and_absent_cgroup",
                "technical_authority_recovery_only": True,
            }
        return original_classify(worker)

    alg.classify_xp_mysql_liveness = classify_recovery_seed
    reset_output.mkdir(parents=True, mode=0o700)
    runtime_v8.scene_scoped_reset(
        freeze, workers, reset_output, "algorithm_b_outer_012_rank_1",
        worker_ids=set(EXPECTED),
    )
    binding = reset_output / "xp_resets/algorithm_b_outer_012_rank_1/reset_binding.json"
    result = refresh.prepare("hybench_xp_io", binding, authority_output)
    print(json.dumps({
        "status": "PASS",
        "reset_binding": str(binding),
        "authority": result["authority"],
        "worker_count": len(result["workers"]),
        "candidate_measurements_reused": 0,
        "accepted_or_rejected_credit": 0,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

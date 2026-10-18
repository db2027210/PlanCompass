#!/usr/bin/env python3
"""Freeze disk2 w1/w2 plus adopted disk1 w3/w4 for every formal scene."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE_FREEZE = (
    ROOT / "config/formal/frozen_mysql_four_scene_two_worker_20260826_v19"
    / "four_scene_effective_resource_successor.json"
)
DISK1_BINDING = Path(
    "/home/anonymous/program/resource_adaptive/config/formal/"
    "mysql_external_baseline_independent_workers_disk1_20260826_v4.json"
)
DISK1_STATE = Path(
    "/disk1/resource_adaptive/mysql_external_baselines_20260826/"
    "20260826_gptuner_aqetuner_four_disk1_r5_affinity_replacement_clean/"
    "gptuner_disk1_r5/state.json"
)
OUTPUT_ROOT = (
    ROOT / "config/formal/"
    "frozen_mysql_four_scene_mixed_disk_four_worker_20260827_v1"
)
OUTPUT = OUTPUT_ROOT / "four_scene_effective_resource_successor.json"
EXPECTED_DISK1_BINDING_SHA256 = (
    "789912c58244d65df442d91328976c2fad109284e3069dafb35661096b156552"
)
MANAGEMENT_CPUSET = "240-243"


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"regular JSON required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def cpus(value: str) -> set[int]:
    result: set[int] = set()
    for field in value.split(","):
        start, separator, end = field.strip().partition("-")
        result.update(range(int(start), int(end) + 1) if separator else [int(start)])
    return result


def main() -> int:
    source = load(SOURCE_FREEZE)
    disk1 = load(DISK1_BINDING)
    disk1_state = load(DISK1_STATE)
    if (
        source.get("schema_version")
        != "mysql_four_scene_two_worker_qps_successor_v19"
        or source.get("formal_scope") != "mysql_only"
        or source.get("cross_engine_comparison_prohibited") is not True
        or sha(DISK1_BINDING) != EXPECTED_DISK1_BINDING_SHA256
        or disk1.get("status") != "PASS"
        or disk1.get("formal_scope") != "mysql_only"
        or disk1.get("cross_engine_comparison_prohibited") is not True
        or str(disk1_state.get("status") or "").upper() != "COMPLETE"
    ):
        raise RuntimeError("mixed-disk resource predecessors differ")

    source_workers = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in source.get("workers") or []
    }
    adopted_workers = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in disk1.get("workers") or []
    }
    credentials = dict(disk1_state.get("credential_preflight") or {})
    if len(source_workers) != 8 or set(adopted_workers) != set(source_workers):
        raise RuntimeError("two-worker predecessor cardinality differs")

    workers: list[dict[str, Any]] = []
    storage_by_worker: dict[str, dict[str, Any]] = {}
    logical_sets: dict[str, set[int]] = {}
    for source_id in sorted(source_workers):
        current = source_workers[source_id]
        workers.append(current)
        storage_by_worker[source_id] = copy.deepcopy(dict(source["storage"]))
        logical_sets[f"{source_id}:database"] = cpus(str(current["database_cpuset"]))
        logical_sets[f"{source_id}:client"] = cpus(str(current["client_cpuset"]))

        adopted = adopted_workers[source_id]
        suffix = source_id.rsplit("_w", 1)[0]
        target_id = f"{suffix}_w{int(source_id.rsplit('_w', 1)[1]) + 2}"
        credential = dict(credentials.get(source_id) or {})
        benchmark_defaults = str(
            credential.get("path") or adopted.get("credential_benchmark") or ""
        )
        if not benchmark_defaults:
            raise RuntimeError(f"{source_id} disk1 benchmark credential is absent")
        row = copy.deepcopy(current)
        row.update({
            "worker_id": target_id,
            "container": str(adopted["container"]),
            "host_port": int(adopted["host_port"]),
            "database_cpuset": str(adopted["database_cpuset"]),
            "database_cpuset_mems": str(adopted["database_cpuset_mems"]),
            "client_cpuset": str(adopted["client_cpuset"]),
            "client_cpuset_mems": str(adopted["client_cpuset_mems"]),
            "datadir": str(adopted["datadir"]),
            "runtime_root": str(Path(str(adopted["datadir"])).parent),
            "credential_defaults": str(adopted["credential_defaults"]),
            "benchmark_defaults": benchmark_defaults,
            "adopted_physical_worker_id": source_id,
            "adopted_container_id_at_freeze": str(adopted["container_id"]),
            "adopted_from_completed_external_baseline": True,
            "external_method_result_credit_reused": False,
            "prior_optimizer_cost_state_credit_reused": False,
            "resource_envelope_equal_to_same_scene_disk2_workers": True,
        })
        # The completed disk1 external run used the older 512 KiB/s XP-I/O
        # write cap.  The formal successor adopts only its data plane and
        # raises the live cap to the already-frozen 750000 B/s scene envelope
        # before any candidate measurement.
        row["io_read_bps"] = int(current["io_read_bps"])
        row["io_write_bps"] = int(current["io_write_bps"])
        row["cpu_quota_cores"] = float(current["cpu_quota_cores"])
        row["memory_bytes"] = int(current["memory_bytes"])
        row["swap_bytes"] = int(current["swap_bytes"])
        row["pids_max"] = int(current["pids_max"])
        workers.append(row)
        storage_by_worker[target_id] = {
            "copy_mode": "adopted_live_datadir_then_full_physical_template_reset",
            "device": "/dev/nvme1n1",
            "device_major_minor": "259:5",
            "filesystem": "ext4",
            "mount": "/disk1",
            "runtime_root": str(Path(str(adopted["datadir"])).parents[1]),
        }
        logical_sets[f"{target_id}:database"] = cpus(str(row["database_cpuset"]))
        logical_sets[f"{target_id}:client"] = cpus(str(row["client_cpuset"]))

    logical_sets["management"] = cpus(MANAGEMENT_CPUSET)
    overlaps: list[dict[str, Any]] = []
    names = sorted(logical_sets)
    for index, left in enumerate(names):
        for right in names[index + 1:]:
            shared = sorted(logical_sets[left] & logical_sets[right])
            if shared:
                overlaps.append({"left": left, "right": right, "cpus": shared})
    if overlaps:
        raise RuntimeError(f"mixed-disk logical CPU sets overlap: {overlaps}")

    result = copy.deepcopy(source)
    result.update({
        "schema_version": "mysql_four_scene_mixed_disk_four_worker_successor_v1",
        "status": "FROZEN_RUNTIME_READBACK_PENDING",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "workers": sorted(workers, key=lambda row: str(row["worker_id"])),
        "storage_by_worker": storage_by_worker,
        "management_cpuset": MANAGEMENT_CPUSET,
        "mixed_disk_successor": {
            "disk2_workers_per_scene": 2,
            "disk1_workers_per_scene": 2,
            "physical_workers_per_scene": 4,
            "maximum_concurrent_mysql_workers": 16,
            "candidate_sampling": False,
            "completed_candidate_replay_increment": 0,
            "transition_only_at_completed_scene_boundary": True,
            "scene_transitions_independent": True,
            "four_scene_barrier": False,
            "sibling_scene_wait": False,
            "shared_nvme_lease": False,
            "numa_exclusive_scheduler_lock": False,
            "external_process_control_or_stop_performed": False,
            "cross_engine_comparison_prohibited": True,
        },
        "source_two_worker_freeze": {
            "path": str(SOURCE_FREEZE.relative_to(ROOT)),
            "sha256": sha(SOURCE_FREEZE),
        },
        "adopted_disk1_binding": {
            "path": str(DISK1_BINDING),
            "sha256": sha(DISK1_BINDING),
            "completed_external_results_reused": False,
            "live_data_plane_only": True,
        },
        "cpu_isolation_readback": {
            "all_database_client_and_management_sets_pairwise_disjoint": True,
            "management_cpuset": MANAGEMENT_CPUSET,
            "overlaps": [],
        },
        "runtime_transition": {
            "launch_performed": False,
            "readback_required_before_launch": True,
            "rolling_dynamic_noninterference_gate_required": True,
            "io_disk1_write_bps_before_adoption": 524288,
            "io_disk1_write_bps_required_at_adoption": 750000,
        },
    })
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=False, mode=0o700)
    OUTPUT.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    digest = sha(OUTPUT)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{digest}  {OUTPUT.name}\n", encoding="ascii"
    )
    print(json.dumps({"path": str(OUTPUT), "sha256": digest}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

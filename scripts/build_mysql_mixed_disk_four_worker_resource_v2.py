#!/usr/bin/env python3
"""Freeze the internally consistent four-worker mixed-disk successor.

The v1 artifact deliberately stopped at runtime-readback pending, but it also
retained two obsolete two-worker fields from its predecessor.  This successor
changes no worker envelope and only removes that ambiguity before any w3/w4
formal measurement is launched.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = (
    ROOT / "config/formal/"
    "frozen_mysql_four_scene_mixed_disk_four_worker_20260827_v1"
)
SOURCE = SOURCE_ROOT / "four_scene_effective_resource_successor.json"
OUTPUT_ROOT = (
    ROOT / "config/formal/"
    "frozen_mysql_four_scene_mixed_disk_four_worker_20260827_v2"
)
OUTPUT = OUTPUT_ROOT / "four_scene_effective_resource_successor.json"
EXPECTED_SOURCE_SHA256 = (
    "7af0a73653ea3d93c04784462371d19bc7efcad8bc61e979c7643fbed0f55675"
)
MANAGEMENT_CPUSET = "488-489,498-499"
FOUR_WORKER_BATCHES = [[1, 2, 3, 4], [5]]


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
    source = load(SOURCE)
    workers = [copy.deepcopy(dict(row)) for row in source.get("workers") or []]
    worker_ids = {str(row.get("worker_id") or "") for row in workers}
    expected = {
        f"{prefix}_w{rank}"
        for prefix in ("job", "tpch", "xp_cpu", "xp_io")
        for rank in range(1, 5)
    }
    if (
        sha(SOURCE) != EXPECTED_SOURCE_SHA256
        or source.get("schema_version")
        != "mysql_four_scene_mixed_disk_four_worker_successor_v1"
        or source.get("status") != "FROZEN_RUNTIME_READBACK_PENDING"
        or source.get("formal_scope") != "mysql_only"
        or source.get("cross_engine_comparison_prohibited") is not True
        or worker_ids != expected
        or len(workers) != 16
    ):
        raise RuntimeError("mixed-disk v1 predecessor differs")

    logical_sets: dict[str, set[int]] = {"management": cpus(MANAGEMENT_CPUSET)}
    for worker in workers:
        worker_id = str(worker["worker_id"])
        logical_sets[f"{worker_id}:database"] = cpus(str(worker["database_cpuset"]))
        logical_sets[f"{worker_id}:client"] = cpus(str(worker["client_cpuset"]))
    overlaps: list[dict[str, Any]] = []
    names = sorted(logical_sets)
    for index, left in enumerate(names):
        for right in names[index + 1:]:
            shared = sorted(logical_sets[left] & logical_sets[right])
            if shared:
                overlaps.append({"left": left, "right": right, "cpus": shared})
    if overlaps:
        raise RuntimeError(f"four-worker CPU placement overlaps: {overlaps}")

    result = copy.deepcopy(source)
    result.update({
        "schema_version": "mysql_four_scene_mixed_disk_four_worker_successor_v2",
        "status": "FROZEN_RUNTIME_READBACK_PENDING",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "management_cpuset": MANAGEMENT_CPUSET,
        "source_mixed_disk_v1": {
            "path": str(SOURCE.relative_to(ROOT)),
            "sha256": sha(SOURCE),
            "formal_measurements_reused": False,
        },
    })
    result["full_parallel_successor"] = {
        "candidate_sampling": False,
        "four_scene_barrier": False,
        "lease_mode": "none",
        "maximum_concurrent_mysql_workers": 16,
        "physical_workers_per_scene": 4,
        "sibling_scene_wait": False,
        "scene_rounds_advance_independently": True,
        "memory_scene_completion_required_for_xp_expansion": False,
        "transition_only_at_each_scene_completed_round_boundary": True,
    }
    result["replay_policy"] = {
        "accepted_round_target_per_scene": 10,
        "effective_replay_parallelism_per_scene": 4,
        "fixed_rank_batches": FOUR_WORKER_BATCHES,
        "independent_mysql_workers_per_scene": 4,
        "same_mysql_instance_parallel_candidate_replay_prohibited": True,
        "candidate_sampling": False,
        "completed_candidate_replay_increment_at_transition": 0,
    }
    mixed = copy.deepcopy(dict(result.get("mixed_disk_successor") or {}))
    mixed.update({
        "physical_workers_per_scene": 4,
        "maximum_concurrent_mysql_workers": 16,
        "candidate_sampling": False,
        "transition_only_at_completed_scene_boundary": True,
        "scene_transitions_independent": True,
        "four_scene_barrier": False,
        "sibling_scene_wait": False,
        "shared_nvme_lease": False,
        "numa_exclusive_scheduler_lock": False,
        "external_process_control_or_stop_performed": False,
        "cross_engine_comparison_prohibited": True,
        "formal_candidate_batches": FOUR_WORKER_BATCHES,
    })
    result["mixed_disk_successor"] = mixed
    result["cpu_isolation_readback"] = {
        "all_database_client_and_management_sets_pairwise_disjoint": True,
        "management_cpuset": MANAGEMENT_CPUSET,
        "overlaps": [],
        "runtime_process_affinity_readback_still_required": True,
    }
    transition = copy.deepcopy(dict(result.get("runtime_transition") or {}))
    transition.update({
        "launch_performed": False,
        "readback_required_before_launch": True,
        "rolling_dynamic_noninterference_gate_required": True,
        "recalibration_required_due_only_to_worker_expansion": False,
        "completed_round_history_preserved": True,
        "completed_candidate_measurements_replayed": False,
    })
    result["runtime_transition"] = transition

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

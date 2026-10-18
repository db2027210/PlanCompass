#!/usr/bin/env python3
"""Plan the final Algorithm A JOB-slot to XP four-worker transition."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Mapping, Sequence


XP_SCENES = ("hybench_xp_cpu", "hybench_xp_io")
TRANSITION_CONTRACT = "mysql_algorithm_a_job_four_slots_to_xp_2_to_4_v1"
DONOR_TO_RECIPIENT = {
    "job_w1": ("hybench_xp_cpu", "xp_cpu_w3", 65333),
    "job_w2": ("hybench_xp_cpu", "xp_cpu_w4", 65334),
    "job_w3": ("hybench_xp_io", "xp_io_w3", 65343),
    "job_w4": ("hybench_xp_io", "xp_io_w4", 65344),
}
RECIPIENT_PROTOTYPE = {
    "hybench_xp_cpu": "xp_cpu_w1",
    "hybench_xp_io": "xp_io_w1",
}
NEW_CONTAINER = {
    "xp_cpu_w3": "ra_mysql_disk2_alga_xp_cpu_w3_20260821",
    "xp_cpu_w4": "ra_mysql_disk2_alga_xp_cpu_w4_20260821",
    "xp_io_w3": "ra_mysql_disk2_alga_xp_io_w3_20260821",
    "xp_io_w4": "ra_mysql_disk2_alga_xp_io_w4_20260821",
}
POSITION_FIELDS = (
    "database_cpuset", "database_cpuset_mems",
    "client_cpuset", "client_cpuset_mems",
)
RESOURCE_FIELDS = (
    "cpu_quota_cores", "memory_bytes", "swap_bytes", "pids_max",
    "io_read_bps", "io_write_bps", "innodb_buffer_pool_bytes",
)


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def parse_cpuset(value: Any) -> set[int]:
    result: set[int] = set()
    for component in str(value or "").split(","):
        component = component.strip()
        if not component:
            continue
        if "-" in component:
            start, end = map(int, component.split("-", 1))
            values = range(start, end + 1)
        else:
            values = (int(component),)
        for cpu in values:
            if cpu < 0 or cpu in result:
                raise ValueError(f"invalid or duplicate CPU: {cpu}")
            result.add(cpu)
    if not result:
        raise ValueError("empty CPU set")
    return result


def search_state_payload(state: Mapping[str, Any]) -> dict[str, Any]:
    required = (
        "accepted", "ranges", "incumbent_params", "incumbent_validation",
        "accepted_history", "normalized_width", "canonical_baseline",
    )
    missing = [name for name in required if state.get(name) is None]
    if missing:
        raise ValueError(f"transition state fields absent: {missing}")
    return {name: copy.deepcopy(state[name]) for name in required}


def _workers(freeze: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    rows = freeze.get("workers") or []
    result = {
        str(row.get("worker_id") or ""): copy.deepcopy(dict(row))
        for row in rows
    }
    if "" in result or len(result) != len(rows):
        raise ValueError("source worker identities differ")
    return result


def build_xp_workers(freeze: Mapping[str, Any]) -> list[dict[str, Any]]:
    workers = _workers(freeze)
    required = set(DONOR_TO_RECIPIENT) | set(RECIPIENT_PROTOTYPE.values())
    if not required.issubset(workers):
        raise ValueError("source freeze lacks JOB donors or XP prototypes")
    additions: list[dict[str, Any]] = []
    for donor_id, (scene, worker_id, port) in DONOR_TO_RECIPIENT.items():
        donor = workers[donor_id]
        prototype = workers[RECIPIENT_PROTOTYPE[scene]]
        row = copy.deepcopy(prototype)
        row.update({
            "worker_id": worker_id,
            "scene": scene,
            "container": NEW_CONTAINER[worker_id],
            "host_port": port,
            "slot_origin": donor_id,
            "slot_reassignment_contract": TRANSITION_CONTRACT,
        })
        for field in POSITION_FIELDS:
            row[field] = copy.deepcopy(donor[field])
        additions.append(row)
    validate_xp_workers(freeze, additions)
    return additions


def validate_xp_workers(
    freeze: Mapping[str, Any], additions: Sequence[Mapping[str, Any]],
) -> None:
    workers = _workers(freeze)
    if {str(row.get("worker_id")) for row in additions} != set(NEW_CONTAINER):
        raise ValueError("new XP worker identities differ")
    original = [
        row for row in workers.values() if row.get("scene") in XP_SCENES
    ]
    if len(original) != 4:
        raise ValueError("source original XP worker cardinality differs")
    occupied_db: dict[int, str] = {}
    occupied_client: dict[int, str] = {}
    for row in [*original, *map(dict, additions)]:
        worker_id = str(row["worker_id"])
        for cpu in parse_cpuset(row["database_cpuset"]):
            if cpu in occupied_db:
                raise ValueError(f"XP database CPU overlap: {cpu}")
            occupied_db[cpu] = worker_id
        for cpu in parse_cpuset(row["client_cpuset"]):
            if cpu in occupied_client:
                raise ValueError(f"XP client CPU overlap: {cpu}")
            occupied_client[cpu] = worker_id
    for row in additions:
        donor = workers[str(row["slot_origin"])]
        prototype = workers[RECIPIENT_PROTOTYPE[str(row["scene"])]]
        if any(row.get(field) != donor.get(field) for field in POSITION_FIELDS):
            raise ValueError(f"JOB donor position differs: {row['worker_id']}")
        if any(row.get(field) != prototype.get(field) for field in RESOURCE_FIELDS):
            raise ValueError(f"XP envelope differs: {row['worker_id']}")


def build_successor_freeze(
    source: Mapping[str, Any], additions: Sequence[Mapping[str, Any]],
    *, created_at: str, job_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    validate_xp_workers(source, additions)
    original_xp = [
        copy.deepcopy(dict(row)) for row in source.get("workers") or []
        if row.get("scene") in XP_SCENES
    ]
    value = copy.deepcopy(dict(source))
    value.update({
        "schema_version": "mysql_algorithm_a_xp_four_worker_after_job_resource_successor_v1",
        "release_version": "job_to_xp_slots_v1",
        "created_at": created_at,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "formal_launch_authorized": True,
        "algorithm": "A",
        "active_worker_scenes_after_transition": list(XP_SCENES),
        "completed_job_evidence": copy.deepcopy(dict(job_evidence)),
        "worker_cohort_transition": {
            "contract": TRANSITION_CONTRACT,
            "xp_parallelism_before": 2,
            "xp_parallelism_after": 4,
            "per_scene_round_boundary_independent": True,
            "mixed_worker_cohort_round_prohibited": True,
            "native_xp_workload_unchanged": True,
            "resource_quota_source": "same_scene_xp_prototype",
            "cpu_numa_position_source": "completed_job_worker",
        },
        "workers": original_xp + [copy.deepcopy(dict(row)) for row in additions],
        "templates": {scene: source["templates"][scene] for scene in XP_SCENES},
    })
    return value

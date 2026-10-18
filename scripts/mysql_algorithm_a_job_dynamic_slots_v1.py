#!/usr/bin/env python3
"""Frozen mapping for expanding Algorithm A JOB from two to four workers."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


SCENE = "job_memory"
TRANSITION_CONTRACT = "mysql_algorithm_a_tpch_slots_to_job_2_to_4_v1"
DONOR_TO_RECIPIENT = {
    "tpch_w1": ("job_w3", 65313),
    "tpch_w2": ("job_w4", 65314),
}
NEW_CONTAINER = {
    "job_w3": "ra_mysql_disk2_alga_job_w3_20260821",
    "job_w4": "ra_mysql_disk2_alga_job_w4_20260821",
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
        values = range(*(
            (lambda bounds: (bounds[0], bounds[1] + 1))(
                tuple(map(int, component.split("-", 1)))
            )
        )) if "-" in component else (int(component),)
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
    result = {str(row.get("worker_id") or ""): copy.deepcopy(dict(row)) for row in rows}
    if "" in result or len(result) != len(rows):
        raise ValueError("frozen worker identities differ")
    return result


def build_job_workers(freeze: Mapping[str, Any]) -> list[dict[str, Any]]:
    workers = _workers(freeze)
    prototype = workers["job_w1"]
    effective_resources = {
        field: prototype[field] for field in RESOURCE_FIELDS
    }
    # v18 retains the historical field for provenance and explicitly seals
    # the effective v11 buffer-pool successor in this dedicated field.
    effective_resources["innodb_buffer_pool_bytes"] = prototype[
        "v11_innodb_buffer_pool_bytes"
    ]
    additions: list[dict[str, Any]] = []
    for donor_id, (worker_id, port) in DONOR_TO_RECIPIENT.items():
        donor = workers[donor_id]
        row = copy.deepcopy(prototype)
        row.update({
            "worker_id": worker_id,
            "scene": SCENE,
            "container": NEW_CONTAINER[worker_id],
            "host_port": port,
            "slot_origin": donor_id,
            "slot_reassignment_contract": TRANSITION_CONTRACT,
        })
        for field in POSITION_FIELDS:
            row[field] = copy.deepcopy(donor[field])
        row.update(copy.deepcopy(effective_resources))
        additions.append(row)
    validate_job_workers(freeze, additions)
    return additions


def validate_job_workers(
    freeze: Mapping[str, Any], additions: Sequence[Mapping[str, Any]],
) -> None:
    workers = _workers(freeze)
    if {str(row.get("worker_id")) for row in additions} != set(NEW_CONTAINER):
        raise ValueError("new JOB worker identities differ")
    active = [workers["job_w1"], workers["job_w2"], *map(dict, additions)]
    occupied: dict[int, str] = {}
    prototype = workers["job_w1"]
    expected_resources = {field: prototype[field] for field in RESOURCE_FIELDS}
    expected_resources["innodb_buffer_pool_bytes"] = prototype[
        "v11_innodb_buffer_pool_bytes"
    ]
    for row in active:
        worker_id = str(row["worker_id"])
        for cpu in parse_cpuset(row["database_cpuset"]):
            if cpu in occupied:
                raise ValueError(f"JOB database CPU overlap: {cpu}")
            occupied[cpu] = worker_id
    for row in additions:
        donor = workers[str(row["slot_origin"])]
        if any(row.get(field) != donor.get(field) for field in POSITION_FIELDS):
            raise ValueError(f"donor position differs: {row['worker_id']}")
        if any(row.get(field) != expected_resources[field] for field in RESOURCE_FIELDS):
            raise ValueError(f"JOB resource envelope differs: {row['worker_id']}")


def build_successor_freeze(
    source: Mapping[str, Any], additions: Sequence[Mapping[str, Any]],
    *, created_at: str, tpch_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    validate_job_workers(source, additions)
    retained = [
        copy.deepcopy(dict(row)) for row in source.get("workers") or []
        if row.get("scene") in (SCENE, "hybench_xp_cpu", "hybench_xp_io")
    ]
    for row in retained:
        if row.get("scene") == SCENE:
            row["innodb_buffer_pool_bytes"] = row[
                "v11_innodb_buffer_pool_bytes"
            ]
    value = copy.deepcopy(dict(source))
    value.update({
        "schema_version": "mysql_algorithm_a_job_four_worker_resource_successor_v1",
        "release_version": "job_dynamic_slots_v1",
        "created_at": created_at,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "formal_launch_authorized": True,
        "algorithm": "A",
        "active_worker_scenes_after_transition": [SCENE, "hybench_xp_cpu", "hybench_xp_io"],
        "completed_tpch_evidence": copy.deepcopy(dict(tpch_evidence)),
        "worker_cohort_transition": {
            "contract": TRANSITION_CONTRACT,
            "job_parallelism_before": 2,
            "job_parallelism_after": 4,
            "round_boundary_required": True,
            "mixed_worker_cohort_round_prohibited": True,
            "full_job_113_once_per_measurement": True,
            "candidate_count_per_round_unchanged": 16,
            "new_worker_resource_quota_source": "job_w1",
            "new_worker_cpu_numa_position_source": "completed_tpch_worker",
        },
        "workers": retained + [copy.deepcopy(dict(row)) for row in additions],
        "templates": {
            key: source["templates"][key]
            for key in (SCENE, "hybench_xp_cpu", "hybench_xp_io")
        },
    })
    return value

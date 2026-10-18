#!/usr/bin/env python3
"""Plan the audited 2-to-4 worker transition for MySQL Algorithm A XP lanes.

The live campaign starts every scene with two workers.  Only after both memory
scenes have completed all ten accepted shrinks are their four isolated
positions released.  A clean nonconvergence is not completion for JOB/TPC-H. This
module maps the two JOB positions to XP/CPU and the two TPC-H positions to
XP/I/O without changing either XP resource quota or native workload.

This module is deliberately side-effect free.  Provisioning and controller
handoff consume its fail-closed transition document at a complete Algorithm A
round boundary.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


MEMORY_SCENES = ("job_memory", "tpch_sf4_memory")
XP_SCENES = ("hybench_xp_cpu", "hybench_xp_io")
TRANSITION_CONTRACT = "mysql_algorithm_a_memory_to_xp_2_to_4_v4"
DONOR_TO_RECIPIENT = {
    "job_w1": ("hybench_xp_cpu", "xp_cpu_w3", 65333),
    "job_w2": ("hybench_xp_cpu", "xp_cpu_w4", 65334),
    "tpch_w1": ("hybench_xp_io", "xp_io_w3", 65343),
    "tpch_w2": ("hybench_xp_io", "xp_io_w4", 65344),
}
RECIPIENT_PROTOTYPE = {
    "hybench_xp_cpu": "xp_cpu_w1",
    "hybench_xp_io": "xp_io_w1",
}
NEW_CONTAINER = {
    "xp_cpu_w3": "ra_mysql_disk2_alga_xp_cpu_w3_20260822",
    "xp_cpu_w4": "ra_mysql_disk2_alga_xp_cpu_w4_20260822",
    "xp_io_w3": "ra_mysql_disk2_alga_xp_io_w3_20260822",
    "xp_io_w4": "ra_mysql_disk2_alga_xp_io_w4_20260822",
}
RESOURCE_FIELDS = (
    "cpu_quota_cores",
    "memory_bytes",
    "swap_bytes",
    "pids_max",
    "io_read_bps",
    "io_write_bps",
    "innodb_buffer_pool_bytes",
)
POSITION_FIELDS = (
    "database_cpuset",
    "database_cpuset_mems",
    "client_cpuset",
    "client_cpuset_mems",
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
            start_raw, end_raw = component.split("-", 1)
            start, end = int(start_raw), int(end_raw)
            if start > end:
                raise ValueError(f"invalid CPU interval: {component}")
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


def clean_nonconvergence_terminal(
    state: Mapping[str, Any], scene: str,
) -> bool:
    """Recognize the PG-aligned, non-technical terminal rejection outcome."""
    return bool(
        state.get("algorithm") == "A"
        and state.get("async_scene") == scene
        and state.get("formal_scope") == "mysql_only"
        and state.get("method") == "exhaustive_binary_factorial_algorithm_a_v2"
        and state.get("candidate_search_contract")
        == "lane_local_complete_lr_cartesian_physical_attempt_v2"
        and state.get("complete_round_requires_every_parameter_vector_attempted")
        is True
        and state.get("status") == "NONCONVERGENCE_CLEAN"
        and state.get("stage") == "ALGORITHM_A_NONCONVERGENCE_CLEAN"
        and state.get("converged") is False
        and 0 <= int(state.get("accepted") or 0)
        < int(state.get("accepted_target") or 10)
        and int(state.get("rejected_rounds") or 0) >= 1
        and state.get("round_checkpoint") is None
    )


def converged_terminal(state: Mapping[str, Any], scene: str) -> bool:
    return bool(
        state.get("algorithm") == "A"
        and state.get("async_scene") == scene
        and state.get("formal_scope") == "mysql_only"
        and state.get("status") == "COMPLETE"
        and state.get("stage") == "COMPLETE"
        and state.get("converged") is True
        and int(state.get("accepted") or 0) == 10
        and float(state.get("normalized_width") or 1.0) <= 0.01
        and (state.get("completion_contract") or {}).get(
            "postgresql_algorithm_a_flow_matched"
        ) is True
    )


def algorithm_a_terminal(state: Mapping[str, Any], scene: str) -> bool:
    return converged_terminal(state, scene) or clean_nonconvergence_terminal(
        state, scene,
    )


def memory_scene_terminal(state: Mapping[str, Any], scene: str) -> bool:
    return scene in MEMORY_SCENES and converged_terminal(state, scene)


def xp_needs_transition(state: Mapping[str, Any], scene: str) -> bool:
    if scene not in XP_SCENES:
        return False
    if (
        state.get("algorithm") != "A"
        or state.get("async_scene") != scene
        or state.get("formal_scope") != "mysql_only"
    ):
        raise ValueError(f"invalid XP Algorithm A state: {scene}")
    return not algorithm_a_terminal(state, scene)


def search_state_payload(state: Mapping[str, Any]) -> dict[str, Any]:
    """Return the semantic state that a cohort transition must preserve."""
    required = (
        "accepted",
        "rejected_rounds",
        "consecutive_rejected_rounds",
        "incumbent_reference_refresh_count",
        "ranges",
        "incumbent_params",
        "incumbent_validation",
        "incumbent_reference_source",
        "last_incumbent_reference_refresh",
        "accepted_history",
        "normalized_width",
        "canonical_baseline",
    )
    missing = [name for name in required if name not in state]
    if missing:
        raise ValueError(f"transition state fields absent: {missing}")
    return {name: copy.deepcopy(state[name]) for name in required}


def _worker_map(freeze: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    rows = freeze.get("workers") or []
    workers = {
        str(row.get("worker_id") or ""): copy.deepcopy(dict(row))
        for row in rows
    }
    if not workers or "" in workers or len(workers) != len(rows):
        raise ValueError("frozen worker identities are incomplete or duplicate")
    return workers


def build_released_xp_workers(freeze: Mapping[str, Any]) -> list[dict[str, Any]]:
    workers = _worker_map(freeze)
    required = set(DONOR_TO_RECIPIENT) | set(RECIPIENT_PROTOTYPE.values())
    if not required.issubset(workers):
        raise ValueError("source freeze lacks donor or XP prototype workers")
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
        for field in RESOURCE_FIELDS:
            if row.get(field) != prototype.get(field):
                raise AssertionError(f"recipient envelope changed: {worker_id}.{field}")
        additions.append(row)
    validate_released_xp_workers(freeze, additions)
    return additions


def validate_released_xp_workers(
    freeze: Mapping[str, Any], additions: Sequence[Mapping[str, Any]],
) -> None:
    workers = _worker_map(freeze)
    if {str(row.get("worker_id")) for row in additions} != set(NEW_CONTAINER):
        raise ValueError("released XP worker set differs")
    occupied_db: dict[int, str] = {}
    occupied_client: dict[int, str] = {}
    active = [
        row for row in workers.values() if row.get("scene") in XP_SCENES
    ] + [dict(row) for row in additions]
    for row in active:
        worker_id = str(row["worker_id"])
        for cpu in parse_cpuset(row["database_cpuset"]):
            if cpu in occupied_db:
                raise ValueError(
                    f"database CPU overlap: {cpu} "
                    f"{occupied_db[cpu]},{worker_id}"
                )
            occupied_db[cpu] = worker_id
        for cpu in parse_cpuset(row["client_cpuset"]):
            if cpu in occupied_client:
                raise ValueError(
                    f"client CPU overlap: {cpu} "
                    f"{occupied_client[cpu]},{worker_id}"
                )
            occupied_client[cpu] = worker_id
    for row in additions:
        worker_id = str(row["worker_id"])
        donor_id = str(row.get("slot_origin") or "")
        donor = workers[donor_id]
        scene = str(row["scene"])
        prototype = workers[RECIPIENT_PROTOTYPE[scene]]
        if any(row.get(field) != donor.get(field) for field in POSITION_FIELDS):
            raise ValueError(f"released position differs: {worker_id}")
        if any(row.get(field) != prototype.get(field) for field in RESOURCE_FIELDS):
            raise ValueError(f"XP resource envelope differs: {worker_id}")
        if int(row.get("host_port") or 0) <= 0:
            raise ValueError(f"invalid host port: {worker_id}")


def build_transition_document(
    *, source_freeze_path: Path, source_freeze_sha256: str,
    source_freeze: Mapping[str, Any], memory_states: Mapping[str, Mapping[str, Any]],
    xp_states: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    if set(memory_states) != set(MEMORY_SCENES) or not all(
        memory_scene_terminal(memory_states[scene], scene)
        for scene in MEMORY_SCENES
    ):
        raise ValueError("both memory scenes must be terminal before slot release")
    if set(xp_states) != set(XP_SCENES):
        raise ValueError("both XP states are required")
    additions = build_released_xp_workers(source_freeze)
    state_evidence = {
        scene: {
            "status": state.get("status"),
            "accepted": int(state.get("accepted") or 0),
            "converged": bool(state.get("converged")),
            "search_state_sha256": (
                canonical_sha256(search_state_payload(state))
                if xp_needs_transition(state, scene) else None
            ),
            "transition_required": xp_needs_transition(state, scene),
        }
        for scene, state in xp_states.items()
    }
    return {
        "schema_version": "mysql_algorithm_a_dynamic_slot_transition_v1",
        "status": "READY_TO_PROVISION",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "contract": TRANSITION_CONTRACT,
        "source_freeze_path": str(source_freeze_path.resolve()),
        "source_freeze_sha256": source_freeze_sha256,
        "trigger": {
            "job_terminal": True,
            "tpch_terminal": True,
            "both_memory_scenes_required": True,
            "terminal_outcomes": ["COMPLETE", "COMPLETE"],
        },
        "parallelism": {
            "initial_per_scene": 2,
            "hybench_xp_cpu_after_transition": 4,
            "hybench_xp_io_after_transition": 4,
            "four_scene_wave_barrier": False,
        },
        "boundary": {
            "complete_accepted_round_preserved": True,
            "partial_next_round_archived_and_not_credited": True,
            "accepted_ranges_incumbent_history_preserved": True,
            "fresh_baseline_all_four_workers": True,
            "canonical_baseline_preserved": True,
            "incumbent_replay_all_four_workers": True,
            "mixed_worker_cohort_round_prohibited": True,
        },
        "new_workers": additions,
        "xp_states": state_evidence,
        "native_xp_workload_unchanged": True,
        "resource_quota_inherited_from_recipient_xp_scene": True,
        "cpu_and_numa_position_inherited_from_completed_memory_slot": True,
    }

#!/usr/bin/env python3
"""Resume the v40 JOB 2-to-4 handoff after fresh-target cleanup.

The original boundary executor deliberately fails closed when a target path or
container identity already exists.  The v40 handoff reached the sealed
boundary and successor-freeze stages before discovering stopped v39 worker
artifacts.  After those artifacts are recoverably renamed, this entry point
continues from the sealed snapshot; it never advances or replays the stopped
two-worker JOB round.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
import json
import os
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from scripts import execute_mysql_algorithm_a_job_slot_transition_v2 as binding


legacy = binding.legacy


def resume(result_path: Path) -> int:
    prior = legacy.load(result_path)
    if prior.get("status") == "LAUNCHED":
        print(json.dumps(prior, ensure_ascii=False, sort_keys=True))
        return 0
    if not (
        prior.get("status") == "TRANSITIONING"
        and prior.get("stage") == "SUCCESSOR_FREEZE_SEALED"
    ):
        raise RuntimeError("JOB handoff is not at the sealed resumable stage")

    legacy.validate_contract()
    tpch = legacy.tpch_completion()
    source = legacy.load(legacy.SOURCE_FREEZE)
    if legacy.sha(legacy.SOURCE_FREEZE) != legacy.SOURCE_FREEZE_SHA256:
        raise RuntimeError("source resource freeze differs")

    snapshot = Path(str(prior.get("snapshot") or "")).resolve()
    if snapshot != (
        legacy.TRANSITION_ROOT / "job_memory.predecessor_boundary_snapshot.json"
    ).resolve():
        raise RuntimeError("sealed JOB boundary snapshot path differs")
    predecessor = legacy.load(snapshot)
    if not (
        predecessor.get("transition_boundary_snapshot") is True
        and predecessor.get("status") == "TRANSITION_BOUNDARY_SNAPSHOT"
        and int(predecessor.get("accepted") or 0) == 7
        and int(predecessor.get("round_index") or 0) == 10
        and predecessor.get("round_checkpoint") is None
    ):
        raise RuntimeError("sealed JOB boundary snapshot content differs")

    successor = legacy.load(legacy.FREEZE_PATH)
    if legacy.sha(legacy.FREEZE_PATH) != prior.get("successor_freeze_sha256"):
        raise RuntimeError("sealed JOB successor freeze differs")
    additions = [
        copy.deepcopy(dict(row))
        for row in successor.get("workers") or []
        if row.get("worker_id") in {"job_w3", "job_w4"}
    ]
    if {str(row.get("worker_id")) for row in additions} != {"job_w3", "job_w4"}:
        raise RuntimeError("sealed JOB successor additions differ")

    released = legacy.stop_tpch_containers(source)
    document: dict[str, Any] = copy.deepcopy(prior)
    document.update({
        "status": "TRANSITIONING",
        "stage": "RECOVERY_PROVISIONING_FRESH_JOB_WORKERS",
        "released_tpch_workers": released,
        "recovery_entrypoint": str(Path(__file__).resolve()),
        "updated_at": legacy.now(),
    })
    legacy.atomic(result_path, document)

    provision_root = legacy.TRANSITION_ROOT / "provisioned_workers"
    new_live: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="alga-job-slot") as pool:
        futures = {
            pool.submit(
                legacy.provision.provision_worker,
                successor,
                row,
                provision_root,
            ): str(row["worker_id"])
            for row in additions
        }
        for future in as_completed(futures):
            new_live[futures[future]] = future.result()

    original_live = legacy.original_job_authority(source, successor)
    all_live = {**original_live, **new_live}
    document.update({
        "stage": "FOUR_JOB_WORKERS_PROVISIONED",
        "worker_ids": sorted(all_live),
        "updated_at": legacy.now(),
    })
    legacy.atomic(result_path, document)

    target_cpus = {
        cpu
        for row in all_live.values()
        for cpu in (
            legacy.slots.parse_cpuset(row["database_cpuset"])
            | legacy.slots.parse_cpuset(row["client_cpuset"])
        )
    }
    gate = legacy.coordination.rolling_quiet_gate(
        run_id=f"{binding.CAMPAIGN}_job_dynamic_slots_v2_recovery",
        required_seconds=60.0,
        maximum_wait_seconds=300.0,
        target_cpus=target_cpus,
    )
    gate["target_worker_ids"] = sorted(all_live)
    gate_path = legacy.TRANSITION_ROOT / "dynamic_noninterference_gate.json"
    legacy.atomic(gate_path, gate)
    if gate.get("status") != "PASS":
        raise RuntimeError("four-worker JOB non-interference gate failed")

    authority = {
        "schema_version": "mysql_algorithm_a_job_four_worker_transition_authority_v1",
        "status": "PASS",
        "created_at": legacy.now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "contract": legacy.slots.TRANSITION_CONTRACT,
        "transition_contract_path": str(legacy.CONTRACT_PATH),
        "transition_contract_sha256": legacy.sha(legacy.CONTRACT_PATH),
        "scene": legacy.slots.SCENE,
        "predecessor_snapshot_path": str(snapshot),
        "predecessor_snapshot_sha256": legacy.sha(snapshot),
        "preserved_search_state_sha256": legacy.slots.canonical_sha256(
            legacy.slots.search_state_payload(predecessor)
        ),
        "successor_freeze_path": str(legacy.FREEZE_PATH),
        "successor_freeze_sha256": legacy.sha(legacy.FREEZE_PATH),
        "memory_terminal_evidence": {"tpch_sf4_memory": tpch},
        "dynamic_noninterference_gate": gate,
        "workers": [all_live[key] for key in sorted(all_live)],
        "full_job_113_once_per_measurement": True,
        "candidate_count_per_round_unchanged": 16,
        "mixed_worker_cohort_round_prohibited": True,
        "fresh_baseline_repetitions_per_worker": 2,
        "incumbent_replay_required": True,
        "resource_recalibration_performed": False,
        "canonical_baseline_preserved": True,
        "accepted_ranges_incumbent_history_preserved": True,
        "recovered_after_stale_v39_target_archive": True,
    }
    authority_path = legacy.TRANSITION_ROOT / "job_memory.transition_authority.json"
    legacy.atomic(authority_path, authority)
    launched = legacy.launch(snapshot, authority_path)
    document.update({
        "status": "LAUNCHED",
        "stage": "FOUR_WORKER_JOB_RESUME_LAUNCHED",
        "updated_at": legacy.now(),
        "launch": launched,
        "authority_path": str(authority_path),
        "authority_sha256": legacy.sha(authority_path),
        "dynamic_noninterference_gate_path": str(gate_path),
        "dynamic_noninterference_gate_sha256": legacy.sha(gate_path),
        "job_parallelism_before": 2,
        "job_parallelism_after": 4,
    })
    legacy.atomic(result_path, document)
    print(json.dumps(document, ensure_ascii=False, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", required=True, type=Path)
    return resume(parser.parse_args().result.resolve())


if __name__ == "__main__":
    raise SystemExit(main())

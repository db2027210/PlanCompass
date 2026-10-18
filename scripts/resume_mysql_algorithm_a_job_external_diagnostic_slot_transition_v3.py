#!/usr/bin/env python3
"""Resume the v99 JOB 2-to-4 handoff after its monitor stopped itself.

The v100 handoff sealed and stopped JOB exactly at the completed round-3
boundary, then stopped its own systemd unit before it could create the
four-worker successor.  Recovery v2 then exposed that the sealed benchmark
defaults do not select a database, although donor adoption expected
``DATABASE()=imdb_job``.  This v3 entry point explicitly selects ``imdb_job``
during that identity read-back.  It does not restart the two-worker
predecessor and never replays or credits the archived partial round-4
checkpoint.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
import json
import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from scripts import (  # noqa: E402
    execute_mysql_algorithm_a_job_external_diagnostic_slot_transition_v1 as binding,
)


current = binding.current
legacy = current.legacy
RECOVERY_CONTRACT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_a_job_external_diagnostic_transition_recovery_20260824_v3"
    / "recovery_contract.json"
)


def validate_recovery_contract() -> dict[str, Any]:
    contract = legacy.load(RECOVERY_CONTRACT)
    if not (
        contract.get("schema_version")
        == "mysql_algorithm_a_job_slot_transition_recovery_v1"
        and contract.get("status") == "FROZEN"
        and contract.get("formal_scope") == "mysql_only"
        and contract.get("cross_engine_comparison_prohibited") is True
        and contract.get("recovery_reason")
        == "monitor_self_stop_then_unselected_job_database"
        and contract.get("predecessor_round") == 3
        and contract.get("predecessor_accepted") == 2
        and contract.get("job_parallelism") == {"before": 2, "after": 4}
        and contract.get("predecessor_replay_prohibited") is True
        and contract.get("resource_recalibration_required") is False
    ):
        raise RuntimeError("frozen JOB transition recovery contract differs")
    for row in (contract.get("source_bindings") or {}).values():
        path = ROOT / str(row.get("path") or "")
        if (
            path.is_symlink()
            or not path.is_file()
            or legacy.sha(path) != row.get("sha256")
        ):
            raise RuntimeError(f"JOB transition recovery binding differs: {path}")
    return contract


def validate_boundary(result_path: Path) -> tuple[dict[str, Any], Path, dict[str, Any]]:
    prior = legacy.load(result_path)
    if prior.get("status") == "LAUNCHED":
        return prior, Path(str(prior["snapshot"])).resolve(), {}
    if not (
        prior.get("status") == "TRANSITIONING"
        and prior.get("stage") in {
            "JOB_PREDECESSOR_STOPPED_AT_BOUNDARY",
            "SUCCESSOR_FREEZE_SEALED",
        }
    ):
        raise RuntimeError("JOB handoff is not at the self-stop recovery stage")
    snapshot = Path(str(prior.get("snapshot") or "")).resolve()
    expected = (
        legacy.TRANSITION_ROOT / "job_memory.predecessor_boundary_snapshot.json"
    ).resolve()
    if snapshot != expected:
        raise RuntimeError("sealed JOB boundary snapshot path differs")
    predecessor = legacy.load(snapshot)
    if not (
        predecessor.get("transition_boundary_snapshot") is True
        and predecessor.get("status") == "TRANSITION_BOUNDARY_SNAPSHOT"
        and predecessor.get("stage") == "ROUND_BOUNDARY_SEALED_FOR_JOB_2_TO_4"
        and int(predecessor.get("accepted") or 0) == 2
        and int(predecessor.get("round_index") or 0) == 3
        and int(predecessor.get("transition_boundary_completed_round") or 0) == 3
        and predecessor.get("round_checkpoint") is None
        and predecessor.get("partial_next_round_credited") is False
    ):
        raise RuntimeError("sealed JOB round-3 boundary content differs")
    return prior, snapshot, predecessor


def recover(result_path: Path, recovery_state_path: Path) -> int:
    contract = validate_recovery_contract()
    legacy.validate_contract()
    prior, snapshot, predecessor = validate_boundary(result_path)
    if prior.get("status") == "LAUNCHED":
        print(json.dumps(prior, ensure_ascii=False, sort_keys=True))
        return 0

    recovery: dict[str, Any] = {
        "schema_version": "mysql_algorithm_a_job_slot_transition_recovery_state_v1",
        "status": "RECOVERING",
        "stage": "SEALED_BOUNDARY_VALIDATED",
        "created_at": legacy.now(),
        "updated_at": legacy.now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "recovery_contract_path": str(RECOVERY_CONTRACT),
        "recovery_contract_sha256": legacy.sha(RECOVERY_CONTRACT),
        "handoff_result_path": str(result_path),
        "snapshot": str(snapshot),
        "snapshot_sha256": legacy.sha(snapshot),
        "predecessor_round": 3,
        "predecessor_accepted": 2,
        "predecessor_replayed": False,
        "archived_partial_round_credited": False,
    }
    legacy.atomic(recovery_state_path, recovery)

    try:
        tpch = legacy.tpch_completion()
        source = legacy.load(legacy.SOURCE_FREEZE)
        if legacy.sha(legacy.SOURCE_FREEZE) != legacy.SOURCE_FREEZE_SHA256:
            raise RuntimeError("source resource freeze differs")

        released = legacy.stop_tpch_containers(source)
        additions = legacy.slots.build_job_workers(source)
        successor = legacy.slots.build_successor_freeze(
            source,
            additions,
            created_at=legacy.now(),
            tpch_evidence={"tpch_sf4_memory": tpch},
        )
        legacy.atomic(legacy.FREEZE_PATH, successor)
        document: dict[str, Any] = copy.deepcopy(prior)
        document.update({
            "status": "TRANSITIONING",
            "stage": "SUCCESSOR_FREEZE_SEALED",
            "released_tpch_workers": released,
            "successor_freeze_path": str(legacy.FREEZE_PATH),
            "successor_freeze_sha256": legacy.sha(legacy.FREEZE_PATH),
            "recovery_contract_path": str(RECOVERY_CONTRACT),
            "recovery_contract_sha256": legacy.sha(RECOVERY_CONTRACT),
            "recovered_from_stage": prior.get("stage"),
            "benchmark_identity_readback_database_selected_explicitly": "imdb_job",
            "updated_at": legacy.now(),
        })
        legacy.atomic(result_path, document)
        recovery.update({
            "stage": "SUCCESSOR_FREEZE_SEALED",
            "successor_freeze_path": str(legacy.FREEZE_PATH),
            "successor_freeze_sha256": legacy.sha(legacy.FREEZE_PATH),
            "updated_at": legacy.now(),
        })
        legacy.atomic(recovery_state_path, recovery)

        provision_root = legacy.TRANSITION_ROOT / "provisioned_workers"
        new_live: dict[str, dict[str, Any]] = {}
        original_host_mysql = legacy.reset._host_mysql

        def host_mysql_with_job_schema(
            worker: dict[str, Any],
            defaults: Path,
            sql: str,
            *,
            timeout: int = 60,
        ):
            if sql.strip() == "SELECT CURRENT_USER(),DATABASE();":
                sql = "USE imdb_job; SELECT CURRENT_USER(),DATABASE();"
            return original_host_mysql(worker, defaults, sql, timeout=timeout)

        legacy.reset._host_mysql = host_mysql_with_job_schema
        try:
            with ThreadPoolExecutor(
                max_workers=2, thread_name_prefix="alga-job-slot",
            ) as pool:
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
        finally:
            legacy.reset._host_mysql = original_host_mysql

        original_live = legacy.original_job_authority(source, successor)
        all_live = {**original_live, **new_live}
        document.update({
            "stage": "FOUR_JOB_WORKERS_PROVISIONED",
            "worker_ids": sorted(all_live),
            "updated_at": legacy.now(),
        })
        legacy.atomic(result_path, document)
        recovery.update({
            "stage": "FOUR_JOB_WORKERS_PROVISIONED",
            "worker_ids": sorted(all_live),
            "updated_at": legacy.now(),
        })
        legacy.atomic(recovery_state_path, recovery)

        target_cpus = {
            cpu
            for row in all_live.values()
            for cpu in (
                legacy.slots.parse_cpuset(row["database_cpuset"])
                | legacy.slots.parse_cpuset(row["client_cpuset"])
            )
        }
        gate = legacy.coordination.rolling_quiet_gate(
            run_id="v109_to_a_job4_external_diagnostic_transition_recovery_20260824",
            required_seconds=60.0,
            maximum_wait_seconds=300.0,
            target_cpus=target_cpus,
        )
        gate["target_worker_ids"] = sorted(all_live)
        gate_path = legacy.TRANSITION_ROOT / "dynamic_noninterference_gate.json"
        legacy.atomic(gate_path, gate)
        if gate.get("status") != "PASS":
            raise RuntimeError("four-worker JOB diagnostic gate failed")

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
            "recovered_after_monitor_self_stop": True,
            "benchmark_identity_readback_database_selected_explicitly": "imdb_job",
            "recovery_contract_path": str(RECOVERY_CONTRACT),
            "recovery_contract_sha256": legacy.sha(RECOVERY_CONTRACT),
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
        recovery.update({
            "status": "LAUNCHED",
            "stage": "FOUR_WORKER_JOB_RESUME_LAUNCHED",
            "launch": launched,
            "authority_path": str(authority_path),
            "authority_sha256": legacy.sha(authority_path),
            "updated_at": legacy.now(),
        })
        legacy.atomic(recovery_state_path, recovery)
        print(json.dumps(document, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as exc:
        recovery.update({
            "status": "FAILED",
            "stage": "RECOVERY_FAILED",
            "error": f"{type(exc).__name__}: {exc}",
            "updated_at": legacy.now(),
        })
        legacy.atomic(recovery_state_path, recovery)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", required=True, type=Path)
    parser.add_argument("--recovery-state", required=True, type=Path)
    args = parser.parse_args()
    return recover(args.result.resolve(), args.recovery_state.resolve())


if __name__ == "__main__":
    raise SystemExit(main())

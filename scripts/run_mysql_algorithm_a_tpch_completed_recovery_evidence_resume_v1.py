#!/usr/bin/env python3
"""Resume TPC-H Algorithm A from completed recovery measurements.

The v83 recovery run completed a fresh two-worker baseline and a full
incumbent replay (Q1-Q22 on both workers), then failed only because the old
reboot gate rejected a QPS improvement above its arbitrary upper ratio.  This
controller verifies and reuses those immutable batch results, applies the v3
one-sided regression gate, preserves every completed outer round, and starts
the interrupted round from rank 1 without repeating either full-suite run.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_a_external_sssp_memory_recovery_v2 as parent
from scripts import run_mysql_algorithm_a_reboot_resume_v3 as recovery


CONTRACT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_a_tpch_completed_recovery_evidence_20260823_v1"
    / "transition_contract.json"
)
BASELINE_BATCH = (
    ROOT
    / "history/mysql_four_scene_20260809/"
    "v83_to_a_tpch_external_sssp_memory_recovery_v2_20260823_attempt001_tpch/"
    "formal_baseline/batch_result.json"
)
INCUMBENT_BATCH = (
    ROOT
    / "history/mysql_four_scene_20260809/"
    "v83_to_a_tpch_external_sssp_memory_recovery_v2_20260823_attempt001_tpch/"
    "recovery_incumbent_replay/batch_result.json"
)
FAILED_STATE = (
    ROOT
    / "history/mysql_four_scene_20260809/"
    "v83_to_a_tpch_external_sssp_memory_recovery_v2_20260823_attempt001_tpch/"
    "algorithm_a_state.json"
)
RECOVERY_CONTRACT = (
    "mysql_algorithm_a_tpch_completed_recovery_evidence_resume_v1"
)
_ORIGINAL_MEASURE = parent.base.measure
_ORIGINAL_VALIDATE_SNAPSHOT = recovery.validate_snapshot
_ORIGINAL_SEARCH_PAYLOAD = parent.search_state_payload
_ACTIVE_CONTRACT: dict[str, Any] | None = None
_TRANSITION_REFERENCE: dict[str, Any] | None = None


def sha(path: Path) -> str:
    return parent.sha(path)


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def validate_contract(scene: str, predecessor_path: Path) -> dict[str, Any]:
    if scene != "tpch_sf4_memory":
        raise RuntimeError("completed recovery evidence is TPC-H only")
    if CONTRACT.is_symlink() or not CONTRACT.is_file():
        raise RuntimeError("TPC-H completed recovery evidence contract is absent")
    value = load(CONTRACT)
    source = ROOT / str(value.get("predecessor_snapshot") or "")
    failed = ROOT / str(value.get("failed_recovery_state") or "")
    baseline = ROOT / str(value.get("completed_baseline_batch") or "")
    incumbent = ROOT / str(value.get("completed_incumbent_batch") or "")
    readback = ROOT / str(value.get("recovery_readback") or "")
    if (
        value.get("schema_version")
        != "mysql_algorithm_a_tpch_completed_recovery_evidence_contract_v1"
        or value.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("reuse_completed_full_suite_evidence") is not True
        or value.get("repeat_baseline") is not False
        or value.get("repeat_incumbent_replay") is not False
        or value.get("qps_improvement_has_no_upper_rejection_bound") is not True
        or value.get("qps_regression_minimum_ratio") != 0.5
        or value.get("target_resource_maximum_absolute_delta") != 0.35
        or value.get("discard_interrupted_partial_round") is not True
        or value.get("preserve_complete_outer_history") is not True
        or source.resolve() != predecessor_path.resolve()
        or source.is_symlink() or not source.is_file()
        or sha(source) != value.get("predecessor_snapshot_sha256")
        or failed.resolve() != FAILED_STATE.resolve()
        or failed.is_symlink() or not failed.is_file()
        or sha(failed) != value.get("failed_recovery_state_sha256")
        or baseline.resolve() != BASELINE_BATCH.resolve()
        or baseline.is_symlink() or not baseline.is_file()
        or sha(baseline) != value.get("completed_baseline_batch_sha256")
        or incumbent.resolve() != INCUMBENT_BATCH.resolve()
        or incumbent.is_symlink() or not incumbent.is_file()
        or sha(incumbent) != value.get("completed_incumbent_batch_sha256")
        or readback.is_symlink() or not readback.is_file()
        or sha(readback) != value.get("recovery_readback_sha256")
    ):
        raise RuntimeError("TPC-H completed recovery evidence contract differs")
    for binding in (value.get("source_bindings") or {}).values():
        path = ROOT / str(binding.get("path") or "")
        if path.is_symlink() or not path.is_file() or sha(path) != binding.get("sha256"):
            raise RuntimeError(f"TPC-H recovery evidence source differs: {path}")
    return value


def completed_rows(path: Path) -> dict[str, dict[str, Any]]:
    document = load(path)
    rows = document.get("measurements") or {}
    if (
        document.get("status") != "PASS"
        or document.get("scene") != "tpch_sf4_memory"
        or set(rows) != {"tpch_w1", "tpch_w2"}
        or not all(parent.base.pg_outer.physical_valid(row) for row in rows.values())
        or not all((row.get("workload_result") or {}).get("query_coverage", {}).get("coverage_complete") is True for row in rows.values())
        or not all((row.get("workload_result") or {}).get("query_coverage", {}).get("completed_queries") == [f"Q{i}" for i in range(1, 23)] for row in rows.values())
    ):
        raise RuntimeError(f"completed TPC-H batch is not reusable: {path}")
    return copy.deepcopy(dict(rows))


def transformed_snapshot(path: Path, scene: str) -> dict[str, Any]:
    global _TRANSITION_REFERENCE
    # The predecessor validator invokes the slot payload helper before this
    # transition field exists.  Validate the immutable boundary with the
    # original helper, then install the evidence-enriched payload below.
    active_search_payload = recovery.slots.search_state_payload
    recovery.slots.search_state_payload = parent._ORIGINAL_SEARCH_PAYLOAD
    try:
        value = _ORIGINAL_VALIDATE_SNAPSHOT(path, scene)
    finally:
        recovery.slots.search_state_payload = active_search_payload
    baseline = completed_rows(BASELINE_BATCH)
    incumbent = completed_rows(INCUMBENT_BATCH)
    normalized = [
        parent.base.normalize_to_worker_baseline(
            incumbent[worker_id], baseline[worker_id], value["canonical_baseline"]
        )
        for worker_id in sorted(baseline)
    ]
    reference = recovery.aggregate_rows(normalized)
    evidence = {
        "contract": RECOVERY_CONTRACT,
        "failed_recovery_state": str(FAILED_STATE),
        "failed_recovery_state_sha256": sha(FAILED_STATE),
        "baseline_batch": str(BASELINE_BATCH),
        "baseline_batch_sha256": sha(BASELINE_BATCH),
        "incumbent_batch": str(INCUMBENT_BATCH),
        "incumbent_batch_sha256": sha(INCUMBENT_BATCH),
        "normalized_incumbent_reference": copy.deepcopy(reference),
        "accepted_rounds_added": 0,
        "incumbent_reference_refresh_count_added": 0,
        "interrupted_partial_round_credited": False,
    }
    value["incumbent_validation"] = copy.deepcopy(reference)
    value["incumbent_reference_source"] = (
        "external_sssp_completed_recovery_replay"
    )
    value["recovery_or_worker_transition_reference"] = (
        "external_sssp_completed_recovery_replay"
    )
    value["completed_recovery_evidence_transition"] = copy.deepcopy(evidence)
    _TRANSITION_REFERENCE = evidence
    return value


def search_state_payload(state: Mapping[str, Any]) -> dict[str, Any]:
    value = _ORIGINAL_SEARCH_PAYLOAD(state)
    evidence = state.get("completed_recovery_evidence_transition")
    if not isinstance(evidence, Mapping):
        raise RuntimeError("completed recovery evidence transition is absent")
    value["completed_recovery_evidence_transition"] = copy.deepcopy(dict(evidence))
    value["recovery_or_worker_transition_reference"] = str(
        state["recovery_or_worker_transition_reference"]
    )
    return value


def cached_recovery_measure(**kwargs: Any):
    name = Path(kwargs["output"]).name
    if name not in {"formal_baseline", "recovery_incumbent_replay"}:
        return _ORIGINAL_MEASURE(**kwargs)
    source = BASELINE_BATCH if name == "formal_baseline" else INCUMBENT_BATCH
    rows = completed_rows(source)
    assignments = kwargs["assignments"]
    for worker_id in assignments:
        kwargs["workers"][worker_id]["formal_client_log"] = str(
            Path(kwargs["output"]) / worker_id / "client_processes.jsonl"
        )
        parent.base.runtime.alg.apply_costs(
            kwargs["workers"][worker_id], assignments[worker_id]["params"]
        )
    if set(assignments) != set(rows):
        raise RuntimeError("cached recovery worker cohort differs")
    for worker_id, assignment in assignments.items():
        row = rows[worker_id]
        if (
            int(row.get("rank") or 0) != int(assignment["rank"])
            or row.get("params") != assignment.get("params")
        ):
            raise RuntimeError("cached recovery assignment differs")
    evidence_path = Path(kwargs["output"]) / "reused_completed_batch_evidence.json"
    parent.base.alg.atomic(evidence_path, {
        "schema_version": "mysql_algorithm_a_reused_completed_batch_evidence_v1",
        "status": "REUSED_HASH_VERIFIED_COMPLETE_FULL_SUITE",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "source_batch": str(source),
        "source_batch_sha256": sha(source),
        "worker_ids": sorted(rows),
        "full_tpch_q1_q22_per_worker": True,
        "physical_measurement_repeated": False,
    })
    state = copy.deepcopy(dict(kwargs["state"]))
    state["stage"] = (
        "FORMAL_BASELINE_REUSED_COMPLETED_RECOVERY_EVIDENCE"
        if name == "formal_baseline"
        else "REBOOT_RECOVERY_INCUMBENT_REPLAY_REUSED_COMPLETED_EVIDENCE"
    )
    state["completed_recovery_evidence_transition"] = copy.deepcopy(
        _TRANSITION_REFERENCE
    )
    parent.base.alg.atomic(Path(kwargs["state_path"]), state)
    return rows, state


def main() -> int:
    global _ACTIVE_CONTRACT
    if "--scene" not in sys.argv or "--predecessor-state" not in sys.argv:
        raise RuntimeError("scene and predecessor snapshot are required")
    if sys.argv[sys.argv.index("--scene") + 1] != "tpch_sf4_memory":
        raise RuntimeError("completed recovery evidence resume is TPC-H only")
    predecessor = Path(
        sys.argv[sys.argv.index("--predecessor-state") + 1]
    ).resolve()
    _ACTIVE_CONTRACT = validate_contract("tpch_sf4_memory", predecessor)
    parent.TRANSITION_CONTRACT = CONTRACT
    parent.RECOVERY_CONTRACT = RECOVERY_CONTRACT
    parent.validate_transition_contract = validate_contract
    parent.recovery = recovery
    parent._ORIGINAL_SEARCH_PAYLOAD = recovery.slots.search_state_payload
    parent.search_state_payload = search_state_payload
    recovery.validate_snapshot = transformed_snapshot
    parent.base.measure = cached_recovery_measure
    return parent.main()


if __name__ == "__main__":
    raise SystemExit(main())

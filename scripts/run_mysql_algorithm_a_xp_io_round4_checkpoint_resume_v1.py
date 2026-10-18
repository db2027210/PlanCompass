#!/usr/bin/env python3
"""Resume XP/I/O Algorithm A from the valid Round-4 checkpoint.

Rounds 1--3, the canonical same-worker baseline, and the last accepted
incumbent are retained.  Round 4 had no completed physical candidate, so its
complete 16-vector Top4 candidate document is regenerated and verified before
new physical work starts at rank 1.  The live eight-worker authority is frozen
only after Algorithm C is terminal.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_algorithm_a_dynamic_slots_v2 as slots
from scripts import run_mysql_algorithm_a_xp_top4_mixed_eight_worker_v12 as runtime


HISTORY = ROOT / "history/mysql_four_scene_20260809"
SOURCE_RUN = (
    HISTORY
    / "v859_to_a_io_round2_wave2_physical_adapter_fixed_resume_20260828_xp_io"
)
SOURCE_STATE = SOURCE_RUN / "algorithm_a_state.json"
SOURCE_CANDIDATES = SOURCE_RUN / "round_004/candidates.json"
REUSE_MANIFEST = (
    HISTORY
    / "v1028_existing_b_a_reuse_no_rerun_a_check_only_20260828"
    / "reuse_manifest.json"
)
CONTRACT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_a_xp_io_round4_checkpoint_resume_20260828_v1"
    / "transition_contract.json"
)
SCENE = "hybench_xp_io"
EXPECTED = {f"xp_io_w{i}" for i in range(1, 9)}
ACTIVE = [
    "io_block_read_cost",
    "disk_temptable_row_cost",
    "disk_temptable_create_cost",
    "memory_temptable_row_cost",
]


def load(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"regular JSON required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def sha(path: Path) -> str:
    return runtime.base.alg.sha(path)


def candidate_vectors(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "rank": int(row["rank"]),
            "active_subset": list(row.get("active_subset") or []),
            "algorithm_a_factor_levels": copy.deepcopy(
                row.get("algorithm_a_factor_levels") or {}
            ),
            "optimized_params": copy.deepcopy(row["optimized_params"]),
            "prediction_used_for_ordering": row.get(
                "prediction_used_for_ordering"
            ),
            "prediction_used_for_selection": row.get(
                "prediction_used_for_selection"
            ),
            "qps_used_for_inner_scoring": row.get("qps_used_for_inner_scoring"),
        }
        for row in document.get("candidates") or []
    ]


def requested_authority() -> Path:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--launch-authority", type=Path, required=True)
    args, _unknown = parser.parse_known_args()
    return args.launch_authority.expanduser().resolve()


def validate_predecessor() -> tuple[dict[str, Any], dict[str, Any]]:
    state = load(SOURCE_STATE)
    candidates = load(SOURCE_CANDIDATES)
    checkpoint = state.get("round_checkpoint") or {}
    baseline = state.get("baseline_by_worker") or {}
    accepted_history = state.get("accepted_history") or []
    authority_contract = ((state.get("authority") or {}).get("contract") or {})
    if (
        state.get("algorithm") != "A"
        or state.get("async_scene") != SCENE
        or state.get("formal_scope") != "mysql_only"
        or state.get("cross_engine_comparison_prohibited") is not True
        or state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or int(state.get("round_index") or 0) != 4
        or int(state.get("accepted") or 0) != 3
        or int(state.get("rejected_rounds") or 0) != 0
        or int(state.get("consecutive_rejected_rounds") or 0) != 0
        or list(state.get("active_parameters") or []) != ACTIVE
        or int(checkpoint.get("round_index") or 0) != 4
        or int(checkpoint.get("accepted_index") or 0) != 3
        or int(checkpoint.get("candidate_count") or 0) != 16
        or list(checkpoint.get("attempted_ranks") or [])
        or list(checkpoint.get("valid_ranks") or [])
        or list(checkpoint.get("technical_ranks") or [])
        or list(checkpoint.get("waves") or [])
        or checkpoint.get("candidate_sha256") != sha(SOURCE_CANDIDATES)
        or int(candidates.get("round_index") or 0) != 4
        or int(candidates.get("accepted_index") or 0) != 3
        or int(candidates.get("candidate_count") or 0) != 16
        or len(candidate_vectors(candidates)) != 16
        or set(baseline) != EXPECTED
        or len(accepted_history) != 3
        or [int(row.get("round_index") or 0) for row in accepted_history]
        != [1, 2, 3]
        or authority_contract.get("historical_algorithm_b_or_c_credit") is not False
        or state.get("prediction_used_for_ordering") is not False
        or state.get("prediction_used_for_selection") is not False
        or state.get("qps_used_for_inner_scoring") is not False
        or "selected.cnf" not in str(state.get("error") or "")
    ):
        raise RuntimeError("XP/I/O Algorithm-A Round-4 predecessor differs")
    for row in accepted_history:
        decision = Path(str(row.get("decision_path") or "")).resolve()
        if not decision.is_file() or sha(decision) != row.get("decision_sha256"):
            raise RuntimeError("XP/I/O accepted-history evidence differs")
    return state, candidates


def validate_contract(authority_path: Path) -> dict[str, Any]:
    contract = load(CONTRACT)
    reuse = load(REUSE_MANIFEST)
    bindings = contract.get("source_bindings") or {}
    required_true = (
        "rounds_1_to_3_retained",
        "round4_completed_candidate_measurements_reused_false",
        "round4_new_physical_work_starts_at_rank1",
        "canonical_baseline_reused_without_replay",
        "incumbent_reference_reused_without_replay",
        "complete_top4_candidate_set_required",
        "physical_replay_parallelism_eight",
        "algorithm_a_independent_posthoc_check_only",
        "algorithm_a_feedback_to_b_or_c_false",
    )
    expected_bindings = {
        "controller": Path(__file__).resolve(),
        "runtime_v12": Path(runtime.__file__).resolve(),
        "source_state": SOURCE_STATE,
        "source_candidates": SOURCE_CANDIDATES,
        "reuse_manifest": REUSE_MANIFEST,
        "live_authority": authority_path,
    }
    if (
        contract.get("schema_version")
        != "mysql_algorithm_a_xp_io_round4_checkpoint_resume_contract_v1"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("scene") != SCENE
        or contract.get("active_parameters") != ACTIVE
        or any(contract.get(key) is not True for key in required_true)
        or reuse.get("status") != "PASS"
        or ((reuse.get("algorithm_a") or {}).get("feedback_to_b_or_c"))
        is not False
        or (
            ((reuse.get("algorithm_a") or {}).get("scenes") or {})
            .get(SCENE, {})
            .get("disposition")
        )
        != "RESUME_FROM_VALID_ROUND4_AFTER_C_TERMINAL"
    ):
        raise RuntimeError("XP/I/O Algorithm-A Round-4 transition contract differs")
    for label, path in expected_bindings.items():
        frozen = bindings.get(label) or {}
        try:
            relative = str(path.relative_to(ROOT))
        except ValueError:
            relative = str(path)
        if frozen.get("path") != relative or frozen.get("sha256") != sha(path):
            raise RuntimeError(f"XP/I/O Round-4 binding differs: {label}")
    return contract


def main() -> int:
    authority_path = requested_authority()
    predecessor, source_candidates = validate_predecessor()
    contract = validate_contract(authority_path)
    base = runtime.base
    preserved = slots.search_state_payload(predecessor)
    original_fresh_state = base.fresh_state
    original_measure = base.measure
    original_aggregate = base.aggregate_baseline
    original_atomic = base.atomic
    baseline_reused = False
    candidate_equivalence_checked = False
    patched_dispatches: list[tuple[dict[str, Any], Any]] = []
    physical_measure: dict[str, Any] = {}

    def resumed_fresh_state(
        *, run_id: str, scene: str, active: Sequence[str],
        ranges: Mapping[str, Any], authority: Mapping[str, Any],
    ) -> dict[str, Any]:
        fresh = original_fresh_state(
            run_id=run_id, scene=scene, active=active, ranges=ranges,
            authority=authority,
        )
        fresh.update(copy.deepcopy(preserved))
        fresh.update({
            "accepted": 3,
            "round_index": 3,
            "rejected_rounds": 0,
            "consecutive_rejected_rounds": 0,
            "status": "RUNNING",
            "stage": "ROUND4_CHECKPOINT_AUTHORITY_VALIDATED",
            "round_checkpoint": None,
            "converged": False,
            "baseline_by_worker": copy.deepcopy(predecessor["baseline_by_worker"]),
            "predecessor_run_id": predecessor.get("run_id"),
            "predecessor_snapshot_path": str(SOURCE_STATE),
            "predecessor_snapshot_sha256": sha(SOURCE_STATE),
            "algorithm_a_independent_check_boundary": {
                "role": "independent_posthoc_check_only",
                "feedback_to_b_or_c": False,
                "candidate_vectors_used_by_b_or_c": False,
                "qps_used_by_b_or_c": False,
                "parameter_ranges_used_by_b_or_c": False,
                "search_direction_used_by_b_or_c": False,
            },
            "round4_completed_candidate_measurements_reused": False,
            "round4_new_physical_work_starts_at_rank": 1,
        })
        return fresh

    def preserved_aggregate(rows: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
        nonlocal baseline_reused
        if rows != predecessor["baseline_by_worker"]:
            raise RuntimeError("XP/I/O preserved baseline differs")
        baseline_reused = True
        return copy.deepcopy(predecessor["canonical_baseline"])

    def resumed_measure(**kwargs: Any):
        nonlocal baseline_reused, candidate_equivalence_checked
        output = Path(kwargs["output"])
        if output.name == "formal_baseline" and not baseline_reused:
            baseline_reused = True
            ranking = output.parent / "plan_ranking_client_processes"
            ranking.mkdir(parents=True, mode=0o700)
            for worker_id, worker in kwargs["workers"].items():
                worker["formal_client_log"] = str(ranking / f"{worker_id}.jsonl")
            return copy.deepcopy(predecessor["baseline_by_worker"]), kwargs["state"]
        if (
            output.name == "attempt_01"
            and output.parent.name == "wave_001"
            and output.parent.parent.name == "round_004"
            and not candidate_equivalence_checked
        ):
            regenerated_path = output.parent.parent / "candidates.json"
            regenerated = load(regenerated_path)
            if candidate_vectors(regenerated) != candidate_vectors(source_candidates):
                raise RuntimeError("XP/I/O regenerated Round-4 candidates differ")
            assignments = kwargs["assignments"]
            if sorted(int(row["rank"]) for row in assignments.values()) != list(
                range(1, 9)
            ):
                raise RuntimeError("XP/I/O resumed Round-4 Wave-1 ranks differ")
            original_atomic(regenerated_path, source_candidates)
            candidate_equivalence_checked = True
        return physical_measure["callable"](**kwargs)

    def resumed_atomic(path: Path, value: Mapping[str, Any]) -> None:
        document = copy.deepcopy(dict(value))
        if path.name == "algorithm_a_state.json" and document.get("formal_started") is True:
            document["canonical_baseline"] = copy.deepcopy(
                predecessor["canonical_baseline"]
            )
            document["baseline_by_worker"] = copy.deepcopy(
                predecessor["baseline_by_worker"]
            )
            document["round4_checkpoint_resume"] = {
                "contract_path": str(CONTRACT),
                "contract_sha256": sha(CONTRACT),
                "rounds_1_to_3_retained": True,
                "round4_completed_candidate_measurements_reused": False,
                "round4_new_physical_work_starts_at_rank": 1,
                "candidate_document_equivalence_checked": (
                    candidate_equivalence_checked
                ),
                "canonical_baseline_reused_without_replay": baseline_reused,
                "algorithm_a_independent_posthoc_check_only": True,
                "algorithm_a_feedback_to_b_or_c": False,
            }
        original_atomic(path, document)

    def patch_measure_dispatch(value: Any, seen: set[int] | None = None) -> None:
        seen = seen or set()
        if id(value) in seen or not isinstance(value, dict):
            return
        seen.add(id(value))
        if "no_lease_measure" in value and callable(value["no_lease_measure"]):
            patched_dispatches.append((value, value["no_lease_measure"]))
            value["no_lease_measure"] = resumed_measure
        for child in value.values():
            if isinstance(child, dict):
                patch_measure_dispatch(child, seen)

    patch_measure_dispatch(runtime.implementation)
    if not patched_dispatches:
        raise RuntimeError("XP/I/O XP8 measurement dispatch binding was not found")
    unique = {id(original): original for _namespace, original in patched_dispatches}
    if len(unique) != 1:
        raise RuntimeError("XP/I/O XP8 measurement dispatch is ambiguous")
    physical_measure["callable"] = next(iter(unique.values()))
    base.fresh_state = resumed_fresh_state
    base.aggregate_baseline = preserved_aggregate
    base.measure = resumed_measure
    base.atomic = resumed_atomic
    try:
        result = int(runtime.main())
    finally:
        for namespace, original in patched_dispatches:
            namespace["no_lease_measure"] = original
        base.fresh_state = original_fresh_state
        base.aggregate_baseline = original_aggregate
        base.measure = original_measure
        base.atomic = original_atomic
    if not baseline_reused or not candidate_equivalence_checked:
        raise RuntimeError("XP/I/O Round-4 resume hooks were not all invoked")
    return result


if __name__ == "__main__":
    raise SystemExit(main())

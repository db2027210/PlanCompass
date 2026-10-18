#!/usr/bin/env python3
"""Run C v86, retaining completed CPU outer-18 fallback evidence."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_c_async_scene_v85 as predecessor


CONTRACT_ROOT = (
    ROOT / "config/formal/frozen_mysql_four_scene_algorithm_c_cpu_a_slots_20260828_v42"
)
PARENT_CONTRACT = CONTRACT_ROOT / "algorithm_c_async_contract.json"
OPTIMIZATION_CONTRACT = CONTRACT_ROOT / "outer_runtime_optimization.json"
CONTRACT = CONTRACT_ROOT / "global_monotonic_incumbent_contract.json"
SOURCE_ROOT = predecessor.CPU_PARTIAL_SOURCE
SOURCE_STATE = SOURCE_ROOT / "run_state.json"
SOURCE_FALLBACK_WAVE1 = (
    SOURCE_ROOT / "outer_018/fallback_plan_change/wave_1/batch_result.json"
)
SOURCE_FALLBACK_WAVE2 = (
    SOURCE_ROOT / "outer_018/fallback_plan_change/wave_2/batch_result.json"
)
SCENE = "hybench_xp_cpu"


def mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} object required")
    return dict(value)


def validate_contract() -> dict[str, Any]:
    v84 = predecessor.predecessor
    v83 = v84.predecessor
    v82 = v83.predecessor
    v81 = v82.predecessor
    v80 = v81.predecessor
    alg = v80.coverage_runtime.alg
    parent = alg.load(PARENT_CONTRACT)
    optimization = alg.load(OPTIMIZATION_CONTRACT)
    contract = alg.load(CONTRACT)
    reuse = mapping(
        contract.get("completed_fallback_measurement_continuation"),
        "fallback continuation",
    )
    boundary = mapping(
        contract.get("algorithm_a_independent_check_boundary"), "A boundary"
    )
    if (
        parent.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or optimization.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("release_version") != "v86"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("parent_contract", {}).get("sha256") != alg.sha(PARENT_CONTRACT)
        or reuse.get("scene") != SCENE
        or reuse.get("outer_attempt") != 18
        or reuse.get("new_physical_work_starts_at")
        != "current_interval_plan_coverage"
        or reuse.get("completed_top5_physically_replayed") is not False
        or reuse.get("completed_fallback_physically_replayed") is not False
        or reuse.get("candidate_vectors_reverified") is not True
        or reuse.get("accepted_or_rejected_credit_for_recovery") != 0
        or reuse.get("candidate_or_outer_policy_changed") is not False
        or boundary.get("algorithm_a_role") != "independent_posthoc_check_only"
        or boundary.get("algorithm_a_feedback_to_b_or_c") is not False
    ):
        raise RuntimeError("Algorithm C v86 fallback continuation contract differs")
    for document, label in ((parent, "parent"), (contract, "successor")):
        for name, raw in mapping(document.get("source_bindings"), f"{label} bindings").items():
            row = mapping(raw, f"{label} {name}")
            path = (ROOT / str(row.get("path") or "")).resolve()
            if not path.is_file() or path.is_symlink() or alg.sha(path) != row.get("sha256"):
                raise RuntimeError(f"Algorithm C v86 source binding differs: {name}")
    return {"parent": parent, "optimization": optimization, "successor": contract}


def validate_source(alg: Any) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    state = alg.load(SOURCE_STATE)
    wave1 = alg.load(SOURCE_FALLBACK_WAVE1)
    wave2 = alg.load(SOURCE_FALLBACK_WAVE2)
    rows = mapping(wave1.get("measurements"), "fallback measurements")
    real = [row for row in rows.values() if int(row.get("rank") or 0) > 0]
    if (
        state.get("status") != "FAILED_CLOSED"
        or state.get("error") != "RuntimeError: xp_cpu_w3 quarantine target already exists"
        or int(state.get("outer_attempt") or 0) != 17
        or int((state.get("accepted_rounds") or {}).get(SCENE) or 0) != 6
        or int((state.get("consecutive_failed_rounds") or {}).get(SCENE) or 0) != 1
        or wave1.get("status") != "PASS"
        or wave1.get("failures") not in ([], None)
        or len(rows) != 4
        or len(real) != 1
        or int(real[0].get("rank") or 0) != 1
        or real[0].get("hard_gate_passed") is not True
        or real[0].get("coverage_complete") is not True
        or wave2.get("status") != "PASS"
        or wave2.get("empty_candidate_wave") is not True
        or wave2.get("candidate_count") != 0
        or wave2.get("failures") not in ([], None)
    ):
        raise RuntimeError("Algorithm C v86 source fallback boundary differs")
    return state, wave1, wave2


def install_fallback_reuse(runtime: Any) -> Any:
    alg = runtime.alg
    source_state, source_wave1, source_wave2 = validate_source(alg)
    original = runtime._replay_pg_expansion_waves

    def replay_or_reuse(**kwargs: Any):
        state = kwargs["state"]
        partial = state.get("formal_partial_continuation") or {}
        if not (
            kwargs.get("batch_kind") == "fallback_plan_change"
            and int(kwargs.get("outer_attempt") or 0) == 18
            and set(kwargs.get("candidate_sets") or {}) == {SCENE}
            and Path(str(partial.get("source_root") or "")).resolve()
            == SOURCE_ROOT.resolve()
        ):
            return original(**kwargs)
        candidate_sets = kwargs["candidate_sets"]
        candidates = candidate_sets[SCENE]
        rows = mapping(source_wave1.get("measurements"), "fallback rows")
        real = [copy.deepcopy(row) for row in rows.values() if int(row.get("rank") or 0) > 0]
        if (
            len(candidates) != 1
            or len(real) != 1
            or alg.optimizer_params_key(real[0].get("params") or {})
            != alg.optimizer_params_key(candidates[0].get("optimized_params") or {})
        ):
            raise RuntimeError("Algorithm C v86 regenerated fallback candidate differs")
        real[0]["candidate_ranking"] = copy.deepcopy(candidates[0])
        evaluations = {SCENE: real}
        companions = [
            {
                "batch_kind": "fallback_plan_change",
                "batch_index": 1,
                "worker_id": worker_id,
                "scene": row.get("scene"),
                "hard_gate_passed": row.get("hard_gate_passed"),
                "coverage_complete": row.get("coverage_complete"),
                "completed_measurement_reused": True,
            }
            for worker_id, row in rows.items()
            if int(row.get("rank") or 0) == 0
        ]
        accepted_by_scene: dict[str, dict[str, Any]] = {}
        accepted, _valid = runtime.pg_outer.evaluate_batch(
            real,
            kwargs["incumbents"][SCENE],
            canonical_baseline=kwargs["canonical_baselines"][SCENE],
            target=alg.TARGET[SCENE],
        )
        if accepted is not None:
            accepted_by_scene[SCENE] = {
                "batch_kind": "fallback_plan_change",
                "wave_ranks": [1, 2, 3, 4, 5],
                "selected_rank": int(accepted["row"]["rank"]),
                "selected_param_signature": alg.optimizer_params_key(
                    accepted["row"]["params"]
                ),
                "semantic_batch_evaluated_after_all_physical_waves": True,
            }
        output = Path(kwargs["output"]) / "outer_018/fallback_plan_change"
        for index, source in ((1, SOURCE_FALLBACK_WAVE1), (2, SOURCE_FALLBACK_WAVE2)):
            destination = output / f"wave_{index}/batch_result.json"
            document = copy.deepcopy(source_wave1 if index == 1 else source_wave2)
            document.update({
                "schema_version": "mysql_algorithm_c_completed_fallback_evidence_reuse_v1",
                "source_batch_path": str(source),
                "source_batch_sha256": alg.sha(source),
                "new_physical_measurement_invoked": False,
                "accepted_or_rejected_credit_for_recovery": 0,
                "candidate_vectors_reverified": True,
            })
            alg.atomic(destination, document)
        state["stage"] = "OUTER_018_FALLBACK_PLAN_CHANGE_COMPLETED_EVIDENCE_REUSED"
        state.setdefault("completed_fallback_measurement_continuations", []).append({
            "source_state_path": str(SOURCE_STATE),
            "source_state_sha256": alg.sha(SOURCE_STATE),
            "source_wave1_path": str(SOURCE_FALLBACK_WAVE1),
            "source_wave1_sha256": alg.sha(SOURCE_FALLBACK_WAVE1),
            "source_wave2_path": str(SOURCE_FALLBACK_WAVE2),
            "source_wave2_sha256": alg.sha(SOURCE_FALLBACK_WAVE2),
            "new_physical_measurement_invoked": False,
            "accepted_or_rejected_credit_for_recovery": 0,
            "new_physical_work_starts_at": "current_interval_plan_coverage",
        })
        alg.atomic(Path(kwargs["state_path"]), state)
        return evaluations, companions, [], accepted_by_scene, state

    runtime._replay_pg_expansion_waves = replay_or_reuse
    return original


def main() -> int:
    validate_contract()
    v84 = predecessor.predecessor
    v83 = v84.predecessor
    v82 = v83.predecessor
    v81 = v82.predecessor
    v80 = v81.predecessor
    runtime = v80.coverage_runtime
    old = {
        "root": predecessor.CONTRACT_ROOT,
        "parent": predecessor.PARENT_CONTRACT,
        "optimization": predecessor.OPTIMIZATION_CONTRACT,
        "contract": predecessor.CONTRACT,
        "validate": predecessor.validate_contract,
    }
    predecessor.CONTRACT_ROOT = CONTRACT_ROOT
    predecessor.PARENT_CONTRACT = PARENT_CONTRACT
    predecessor.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    predecessor.CONTRACT = CONTRACT
    predecessor.validate_contract = validate_contract
    original_replay = install_fallback_reuse(runtime)
    try:
        return int(predecessor.main())
    finally:
        runtime._replay_pg_expansion_waves = original_replay
        predecessor.CONTRACT_ROOT = old["root"]
        predecessor.PARENT_CONTRACT = old["parent"]
        predecessor.OPTIMIZATION_CONTRACT = old["optimization"]
        predecessor.CONTRACT = old["contract"]
        predecessor.validate_contract = old["validate"]


if __name__ == "__main__":
    raise SystemExit(main())

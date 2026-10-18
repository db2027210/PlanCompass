#!/usr/bin/env python3
"""Run IO C v90 with unconditional exact-boundary fallback evidence reuse."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_c_async_scene_v89 as predecessor


CONTRACT_ROOT = ROOT / "config/formal/frozen_mysql_four_scene_algorithm_c_cpu_a_slots_20260828_v46"
PARENT_CONTRACT = CONTRACT_ROOT / "algorithm_c_async_contract.json"
OPTIMIZATION_CONTRACT = CONTRACT_ROOT / "outer_runtime_optimization.json"
CONTRACT = CONTRACT_ROOT / "global_monotonic_incumbent_contract.json"
SCENE = predecessor.SCENE


def mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} object required")
    return dict(value)


def validate_contract() -> dict[str, Any]:
    alg = predecessor.partial_runtime.alg
    parent = alg.load(PARENT_CONTRACT)
    optimization = alg.load(OPTIMIZATION_CONTRACT)
    contract = alg.load(CONTRACT)
    hook = mapping(contract.get("exact_boundary_fallback_reuse_hook"), "reuse hook")
    a_boundary = mapping(contract.get("algorithm_a_independent_check_boundary"), "A boundary")
    if (
        parent.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or optimization.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("release_version") != "v90"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("parent_contract", {}).get("sha256") != alg.sha(PARENT_CONTRACT)
        or hook.get("scene") != SCENE
        or hook.get("outer_attempt") != 13
        or hook.get("formal_partial_state_presence_not_used_as_hook_guard") is not True
        or hook.get("candidate_vector_reverified") is not True
        or hook.get("physical_fallback_remeasurement") is not False
        or hook.get("accepted_or_rejected_credit") != 0
        or a_boundary.get("algorithm_a_role") != "independent_posthoc_check_only"
        or a_boundary.get("algorithm_a_feedback_to_b_or_c") is not False
    ):
        raise RuntimeError("Algorithm C v90 exact fallback hook contract differs")
    for document, label in ((parent, "parent"), (contract, "successor")):
        for name, raw in mapping(document.get("source_bindings"), f"{label} bindings").items():
            row = mapping(raw, f"{label} {name}")
            path = (ROOT / str(row.get("path") or "")).resolve()
            if not path.is_file() or path.is_symlink() or alg.sha(path) != row.get("sha256"):
                raise RuntimeError(f"Algorithm C v90 source binding differs: {name}")
    return {"parent": parent, "optimization": optimization, "successor": contract}


def install_exact_fallback_reuse(runtime: Any) -> Any:
    alg = runtime.alg
    _source_state, source_wave1, source_wave2 = predecessor.validate_source(alg)
    original = runtime._replay_pg_expansion_waves

    def replay_or_reuse(**kwargs: Any):
        if not (
            kwargs.get("batch_kind") == "fallback_plan_change"
            and int(kwargs.get("outer_attempt") or 0) == 13
            and set(kwargs.get("candidate_sets") or {}) == {SCENE}
        ):
            return original(**kwargs)
        candidates = kwargs["candidate_sets"][SCENE]
        rows = mapping(source_wave1.get("measurements"), "fallback rows")
        real = [copy.deepcopy(row) for row in rows.values() if int(row.get("rank") or 0) > 0]
        if (
            len(candidates) != 1
            or len(real) != 1
            or alg.optimizer_params_key(real[0].get("params") or {})
            != alg.optimizer_params_key(candidates[0].get("optimized_params") or {})
        ):
            raise RuntimeError("Algorithm C v90 regenerated IO fallback candidate differs")
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
            for worker_id, row in rows.items() if int(row.get("rank") or 0) == 0
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
                "selected_param_signature": alg.optimizer_params_key(accepted["row"]["params"]),
                "semantic_batch_evaluated_after_all_physical_waves": True,
            }
        output = Path(kwargs["output"]) / "outer_013/fallback_plan_change"
        for index, source in ((1, predecessor.SOURCE_FALLBACK_WAVE1), (2, predecessor.SOURCE_FALLBACK_WAVE2)):
            destination = output / f"wave_{index}/batch_result.json"
            document = copy.deepcopy(source_wave1 if index == 1 else source_wave2)
            document.update({
                "schema_version": "mysql_algorithm_c_completed_fallback_evidence_reuse_v2",
                "source_batch_path": str(source),
                "source_batch_sha256": alg.sha(source),
                "new_physical_measurement_invoked": False,
                "accepted_or_rejected_credit_for_recovery": 0,
                "candidate_vectors_reverified": True,
                "formal_partial_state_presence_not_used_as_hook_guard": True,
            })
            alg.atomic(destination, document)
        state = kwargs["state"]
        state["stage"] = "OUTER_013_FALLBACK_PLAN_CHANGE_COMPLETED_EVIDENCE_REUSED"
        state.setdefault("completed_fallback_measurement_continuations", []).append({
            "source_state_path": str(predecessor.SOURCE_STATE),
            "source_state_sha256": alg.sha(predecessor.SOURCE_STATE),
            "source_wave1_path": str(predecessor.SOURCE_FALLBACK_WAVE1),
            "source_wave1_sha256": alg.sha(predecessor.SOURCE_FALLBACK_WAVE1),
            "source_wave2_path": str(predecessor.SOURCE_FALLBACK_WAVE2),
            "source_wave2_sha256": alg.sha(predecessor.SOURCE_FALLBACK_WAVE2),
            "new_physical_measurement_invoked": False,
            "accepted_or_rejected_credit_for_recovery": 0,
            "new_physical_work_starts_at": "current_interval_plan_coverage",
            "formal_partial_state_presence_not_used_as_hook_guard": True,
        })
        alg.atomic(Path(kwargs["state_path"]), state)
        return evaluations, companions, [], accepted_by_scene, state

    runtime._replay_pg_expansion_waves = replay_or_reuse
    return original


def main() -> int:
    validate_contract()
    old = {
        "root": predecessor.CONTRACT_ROOT,
        "parent": predecessor.PARENT_CONTRACT,
        "optimization": predecessor.OPTIMIZATION_CONTRACT,
        "contract": predecessor.CONTRACT,
        "validate": predecessor.validate_contract,
        "install": predecessor.predecessor.install_fallback_reuse,
    }
    predecessor.CONTRACT_ROOT = CONTRACT_ROOT
    predecessor.PARENT_CONTRACT = PARENT_CONTRACT
    predecessor.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    predecessor.CONTRACT = CONTRACT
    predecessor.validate_contract = validate_contract
    predecessor.predecessor.install_fallback_reuse = install_exact_fallback_reuse
    try:
        return int(predecessor.main())
    finally:
        predecessor.CONTRACT_ROOT = old["root"]
        predecessor.PARENT_CONTRACT = old["parent"]
        predecessor.OPTIMIZATION_CONTRACT = old["optimization"]
        predecessor.CONTRACT = old["contract"]
        predecessor.validate_contract = old["validate"]
        predecessor.predecessor.install_fallback_reuse = old["install"]


if __name__ == "__main__":
    raise SystemExit(main())

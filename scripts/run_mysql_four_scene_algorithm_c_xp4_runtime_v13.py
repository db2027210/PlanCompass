#!/usr/bin/env python3
"""IO-C runtime that skips physical waves with no unresolved candidates."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_four_scene_algorithm_c_xp4_runtime_v12 as predecessor


runtime_v1 = predecessor.runtime_v1
alg = predecessor.alg
OPTIMIZATION_CONTRACT = predecessor.OPTIMIZATION_CONTRACT
WAVE_COORDINATION_CONTRACT = predecessor.WAVE_COORDINATION_CONTRACT
CHECKPOINT = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v261_to_c_top4_io_outer6_checkpoint_20260826/prepared_binding.json"
)


def install() -> None:
    predecessor.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    predecessor.WAVE_COORDINATION_CONTRACT = WAVE_COORDINATION_CONTRACT
    predecessor.install()
    original_state_document = runtime_v1._state_document
    original_single = runtime_v1._build_single_candidate_assignments
    original_multi = runtime_v1._build_multi_candidate_assignments
    original_measure = runtime_v1._measure_batch_with_fair_coordination

    def state_document(*args: Any, **kwargs: Any) -> dict[str, Any]:
        state = original_state_document(*args, **kwargs)
        checkpoint = kwargs.get("checkpoint")
        if not isinstance(checkpoint, Mapping):
            raise RuntimeError("IO empty-wave checkpoint object differs")
        seed = checkpoint.get("runtime_seed") or {}
        source = seed.get("source_state") or {}
        if (
            Path(str(kwargs.get("checkpoint_path") or "")).resolve() != CHECKPOINT.resolve()
            or source.get("sha256")
            != "009dc78a2fbf6371ec3e13027212840591fc8ff167c0006504e30aa4432b6c37"
        ):
            raise RuntimeError("IO empty-wave runtime seed differs")
        archives = copy.deepcopy(seed.get("valid_replay_archive_by_scene") or {})
        signatures = copy.deepcopy(
            seed.get("previously_accepted_parameter_signatures") or {}
        )
        if len(archives.get("hybench_xp_io") or []) != 4 or len(
            signatures.get("hybench_xp_io") or []
        ) != 1:
            raise RuntimeError("IO empty-wave replay seed cardinality differs")
        state["valid_replay_archive_by_scene"].update(archives)
        state["previously_accepted_parameter_signatures"].update(signatures)
        state["empty_candidate_wave_policy"] = {
            "status": "ACTIVE",
            "physical_workload_skipped_only_when_assignments_are_empty": True,
            "candidate_set_changed": False,
            "candidate_sampling": False,
            "incumbent_companion_required_for_empty_independent_wave": False,
        }
        return state

    def single_assignments(**kwargs: Any):
        rankings = kwargs.get("rankings") or {}
        if runtime_v1.INDEPENDENT_SCENE_EXECUTION and not rankings:
            return {}, set()
        return original_single(**kwargs)

    def multi_assignments(**kwargs: Any):
        rankings = kwargs.get("rankings") or {}
        if runtime_v1.INDEPENDENT_SCENE_EXECUTION and not rankings:
            return {}, set()
        return original_multi(**kwargs)

    def measure_batch(**kwargs: Any):
        assignments = kwargs.get("assignments") or {}
        if assignments:
            return original_measure(**kwargs)
        output = Path(kwargs["output"])
        output.mkdir(parents=True, exist_ok=False, mode=0o700)
        state = kwargs["state"]
        stage_prefix = str(kwargs["stage_prefix"])
        audit = {
            "schema_version": "mysql_algorithm_c_empty_candidate_wave_skip_v1",
            "status": "PASS",
            "scene": runtime_v1.SCENES[0],
            "stage_prefix": stage_prefix,
            "candidate_assignment_count": 0,
            "physical_workload_started": False,
            "candidate_set_changed": False,
            "candidate_sampling": False,
            "accepted_or_rejected_state_credit_increment": 0,
            "reason": "no_unresolved_candidate_assignment",
        }
        alg.atomic(output / "empty_candidate_wave_skip.json", audit)
        alg.atomic(output / "batch_result.json", {
            "schema_version": "mysql_algorithm_c_empty_candidate_wave_batch_v1",
            "status": "PASS", "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "elapsed_seconds": 0.0, "measurements": {}, "failures": [],
            "empty_candidate_wave_skip": copy.deepcopy(audit),
        })
        state["stage"] = f"{stage_prefix}_EMPTY_CANDIDATE_WAVE_SKIPPED"
        state.setdefault("empty_candidate_waves_skipped", []).append({
            "stage_prefix": stage_prefix,
            "audit_path": str(output / "empty_candidate_wave_skip.json"),
            "audit_sha256": alg.sha(output / "empty_candidate_wave_skip.json"),
        })
        alg.atomic(Path(kwargs["state_path"]), state)
        return {}, state

    runtime_v1._state_document = state_document
    runtime_v1._build_single_candidate_assignments = single_assignments
    runtime_v1._build_multi_candidate_assignments = multi_assignments
    runtime_v1._measure_batch_with_fair_coordination = measure_batch


def main() -> int:
    install()
    return runtime_v1.main()


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Resume B outer 5 with fully scene-local measurement and decisions."""

from __future__ import annotations

import copy
from pathlib import Path
import re
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import (
    run_mysql_four_scene_algorithm_b_independent_lane_failure_isolation_core_v17
    as base,
)
from scripts import (
    run_mysql_four_scene_algorithm_b_scene_independent_measurement_v1
    as independent_measurement,
)
from scripts import (
    run_mysql_four_scene_algorithm_b_independent_parallel_221_resume_v16
    as predecessor,
)


v12 = predecessor.v12
core = predecessor.core
alg = predecessor.alg
runtime = predecessor.runtime
SOURCE = (
    predecessor.base.HISTORY
    / "v16_formal_parallel221_outer5_rank5_write_settle_resume_20260818_attempt010"
)
SCHEDULER_CONTRACT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_b_parallel_221_20260818_v17"
    / "algorithm_b_parallel_221_contract.json"
)
CARDINALITY_ERROR = (
    "RuntimeError: job_memory Algorithm B valid real evaluation cardinality differs"
)
REUSED_BATCHES = {
    key: path for key, path in predecessor.REUSED_BATCHES.items() if key[0] <= 4
}
_ORIGINAL_ATOMIC = predecessor._ORIGINAL_ATOMIC


def _regular(path: Path) -> dict[str, Any]:
    return dict(v12._regular_json(path))


def validate_source() -> dict[str, Any]:
    state = _regular(SOURCE / "run_state.json")
    if (
        state.get("algorithm") != "B"
        or state.get("formal_scope") != "mysql_only"
        or state.get("cross_engine_comparison_prohibited") is not True
        or state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or state.get("error") != CARDINALITY_ERROR
        or int(state.get("outer_attempt") or 0) != 4
        or state.get("accepted_rounds") != {
            "job_memory": 1,
            "tpch_sf4_memory": 0,
            "hybench_xp_cpu": 1,
            "hybench_xp_io": 0,
        }
        or state.get("rejected_outer_attempts") != {
            "job_memory": 3,
            "tpch_sf4_memory": 4,
            "hybench_xp_cpu": 3,
            "hybench_xp_io": 4,
        }
        or any(bool(value) for value in (state.get("converged") or {}).values())
        or any((SOURCE / "outer_005").glob("*.decision.json"))
    ):
        raise RuntimeError("v17 outer-5 pre-decision cardinality boundary differs")
    for outer in (1, 2, 3, 4):
        decisions = sorted((SOURCE / f"outer_{outer:03d}").glob("*.decision.json"))
        if len(decisions) != 4:
            raise RuntimeError(f"v17 source outer {outer} decision cardinality differs")
        for path in decisions:
            _regular(path)
    rankings = _regular(SOURCE / "outer_005/predicted_rankings.json")
    if (
        rankings.get("status") != "PASS"
        or int(rankings.get("outer_attempt") or 0) != 5
        or rankings.get("active_scenes") != list(base.SCENES)
        or any(len(rows) != 5 for rows in
               (rankings.get("selected_top_k") or {}).values())
    ):
        raise RuntimeError("v17 source outer-5 ranking differs")
    for rank in range(1, 6):
        batch = _regular(SOURCE / f"outer_005/top5_batch_{rank}/batch_result.json")
        measured = batch.get("measurements") or {}
        if (
            batch.get("status") != "FAILED_CLOSED"
            or len(measured) != 4
            or sum(
                row.get("hard_gate_passed") is True
                and row.get("coverage_complete") is True
                for row in measured.values()
            ) != 2
        ):
            raise RuntimeError(f"v17 source outer-5 failure shape differs: rank {rank}")
    for key, path in REUSED_BATCHES.items():
        batch = _regular(path)
        rank = key[1]
        if (
            int(batch.get("candidate_rank") or rank) != rank
            or not (batch.get("measurements") or {})
        ):
            raise RuntimeError(f"v17 reused batch identity differs: {key}")
    return state


def measure_resume(**kwargs: Any):
    output = Path(kwargs["output"])
    match = re.search(r"outer_(\d+)/top5_batch_(\d+)$", str(output))
    if match is not None:
        key = (int(match.group(1)), int(match.group(2)))
        source = REUSED_BATCHES.get(key)
        if source is not None:
            destination = output / "batch_result.json"
            predecessor.base._mirror_batch(source, destination)
            v12._mirror_supporting_wave_evidence(source, destination)
            document = alg.load(destination)
            state = kwargs["state"]
            state["stage"] = f"{kwargs['stage_prefix']}_RESTORED_VERIFIED"
            state["scene_independent_continuation_source"] = str(SOURCE)
            state["scene_independent_continuation_reused_batches"] = [
                [outer, rank] for outer, rank in sorted(REUSED_BATCHES)
                if (outer, rank) <= key
            ]
            alg.atomic(Path(kwargs["state_path"]), state)
            return copy.deepcopy(dict(document["measurements"])), state
    return core._measure_221(**kwargs)


def atomic_with_outer4_identity(path: Path, value: Mapping[str, Any]) -> None:
    if path.name.endswith(".decision.json") and path.parent.name in {
        "outer_002", "outer_003", "outer_004",
    }:
        source = _regular(SOURCE / path.parent.name / path.name)
        if v12._decision_semantics(value) != v12._decision_semantics(source):
            raise RuntimeError(
                f"{path.parent.name} reconstructed decision differs: {path.name}"
            )
    if path.name == "predicted_rankings.json" and path.parent.name == "outer_005":
        source = _regular(SOURCE / "outer_005/predicted_rankings.json")
        if (
            predecessor.base.ranking_identity(value.get("selected_top_k") or {})
            != predecessor.base.ranking_identity(source.get("selected_top_k") or {})
        ):
            raise RuntimeError("outer-5 recomputed Top-5 differs from source")
    _ORIGINAL_ATOMIC(path, value)


def state_document_v17(run_id: str) -> dict[str, Any]:
    original_contract = predecessor.SCHEDULER_CONTRACT
    try:
        predecessor.SCHEDULER_CONTRACT = SCHEDULER_CONTRACT
        document = predecessor.state_document_v16(run_id)
    finally:
        predecessor.SCHEDULER_CONTRACT = original_contract
    document["technical_continuation"].update({
        "kind": "outer5_scene_independent_fresh_replay_v17",
        "source_run_root": str(SOURCE),
        "source_failure_state_sha256": alg.sha(SOURCE / "run_state.json"),
        "completed_outer_decisions_reconstructed": [1, 2, 3, 4],
        "outer5_prior_candidate_ranks_reused": [],
        "outer5_fresh_candidate_ranks_required": [1, 2, 3, 4, 5],
        "outer5_ranking_recomputed_and_identity_checked": True,
        "four_scene_algorithmic_progress_independent": True,
        "scene_local_valid_candidate_cardinality": True,
        "scene_local_failure_attribution": True,
        "scene_local_resource_windows": True,
        "job_plan_capture_independent_of_tpch": True,
        "accepted_or_rejected_state_after_outer4_reconstructed": True,
    })
    document["scene_independence_release"] = {
        "release_version": "v17",
        "decision_core_path": str(Path(base.__file__).resolve()),
        "decision_core_sha256": alg.sha(Path(base.__file__).resolve()),
        "measurement_path": str(Path(independent_measurement.__file__).resolve()),
        "measurement_sha256": alg.sha(Path(independent_measurement.__file__).resolve()),
        "physical_wave_parallelism_unchanged": True,
        "four_scene_progress_lockstep_required": False,
        "workload_contract_unchanged": True,
        "resource_envelope_unchanged": True,
    }
    document["controller_source_path"] = str(Path(__file__).resolve())
    document["controller_source_sha256"] = alg.sha(Path(__file__).resolve())
    return document


def validate_contract() -> dict[str, Any]:
    if not SCHEDULER_CONTRACT.is_file() or SCHEDULER_CONTRACT.is_symlink():
        raise RuntimeError(f"sealed v17 scheduler contract absent: {SCHEDULER_CONTRACT}")
    contract = alg.load(SCHEDULER_CONTRACT)
    continuation = contract.get("continuation") or {}
    if (
        contract.get("release_version") != "v17"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("candidate_batches") != [[1, 2], [3, 4], [5]]
        or int(contract.get("candidate_parallelism") or 0) != 2
        or continuation.get("resume_boundary")
        != "outer_005_before_scene_independent_decisions"
        or continuation.get("four_scene_algorithmic_progress_independent") is not True
        or continuation.get("outer5_fresh_replay_required") is not True
    ):
        raise RuntimeError("parallel v17 continuation contract differs")
    return contract


def install() -> None:
    predecessor.install()
    base.FreshBaselineObjective.score = core._score_with_resource_payload
    base.c32.reset_xp = predecessor.reset_xp_write_precise
    runtime.alg.reset_xp = predecessor.reset_xp_write_precise
    runtime.alg.measure_batch = independent_measurement.measure_batch
    runtime._build_single_candidate_assignments = core._build_assignments_221
    runtime._measure_batch_with_fair_coordination = measure_resume
    base.state_document = state_document_v17
    alg.atomic = atomic_with_outer4_identity


def main() -> int:
    validate_contract()
    predecessor.validate_source()
    validate_source()
    install()
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())

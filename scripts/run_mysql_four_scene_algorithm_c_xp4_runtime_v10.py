#!/usr/bin/env python3
"""XP4 C runtime that resumes v225 IO Outer-6 at the rank-5 companion wave."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_four_scene_algorithm_c_xp4_runtime_v9 as predecessor


runtime_v1 = predecessor.predecessor.runtime_v1
alg = runtime_v1.alg
OPTIMIZATION_CONTRACT = predecessor.OPTIMIZATION_CONTRACT
WAVE_COORDINATION_CONTRACT = predecessor.WAVE_COORDINATION_CONTRACT
SOURCE = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v225_to_c_top4_io_xp4_20260826_attempt020_xp_io"
)
EXPECTED_ERROR_PREFIX = "CalledProcessError: Command '['mysql',"


def bound(path: Path) -> dict[str, str]:
    path = path.resolve()
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular partial evidence required: {path}")
    digest = alg.sha(path)
    sidecar = path.with_name(path.name + ".sha256")
    if sidecar.is_file() and sidecar.read_text(encoding="ascii").split()[0] != digest:
        raise RuntimeError(f"partial evidence checksum differs: {path}")
    return {"path": str(path), "sha256": digest}


def validate_v225_physical_partial(
    *, source_root: Path, checkpoint: Mapping[str, Any],
    reconstructed: Mapping[str, Any], start_rank: int,
    completed_rank5_root: Path | None = None,
) -> dict[str, Any]:
    source_root = source_root.resolve()
    if (
        source_root != SOURCE.resolve()
        or start_rank != 5
        or completed_rank5_root is not None
        or int(checkpoint.get("completed_outer_attempts") or 0) != 5
        or int(reconstructed.get("outer_attempt") or 0) != 5
    ):
        raise RuntimeError("v225 physical partial continuation boundary differs")
    state_path = source_root / "run_state.json"
    state = alg.load(state_path)
    outer = source_root / "outer_006"
    ranking_path = outer / "predicted_rankings.json"
    batch_path = outer / "top5_batch_1/batch_result.json"
    failed_batch = outer / "top5_batch_2"
    gate_path = outer / "top5_batch_2_dynamic_noninterference_gate.json"
    ranking = alg.load(ranking_path)
    batch = alg.load(batch_path)
    gate = alg.load(gate_path)
    baseline_path = Path(str(state["formal_incumbent_baseline"]["path"])).resolve()
    baseline = alg.load(baseline_path)
    selected = list((ranking.get("selected_top_k") or {}).get("hybench_xp_io") or [])
    measurements = dict(batch.get("measurements") or {})
    state_fields = (
        "accepted_rounds", "rejected_outer_attempts", "acceptance_tier",
        "consecutive_failed_rounds", "interval_stable_rounds",
        "stable_intervals", "converged", "full_plan_replay_history_v2",
    )
    if (
        state.get("algorithm") != "C"
        or state.get("async_scene") != "hybench_xp_io"
        or state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or not str(state.get("error") or "").startswith(EXPECTED_ERROR_PREFIX)
        or int(state.get("outer_attempt") or 0) != 5
        or state.get("accepted_rounds", {}).get("hybench_xp_io") != 3
        or state.get("rejected_outer_attempts", {}).get("hybench_xp_io") != 2
        or any(state.get(field) != reconstructed.get(field) for field in state_fields)
        or ranking.get("schema_version")
        != "mysql_four_scene_algorithm_c_rankings_boundary_resume_v2"
        or int(ranking.get("outer_attempt") or 0) != 6
        or ranking.get("active_decision_scenes") != ["hybench_xp_io"]
        or len(selected) != 4
        or batch.get("status") != "PASS"
        or batch.get("failures") not in ([], None)
        or len(measurements) != 4
        or sorted(int(row.get("rank") or 0) for row in measurements.values())
        != [1, 2, 3, 4]
        or gate.get("status") != "PASS"
        or gate.get("errors") not in ([], None)
        or (failed_batch / "batch_result.json").exists()
        or (failed_batch / "xp_io_w1/adapter_result.json").exists()
        or not (failed_batch / "xp_io_w1/resource_stability.json").is_file()
        or baseline.get("status") != "PASS"
        or baseline.get("failures") not in ([], None)
        or len(baseline.get("measurements") or {}) != 4
        or list(outer.glob("*.decision.json"))
    ):
        raise RuntimeError("v225 physical partial evidence differs")
    evaluations: list[dict[str, Any]] = []
    for row in sorted(measurements.values(), key=lambda item: int(item["rank"])):
        rank = int(row["rank"])
        if (
            row.get("scene") != "hybench_xp_io"
            or row.get("hard_gate_passed") is not True
            or row.get("coverage_complete") is not True
            or alg.optimizer_params_key(row["params"])
            != alg.optimizer_params_key(selected[rank - 1]["optimized_params"])
        ):
            raise RuntimeError(f"v225 completed physical rank {rank} differs")
        seeded = copy.deepcopy(dict(row))
        seeded["formal_partial_replay_source"] = {
            **bound(batch_path), "physical_candidate_ranks": [1, 2, 3, 4],
        }
        evaluations.append(seeded)
    completed_binding: dict[str, Any] = {
        **bound(batch_path), "physical_candidate_ranks": [1, 2, 3, 4],
        "measurement_values_unchanged": True,
    }
    return {
        "source_root": str(source_root),
        "source_state": bound(state_path), "baseline": bound(baseline_path),
        "predicted_rankings": bound(ranking_path), "blocked_gate": bound(gate_path),
        "continuation_reason": "post_measurement_mysql_counter_probe_failure",
        "runtime_interruption_watchdog": None,
        "completed_batches": [completed_binding], "start_rank": 5,
        "outer_attempt": 6, "ranking_document": ranking,
        "evaluations": {"hybench_xp_io": evaluations},
        "technical_batch_exclusions": [],
        "candidate_state_mutation_before_continuation": False,
        "accepted_state_credit_before_continuation": 0,
        "completed_rank_release_race": False,
        "completed_rank5_recovery": None,
        "physical_wave_partial_continuation": True,
        "completed_candidate_ranks_reused": [1, 2, 3, 4],
        "failed_companion_wave_replayed": [5],
    }


def install() -> None:
    predecessor.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    predecessor.WAVE_COORDINATION_CONTRACT = WAVE_COORDINATION_CONTRACT
    predecessor.install()
    runtime_v1._validate_formal_partial_continuation = validate_v225_physical_partial


def main() -> int:
    install()
    return runtime_v1.main()


if __name__ == "__main__":
    raise SystemExit(main())

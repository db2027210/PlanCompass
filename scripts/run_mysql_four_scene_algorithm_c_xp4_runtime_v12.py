#!/usr/bin/env python3
"""Resume IO-C after a valid rank-5 incumbent companion measurement."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_four_scene_algorithm_c_xp4_runtime_v11 as predecessor


runtime_v1 = predecessor.runtime_v1
alg = predecessor.alg
OPTIMIZATION_CONTRACT = predecessor.OPTIMIZATION_CONTRACT
WAVE_COORDINATION_CONTRACT = predecessor.WAVE_COORDINATION_CONTRACT
COMPLETED = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v253_to_c_top4_io_outer6_partial_20260826_attempt022_xp_io"
)


def validate_completed_companion(
    *, source_root: Path, checkpoint: Mapping[str, Any],
    reconstructed: Mapping[str, Any], start_rank: int,
    completed_rank5_root: Path | None = None,
) -> dict[str, Any]:
    if completed_rank5_root is None or completed_rank5_root.resolve() != COMPLETED.resolve():
        raise RuntimeError("IO completed companion recovery authority differs")
    result = predecessor.validate_active_scene_history(
        source_root=source_root,
        checkpoint=checkpoint,
        reconstructed=reconstructed,
        start_rank=start_rank,
        completed_rank5_root=None,
    )
    selected = (result["ranking_document"].get("selected_top_k") or {}).get(
        "hybench_xp_io"
    ) or []
    evaluations = (result.get("evaluations") or {}).get("hybench_xp_io") or []
    if len(selected) != 4 or sorted(int(row.get("rank") or 0) for row in evaluations) != [1, 2, 3, 4]:
        raise RuntimeError("IO inherited candidate set differs")
    for row in evaluations:
        rank = int(row["rank"])
        row["candidate_ranking"] = copy.deepcopy(selected[rank - 1])

    state_path = completed_rank5_root / "run_state.json"
    batch_path = completed_rank5_root / "outer_006/top5_batch_5/batch_result.json"
    state = alg.load(state_path)
    batch = alg.load(batch_path)
    measurements = dict(batch.get("measurements") or {})
    if (
        state.get("status") != "FAILED_CLOSED"
        or state.get("error") != "KeyError: 'candidate_ranking'"
        or int(state.get("outer_attempt") or 0) != 5
        or (state.get("accepted_rounds") or {}).get("hybench_xp_io") != 3
        or (state.get("rejected_outer_attempts") or {}).get("hybench_xp_io") != 2
        or state.get("candidate_batches_for_this_execution") != [[5]]
        or batch.get("status") != "PASS"
        or batch.get("failures") not in ([], None)
        or len(measurements) != 1
        or list((completed_rank5_root / "outer_006").glob("*.decision.json"))
    ):
        raise RuntimeError("IO completed companion state differs")
    companion = next(iter(measurements.values()))
    incumbent_params = (reconstructed.get("params_by_scene") or {}).get(
        "hybench_xp_io"
    ) or {}
    if (
        companion.get("scene") != "hybench_xp_io"
        or int(companion.get("rank") or 0) != 0
        or companion.get("hard_gate_passed") is not True
        or companion.get("coverage_complete") is not True
        or alg.optimizer_params_key(companion.get("params") or {})
        != alg.optimizer_params_key(incumbent_params)
    ):
        raise RuntimeError("IO completed incumbent companion measurement differs")
    companion_binding = {
        "path": str(batch_path),
        "sha256": alg.sha(batch_path),
        "physical_rank": 0,
        "semantic_role": "incumbent_companion_only",
        "candidate_evidence_eligible": False,
        "measurement_replayed_without_rerun": True,
    }
    result["start_rank"] = 6
    result["completed_rank5_recovery"] = {
        "source_root": str(completed_rank5_root),
        "source_state": {"path": str(state_path), "sha256": alg.sha(state_path)},
        "batch": copy.deepcopy(companion_binding),
        "decision_state_mutation_before_recovery": False,
    }
    result["completed_batches"].append(companion_binding)
    result["failed_companion_wave_replayed"] = []
    result["completed_companion_wave_reused"] = True
    result["candidate_measurements_replayed"] = []
    result["candidate_ranking_metadata_reconstructed_from_frozen_ranking"] = True
    return result


def install() -> None:
    predecessor.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    predecessor.WAVE_COORDINATION_CONTRACT = WAVE_COORDINATION_CONTRACT
    predecessor.install()
    runtime_v1._validate_formal_partial_continuation = validate_completed_companion


def main() -> int:
    install()
    return runtime_v1.main()


if __name__ == "__main__":
    raise SystemExit(main())

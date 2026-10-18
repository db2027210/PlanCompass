#!/usr/bin/env python3
"""Resume Algorithm B outer 5 rank 3 after bounded disk2 settle failure."""

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
    run_mysql_four_scene_algorithm_b_independent_parallel_221_resume_v14
    as predecessor,
)


v12 = predecessor.v12
core = predecessor.core
base = predecessor.base
alg = predecessor.alg
runtime = predecessor.runtime
SOURCE = (
    base.HISTORY
    / "v14_formal_parallel221_outer4_quiet_resume_20260818_attempt008"
)
SCHEDULER_CONTRACT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_b_parallel_221_20260818_v15"
    / "algorithm_b_parallel_221_contract.json"
)
SETTLE_ERROR = "RuntimeError: disk2 post-reset writeback did not settle in 120s"
FAILED_SETTLE = (
    SOURCE
    / "xp_resets/algorithm_b_outer_005_rank_3/post_reset_storage_settle.json"
)
FAILED_RESET_BINDING = (
    SOURCE / "xp_resets/algorithm_b_outer_005_rank_3/reset_binding.json"
)
REUSED_BATCHES = {
    **predecessor.REUSED_BATCHES,
    **{
        (4, rank): SOURCE / f"outer_004/top5_batch_{rank}/batch_result.json"
        for rank in range(1, 6)
    },
    **{
        (5, rank): SOURCE / f"outer_005/top5_batch_{rank}/batch_result.json"
        for rank in (1, 2)
    },
}
_ORIGINAL_ATOMIC = predecessor._ORIGINAL_ATOMIC


def _regular(path: Path) -> dict[str, Any]:
    return dict(v12._regular_json(path))


def validate_source() -> dict[str, Any]:
    state = _regular(SOURCE / "run_state.json")
    settle = _regular(FAILED_SETTLE)
    reset = _regular(FAILED_RESET_BINDING)
    expected_accepted = {
        "job_memory": 1,
        "tpch_sf4_memory": 0,
        "hybench_xp_cpu": 1,
        "hybench_xp_io": 0,
    }
    expected_rejected = {
        "job_memory": 3,
        "tpch_sf4_memory": 4,
        "hybench_xp_cpu": 3,
        "hybench_xp_io": 4,
    }
    if (
        state.get("algorithm") != "B"
        or state.get("formal_scope") != "mysql_only"
        or state.get("cross_engine_comparison_prohibited") is not True
        or state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or state.get("error") != SETTLE_ERROR
        or int(state.get("outer_attempt") or 0) != 4
        or state.get("accepted_rounds") != expected_accepted
        or state.get("rejected_outer_attempts") != expected_rejected
        or any(bool(value) for value in (state.get("converged") or {}).values())
        or state.get("formal_candidate_batches") != [[1, 2], [3, 4], [5]]
        or int(state.get("candidate_parallelism") or 0) != 2
        or settle.get("status") != "FAILED_CLOSED"
        or settle.get("schema_version")
        != "mysql_algorithm_c_post_reset_storage_settle_v1"
        or int(settle.get("quiet_required_seconds") or 0) != 15
        or int(settle.get("quiet_maximum_seconds") or 0) != 120
        or int(settle.get("maximum_write_bytes_per_second") or 0)
        != 8 * 1024 * 1024
        or int(settle.get("maximum_in_flight_ios") or 0) != 1
        or settle.get("workload_started_before_gate") is not False
        or settle.get("accepted_state_credit_mutated") is not False
        or len(settle.get("samples") or []) < 120
        or reset.get("status") != "PASS"
        or len(reset.get("workers") or []) != 4
        or any(row.get("ready") is not True for row in reset.get("workers") or [])
        or (SOURCE / "outer_005/top5_batch_3").exists()
        or any((SOURCE / "outer_005").glob("*.decision.json"))
    ):
        raise RuntimeError("v15 outer-5 rank-3 settle-failure boundary differs")
    for outer in (1, 2, 3, 4):
        decisions = sorted((SOURCE / f"outer_{outer:03d}").glob("*.decision.json"))
        if len(decisions) != 4:
            raise RuntimeError(f"v15 source outer {outer} decision cardinality differs")
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
        raise RuntimeError("v15 source outer-5 ranking differs")
    for key, path in REUSED_BATCHES.items():
        batch = _regular(path)
        rank = key[1]
        if (
            int(batch.get("candidate_rank") or rank) != rank
            or not (batch.get("measurements") or {})
            or any(int(row.get("rank") or 0) != rank
                   for row in (batch.get("measurements") or {}).values())
        ):
            raise RuntimeError(f"v15 reused batch identity differs: {key}")
    return state


def measure_resume(**kwargs: Any):
    output = Path(kwargs["output"])
    match = re.search(r"outer_(\d+)/top5_batch_(\d+)$", str(output))
    if match is not None:
        key = (int(match.group(1)), int(match.group(2)))
        source = REUSED_BATCHES.get(key)
        if source is not None:
            destination = output / "batch_result.json"
            base._mirror_batch(source, destination)
            v12._mirror_supporting_wave_evidence(source, destination)
            document = alg.load(destination)
            state = kwargs["state"]
            state["stage"] = f"{kwargs['stage_prefix']}_RESTORED_VERIFIED"
            state["outer5_settle_continuation_source"] = str(SOURCE)
            state["outer5_settle_continuation_reused_batches"] = [
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
            base.ranking_identity(value.get("selected_top_k") or {})
            != base.ranking_identity(source.get("selected_top_k") or {})
        ):
            raise RuntimeError("outer-5 recomputed Top-5 differs from source")
    _ORIGINAL_ATOMIC(path, value)


def state_document_v15(run_id: str) -> dict[str, Any]:
    original_contract = predecessor.SCHEDULER_CONTRACT
    try:
        predecessor.SCHEDULER_CONTRACT = SCHEDULER_CONTRACT
        document = predecessor.state_document_v14(run_id)
    finally:
        predecessor.SCHEDULER_CONTRACT = original_contract
    document["technical_continuation"].update({
        "kind": "outer5_rank3_post_reset_settle_retry_v15",
        "source_run_root": str(SOURCE),
        "source_failure_state_sha256": alg.sha(SOURCE / "run_state.json"),
        "completed_outer_decisions_reconstructed": [1, 2, 3, 4],
        "outer4_completed_candidate_ranks_reused": [1, 2, 3, 4, 5],
        "outer5_completed_candidate_ranks_reused": [1, 2],
        "outer5_ranking_recomputed_and_identity_checked": True,
        "failed_outer5_rank3_measurement_reused": False,
        "failed_outer5_rank3_workload_started": False,
        "fresh_outer5_rank3_reset_required": True,
        "post_reset_settle_thresholds_unchanged": True,
        "failed_settle_path": str(FAILED_SETTLE),
        "failed_settle_sha256": alg.sha(FAILED_SETTLE),
        "accepted_or_rejected_state_after_outer4_reconstructed": True,
    })
    document["controller_source_path"] = str(Path(__file__).resolve())
    document["controller_source_sha256"] = alg.sha(Path(__file__).resolve())
    return document


def validate_contract() -> dict[str, Any]:
    if not SCHEDULER_CONTRACT.is_file() or SCHEDULER_CONTRACT.is_symlink():
        raise RuntimeError(f"sealed v15 scheduler contract absent: {SCHEDULER_CONTRACT}")
    contract = alg.load(SCHEDULER_CONTRACT)
    if (
        contract.get("release_version") != "v15"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("candidate_batches") != [[1, 2], [3, 4], [5]]
        or int(contract.get("candidate_parallelism") or 0) != 2
        or (contract.get("continuation") or {}).get("resume_boundary")
        != "outer_005_rank_3_post_reset_settle"
        or (contract.get("continuation") or {}).get(
            "post_reset_settle_thresholds_unchanged"
        ) is not True
    ):
        raise RuntimeError("parallel v15 continuation contract differs")
    return contract


def install() -> None:
    predecessor.install()
    runtime._measure_batch_with_fair_coordination = measure_resume
    base.state_document = state_document_v15
    alg.atomic = atomic_with_outer4_identity


def main() -> int:
    validate_contract()
    v12.validate_source()
    predecessor.predecessor.validate_recovery()
    predecessor.validate_source()
    validate_source()
    install()
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Resume Algorithm B at outer 3 rank 3 after a pre-workload reset timeout."""

from __future__ import annotations

import copy
from pathlib import Path
import re
import shutil
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import reset_mysql_xp_data_planes_v8 as reset_v8
from scripts import (
    run_mysql_four_scene_algorithm_b_independent_parallel_221_resume_v11
    as predecessor,
)


core = predecessor.predecessor.predecessor_221
base = predecessor.base
alg = predecessor.alg
runtime = predecessor.runtime
HISTORY = base.HISTORY
SOURCE = (
    HISTORY
    / "v11_formal_independent_parallel221_outer1_score_resume_20260818_attempt003"
)
SCHEDULER_CONTRACT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_b_parallel_221_20260818_v12"
    / "algorithm_b_parallel_221_contract.json"
)
RESET_FAILURE = (
    "TimeoutExpired: Command '['sudo', '-n', 'chown', '999:999', "
    "'/disk2/resource_adaptive_mysql_algorithm_c_disk2_v4/xp_io_w1/"
    "credentials/reset-init.sql']' timed out after 30 seconds"
)
REUSED_BATCHES = {
    **{(2, rank): SOURCE / f"outer_002/top5_batch_{rank}/batch_result.json"
       for rank in range(1, 6)},
    (3, 1): SOURCE / "outer_003/top5_batch_1/batch_result.json",
    (3, 2): SOURCE / "outer_003/top5_batch_2/batch_result.json",
}
_ORIGINAL_ATOMIC = alg.atomic


def _regular_json(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular continuation artifact required: {path}")
    base._verified_sidecar(path)
    value = alg.load(path)
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def validate_source() -> dict[str, Any]:
    state = _regular_json(SOURCE / "run_state.json")
    if (
        state.get("algorithm") != "B"
        or state.get("formal_scope") != "mysql_only"
        or state.get("cross_engine_comparison_prohibited") is not True
        or state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or state.get("error") != RESET_FAILURE
        or int(state.get("outer_attempt") or 0) != 2
        or any(int(value) != 0 for value in
               (state.get("accepted_rounds") or {}).values())
        or any(int(value) != 2 for value in
               (state.get("rejected_outer_attempts") or {}).values())
        or any(bool(value) for value in (state.get("converged") or {}).values())
        or state.get("formal_candidate_batches") != [[1, 2], [3, 4], [5]]
    ):
        raise RuntimeError("v11 outer-3 reset failure boundary differs")

    for outer in (1, 2):
        decisions = sorted((SOURCE / f"outer_{outer:03d}").glob("*.decision.json"))
        if len(decisions) != 4:
            raise RuntimeError(f"source outer {outer} decision cardinality differs")
        for path in decisions:
            _regular_json(path)
    rankings = _regular_json(SOURCE / "outer_003/predicted_rankings.json")
    if (
        rankings.get("status") != "PASS"
        or rankings.get("outer_attempt") != 3
        or rankings.get("active_scenes") != list(base.SCENES)
        or any(len(rows) != 5 for rows in
               (rankings.get("selected_top_k") or {}).values())
    ):
        raise RuntimeError("source outer-3 ranking evidence differs")
    for key, path in REUSED_BATCHES.items():
        batch = _regular_json(path)
        expected_rank = key[1]
        if (
            int(batch.get("candidate_rank") or expected_rank) != expected_rank
            or set(batch.get("measurements") or {}) == set()
            or any(int(row.get("rank") or 0) != expected_rank
                   for row in (batch.get("measurements") or {}).values())
        ):
            raise RuntimeError(f"source batch identity differs: {key}")
    if (
        (SOURCE / "outer_003/top5_batch_3/batch_result.json").exists()
        or any((SOURCE / "outer_003").glob("*.decision.json"))
    ):
        raise RuntimeError("failed outer-3 rank 3 unexpectedly started workload")
    return state


def _mirror_supporting_wave_evidence(source: Path, destination: Path) -> None:
    for name in (
        "physical_joint_wave_batch_result.json",
        "parallel_wave_provenance.json",
        "physical_single_wave_batch_result.json",
    ):
        source_path = source.parent / name
        if not source_path.is_file():
            continue
        base._verified_sidecar(source_path)
        destination_path = destination.parent / name
        if destination_path.exists() or destination_path.is_symlink():
            raise FileExistsError(f"fresh mirrored evidence required: {destination_path}")
        shutil.copy2(source_path, destination_path)
        shutil.copy2(
            source_path.with_name(source_path.name + ".sha256"),
            destination_path.with_name(destination_path.name + ".sha256"),
        )


def measure_resume(**kwargs: Any):
    output = Path(kwargs["output"])
    match = re.search(r"outer_(\d+)/top5_batch_(\d+)$", str(output))
    if match is not None:
        key = (int(match.group(1)), int(match.group(2)))
        source = REUSED_BATCHES.get(key)
        if source is not None:
            destination = output / "batch_result.json"
            base._mirror_batch(source, destination)
            _mirror_supporting_wave_evidence(source, destination)
            document = alg.load(destination)
            state = kwargs["state"]
            state["stage"] = f"{kwargs['stage_prefix']}_RESTORED_VERIFIED"
            state["outer3_reset_continuation_source"] = str(SOURCE)
            state["outer3_reset_continuation_reused_batches"] = [
                [outer, rank] for outer, rank in sorted(REUSED_BATCHES)
                if (outer, rank) <= key
            ]
            alg.atomic(Path(kwargs["state_path"]), state)
            return copy.deepcopy(dict(document["measurements"])), state
    return core._measure_221(**kwargs)


def _decision_semantics(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        name: copy.deepcopy(value.get(name))
        for name in (
            "scene", "outer_attempt", "accepted",
            "accepted_state_credit_increment", "accepted_rounds",
            "rejected_outer_attempts", "unique_real_resource_score_best_rank",
            "unique_real_resource_score_best", "valid_real_evaluation_count",
            "interval_convergence", "converged", "tier_progress",
        )
    }


def atomic_with_outer2_identity(path: Path, value: Mapping[str, Any]) -> None:
    if path.parent.name == "outer_002" and path.name.endswith(".decision.json"):
        source = _regular_json(SOURCE / "outer_002" / path.name)
        if _decision_semantics(value) != _decision_semantics(source):
            raise RuntimeError(f"outer-2 reconstructed decision differs: {path.name}")
    _ORIGINAL_ATOMIC(path, value)


def state_document_v12(run_id: str):
    original_contract = predecessor.SCHEDULER_CONTRACT
    try:
        predecessor.SCHEDULER_CONTRACT = SCHEDULER_CONTRACT
        document = predecessor.state_document_v11(run_id)
    finally:
        predecessor.SCHEDULER_CONTRACT = original_contract
    document["technical_continuation"].update({
        "kind": "outer3_rank3_preworkload_reset_timeout_resume_v12",
        "source_run_root": str(SOURCE),
        "source_failure_state_sha256": alg.sha(SOURCE / "run_state.json"),
        "completed_outer_decisions_reconstructed": [1, 2],
        "outer3_completed_candidate_ranks_reused": [1, 2],
        "failed_outer3_rank3_measurement_reused": False,
        "failed_outer3_rank3_workload_started": False,
        "accepted_or_rejected_state_after_outer2_reconstructed": True,
        "credential_metadata_timeout_seconds": 120,
    })
    document["reset_source_path"] = str(Path(reset_v8.__file__).resolve())
    document["reset_source_sha256"] = alg.sha(Path(reset_v8.__file__).resolve())
    document["controller_source_path"] = str(Path(__file__).resolve())
    document["controller_source_sha256"] = alg.sha(Path(__file__).resolve())
    return document


def validate_contract() -> dict[str, Any]:
    if not SCHEDULER_CONTRACT.is_file() or SCHEDULER_CONTRACT.is_symlink():
        raise RuntimeError(f"sealed v12 scheduler contract absent: {SCHEDULER_CONTRACT}")
    contract = alg.load(SCHEDULER_CONTRACT)
    if (
        contract.get("release_version") != "v12"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("candidate_batches") != [[1, 2], [3, 4], [5]]
        or int(contract.get("candidate_parallelism") or 0) != 2
        or (contract.get("continuation") or {}).get(
            "outer3_completed_candidate_ranks_reused"
        ) != [1, 2]
    ):
        raise RuntimeError("parallel v12 continuation contract differs")
    return contract


def install() -> None:
    reset_v8.install()
    base.FreshBaselineObjective.score = core._score_with_resource_payload
    runtime._build_single_candidate_assignments = core._build_assignments_221
    runtime._measure_batch_with_fair_coordination = measure_resume
    base.state_document = state_document_v12
    alg.atomic = atomic_with_outer2_identity


def main() -> int:
    validate_contract()
    validate_source()
    install()
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())

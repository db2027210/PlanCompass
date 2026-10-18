#!/usr/bin/env python3
"""Resume Algorithm B outer 4 after a pre-workload quiet-gate timeout."""

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
    run_mysql_four_scene_algorithm_b_independent_parallel_221_resume_v13
    as predecessor,
)


v12 = predecessor.predecessor
core = v12.core
base = predecessor.base
alg = predecessor.alg
runtime = v12.runtime
SOURCE = (
    base.HISTORY
    / "v13_formal_parallel221_outer3_rank3_recovered_resume_20260818_attempt007"
)
SCHEDULER_CONTRACT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_b_parallel_221_20260818_v14"
    / "algorithm_b_parallel_221_contract.json"
)
QUIET_GATE_ERROR = "RuntimeError: cross-project quiet gate exhausted its single bounded attempt"
QUIET_GATE = (
    SOURCE / "outer_004/top5_batch_1_cross_project_wave_quiet_gate_attempt_001.json"
)
REUSED_BATCHES = {
    **{
        (2, rank): v12.SOURCE / f"outer_002/top5_batch_{rank}/batch_result.json"
        for rank in range(1, 6)
    },
    **{
        (3, rank): SOURCE / f"outer_003/top5_batch_{rank}/batch_result.json"
        for rank in range(1, 6)
    },
}
_ORIGINAL_ATOMIC = v12._ORIGINAL_ATOMIC


def _regular(path: Path) -> dict[str, Any]:
    return dict(v12._regular_json(path))


def validate_source() -> dict[str, Any]:
    state = _regular(SOURCE / "run_state.json")
    gate = _regular(QUIET_GATE)
    if (
        state.get("algorithm") != "B"
        or state.get("formal_scope") != "mysql_only"
        or state.get("cross_engine_comparison_prohibited") is not True
        or state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or state.get("error") != QUIET_GATE_ERROR
        or int(state.get("outer_attempt") or 0) != 3
        or any(int(value) != 0 for value in
               (state.get("accepted_rounds") or {}).values())
        or any(int(value) != 3 for value in
               (state.get("rejected_outer_attempts") or {}).values())
        or any(bool(value) for value in (state.get("converged") or {}).values())
        or state.get("formal_candidate_batches") != [[1, 2], [3, 4], [5]]
        or gate.get("status") != "BLOCKED_FAIL_CLOSED"
        or gate.get("errors") != ["coordinated_quiet_window_timeout"]
        or float(gate.get("observed_seconds") or 0) < 60.0
        or (SOURCE / "outer_004/top5_batch_1").exists()
        or any((SOURCE / "outer_004").glob("*.decision.json"))
    ):
        raise RuntimeError("v14 outer-4 pre-workload quiet-gate boundary differs")
    for outer in (1, 2, 3):
        decisions = sorted((SOURCE / f"outer_{outer:03d}").glob("*.decision.json"))
        if len(decisions) != 4:
            raise RuntimeError(f"v14 source outer {outer} decision cardinality differs")
        for path in decisions:
            _regular(path)
    rankings = _regular(SOURCE / "outer_004/predicted_rankings.json")
    if (
        rankings.get("status") != "PASS"
        or int(rankings.get("outer_attempt") or 0) != 4
        or rankings.get("active_scenes") != list(base.SCENES)
        or any(len(rows) != 5 for rows in
               (rankings.get("selected_top_k") or {}).values())
    ):
        raise RuntimeError("v14 source outer-4 ranking differs")
    for key, path in REUSED_BATCHES.items():
        batch = _regular(path)
        rank = key[1]
        if (
            int(batch.get("candidate_rank") or rank) != rank
            or not (batch.get("measurements") or {})
            or any(int(row.get("rank") or 0) != rank
                   for row in (batch.get("measurements") or {}).values())
        ):
            raise RuntimeError(f"v14 reused batch identity differs: {key}")
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
            state["outer4_quiet_gate_continuation_source"] = str(SOURCE)
            state["outer4_quiet_gate_continuation_reused_batches"] = [
                [outer, rank] for outer, rank in sorted(REUSED_BATCHES)
                if (outer, rank) <= key
            ]
            alg.atomic(Path(kwargs["state_path"]), state)
            return copy.deepcopy(dict(document["measurements"])), state
    return core._measure_221(**kwargs)


def atomic_with_outer3_identity(path: Path, value: Mapping[str, Any]) -> None:
    if path.name.endswith(".decision.json") and path.parent.name in {
        "outer_002", "outer_003",
    }:
        outer = int(path.parent.name.removeprefix("outer_"))
        source_root = v12.SOURCE if outer == 2 else SOURCE
        source = _regular(source_root / path.parent.name / path.name)
        if v12._decision_semantics(value) != v12._decision_semantics(source):
            raise RuntimeError(f"outer-{outer} reconstructed decision differs: {path.name}")
    if path.name == "predicted_rankings.json" and path.parent.name == "outer_004":
        source = _regular(SOURCE / "outer_004/predicted_rankings.json")
        if (
            base.ranking_identity(value.get("selected_top_k") or {})
            != base.ranking_identity(source.get("selected_top_k") or {})
        ):
            raise RuntimeError("outer-4 recomputed Top-5 differs from source")
    _ORIGINAL_ATOMIC(path, value)


def state_document_v14(run_id: str) -> dict[str, Any]:
    original_contract = predecessor.SCHEDULER_CONTRACT
    try:
        predecessor.SCHEDULER_CONTRACT = SCHEDULER_CONTRACT
        document = predecessor.state_document_v13(run_id)
    finally:
        predecessor.SCHEDULER_CONTRACT = original_contract
    document["technical_continuation"].update({
        "kind": "outer4_rank1_preworkload_quiet_gate_resume_v14",
        "source_run_root": str(SOURCE),
        "source_failure_state_sha256": alg.sha(SOURCE / "run_state.json"),
        "completed_outer_decisions_reconstructed": [1, 2, 3],
        "outer3_completed_candidate_ranks_reused": [1, 2, 3, 4, 5],
        "outer4_ranking_recomputed_and_identity_checked": True,
        "failed_outer4_rank1_measurement_reused": False,
        "failed_outer4_rank1_workload_started": False,
        "failed_quiet_gate_path": str(QUIET_GATE),
        "failed_quiet_gate_sha256": alg.sha(QUIET_GATE),
        "accepted_or_rejected_state_after_outer3_reconstructed": True,
    })
    document["controller_source_path"] = str(Path(__file__).resolve())
    document["controller_source_sha256"] = alg.sha(Path(__file__).resolve())
    return document


def validate_contract() -> dict[str, Any]:
    if not SCHEDULER_CONTRACT.is_file() or SCHEDULER_CONTRACT.is_symlink():
        raise RuntimeError(f"sealed v14 scheduler contract absent: {SCHEDULER_CONTRACT}")
    contract = alg.load(SCHEDULER_CONTRACT)
    if (
        contract.get("release_version") != "v14"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("candidate_batches") != [[1, 2], [3, 4], [5]]
        or int(contract.get("candidate_parallelism") or 0) != 2
        or (contract.get("continuation") or {}).get("resume_boundary")
        != "outer_004_rank_1_preworkload_quiet_gate"
    ):
        raise RuntimeError("parallel v14 continuation contract differs")
    return contract


def install() -> None:
    predecessor.install()
    runtime._measure_batch_with_fair_coordination = measure_resume
    base.state_document = state_document_v14
    alg.atomic = atomic_with_outer3_identity


def main() -> int:
    validate_contract()
    v12.validate_source()
    predecessor.validate_recovery()
    validate_source()
    install()
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())

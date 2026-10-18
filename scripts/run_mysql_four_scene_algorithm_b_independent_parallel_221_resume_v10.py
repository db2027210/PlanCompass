#!/usr/bin/env python3
"""Algorithm B 2+2+1 continuation with initialized v10 state metadata."""

from __future__ import annotations

import argparse
import copy
from pathlib import Path
import re

from scripts import (
    run_mysql_four_scene_algorithm_b_independent_parallel_221_resume_v9
    as predecessor_221,
)


base = predecessor_221.predecessor
alg = predecessor_221.alg
runtime = predecessor_221.runtime
SCHEDULER_CONTRACT = (
    predecessor_221.ROOT
    / "config/formal/frozen_mysql_algorithm_b_parallel_221_20260818_v10"
    / "algorithm_b_parallel_221_contract.json"
)


def state_document_v10(run_id: str):
    document = predecessor_221._ORIGINAL_STATE_DOCUMENT(run_id)
    continuation = document.setdefault("technical_continuation", {})
    continuation.update({
        "kind": "post_outer1_score_interface_parallel_221_resume_v10",
        "outer1_complete_replays_reused": [1, 3, 4, 5],
        "outer1_invalid_timeout_preserved": [2],
        "outer1_decision_state_reused": False,
        "score_api_raw_payload_supplied": True,
        "qps_used_for_real_resource_score": False,
        "parallel_scheduler_contract_path": str(SCHEDULER_CONTRACT),
        "parallel_scheduler_contract_sha256": alg.sha(SCHEDULER_CONTRACT),
        "v9_prestart_failure_reused": False,
        "v9_prestart_failure_started_workload": False,
    })
    document["formal_candidate_batches"] = copy.deepcopy(
        predecessor_221.FORMAL_CANDIDATE_BATCHES
    )
    document["candidate_parallelism"] = 2
    document["parallel_lane_mapping"] = {
        "first_rank_in_wave": "w1",
        "second_rank_in_wave": "w2",
        "final_rank": "w1",
    }
    document["outer1_replay_resume_source"] = str(
        predecessor_221.OUTER1_REPLAY_SOURCE
    )
    document["outer1_replay_resume_source_state_sha256"] = alg.sha(
        predecessor_221.OUTER1_REPLAY_SOURCE / "run_state.json"
    )
    document["controller_source_path"] = str(Path(__file__).resolve())
    document["controller_source_sha256"] = alg.sha(Path(__file__).resolve())
    return document


def install() -> None:
    base.FreshBaselineObjective.score = predecessor_221._score_with_resource_payload
    runtime._build_single_candidate_assignments = (
        predecessor_221._build_assignments_221
    )
    runtime._measure_batch_with_fair_coordination = predecessor_221._measure_221
    base.state_document = state_document_v10


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--resume-from", type=Path, required=True)
    known, _remaining = parser.parse_known_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", known.run_id) is None:
        raise RuntimeError("unsafe run id")
    if not SCHEDULER_CONTRACT.is_file() or SCHEDULER_CONTRACT.is_symlink():
        raise RuntimeError(f"sealed v10 scheduler contract absent: {SCHEDULER_CONTRACT}")
    contract = alg.load(SCHEDULER_CONTRACT)
    if (
        contract.get("release_version") != "v10"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("candidate_batches")
        != predecessor_221.FORMAL_CANDIDATE_BATCHES
        or int(contract.get("candidate_parallelism") or 0) != 2
    ):
        raise RuntimeError("parallel v10 scheduler contract differs")
    install()
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())

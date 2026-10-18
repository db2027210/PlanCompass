#!/usr/bin/env python3
"""Path-stable v11 entrypoint for Algorithm B parallel 2+2+1 replay."""

from __future__ import annotations

import copy
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import (
    run_mysql_four_scene_algorithm_b_independent_parallel_221_resume_v10
    as predecessor,
)


base = predecessor.base
alg = predecessor.alg
runtime = predecessor.runtime
SCHEDULER_CONTRACT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_b_parallel_221_20260818_v11"
    / "algorithm_b_parallel_221_contract.json"
)


def state_document_v11(run_id: str):
    original_contract = predecessor.SCHEDULER_CONTRACT
    try:
        predecessor.SCHEDULER_CONTRACT = SCHEDULER_CONTRACT
        document = predecessor.state_document_v10(run_id)
    finally:
        predecessor.SCHEDULER_CONTRACT = original_contract
    document["technical_continuation"].update({
        "kind": "post_outer1_score_interface_parallel_221_resume_v11",
        "v10_prestart_failure_reused": False,
        "v10_prestart_failure_started_workload": False,
    })
    document["formal_candidate_batches"] = copy.deepcopy(
        predecessor.predecessor_221.FORMAL_CANDIDATE_BATCHES
    )
    document["controller_source_path"] = str(Path(__file__).resolve())
    document["controller_source_sha256"] = alg.sha(Path(__file__).resolve())
    return document


def validate_contract():
    if not SCHEDULER_CONTRACT.is_file() or SCHEDULER_CONTRACT.is_symlink():
        raise RuntimeError(f"sealed v11 scheduler contract absent: {SCHEDULER_CONTRACT}")
    contract = alg.load(SCHEDULER_CONTRACT)
    if (
        contract.get("release_version") != "v11"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("candidate_batches") != [[1, 2], [3, 4], [5]]
        or int(contract.get("candidate_parallelism") or 0) != 2
    ):
        raise RuntimeError("parallel v11 scheduler contract differs")
    return contract


def install() -> None:
    base.FreshBaselineObjective.score = (
        predecessor.predecessor_221._score_with_resource_payload
    )
    runtime._build_single_candidate_assignments = (
        predecessor.predecessor_221._build_assignments_221
    )
    runtime._measure_batch_with_fair_coordination = (
        predecessor.predecessor_221._measure_221
    )
    base.state_document = state_document_v11


def main() -> int:
    validate_contract()
    install()
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())

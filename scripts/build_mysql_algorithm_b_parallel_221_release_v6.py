#!/usr/bin/env python3
"""Build the v14 outer-4 quiet-gate continuation release."""

import copy
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_b_parallel_221_release_v1 as predecessor


SOURCE = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v13_formal_parallel221_outer3_rank3_recovered_resume_20260818_attempt007"
)
V11 = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v11_formal_independent_parallel221_outer1_score_resume_20260818_attempt003"
)
RECOVERY = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v13_b_partial_xp_reset_recovery_20260818_attempt006"
)
predecessor.OUTPUT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_b_parallel_221_20260818_v14"
    / "algorithm_b_parallel_221_contract.json"
)
predecessor.CONTROLLER = (
    ROOT
    / "scripts/run_mysql_four_scene_algorithm_b_independent_parallel_221_resume_v14.py"
)
predecessor.LAUNCHER = (
    ROOT
    / "scripts/launch_mysql_four_scene_algorithm_b_independent_parallel_221_resume_v14.py"
)
predecessor.TEST = ROOT / "tests/test_mysql_algorithm_b_parallel_221_resume_v14.py"
original_atomic = predecessor.atomic


def atomic_v14(path, value):
    document = copy.deepcopy(dict(value))
    document["release_version"] = "v14"
    bindings = document["source_bindings"]
    bindings.update({
        "v14_predecessor_controller": predecessor.binding(
            ROOT / "scripts/run_mysql_four_scene_algorithm_b_independent_parallel_221_resume_v13.py"
        ),
        "v13_failure_state": predecessor.binding(SOURCE / "run_state.json"),
        "v13_outer4_rankings": predecessor.binding(
            SOURCE / "outer_004/predicted_rankings.json"
        ),
        "v13_outer4_quiet_gate": predecessor.binding(
            SOURCE / "outer_004/top5_batch_1_cross_project_wave_quiet_gate_attempt_001.json"
        ),
        "v13_xp_recovery": predecessor.binding(RECOVERY / "recovered_xp_binding.json"),
        "v13_prepared_binding": predecessor.binding(RECOVERY / "prepared_binding.json"),
        "v13_benchmark_accounts": predecessor.binding(
            RECOVERY / "benchmark_accounts_binding.json"
        ),
        **{
            f"v11_outer2_{source.name.removesuffix('.json')}_{index}":
                predecessor.binding(source)
            for index, source in enumerate(sorted(
                list((V11 / "outer_002").glob("*.decision.json"))
                + list((V11 / "outer_002").glob("top5_batch_*/batch_result.json"))
            ), 1)
        },
        **{
            f"v13_outer3_{source.name.removesuffix('.json')}_{index}":
                predecessor.binding(source)
            for index, source in enumerate(sorted(
                list((SOURCE / "outer_003").glob("*.decision.json"))
                + list((SOURCE / "outer_003").glob("top5_batch_*/batch_result.json"))
            ), 1)
        },
    })
    document["continuation"].update({
        "source_run_root": str(SOURCE),
        "completed_outer_decisions_reconstructed": [1, 2, 3],
        "outer3_completed_candidate_ranks_reused": [1, 2, 3, 4, 5],
        "failed_outer4_rank1_measurement_reused": False,
        "failed_outer4_rank1_workload_started": False,
        "failure_class": "coordinated_quiet_window_timeout",
        "failure_gate_maximum_wait_seconds": 60,
        "fresh_gate_required": True,
        "resume_boundary": "outer_004_rank_1_preworkload_quiet_gate",
        "candidate_or_convergence_state_mutated_by_failure": False,
        "xp_authority_recovered": True,
    })
    original_atomic(path, document)


predecessor.atomic = atomic_v14


if __name__ == "__main__":
    raise SystemExit(predecessor.main())

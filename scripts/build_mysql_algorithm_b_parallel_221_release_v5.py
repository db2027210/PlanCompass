#!/usr/bin/env python3
"""Build the v13 post-partial-XP-reset continuation release."""

import copy
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_b_parallel_221_release_v1 as predecessor


SOURCE = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v11_formal_independent_parallel221_outer1_score_resume_20260818_attempt003"
)
V12_FAILURE = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v12_formal_parallel221_outer3_rank3_reset_resume_20260818_attempt004"
    / "run_state.json"
)
RECOVERY_ROOT = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v13_b_partial_xp_reset_recovery_20260818_attempt006"
)
predecessor.OUTPUT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_b_parallel_221_20260818_v13"
    / "algorithm_b_parallel_221_contract.json"
)
predecessor.CONTROLLER = (
    ROOT
    / "scripts/run_mysql_four_scene_algorithm_b_independent_parallel_221_resume_v13.py"
)
predecessor.LAUNCHER = (
    ROOT
    / "scripts/launch_mysql_four_scene_algorithm_b_independent_parallel_221_resume_v13.py"
)
predecessor.TEST = ROOT / "tests/test_mysql_algorithm_b_parallel_221_resume_v13.py"
original_atomic = predecessor.atomic


def atomic_v13(path, value):
    document = copy.deepcopy(dict(value))
    document["release_version"] = "v13"
    bindings = document["source_bindings"]
    bindings.update({
        "reset_v8": predecessor.binding(
            ROOT / "scripts/reset_mysql_xp_data_planes_v8.py"
        ),
        "partial_xp_recovery_source": predecessor.binding(
            ROOT / "scripts/recover_mysql_algorithm_b_v12_partial_xp_reset_v1.py"
        ),
        "v11_failure_state": predecessor.binding(SOURCE / "run_state.json"),
        "v11_outer3_rankings": predecessor.binding(
            SOURCE / "outer_003/predicted_rankings.json"
        ),
        "v11_outer3_rank1": predecessor.binding(
            SOURCE / "outer_003/top5_batch_1/batch_result.json"
        ),
        "v11_outer3_rank2": predecessor.binding(
            SOURCE / "outer_003/top5_batch_2/batch_result.json"
        ),
        "v12_prestart_failure_state": predecessor.binding(V12_FAILURE),
        "v13_xp_recovery": predecessor.binding(
            RECOVERY_ROOT / "recovered_xp_binding.json"
        ),
        "v13_prepared_binding": predecessor.binding(
            RECOVERY_ROOT / "prepared_binding.json"
        ),
        "v13_benchmark_accounts": predecessor.binding(
            RECOVERY_ROOT / "benchmark_accounts_binding.json"
        ),
        **{
            f"v11_outer2_{source.name.removesuffix('.json')}_{index}":
                predecessor.binding(source)
            for index, source in enumerate(sorted(
                list((SOURCE / "outer_002").glob("*.decision.json"))
                + list((SOURCE / "outer_002").glob("top5_batch_*/batch_result.json"))
            ), 1)
        },
    })
    document["continuation"].update({
        "source_run_root": str(SOURCE),
        "completed_outer_decisions_reconstructed": [1, 2],
        "outer3_completed_candidate_ranks_reused": [1, 2],
        "failed_outer3_rank3_measurement_reused": False,
        "failed_outer3_rank3_workload_started": False,
        "failure_phase": "outer_003_rank_3_xp_reset",
        "credential_metadata_timeout_seconds": 120,
        "v12_prestart_failure_workload_started": False,
        "xp_authority_recovered": True,
        "xp_workers_recovered": ["xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2"],
        "job_tpch_container_mutation": False,
        "formal_state_credit_increment_during_recovery": 0,
        "resume_boundary": "outer_003_rank_3_preworkload",
    })
    original_atomic(path, document)


predecessor.atomic = atomic_v13


if __name__ == "__main__":
    raise SystemExit(predecessor.main())

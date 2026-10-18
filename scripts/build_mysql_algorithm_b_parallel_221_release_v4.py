#!/usr/bin/env python3
"""Build the v12 outer-3 reset-timeout continuation release."""

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
predecessor.OUTPUT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_b_parallel_221_20260818_v12"
    / "algorithm_b_parallel_221_contract.json"
)
predecessor.CONTROLLER = (
    ROOT
    / "scripts/run_mysql_four_scene_algorithm_b_independent_parallel_221_resume_v12.py"
)
predecessor.LAUNCHER = (
    ROOT
    / "scripts/launch_mysql_four_scene_algorithm_b_independent_parallel_221_resume_v12.py"
)
predecessor.TEST = ROOT / "tests/test_mysql_algorithm_b_parallel_221_resume_v12.py"
original_atomic = predecessor.atomic


def atomic_v12(path, value):
    document = copy.deepcopy(dict(value))
    document["release_version"] = "v12"
    bindings = document["source_bindings"]
    bindings.update({
        "reset_v8": predecessor.binding(
            ROOT / "scripts/reset_mysql_xp_data_planes_v8.py"
        ),
        "v11_failure_state": predecessor.binding(SOURCE / "run_state.json"),
        "v11_outer3_rankings": predecessor.binding(
            SOURCE / "outer_003/predicted_rankings.json"
        ),
        **{
            f"v11_outer2_{path.name.removesuffix('.json')}_{index}":
                predecessor.binding(path)
            for index, path in enumerate(sorted(
                list((SOURCE / "outer_002").glob("*.decision.json"))
                + list((SOURCE / "outer_002").glob("top5_batch_*/batch_result.json"))
            ), 1)
        },
        "v11_outer3_rank1": predecessor.binding(
            SOURCE / "outer_003/top5_batch_1/batch_result.json"
        ),
        "v11_outer3_rank2": predecessor.binding(
            SOURCE / "outer_003/top5_batch_2/batch_result.json"
        ),
    })
    document["continuation"].update({
        "source_run_root": str(SOURCE),
        "completed_outer_decisions_reconstructed": [1, 2],
        "outer3_completed_candidate_ranks_reused": [1, 2],
        "failed_outer3_rank3_measurement_reused": False,
        "failed_outer3_rank3_workload_started": False,
        "failure_phase": "outer_003_rank_3_xp_reset",
        "failure_class": "credential_metadata_chown_timeout",
        "credential_metadata_timeout_seconds_before": 30,
        "credential_metadata_timeout_seconds_after": 120,
    })
    original_atomic(path, document)


predecessor.atomic = atomic_v12


if __name__ == "__main__":
    raise SystemExit(predecessor.main())

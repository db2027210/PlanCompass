#!/usr/bin/env python3
"""Resume with one extra zero-credit confirmation opportunity.

The accepted-round target remains 10 and convergence still requires two
consecutive accepted intervals at width <=1%.  Outer 17 proved that two
post-target acceptances can be consumed while establishing a new interval;
allow a third real Top-5 confirmation so the next outer can verify it.
"""

from __future__ import annotations

from pathlib import Path
import copy
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v20 as confirmation
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v23 as durable
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v28 as v28


runtime = v28.runtime
ORIGINAL_STATE_DOCUMENT = v28.state_document
MAX_POST_TARGET_ACCEPTED_CONFIRMATIONS_V29 = 3


def state_document(**kwargs: Any) -> dict[str, Any]:
    document = ORIGINAL_STATE_DOCUMENT(**kwargs)
    document["attempt_budget"]["post_target_stability_confirmation"].update({
        "maximum_accepted_confirmations": (
            MAX_POST_TARGET_ACCEPTED_CONFIRMATIONS_V29
        ),
        "third_confirmation_reason": (
            "an accepted confirmation may establish a new <=1% interval; "
            "one subsequent accepted outer is still required to prove stability"
        ),
    })
    document["post_target_stability_confirmation_release_v29"] = {
        "release_version": "v29",
        "source_path": str(Path(__file__).resolve()),
        "source_sha256": runtime.alg.sha(Path(__file__).resolve()),
        "formal_accepted_round_target": 10,
        "accepted_state_credit_capped_at_target": True,
        "maximum_post_target_accepted_confirmations": 3,
        "required_stable_rounds": 2,
        "maximum_interval_ratio": 1.01,
        "top5_real_replay_required": True,
        "workload_scoring_and_binary_shrink_changed": False,
    }
    return document


def validate_three_confirmation_release() -> None:
    sample = {
        "schema_version": durable.STATE_SCHEMA,
        "post_target_stability_confirmation_release": {
            "release_version": "v20"
        },
        "accepted_rounds": {
            "job_memory": 10,
            "tpch_sf4_memory": 10,
            "hybench_xp_cpu": 9,
            "hybench_xp_io": 8,
        },
        durable.COUNTER_FIELD: {
            "job_memory": 0,
            "tpch_sf4_memory": 0,
            "hybench_xp_cpu": 0,
            "hybench_xp_io": 0,
        },
    }
    accepted = durable._rehydrate(copy.deepcopy(sample))["accepted_rounds"]
    if accepted["job_memory"] >= 10:
        raise RuntimeError("third-confirmation initial open-state test failed")
    for ordinal in (1, 2):
        accepted["job_memory"] += 1
        if (
            accepted.confirmation_acceptances["job_memory"] != ordinal
            or accepted["job_memory"] >= 10
        ):
            raise RuntimeError(
                f"third-confirmation open-state test {ordinal} failed"
            )
    accepted["job_memory"] += 1
    if (
        int(accepted["job_memory"]) != 10
        or accepted.confirmation_acceptances["job_memory"] != 3
        or not (accepted["job_memory"] >= 10)
    ):
        raise RuntimeError("third-confirmation terminal-state test failed")


def main() -> int:
    confirmation.MAX_POST_TARGET_ACCEPTED_CONFIRMATIONS = (
        MAX_POST_TARGET_ACCEPTED_CONFIRMATIONS_V29
    )
    durable.validate_release = validate_three_confirmation_release
    v28.state_document = state_document
    return v28.main()


if __name__ == "__main__":
    raise SystemExit(main())

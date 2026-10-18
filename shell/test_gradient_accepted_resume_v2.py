#!/usr/bin/env python3
"""End-to-end state-contract tests for generic accepted checkpoint v2."""

from __future__ import annotations

import hashlib
import json
import math
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gradient_descent_optimizer import ParameterRange  # noqa: E402
from parameter_optimizer import (  # noqa: E402
    _gradient_restored_incumbent_decision,
    _gradient_resume_requires_online_replay_state,
    _gradient_resume_restores_accepted_candidate,
    _gradient_resume_skips_round0_subset_search,
    _gradient_validate_accepted_resume_v2_state,
    _load_sha256_pinned_json_object,
)


INITIAL_RANGES = {
    "seq_page_cost": ParameterRange(0.1, 10.0),
    "parallel_setup_cost": ParameterRange(1000.0, 1000.0),
}
ACTIVE_PARAMS = ["seq_page_cost"]


def checkpoint(
    *,
    accepted_count: int,
    completed_explorations: int,
    resumed_max: float,
) -> dict:
    return {
        "schema_version": 1,
        "kind": "gradient_accepted_resume_checkpoint_v2",
        "counters": {
            "accepted_shrink_count": accepted_count,
            "completed_outer_explorations": completed_explorations,
        },
        "next_feedback_round": completed_explorations,
        "resume_contract": {
            "resume_at_accepted_boundary": True,
            "direct_range_restore": True,
            "do_not_rerun_prior_rounds": True,
            "do_not_repeat_accepted_shrink": True,
        },
        "accepted_params": {
            "seq_page_cost": math.sqrt(0.1 * resumed_max),
            "parallel_setup_cost": 1000.0,
        },
        "expected_range_state_after_accept": {
            "seq_page_cost": {
                "min_value": 0.1,
                "max_value": resumed_max,
                "transform": "log",
            },
            "parallel_setup_cost": {
                "min_value": 1000.0,
                "max_value": 1000.0,
                "transform": "linear",
            },
        },
    }


def expect_runtime_error(func, contains: str) -> None:
    try:
        func()
    except RuntimeError as exc:
        assert contains in str(exc), str(exc)
    else:
        raise AssertionError(f"expected RuntimeError containing {contains!r}")


def main() -> int:
    # Clean accepted boundary after round 0: restore count/ranges and start at 1.
    count1 = checkpoint(
        accepted_count=1,
        completed_explorations=1,
        resumed_max=1.0,
    )
    restored1 = _gradient_validate_accepted_resume_v2_state(
        count1,
        INITIAL_RANGES,
        ACTIVE_PARAMS,
        persisted_accepted_count=1,
    )
    assert restored1["accepted_shrink_count"] == 1
    assert restored1["next_feedback_round"] == 1
    assert math.isclose(
        restored1["normalized_width_by_param"]["seq_page_cost"],
        0.5,
        rel_tol=0.0,
        abs_tol=1e-12,
    )
    assert _gradient_resume_skips_round0_subset_search(count1)
    assert _gradient_resume_restores_accepted_candidate(count1)
    assert not _gradient_resume_requires_online_replay_state(count1)
    assert _gradient_resume_requires_online_replay_state(
        {"kind": "gradient_partial_round_resume_checkpoint_v1"}
    )
    restored_decision = _gradient_restored_incumbent_decision(
        {
            "db_qps": 123.0,
            "utilization_pct": {"cpu": 45.0, "memory": 50.0, "io": 87.0},
        },
        target_resource="io",
        db_qps_key="db_qps",
    )
    assert restored_decision.accepted
    assert restored_decision.reason == (
        "sealed_accepted_checkpoint_restored_as_incumbent"
    )
    assert restored_decision.incumbent_db_qps == 123.0
    assert restored_decision.candidate_db_qps == 123.0
    assert restored_decision.incumbent_target_utilization == 87.0
    assert restored_decision.candidate_target_utilization == 87.0
    assert restored_decision.candidate_non_target_utilization == {
        "cpu": 45.0,
        "memory": 50.0,
    }

    unsafe_restored_decision = _gradient_restored_incumbent_decision(
        {
            "db_qps": 123.0,
            "utilization_pct": {"cpu": 80.0, "memory": 50.0, "io": 87.0},
        },
        target_resource="io",
        db_qps_key="db_qps",
    )
    assert not unsafe_restored_decision.accepted
    assert unsafe_restored_decision.reason == (
        "sealed_checkpoint_non_target_resource_is_bottleneck"
    )
    assert unsafe_restored_decision.candidate_non_target_bottlenecks == ["cpu"]

    # Two accepted shrinks with one rejected exploration in between: restore the
    # exact quarter-width interval and continue at round index 3.
    count2 = checkpoint(
        accepted_count=2,
        completed_explorations=3,
        resumed_max=math.sqrt(0.1),
    )
    restored2 = _gradient_validate_accepted_resume_v2_state(
        count2,
        INITIAL_RANGES,
        ACTIVE_PARAMS,
        persisted_accepted_count=2,
    )
    assert restored2["accepted_shrink_count"] == 2
    assert restored2["next_feedback_round"] == 3
    assert math.isclose(
        restored2["normalized_width_by_param"]["seq_page_cost"],
        0.25,
        rel_tol=0.0,
        abs_tol=1e-12,
    )

    bad_range = checkpoint(
        accepted_count=2,
        completed_explorations=2,
        resumed_max=1.0,
    )
    expect_runtime_error(
        lambda: _gradient_validate_accepted_resume_v2_state(
            bad_range,
            INITIAL_RANGES,
            ACTIVE_PARAMS,
            persisted_accepted_count=2,
        ),
        "normalized width mismatch",
    )
    expect_runtime_error(
        lambda: _gradient_validate_accepted_resume_v2_state(
            count2,
            INITIAL_RANGES,
            ACTIVE_PARAMS,
            persisted_accepted_count=1,
        ),
        "persisted counter mismatch",
    )

    with tempfile.TemporaryDirectory(prefix="gradient_resume_v2_") as tmp:
        checkpoint_path = Path(tmp) / "checkpoint.json"
        checkpoint_path.write_text(
            json.dumps(count2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        digest = hashlib.sha256(checkpoint_path.read_bytes()).hexdigest()
        loaded, actual = _load_sha256_pinned_json_object(
            checkpoint_path,
            digest,
            description="test checkpoint",
        )
        assert loaded == count2
        assert actual == digest
        expect_runtime_error(
            lambda: _load_sha256_pinned_json_object(
                checkpoint_path,
                "0" * 64,
                description="test checkpoint",
            ),
            "hash mismatch",
        )

    print("gradient accepted resume v2 tests passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

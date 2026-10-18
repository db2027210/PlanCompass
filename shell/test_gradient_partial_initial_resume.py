#!/usr/bin/env python3
"""Pure contract checks for the uncommitted round-0 replay-prefix resume."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from parameter_optimizer import (  # noqa: E402
    _gradient_expected_normalized_width,
    _gradient_is_partial_initial_round_checkpoint,
    _gradient_replay_stage_action,
    _gradient_resume_range_record_is_valid,
    _gradient_resume_skips_round0_subset_search,
    _resolve_partial_resume_rank_prefix,
)


def main() -> int:
    checkpoint = {
        "kind": "gradient_partial_initial_round_resume_checkpoint_v1",
        "resume_contract": {"do_not_rerun_round0": True},
    }
    assert _gradient_is_partial_initial_round_checkpoint(checkpoint)
    assert _gradient_resume_skips_round0_subset_search(checkpoint)
    assert not _gradient_is_partial_initial_round_checkpoint(
        {"kind": "gradient_partial_round_resume_checkpoint_v1"}
    )

    replayed = [{"p": 1.0}]
    assert _resolve_partial_resume_rank_prefix(
        [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
        replayed,
        [1],
        2,
    ) == [1]
    try:
        _resolve_partial_resume_rank_prefix(
            [1, 2, 3, 4, 5],
            replayed,
            [1],
            3,
        )
    except RuntimeError:
        pass
    else:
        raise AssertionError("a non-contiguous next rank must fail closed")

    assert _gradient_replay_stage_action(
        validated_count=5,
        eligible_count=1,
        available_count=10,
        initial_top_k=5,
        second_top_k=10,
        max_top_k=10,
    ) == "accept_current_batch"
    assert _gradient_replay_stage_action(
        validated_count=5,
        eligible_count=0,
        available_count=10,
        initial_top_k=5,
        second_top_k=10,
        max_top_k=10,
    ) == "expand_second_batch"
    assert _gradient_expected_normalized_width(5) == 1.0 / 32.0

    fixed_inactive = {
        "min_value": 1000.0,
        "max_value": 1000.0,
        "transform": "linear",
    }
    assert _gradient_resume_range_record_is_valid(
        "parallel_setup_cost",
        fixed_inactive,
        1000.0,
        ["seq_page_cost"],
    )
    assert not _gradient_resume_range_record_is_valid(
        "parallel_setup_cost",
        fixed_inactive,
        999.0,
        ["seq_page_cost"],
    )
    assert not _gradient_resume_range_record_is_valid(
        "parallel_setup_cost",
        fixed_inactive,
        1000.0,
        ["parallel_setup_cost"],
    )
    assert _gradient_resume_range_record_is_valid(
        "seq_page_cost",
        {"min_value": 0.1, "max_value": 100.0, "transform": "log"},
        1.0,
        ["seq_page_cost"],
    )
    print("gradient partial initial resume tests passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Minimal no-workload tests for formal final-feedback physical accounting."""

from __future__ import annotations

import tempfile
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from parameter_optimizer import (
    FormalFeedbackPhysicalBudgetExhausted,
    reserve_formal_feedback_physical_call,
)


def _read_int(path: Path) -> int:
    return int(path.read_text(encoding="utf-8").strip())


def main() -> None:
    disabled = reserve_formal_feedback_physical_call(
        {},
        round_index=0,
        candidate_index=1,
        replicate_index=None,
    )
    assert disabled["enabled"] is False
    assert disabled["reserved"] is False

    with tempfile.TemporaryDirectory(
        prefix="formal_feedback_budget_test_"
    ) as temporary_dir:
        root = Path(temporary_dir)
        formal_counter = root / "formal.txt"
        global_counter = root / "global.txt"
        accepted_counter = root / "accepted.txt"
        formal_counter.write_text("8\n", encoding="utf-8")
        global_counter.write_text("8\n", encoding="utf-8")
        accepted_counter.write_text("4\n", encoding="utf-8")
        protocol = {
            "formal_search_replay_counter_path": str(formal_counter),
            "global_replay_counter_path": str(global_counter),
            "formal_search_replay_count_start": 8,
            "formal_search_max_replay_top_k": 5,
            "maximum_resource_qualified_real_replays_per_batch": 5,
            "formal_search_accepted_counter_path": str(accepted_counter),
        }

        for candidate_index in range(1, 6):
            audit = reserve_formal_feedback_physical_call(
                protocol,
                round_index=0,
                candidate_index=candidate_index,
                replicate_index=1,
            )
            assert audit["reserved"] is True
            assert audit["batch_physical_call_ordinal"] == candidate_index
            assert audit["accepted_counter_updated"] is False

        assert _read_int(formal_counter) == 13
        assert _read_int(global_counter) == 13
        assert _read_int(accepted_counter) == 4

        try:
            reserve_formal_feedback_physical_call(
                protocol,
                round_index=1,
                candidate_index=None,
                replicate_index=1,
            )
        except FormalFeedbackPhysicalBudgetExhausted as exc:
            assert exc.code == 75
            assert exc.audit["batch_physical_calls_before"] == 5
            assert exc.audit["batch_physical_call_cap"] == 5
            assert exc.audit["stop_before_apply_params"] is True
            assert exc.audit["stop_before_workload"] is True
        else:
            raise AssertionError("sixth physical call was not rejected")

        assert _read_int(formal_counter) == 13
        assert _read_int(global_counter) == 13
        assert _read_int(accepted_counter) == 4

    source = (PROJECT_ROOT / "parameter_optimizer.py").read_text(encoding="utf-8")
    function_start = source.index("    def _run_feedback_final_validation_once(")
    function_end = source.index(
        "    formal_replicate_protocol =",
        function_start,
    )
    function_source = source[function_start:function_end]
    reserve_position = function_source.index(
        "physical_reservation = reserve_formal_feedback_physical_call("
    )
    apply_position = function_source.index("apply_params_via_alter_system(")
    workload_position = function_source.index(
        "_run_with_db_view_monitor(label_key, _run_feedback_runtime_monitor)"
    )
    assert reserve_position < apply_position < workload_position

    print("formal feedback physical budget tests passed", flush=True)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Tests for Algorithm C pilot/formal execution-role contracts."""

from __future__ import annotations

import inspect

from algorithm_c_execution_role import (
    FORMAL,
    FORMAL_ROLE,
    PILOT,
    PILOT_ROLE,
    resolve_execution_role,
)


def main() -> int:
    assert resolve_execution_role({}) == PILOT
    assert resolve_execution_role({"execution_role": PILOT_ROLE}) == PILOT
    assert PILOT.search_batch_kind == "trial_search"
    assert PILOT.result_filename == "budgeted_trial_result.json"
    assert PILOT.interrupted_resume_allowed is True

    formal_policy = {
        "execution_role": FORMAL_ROLE,
        "algorithm_revision": "coverage_revisit_archive_v2",
        "algorithm_design_sha256": "a" * 64,
    }
    assert resolve_execution_role(formal_policy) == FORMAL
    assert FORMAL.search_batch_kind == "formal_search"
    assert FORMAL.finalist_batch_kind(2) == "formal_finalist_rep2"
    assert FORMAL.post_baseline_batch_kind(2) == "formal_post_baseline_rep2"
    assert FORMAL.result_filename == "budgeted_formal_result.json"
    assert FORMAL.interrupted_resume_allowed is False
    assert FORMAL.historical_comparison_provisional is False

    invalid_policies = (
        {"execution_role": "unknown"},
        {"execution_role": FORMAL_ROLE, "algorithm_design_sha256": "a" * 64},
        {
            "execution_role": FORMAL_ROLE,
            "algorithm_revision": "v2",
            "algorithm_design_sha256": "not-a-digest",
        },
    )
    for policy in invalid_policies:
        try:
            resolve_execution_role(policy)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid execution role policy was accepted: {policy}")

    for method in (FORMAL.finalist_batch_kind, FORMAL.post_baseline_batch_kind):
        try:
            method(0)
        except ValueError:
            pass
        else:
            raise AssertionError("non-positive validation repetition must fail")

    source = inspect.getsource(resolve_execution_role).lower()
    for forbidden in ("job_memory", "tpch_memory", "xp_cpu", "xp_io", "algorithm_a", "algorithm_b"):
        assert forbidden not in source

    print("PASS: Algorithm C execution-role contract", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

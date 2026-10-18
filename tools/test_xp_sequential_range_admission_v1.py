#!/usr/bin/env python3
"""Regression test for XP sequential accepted-range admission."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path


def candidate(identifier: str, role: str) -> dict:
    return {
        "selection_role": role,
        "three_level_fingerprint": {"id": identifier},
    }


def replay_item(identifier: str, score: float, value: float) -> dict:
    return {
        "fingerprint_id": identifier,
        "actual_score": score,
        "candidate_params": {"p": value},
        "execution": "ran_once",
        "physical_call": True,
        "technical_failure": False,
        "rejected": False,
        "accepted": False,
        "state_commit_eligible": False,
        "incumbent_update_applied": False,
        "range_update_applied": False,
        "gradient_update_applied": False,
        "state_patch": {},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.bundle_root.resolve()))

    from xp_unattended_adapters import _promote_continuation_acceptance  # type: ignore
    from xp_unattended_continuation import (  # type: ignore
        CONTRACT,
        REQUIRED_CPUSET,
        _apply_accepted_patches,
        validate_batch_summary,
    )

    selection = {
        "top5": [
            candidate("fp1", "trusted_calibrated_rank"),
            candidate("fp2", "trusted_calibrated_rank"),
            candidate("fp3", "trusted_calibrated_rank"),
            candidate("fp4", "maximin_uncertain_probe"),
            candidate("fp5", "maximin_uncertain_probe"),
        ]
    }
    items = [
        replay_item("fp1", 11.0, 4.0),
        replay_item("fp2", 12.0, 8.0),
        replay_item("fp3", 10.5, 3.0),
        replay_item("fp4", 20.0, 9.0),
        replay_item("fp5", 21.0, 2.0),
    ]
    summary = {
        "successful": copy.deepcopy(items),
        "ledger": copy.deepcopy(items),
        "qps_used_for_inner_scoring": False,
        "qps_role": "outer_acceptance_and_audit_only",
        "cpuset_cpus": REQUIRED_CPUSET,
        "baseline_restore_verified": True,
    }
    environment = {
        "XP_INCUMBENT_SCORE": "10.0",
        "XP_INCUMBENT_PARAMS_JSON": json.dumps({"p": 5.0}),
        "XP_PARAMETER_RANGES_JSON": json.dumps(
            {"p": {"min_value": 0.0, "max_value": 10.0}}
        ),
        "XP_ACCEPTED_REMAINING": "10",
    }
    promoted = _promote_continuation_acceptance(
        summary, selection, environment
    )
    promoted.update(
        {
            "contract": CONTRACT,
            "batch_mode": "formal",
            "diagnostic_only": False,
        }
    )
    checked = validate_batch_summary(promoted, selection)
    assert promoted["accepted_count_delta"] == 1
    assert len(checked["accepted"]) == 1
    assert len(checked["rejected"]) == 2
    assert checked["accepted"][0]["fingerprint_id"] == "fp1"
    assert checked["accepted"][0]["state_patch"]["incumbent_score"] == 11.0
    assert checked["rejected"][0]["fingerprint_id"] == "fp2"
    assert checked["rejected"][0]["algorithmic_rejection_reason"] == (
        "outside_current_accepted_range"
    )
    assert checked["rejected"][0]["state_patch"] == {}
    assert all(
        item.get("accepted") is not True
        for item in promoted["ledger"]
        if item["fingerprint_id"] in {"fp4", "fp5"}
    )
    state = {
        "incumbent_score": 10.0,
        "incumbent_params": {"p": 5.0},
        "parameter_ranges": {"p": {"min_value": 0.0, "max_value": 10.0}},
    }
    _apply_accepted_patches(state, checked["accepted"])
    assert state["incumbent_score"] == 11.0
    assert state["incumbent_params"] == {"p": 4.0}
    assert state["parameter_ranges"] == {
        "p": {"min_value": 0.0, "max_value": 5.0}
    }
    print(
        json.dumps(
            {
                "status": "passed",
                "accepted": len(checked["accepted"]),
                "rejected": len(checked["rejected"]),
                "probes_state_neutral": True,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

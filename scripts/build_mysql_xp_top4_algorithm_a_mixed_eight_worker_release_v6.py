#!/usr/bin/env python3
"""Freeze XP8 Algorithm-A v6 zero-baseline continuation release."""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name(
    "build_mysql_xp_top4_algorithm_a_mixed_eight_worker_release_v5.py"
)
text = SOURCE.read_text(encoding="utf-8")
replacements = {
    "frozen_mysql_xp_top4_algorithm_a_mixed_eight_worker_20260827_v5":
        "frozen_mysql_xp_top4_algorithm_a_mixed_eight_worker_20260827_v6",
    "run_mysql_algorithm_a_xp_top4_mixed_eight_worker_v5.py":
        "run_mysql_algorithm_a_xp_top4_mixed_eight_worker_v6.py",
}
for old, new in replacements.items():
    if text.count(old) != 1:
        raise RuntimeError(f"XP8 A release v6 predecessor differs: {old}")
    text = text.replace(old, new)
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._build_mysql_xp8_a_release_v6_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)
builder = implementation["implementation"]["builder"]
builder.SOURCES.update({
    "builder": Path(__file__).resolve(),
    "scene_launcher": Path(__file__).resolve().with_name(
        "launch_mysql_algorithm_a_xp_top4_mixed_eight_worker_v6.py"
    ),
    "b_to_a_monitor": Path(__file__).resolve().with_name(
        "monitor_mysql_b_then_a_mixed_eight_worker_scene_v7.py"
    ),
    "zero_baseline_runtime": Path(__file__).resolve().with_name(
        "run_mysql_algorithm_a_xp_top4_mixed_eight_worker_v6.py"
    ),
    "cpu_wave1_resume": Path(__file__).resolve().with_name(
        "run_mysql_algorithm_a_xp_cpu_wave1_zero_baseline_resume_v1.py"
    ),
})
predecessor_main = implementation["implementation"]["main"]


def main() -> int:
    result = int(predecessor_main())
    contract_path = builder.CONTRACT
    contract = builder.load(contract_path)
    contract["zero_baseline_normalization_policy"] = {
        "negative_resource_fails_closed": True,
        "both_zero_semantics": "canonical_ratio_one",
        "positive_candidate_after_zero_baseline": "raw_frozen_envelope_utilization",
        "qps_normalization_unchanged": True,
        "target_resource_acceptance_policy_unchanged": True,
    }
    contract["technical_measurement_reuse_policy"] = {
        "source_run": "v566",
        "complete_valid_baseline_reused": True,
        "complete_valid_round_001_wave_001_reused": True,
        "partial_or_failed_measurement_reused": False,
        "accepted_or_rejected_credit_added": 0,
        "candidate_vector_identity_required": True,
    }
    builder.atomic(contract_path, contract)
    return result


if __name__ == "__main__":
    raise SystemExit(main())

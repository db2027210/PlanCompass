#!/usr/bin/env python3
"""Freeze IO Outer-7 invalid-skip B v6 with split decision roots."""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name(
    "build_mysql_xp_top4_algorithm_b_mixed_eight_worker_release_v2.py"
)
text = SOURCE.read_text(encoding="utf-8")
replacements = {
    "frozen_mysql_xp_top4_algorithm_b_mixed_eight_worker_20260827_v2":
        "frozen_mysql_xp_top4_algorithm_b_mixed_eight_worker_20260827_v6",
    '"runtime": ROOT / "scripts/run_mysql_algorithm_b_xp_top4_mixed_eight_worker_v2.py"':
        '"runtime": ROOT / "scripts/run_mysql_algorithm_b_xp_top4_mixed_eight_worker_v6.py"',
    '"boundary_controller": ROOT / "scripts/run_mysql_algorithm_b_top4_mixed_eight_worker_boundary_resume_v2.py"':
        '"boundary_controller": ROOT / "scripts/run_mysql_algorithm_b_top4_mixed_eight_worker_io_outer7_invalid_skip_resume_v3.py"',
}
for old, new in replacements.items():
    if text.count(old) != 1:
        raise RuntimeError(f"XP8 B release v6 predecessor differs: {old}")
    text = text.replace(old, new)
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._build_mysql_xp8_b_release_v6_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)
inner = implementation["implementation"]
inner["SOURCES"].update({
    "builder": Path(__file__).resolve(),
    "invalid_candidate_runtime_predecessor": Path(__file__).resolve().with_name(
        "run_mysql_algorithm_b_xp_top4_mixed_eight_worker_v5.py"
    ),
})
_original_main = inner["main"]


def main() -> int:
    result = int(_original_main())
    contract_path = inner["OUTPUT"] / "algorithm_b_top4_contract.json"
    contract = inner["load"](contract_path)
    contract["technical_invalid_candidate_policy"] = {
        "policy_id": "mysql_xp_hard_max_resource_instability_skip_v1",
        "maximum_measurement_seconds": 900,
        "only_failed_hard_gate": "target_resource_stability_passed",
        "coverage_qps_liveness_restart_fingerprint_gates_preserved": True,
        "invalid_measurement_data_used": False,
        "accepted_or_rejected_credit_for_partial_failed_outer": 0,
        "global_campaign_abort": False,
    }
    contract["continuation_decision_roots"] = {
        "outer_001": "v505",
        "outer_002_to_005": "v521",
        "outer_006_to_007": "v554",
    }
    inner["atomic"](contract_path, contract)
    return result


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Freeze XP8 Algorithm-B v8 unlocked-reset successor."""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name(
    "build_mysql_xp_top4_algorithm_b_mixed_eight_worker_release_v7.py"
)
text = SOURCE.read_text(encoding="utf-8")
replacements = {
    "frozen_mysql_xp_top4_algorithm_b_mixed_eight_worker_20260827_v7":
        "frozen_mysql_xp_top4_algorithm_b_mixed_eight_worker_20260827_v8",
    "run_mysql_algorithm_b_xp_top4_mixed_eight_worker_v7.py":
        "run_mysql_algorithm_b_xp_top4_mixed_eight_worker_v8.py",
}
for old, new in replacements.items():
    if text.count(old) != 1:
        raise RuntimeError(f"XP8 B release v8 predecessor differs: {old}")
    text = text.replace(old, new)
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._build_mysql_xp8_b_release_v8_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)
inner = implementation["inner"]
inner["SOURCES"].update({
    "builder": Path(__file__).resolve(),
    "reset_authority": Path(__file__).resolve().with_name(
        "reset_mysql_xp_data_planes_mixed_eight_worker_v3.py"
    ),
    "locked_reset_predecessor": Path(__file__).resolve().with_name(
        "reset_mysql_xp_data_planes_mixed_eight_worker_v1.py"
    ),
})
predecessor_main = implementation["predecessor_main"]


def main() -> int:
    result = int(predecessor_main())
    contract_path = inner["OUTPUT"] / "algorithm_b_top4_contract.json"
    contract = inner["load"](contract_path)
    contract["parallel_reset_successor"] = {
        "docker_create_start_lock": False,
        "docker_control_concurrency_per_scene": 8,
        "worker_reset_parallelism": 8,
        "cross_scene_barrier": False,
        "committed_outer_reused_through": 11,
        "partial_outer_012_measurements_reused": 0,
        "accepted_or_rejected_credit_for_partial_outer": 0,
    }
    inner["atomic"](contract_path, contract)
    return result


if __name__ == "__main__":
    raise SystemExit(main())


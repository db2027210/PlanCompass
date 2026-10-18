#!/usr/bin/env python3
"""Freeze XP8 Algorithm-A v7 unlocked-reset successor."""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name(
    "build_mysql_xp_top4_algorithm_a_mixed_eight_worker_release_v6.py"
)
text = SOURCE.read_text(encoding="utf-8")
replacements = {
    "frozen_mysql_xp_top4_algorithm_a_mixed_eight_worker_20260827_v6":
        "frozen_mysql_xp_top4_algorithm_a_mixed_eight_worker_20260827_v7",
    "run_mysql_algorithm_a_xp_top4_mixed_eight_worker_v6.py":
        "run_mysql_algorithm_a_xp_top4_mixed_eight_worker_v7.py",
    "launch_mysql_algorithm_a_xp_top4_mixed_eight_worker_v6.py":
        "launch_mysql_algorithm_a_xp_top4_mixed_eight_worker_v7.py",
    "monitor_mysql_b_then_a_mixed_eight_worker_scene_v7.py":
        "monitor_mysql_b_then_a_mixed_eight_worker_scene_v8.py",
}
expected_counts = {
    "run_mysql_algorithm_a_xp_top4_mixed_eight_worker_v6.py": 2,
}
for old, new in replacements.items():
    if text.count(old) != expected_counts.get(old, 1):
        raise RuntimeError(f"XP8 A release v7 predecessor differs: {old}")
    text = text.replace(old, new)
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._build_mysql_xp8_a_release_v7_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)
builder = implementation["builder"]
builder.SOURCES.update({
    "builder": Path(__file__).resolve(),
    "xp_reset": Path(__file__).resolve().with_name(
        "reset_mysql_xp_data_planes_mixed_eight_worker_scene_v3.py"
    ),
    "locked_reset_predecessor": Path(__file__).resolve().with_name(
        "reset_mysql_xp_data_planes_mixed_eight_worker_scene_v2.py"
    ),
})
predecessor_main = implementation["predecessor_main"]


def main() -> int:
    result = int(predecessor_main())
    contract_path = builder.CONTRACT
    contract = builder.load(contract_path)
    contract["cross_scene_docker_control_lock"] = False
    contract["worker_reset_parallelism"] = 8
    contract["docker_control_concurrency_per_scene"] = 8
    builder.atomic(contract_path, contract)
    return result


if __name__ == "__main__":
    raise SystemExit(main())

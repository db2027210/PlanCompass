#!/usr/bin/env python3
"""Freeze XP8 Algorithm-A v8 long-storage-bound successor."""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name(
    "build_mysql_xp_top4_algorithm_a_mixed_eight_worker_release_v7.py"
)
text = SOURCE.read_text(encoding="utf-8")
replacements = {
    "frozen_mysql_xp_top4_algorithm_a_mixed_eight_worker_20260827_v7":
        "frozen_mysql_xp_top4_algorithm_a_mixed_eight_worker_20260827_v8",
    "run_mysql_algorithm_a_xp_top4_mixed_eight_worker_v7.py":
        "run_mysql_algorithm_a_xp_top4_mixed_eight_worker_v8.py",
    "launch_mysql_algorithm_a_xp_top4_mixed_eight_worker_v7.py":
        "launch_mysql_algorithm_a_xp_top4_mixed_eight_worker_v8.py",
    "monitor_mysql_b_then_a_mixed_eight_worker_scene_v8.py":
        "monitor_mysql_b_then_a_mixed_eight_worker_scene_v9.py",
}
for old, new in replacements.items():
    if text.count(old) != 1:
        raise RuntimeError(f"XP8 A release v8 predecessor differs: {old}")
    text = text.replace(old, new)
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._build_mysql_xp8_a_release_v8_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)
builder = implementation["builder"]
builder.SOURCES.update({
    "builder": Path(__file__).resolve(),
    "xp_reset": Path(__file__).resolve().with_name(
        "reset_mysql_xp_data_planes_mixed_eight_worker_scene_v4.py"
    ),
    "xp_reset_implementation": Path(__file__).resolve().with_name(
        "reset_mysql_xp_data_planes_v7.py"
    ),
})
predecessor_main = implementation["predecessor_main"]


def main() -> int:
    result = int(predecessor_main())
    contract = builder.load(builder.CONTRACT)
    contract["physical_storage_operation_bounds"] = {
        "template_copy_seconds": 7200,
        "quarantine_cleanup_seconds": 7200,
        "copy_mode": "cp_a_reflink_never",
        "byte_equivalence_required": True,
    }
    builder.atomic(builder.CONTRACT, contract)
    return result


if __name__ == "__main__":
    raise SystemExit(main())

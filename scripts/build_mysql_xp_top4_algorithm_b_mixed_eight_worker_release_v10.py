#!/usr/bin/env python3
"""Freeze XP8 Algorithm-B v10 corrected long-storage successor."""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name(
    "build_mysql_xp_top4_algorithm_b_mixed_eight_worker_release_v8.py"
)
text = SOURCE.read_text(encoding="utf-8")
replacements = {
    "frozen_mysql_xp_top4_algorithm_b_mixed_eight_worker_20260827_v8":
        "frozen_mysql_xp_top4_algorithm_b_mixed_eight_worker_20260827_v10",
    "run_mysql_algorithm_b_xp_top4_mixed_eight_worker_v8.py":
        "run_mysql_algorithm_b_xp_top4_mixed_eight_worker_v10.py",
}
for old, new in replacements.items():
    if text.count(old) != 1:
        raise RuntimeError(f"XP8 B release v10 predecessor differs: {old}")
    text = text.replace(old, new)
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._build_mysql_xp8_b_release_v10_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)
inner = implementation["inner"]
inner["SOURCES"].update({
    "builder": Path(__file__).resolve(),
    "reset_authority": Path(__file__).resolve().with_name(
        "reset_mysql_xp_data_planes_mixed_eight_worker_v4.py"
    ),
    "reset_core": Path(__file__).resolve().with_name(
        "reset_mysql_xp_data_planes_mixed_four_worker_v3_base.py"
    ),
    "reset_run_scoped": Path(__file__).resolve().with_name(
        "reset_mysql_xp_data_planes_mixed_four_worker_v3.py"
    ),
})
predecessor_main = implementation["predecessor_main"]


def main() -> int:
    result = int(predecessor_main())
    contract_path = inner["OUTPUT"] / "algorithm_b_top4_contract.json"
    contract = inner["load"](contract_path)
    contract["physical_storage_operation_bounds"] = {
        "template_copy_seconds": 7200,
        "quarantine_cleanup_seconds": 7200,
        "copy_mode": "cp_a_reflink_never",
        "byte_equivalence_required": True,
    }
    inner["atomic"](contract_path, contract)
    return result


if __name__ == "__main__":
    raise SystemExit(main())

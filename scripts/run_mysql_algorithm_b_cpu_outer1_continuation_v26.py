#!/usr/bin/env python3
"""Correct the CPU-B continuation's post-outer1 no-lease dispatch."""

from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "scripts/run_mysql_algorithm_b_cpu_outer1_continuation_v25.py").read_text(
    encoding="utf-8"
)
replacements = {
    "original_measure = runtime._measure_batch_with_fair_coordination":
        "installed_measure: dict[str, Any] = {}",
    "return original_measure(**kwargs)":
        'return installed_measure["measure"](**kwargs)',
    "original_install_scene(scene)\n        runtime._measure_batch_with_fair_coordination = replay_outer_tail":
        "original_install_scene(scene)\n"
        "        installed_measure[\"measure\"] = runtime._measure_batch_with_fair_coordination\n"
        "        runtime._measure_batch_with_fair_coordination = replay_outer_tail",
}
for old, new in replacements.items():
    if source.count(old) != 1:
        raise RuntimeError(f"CPU-B continuation v26 fragment differs: {old}")
    source = source.replace(old, new)

implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._cpu_b_outer1_continuation_v26_impl",
    "__package__": "scripts",
}
exec(compile(source, str(Path(__file__).resolve()), "exec"), implementation)
implementation["release"].CONTRACT_ROOT = (
    ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_b_20260826_v34"
)

if __name__ == "__main__":
    raise SystemExit(int(implementation["main"]()))

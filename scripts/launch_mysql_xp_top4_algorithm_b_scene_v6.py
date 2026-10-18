#!/usr/bin/env python3
"""Launch fresh Top4 B on the actual-disjoint XP4 resource successor."""

from __future__ import annotations

from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PREDECESSOR = ROOT / "scripts/launch_mysql_xp_top4_algorithm_b_scene_v2.py"
_source = PREDECESSOR.read_text(encoding="utf-8")
replacements = {
    'CONTROLLER = ROOT / "scripts/run_mysql_algorithm_b_xp_top4_async_v2.py"':
        'CONTROLLER = ROOT / "scripts/run_mysql_algorithm_b_xp_top4_async_v6.py"',
    'REFRESH = ROOT / "scripts/refresh_mysql_xp4_live_authority_from_reset_v1.py"':
        'REFRESH = ROOT / "scripts/refresh_mysql_xp_cpu_current_gap_live_authority_v1.py"',
    "frozen_mysql_xp_top4_algorithm_b_20260826_v11":
        "frozen_mysql_xp_top4_algorithm_b_20260826_v15",
    "v59_to_c_xp_top4_20260825_attempt033_cpu_zero_safe_xp_cpu/run_state.json":
        "v59_to_c_xp_top4_20260825_attempt033_cpu_zero_safe_xp_cpu/run_state.json",
    "v161_to_b_top4_fresh_bootstrap_20260826_v11_cpu":
        "v161_to_b_top4_fresh_bootstrap_20260826_v15_cpu",
    "v161_to_b_top4_fresh_bootstrap_20260826_v11_io":
        "v161_to_b_top4_fresh_bootstrap_20260826_v15_io",
    "v161_to_b_top4_fresh_20260826_attempt008_xp_cpu/xp_resets/algorithm_b_independent_fresh_baseline/reset_binding.json":
        "v210_to_xp_cpu_current_gap_transition_20260826/reset_binding.json",
    'f"v179_to_b_top4_fresh_20260826_attempt009_{suffix}"':
        'f"v203_to_b_top4_fresh_20260826_attempt013_{suffix}"',
    'HISTORY / "v179_to_b_top4_pipeline_20260826"':
        'HISTORY / "v203_to_b_top4_pipeline_20260826"',
    'HISTORY / "v179_to_b_top4_live_authority_20260826" / scene':
        'HISTORY / "v211_to_xp_cpu_current_gap_live_authority_20260826" / scene',
    'f"mysql-algb-top4-v179-{suffix}.service"':
        'f"mysql-algb-top4-v203-{suffix}.service"',
}
for old, new in replacements.items():
    count = _source.count(old)
    if old == new:
        if count != 1:
            raise RuntimeError(f"Top4 B v15 unchanged fragment differs: {old}")
        continue
    if count != 1:
        raise RuntimeError(f"Top4 B v15 launcher predecessor fragment differs: {old}")
    _source = _source.replace(old, new)
_impl: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._launch_mysql_xp_top4_algorithm_b_scene_v6_impl",
    "__package__": "scripts",
}
exec(compile(_source, str(Path(__file__).resolve()), "exec"), _impl)


def main() -> int:
    return int(_impl["main"]())


if __name__ == "__main__":
    raise SystemExit(main())

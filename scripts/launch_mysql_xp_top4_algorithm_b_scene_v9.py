#!/usr/bin/env python3
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "scripts/launch_mysql_xp_top4_algorithm_b_scene_v6.py").read_text()
replacements = {
    "run_mysql_algorithm_b_xp_top4_async_v6.py":
        "run_mysql_algorithm_b_xp_top4_async_v9.py",
    "frozen_mysql_xp_top4_algorithm_b_20260826_v15":
        "frozen_mysql_xp_top4_algorithm_b_20260826_v18",
    "v161_to_b_top4_fresh_bootstrap_20260826_v15_cpu":
        "v161_to_b_top4_fresh_bootstrap_20260826_v18_cpu",
    "v161_to_b_top4_fresh_bootstrap_20260826_v15_io":
        "v161_to_b_top4_fresh_bootstrap_20260826_v18_io",
    "v210_to_xp_cpu_current_gap_transition_20260826/reset_binding.json":
        "v221_to_xp_cpu_combined_v4_transition_20260826/reset_binding.json",
    "v203_to_b_top4_fresh_20260826_attempt013":
        "v227_to_b_top4_fresh_20260826_attempt016",
    "v203_to_b_top4_pipeline_20260826":
        "v227_to_b_top4_pipeline_20260826",
    "v211_to_xp_cpu_current_gap_live_authority_20260826":
        "v226_to_xp_combined_v4_schema_rebind_20260826",
    "mysql-algb-top4-v203": "mysql-algb-top4-v227",
}
for old, new in replacements.items():
    if source.count(old) != 1:
        raise RuntimeError(f"B v18 launcher fragment differs: {old}")
    source = source.replace(old, new)

implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._b_v18_launch",
    "__package__": "scripts",
}
exec(compile(source, str(Path(__file__).resolve()), "exec"), implementation)

if __name__ == "__main__":
    raise SystemExit(int(implementation["main"]()))

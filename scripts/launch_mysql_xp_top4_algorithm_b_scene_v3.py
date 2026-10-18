#!/usr/bin/env python3
"""Launch v12 Top4 B with four-lane accounting and baseline continuation."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


PREDECESSOR = ROOT / "scripts/launch_mysql_xp_top4_algorithm_b_scene_v2.py"
_source = PREDECESSOR.read_text(encoding="utf-8")
replacements = {
    'CONTROLLER = ROOT / "scripts/run_mysql_algorithm_b_xp_top4_async_v2.py"':
        'CONTROLLER = ROOT / "scripts/run_mysql_algorithm_b_xp_top4_async_v3.py"',
    "frozen_mysql_xp_top4_algorithm_b_20260826_v11":
        "frozen_mysql_xp_top4_algorithm_b_20260826_v12",
    "v161_to_b_top4_fresh_bootstrap_20260826_v11_cpu":
        "v161_to_b_top4_fresh_bootstrap_20260826_v12_cpu",
    "v161_to_b_top4_fresh_bootstrap_20260826_v11_io":
        "v161_to_b_top4_fresh_bootstrap_20260826_v12_io",
    "v178_to_c_top4_io_xp4_20260826_attempt011_xp_io":
        "v183_to_c_top4_io_xp4_20260826_attempt012_xp_io",
    "v161_to_b_top4_fresh_20260826_attempt008_xp_cpu/xp_resets/algorithm_b_independent_fresh_baseline/reset_binding.json":
        "v179_to_b_top4_fresh_20260826_attempt009_xp_cpu/xp_resets/algorithm_b_independent_fresh_baseline/reset_binding.json",
    'f"v179_to_b_top4_fresh_20260826_attempt009_{suffix}"':
        'f"v184_to_b_top4_fresh_20260826_attempt010_{suffix}"',
    '"v179_to_b_top4_pipeline_20260826"':
        '"v184_to_b_top4_pipeline_20260826"',
    '"v179_to_b_top4_live_authority_20260826"':
        '"v184_to_b_top4_live_authority_20260826"',
    'f"mysql-algb-top4-v179-{suffix}.service"':
        'f"mysql-algb-top4-v184-{suffix}.service"',
}
for old, new in replacements.items():
    if _source.count(old) != 1:
        raise RuntimeError(f"Top4 B launcher predecessor fragment differs: {old}")
    _source = _source.replace(old, new)
env_fragment = (
    '"/usr/bin/env", f"MYSQL_XP4_AUTHORITY_ROOT={authority_root}",\n'
    '        str(PYTHON), str(CONTROLLER), "--run-id", run_id,'
)
env_successor = (
    '"/usr/bin/env", f"MYSQL_XP4_AUTHORITY_ROOT={authority_root}",\n'
    '        *([f"MYSQL_B_VERIFIED_BASELINE={VERIFIED_BASELINES[scene]}"]\n'
    '          if scene in VERIFIED_BASELINES else []),\n'
    '        str(PYTHON), str(CONTROLLER), "--run-id", run_id,'
)
if _source.count(env_fragment) != 1:
    raise RuntimeError("Top4 B launcher environment fragment differs")
_source = _source.replace(env_fragment, env_successor)
_impl: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._launch_mysql_xp_top4_algorithm_b_scene_v3_impl",
    "__package__": "scripts",
}
exec(compile(_source, str(Path(__file__).resolve()), "exec"), _impl)
_impl["VERIFIED_BASELINES"] = {
    "hybench_xp_cpu": (
        _impl["HISTORY"]
        / "v179_to_b_top4_fresh_20260826_attempt009_xp_cpu"
        / "formal_independent_baseline/batch_result.json"
    ),
}


def main() -> int:
    return int(_impl["main"]())


if __name__ == "__main__":
    raise SystemExit(main())

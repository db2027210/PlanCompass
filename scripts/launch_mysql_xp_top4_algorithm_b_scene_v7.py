#!/usr/bin/env python3
"""Launch Top4 B v16 on the passed current-gap authority."""

from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PREDECESSOR = ROOT / "scripts/launch_mysql_xp_top4_algorithm_b_scene_v6.py"
_source = PREDECESSOR.read_text(encoding="utf-8")
replacements = {
    "run_mysql_algorithm_b_xp_top4_async_v6.py":
        "run_mysql_algorithm_b_xp_top4_async_v7.py",
    "frozen_mysql_xp_top4_algorithm_b_20260826_v15":
        "frozen_mysql_xp_top4_algorithm_b_20260826_v16",
    "v161_to_b_top4_fresh_bootstrap_20260826_v15_cpu":
        "v161_to_b_top4_fresh_bootstrap_20260826_v16_cpu",
    "v161_to_b_top4_fresh_bootstrap_20260826_v15_io":
        "v161_to_b_top4_fresh_bootstrap_20260826_v16_io",
    "v203_to_b_top4_fresh_20260826_attempt013":
        "v215_to_b_top4_fresh_20260826_attempt014",
    "v203_to_b_top4_pipeline_20260826":
        "v215_to_b_top4_pipeline_20260826",
    "mysql-algb-top4-v203": "mysql-algb-top4-v215",
}
for old, new in replacements.items():
    if _source.count(old) != 1:
        raise RuntimeError(f"Top4 B v16 launcher predecessor fragment differs: {old}")
    _source = _source.replace(old, new)
_impl: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._launch_mysql_xp_top4_algorithm_b_scene_v7_impl",
    "__package__": "scripts",
}
exec(compile(_source, str(Path(__file__).resolve()), "exec"), _impl)


def main() -> int:
    return int(_impl["main"]())


if __name__ == "__main__":
    raise SystemExit(main())


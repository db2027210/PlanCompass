#!/usr/bin/env python3
"""Launch Top4 A after the current independent Top4 B successor."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


PREDECESSOR = ROOT / "scripts/launch_mysql_algorithm_a_xp_top4_scene_v1.py"
_source = PREDECESSOR.read_text(encoding="utf-8")
replacements = {
    'CONTROLLER = ROOT / "scripts/run_mysql_algorithm_a_xp_top4_async_v1.py"':
        'CONTROLLER = ROOT / "scripts/run_mysql_algorithm_a_xp_top4_async_v2.py"',
    "frozen_mysql_xp_top4_algorithm_a_20260826_v1":
        "frozen_mysql_xp_top4_algorithm_a_20260826_v2",
    "v179_to_b_top4_fresh_20260826_attempt009_xp_cpu":
        "v188_to_b_top4_fresh_20260826_attempt012_xp_cpu",
    "v179_to_b_top4_fresh_20260826_attempt009_xp_io":
        "v188_to_b_top4_fresh_20260826_attempt012_xp_io",
    'f"v180_to_a_top4_fresh_20260826_attempt001_{suffix}"':
        'f"v189_to_a_top4_fresh_20260826_attempt002_{suffix}"',
    '"v180_to_a_top4_pipeline_20260826"':
        '"v189_to_a_top4_pipeline_20260826"',
    '"v180_to_a_top4_live_authority_20260826"':
        '"v189_to_a_top4_live_authority_20260826"',
    'f"mysql-alga-top4-v180-{suffix}.service"':
        'f"mysql-alga-top4-v189-{suffix}.service"',
}
for old, new in replacements.items():
    if _source.count(old) != 1:
        raise RuntimeError(f"Top4 A launcher predecessor fragment differs: {old}")
    _source = _source.replace(old, new)
_impl: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._launch_mysql_algorithm_a_xp_top4_scene_v2_impl",
    "__package__": "scripts",
}
exec(compile(_source, str(Path(__file__).resolve()), "exec"), _impl)


def main() -> int:
    return int(_impl["main"]())


if __name__ == "__main__":
    raise SystemExit(main())

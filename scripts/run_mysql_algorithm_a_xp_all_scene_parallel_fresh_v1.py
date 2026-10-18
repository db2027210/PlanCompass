#!/usr/bin/env python3
"""Start one fresh native-XP Algorithm A scene in the 2+2+2+2 cohort."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_algorithm_a_all_scene_parallel_policy_v1 as parallel
from scripts import run_mysql_algorithm_a_async_scene_v3 as base


_ORIGINAL_FRESH_STATE = base.fresh_state


def fresh_state(**kwargs):
    return parallel.annotate_state(_ORIGINAL_FRESH_STATE(**kwargs))


def main() -> int:
    if "--scene" not in sys.argv:
        raise RuntimeError("XP scene is required")
    scene = sys.argv[sys.argv.index("--scene") + 1]
    if scene not in {"hybench_xp_cpu", "hybench_xp_io"}:
        raise RuntimeError("parallel fresh controller is XP-only")
    parallel.validate_contract()
    base.fresh_state = fresh_state
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())

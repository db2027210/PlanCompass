#!/usr/bin/env python3
"""Algorithm B using the v42 independent-scene-safe shared outer runtime."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v41 as outer_constants
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v42 as outer_runtime


outer_runtime.LOCAL_REAL_REPLAY_LIMIT = outer_constants.LOCAL_REAL_REPLAY_LIMIT
outer_runtime.GLOBAL_COVERAGE_SAMPLE_COUNT = outer_constants.GLOBAL_COVERAGE_SAMPLE_COUNT
outer_runtime.GLOBAL_COVERAGE_REAL_REPLAY_LIMIT = (
    outer_constants.GLOBAL_COVERAGE_REAL_REPLAY_LIMIT
)


SOURCE = Path(__file__).resolve().with_name(
    "run_mysql_algorithm_b_four_scene_client_primary_pg_latest_v1.py"
)
_source = SOURCE.read_text(encoding="utf-8")


def _replace(old: str, new: str, label: str, *, count: int | None = None) -> None:
    global _source
    actual = _source.count(old)
    if actual == 0 or (count is not None and actual != count):
        raise RuntimeError(f"Algorithm B v1 {label} boundary differs: {actual}")
    _source = _source.replace(old, new)


_replace(
    "run_mysql_four_scene_algorithm_c_boundary_resume_v41 as outer",
    "run_mysql_four_scene_algorithm_c_boundary_resume_v42 as outer",
    "shared outer import", count=1,
)
_replace("shared_outer_v41_v1", "shared_outer_v42_v2", "method version")
_replace(
    "mysql_algorithm_c_pg_latest_outer_v41",
    "mysql_algorithm_c_pg_latest_outer_v42",
    "outer-runtime labels",
)
_replace(
    "frozen_mysql_algorithm_b_pg_latest_outer_20260905_v104",
    "frozen_mysql_algorithm_b_pg_latest_outer_20260905_v106",
    "contract root", count=1,
)
_replace(
    'contract.get("release_version") != "v104-pg-latest-outer"',
    'contract.get("release_version") != "v106-pg-latest-outer-recovery"',
    "release version", count=1,
)
_replace("Algorithm B v104", "Algorithm B v106", "diagnostic version")

exec(compile(_source, str(Path(__file__).resolve()), "exec"), globals())

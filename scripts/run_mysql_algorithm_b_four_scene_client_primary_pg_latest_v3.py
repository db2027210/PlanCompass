#!/usr/bin/env python3
"""Algorithm B using the self-contained v43 shared outer runtime."""

from pathlib import Path


SOURCE = Path(__file__).resolve().with_name(
    "run_mysql_algorithm_b_four_scene_client_primary_pg_latest_v2.py"
)
_source = SOURCE.read_text(encoding="utf-8")
replacements = (
    (
        "run_mysql_four_scene_algorithm_c_boundary_resume_v42 as outer_runtime",
        "run_mysql_four_scene_algorithm_c_boundary_resume_v43 as outer_runtime",
    ),
    (
        '"run_mysql_four_scene_algorithm_c_boundary_resume_v42 as outer"',
        '"run_mysql_four_scene_algorithm_c_boundary_resume_v43 as outer"',
    ),
    ("shared_outer_v42_v2", "shared_outer_v43_v3"),
    ("mysql_algorithm_c_pg_latest_outer_v42", "mysql_algorithm_c_pg_latest_outer_v43"),
    (
        "frozen_mysql_algorithm_b_pg_latest_outer_20260905_v106",
        "frozen_mysql_algorithm_b_pg_latest_outer_20260905_v108",
    ),
    ("v106-pg-latest-outer-recovery", "v108-pg-latest-outer-recovery"),
    ("Algorithm B v106", "Algorithm B v108"),
)
for old, new in replacements:
    if old not in _source:
        raise RuntimeError(f"Algorithm B v2 boundary differs: {old}")
    _source = _source.replace(old, new)
exec(compile(_source, str(Path(__file__).resolve()), "exec"), globals())

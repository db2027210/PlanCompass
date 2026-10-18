#!/usr/bin/env python3
"""Algorithm C controller using the self-contained v43 outer runtime."""

from pathlib import Path


SOURCE = Path(__file__).resolve().with_name(
    "run_mysql_algorithm_c_four_scene_client_primary_v14.py"
)
_source = SOURCE.read_text(encoding="utf-8")
replacements = (
    (
        "run_mysql_four_scene_algorithm_c_boundary_resume_v42 as runtime",
        "run_mysql_four_scene_algorithm_c_boundary_resume_v43 as runtime",
    ),
    (
        "frozen_mysql_algorithm_c_pg_latest_outer_20260905_v105",
        "frozen_mysql_algorithm_c_pg_latest_outer_20260905_v107",
    ),
    ("v105-pg-latest-outer-recovery", "v107-pg-latest-outer-recovery"),
    ("Algorithm C v105", "Algorithm C v107"),
)
for old, new in replacements:
    if old not in _source:
        raise RuntimeError(f"Algorithm C v14 controller boundary differs: {old}")
    _source = _source.replace(old, new)
exec(compile(_source, str(Path(__file__).resolve()), "exec"), globals())

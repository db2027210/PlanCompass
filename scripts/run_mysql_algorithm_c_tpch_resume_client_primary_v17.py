#!/usr/bin/env python3
"""TPC-H normalized resume on the v43/v107 Algorithm C runtime."""

from pathlib import Path

SOURCE = Path(__file__).resolve().with_name(
    "run_mysql_algorithm_c_tpch_resume_client_primary_v15.py"
)
_source = SOURCE.read_text(encoding="utf-8")
old = "run_mysql_algorithm_c_four_scene_client_primary_v14 as base"
new = "run_mysql_algorithm_c_four_scene_client_primary_v16 as base"
if _source.count(old) != 1:
    raise RuntimeError("TPC-H normalized v15 controller boundary differs")
_source = _source.replace(old, new)
exec(compile(_source, str(Path(__file__).resolve()), "exec"), globals())

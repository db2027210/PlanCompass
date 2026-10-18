#!/usr/bin/env python3
"""Launch XP-CPU C with nested measurement run scoping v115."""

from pathlib import Path


SOURCE = Path(__file__).resolve().with_name(
    "launch_mysql_c_xp_cpu_resume_v114_v1265.py"
)
_source = SOURCE.read_text(encoding="utf-8")
replacements = {
    '"v1265_mysql_c_xp_cpu_resume_v114_20260907"': (
        '"v1267_mysql_c_xp_cpu_resume_v115_20260907"'
    ),
    '"v1265_to_c_xp_cpu_live_authority_gate_v114_20260907"': (
        '"v1267_to_c_nested_measurement_scope_v115_20260907_xp_cpu"'
    ),
    '"/tmp/mysql_async_outer_start/v1265_mysql_c_xp_cpu_v114"': (
        '"/tmp/mysql_async_outer_start/v1267_mysql_c_xp_cpu_v115"'
    ),
    '"scripts/run_mysql_algorithm_c_four_scene_client_primary_v24.py"': (
        '"scripts/run_mysql_algorithm_c_four_scene_client_primary_v25.py"'
    ),
    '"config/formal/frozen_mysql_algorithm_c_pg_latest_outer_20260907_v114/"': (
        '"config/formal/frozen_mysql_algorithm_c_pg_latest_outer_20260907_v115/"'
    ),
    '"v114-live-authority-kernel-gate"': (
        '"v115-nested-measurement-run-scope"'
    ),
}
for old, new in replacements.items():
    if _source.count(old) != 1:
        raise RuntimeError(f"v115 launch source boundary differs: {old}")
    _source = _source.replace(old, new)
exec(compile(_source, str(Path(__file__).resolve()), "exec"), globals())


#!/usr/bin/env python3
"""Launch XP-I/O C v117 from the completed outer-10 boundary."""

from pathlib import Path


SOURCE = Path(__file__).resolve().with_name(
    "launch_mysql_c_xp_io_resume_v116_v1270.py"
)
_source = SOURCE.read_text(encoding="utf-8")
replacements = {
    '"v1270_mysql_c_xp_io_resume_v116_20260907"': (
        '"v1271_mysql_c_xp_io_resume_v117_20260907"'
    ),
    '"v1270_to_c_xp_io_candidate_instability_v116_20260907_xp_io"': (
        '"v1271_to_c_xp_io_checkpoint_runtime_v117_20260907_xp_io"'
    ),
    '"/tmp/mysql_async_outer_start/v1270_mysql_c_xp_io_v116"': (
        '"/tmp/mysql_async_outer_start/v1271_mysql_c_xp_io_v117"'
    ),
    '"scripts/run_mysql_algorithm_c_four_scene_client_primary_v26.py"': (
        '"scripts/run_mysql_algorithm_c_four_scene_client_primary_v27.py"'
    ),
    '"config/formal/frozen_mysql_algorithm_c_pg_latest_outer_20260907_v116/"': (
        '"config/formal/frozen_mysql_algorithm_c_pg_latest_outer_20260907_v117/"'
    ),
    '"v116-xp-candidate-local-instability"': (
        '"v117-xp-io-checkpoint-runtime-binding"'
    ),
    '"mysql_algorithm_c_xp_io_resume_launch_v4"': (
        '"mysql_algorithm_c_xp_io_resume_launch_v5"'
    ),
}
for old, new in replacements.items():
    if _source.count(old) != 1:
        raise RuntimeError(f"v117 XP-I/O launch source boundary differs: {old}")
    _source = _source.replace(old, new)
exec(compile(_source, str(Path(__file__).resolve()), "exec"), globals())

#!/usr/bin/env python3
"""Launch XP-CPU C v119 with the outer-13 quarantine binding."""

from pathlib import Path


SOURCE = Path(__file__).resolve().with_name(
    "launch_mysql_c_xp_cpu_resume_v119_v1273.py"
)
_source = SOURCE.read_text(encoding="utf-8")
replacements = {
    '"v1273_mysql_c_xp_cpu_resume_v119_20260907"': (
        '"v1275_mysql_c_xp_cpu_resume_v119_20260907"'
    ),
    '"v1273_to_c_control_queue_recovery_v119_20260907_xp_cpu"': (
        '"v1275_to_c_control_queue_recovery_v119_20260907_xp_cpu"'
    ),
    '"/tmp/mysql_async_outer_start/v1273_mysql_c_xp_cpu_v119"': (
        '"/tmp/mysql_async_outer_start/v1275_mysql_c_xp_cpu_v119"'
    ),
    '"v1258_c_xp_cpu_resume_quarantine_v112_20260906/replay_quarantine.json"': (
        '"v1274_c_xp_cpu_resume_quarantine_v119_20260907/replay_quarantine.json"'
    ),
    '"mysql_algorithm_c_xp_cpu_resume_launch_v4"': (
        '"mysql_algorithm_c_xp_cpu_resume_launch_v5"'
    ),
}
for old, new in replacements.items():
    if _source.count(old) != 1:
        raise RuntimeError(f"v119 XP-CPU quarantine launch boundary differs: {old}")
    _source = _source.replace(old, new)
exec(compile(_source, str(Path(__file__).resolve()), "exec"), globals())


#!/usr/bin/env python3
"""Launch XP-CPU C on the refreshed authority and frozen v113 runtime."""

from pathlib import Path


SOURCE = Path(__file__).resolve().with_name(
    "launch_mysql_c_xp_cpu_resume_v112_v1257.py"
)
_source = SOURCE.read_text(encoding="utf-8")
replacements = {
    '"v1257_mysql_c_xp_cpu_resume_v112_20260906"': (
        '"v1262_mysql_c_xp_cpu_resume_v113_20260906"'
    ),
    '"v1257_to_c_xp_cpu_stale_task_recovery_v112_20260906"': (
        '"v1262_to_c_xp_cpu_legacy_stale_recovery_v113_20260906"'
    ),
    '"/tmp/mysql_async_outer_start/v1257_mysql_c_xp_cpu_v112"': (
        '"/tmp/mysql_async_outer_start/v1262_mysql_c_xp_cpu_v113"'
    ),
    '"scripts/run_mysql_algorithm_c_four_scene_client_primary_v22.py"': (
        '"scripts/run_mysql_algorithm_c_four_scene_client_primary_v23.py"'
    ),
    '"config/formal/frozen_mysql_algorithm_c_pg_latest_outer_20260906_v112/"': (
        '"config/formal/frozen_mysql_algorithm_c_pg_latest_outer_20260906_v113/"'
    ),
    '"v1236_c_xp_cpu_scene_authority_v107_20260905/authority"': (
        '"v1261_c_xp_cpu_scene_authority_v113_20260906/authority"'
    ),
    '"v1233_c_xp_cpu_resume_quarantine_v105_20260905/replay_quarantine.json"': (
        '"v1258_c_xp_cpu_resume_quarantine_v112_20260906/replay_quarantine.json"'
    ),
    '"v112-stale-containerd-task-recovery"': (
        '"v113-legacy-freeze-stale-containerd-task-recovery"'
    ),
    '"mysql_algorithm_c_xp_cpu_resume_launch_v1"': (
        '"mysql_algorithm_c_xp_cpu_resume_launch_v2"'
    ),
}
for old, new in replacements.items():
    if _source.count(old) != 1:
        raise RuntimeError(f"v113 launch source boundary differs: {old}")
    _source = _source.replace(old, new)
exec(compile(_source, str(Path(__file__).resolve()), "exec"), globals())

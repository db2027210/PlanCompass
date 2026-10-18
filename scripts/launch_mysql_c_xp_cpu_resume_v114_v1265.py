#!/usr/bin/env python3
"""Launch XP-CPU C with live-authority kernel gate v114."""

from pathlib import Path


SOURCE = Path(__file__).resolve().with_name(
    "launch_mysql_c_xp_cpu_resume_v112_v1257.py"
)
_source = SOURCE.read_text(encoding="utf-8")
replacements = {
    '"v1257_mysql_c_xp_cpu_resume_v112_20260906"': (
        '"v1265_mysql_c_xp_cpu_resume_v114_20260907"'
    ),
    '"v1257_to_c_xp_cpu_stale_task_recovery_v112_20260906"': (
        '"v1265_to_c_xp_cpu_live_authority_gate_v114_20260907"'
    ),
    '"/tmp/mysql_async_outer_start/v1257_mysql_c_xp_cpu_v112"': (
        '"/tmp/mysql_async_outer_start/v1265_mysql_c_xp_cpu_v114"'
    ),
    '"scripts/run_mysql_algorithm_c_four_scene_client_primary_v22.py"': (
        '"scripts/run_mysql_algorithm_c_four_scene_client_primary_v24.py"'
    ),
    '"config/formal/frozen_mysql_algorithm_c_pg_latest_outer_20260906_v112/"': (
        '"config/formal/frozen_mysql_algorithm_c_pg_latest_outer_20260907_v114/"'
    ),
    '"v1236_c_xp_cpu_scene_authority_v107_20260905/authority"': (
        '"v1261_c_xp_cpu_scene_authority_v113_20260906/authority"'
    ),
    '"v1236_c_xp_cpu_scene_authority_v107_20260905_r0/fresh_r0_binding.json"': (
        '"v1263_c_xp_cpu_fresh_r0_authority_v113_20260906/fresh_r0_binding.json"'
    ),
    '"v1233_c_xp_cpu_resume_quarantine_v105_20260905/replay_quarantine.json"': (
        '"v1258_c_xp_cpu_resume_quarantine_v112_20260906/replay_quarantine.json"'
    ),
    '"v112-stale-containerd-task-recovery"': (
        '"v114-live-authority-kernel-gate"'
    ),
    '"mysql_algorithm_c_xp_cpu_resume_launch_v1"': (
        '"mysql_algorithm_c_xp_cpu_resume_launch_v3"'
    ),
}
for old, new in replacements.items():
    if _source.count(old) != 1:
        raise RuntimeError(f"v114 launch source boundary differs: {old}")
    _source = _source.replace(old, new)
exec(compile(_source, str(Path(__file__).resolve()), "exec"), globals())

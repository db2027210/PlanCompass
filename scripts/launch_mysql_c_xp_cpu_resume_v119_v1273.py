#!/usr/bin/env python3
"""Launch XP-CPU C v119 from the completed outer-13 boundary."""

from pathlib import Path


SOURCE = Path(__file__).resolve().with_name(
    "launch_mysql_c_xp_cpu_resume_v112_v1257.py"
)
_source = SOURCE.read_text(encoding="utf-8")
replacements = {
    '"v1257_mysql_c_xp_cpu_resume_v112_20260906"': (
        '"v1273_mysql_c_xp_cpu_resume_v119_20260907"'
    ),
    '"v1257_to_c_xp_cpu_stale_task_recovery_v112_20260906"': (
        '"v1273_to_c_control_queue_recovery_v119_20260907_xp_cpu"'
    ),
    '"/tmp/mysql_async_outer_start/v1257_mysql_c_xp_cpu_v112"': (
        '"/tmp/mysql_async_outer_start/v1273_mysql_c_xp_cpu_v119"'
    ),
    '"scripts/run_mysql_algorithm_c_four_scene_client_primary_v22.py"': (
        '"scripts/run_mysql_algorithm_c_four_scene_client_primary_v29.py"'
    ),
    '"config/formal/frozen_mysql_algorithm_c_pg_latest_outer_20260906_v112/"': (
        '"config/formal/frozen_mysql_algorithm_c_pg_latest_outer_20260907_v119/"'
    ),
    '"v1256_c_xp_cpu_resume_checkpoint_v112_20260906/prepared_binding.json"': (
        '"v1272_c_xp_cpu_resume_checkpoint_v119_20260907/prepared_binding.json"'
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
        '"v119-docker-control-queue-sealed-lineage"'
    ),
    'int(checkpoint.get("completed_outer_attempts") or 0) != 7': (
        'int(checkpoint.get("completed_outer_attempts") or 0) != 13'
    ),
    '"completed_outer_attempts_preserved": 7': (
        '"completed_outer_attempts_preserved": 13'
    ),
    '"mysql_algorithm_c_xp_cpu_resume_launch_v1"': (
        '"mysql_algorithm_c_xp_cpu_resume_launch_v4"'
    ),
    '"fresh XP-CPU v112 launch boundary differs"': (
        '"fresh XP-CPU v119 launch boundary differs"'
    ),
    '"XP-CPU v112 contract or checkpoint differs"': (
        '"XP-CPU v119 contract or checkpoint differs"'
    ),
}
for old, new in replacements.items():
    if _source.count(old) != 1:
        raise RuntimeError(f"v119 XP-CPU launch source boundary differs: {old}")
    _source = _source.replace(old, new)
exec(compile(_source, str(Path(__file__).resolve()), "exec"), globals())


#!/usr/bin/env python3
"""Launch XP-I/O C v116 from the completed outer-10 boundary."""

from pathlib import Path


SOURCE = Path(__file__).resolve().with_name(
    "launch_mysql_c_xp_cpu_resume_v112_v1257.py"
)
_source = SOURCE.read_text(encoding="utf-8")
replacements = {
    '"v1257_mysql_c_xp_cpu_resume_v112_20260906"': (
        '"v1270_mysql_c_xp_io_resume_v116_20260907"'
    ),
    '"v1257_to_c_xp_cpu_stale_task_recovery_v112_20260906"': (
        '"v1270_to_c_xp_io_candidate_instability_v116_20260907_xp_io"'
    ),
    '"/tmp/mysql_async_outer_start/v1257_mysql_c_xp_cpu_v112"': (
        '"/tmp/mysql_async_outer_start/v1270_mysql_c_xp_io_v116"'
    ),
    '"scripts/run_mysql_algorithm_c_four_scene_client_primary_v22.py"': (
        '"scripts/run_mysql_algorithm_c_four_scene_client_primary_v26.py"'
    ),
    '"config/formal/frozen_mysql_algorithm_c_pg_latest_outer_20260906_v112/"': (
        '"config/formal/frozen_mysql_algorithm_c_pg_latest_outer_20260907_v116/"'
    ),
    '"v1256_c_xp_cpu_resume_checkpoint_v112_20260906/prepared_binding.json"': (
        '"v1268_c_xp_io_resume_checkpoint_v116_20260907/prepared_binding.json"'
    ),
    '"v1236_c_xp_cpu_scene_authority_v107_20260905/authority"': (
        '"v1236_c_xp_io_scene_authority_v107_20260905/authority"'
    ),
    '"v1236_c_xp_cpu_scene_authority_v107_20260905_r0/fresh_r0_binding.json"': (
        '"v1236_c_xp_io_scene_authority_v107_20260905_r0/fresh_r0_binding.json"'
    ),
    '"v1233_c_xp_cpu_resume_quarantine_v105_20260905/replay_quarantine.json"': (
        '"v1269_c_xp_io_resume_quarantine_v116_20260907/replay_quarantine.json"'
    ),
    'CPUSET = {*(range(16, 24)), *(range(272, 280))}': (
        'CPUSET = {*(range(24, 32)), *(range(280, 288))}'
    ),
    '"v112-stale-containerd-task-recovery"': (
        '"v116-xp-candidate-local-instability"'
    ),
    '"mysql_algorithm_c_xp_cpu_resume_launch_v1"': (
        '"mysql_algorithm_c_xp_io_resume_launch_v4"'
    ),
    '"16-23,272-279"': '"24-31,280-287"',
}
for old, new in replacements.items():
    if _source.count(old) != 1:
        raise RuntimeError(f"v116 XP-I/O launch source boundary differs: {old}")
    _source = _source.replace(old, new)
if _source.count('"hybench_xp_cpu"') != 3:
    raise RuntimeError("v116 XP-I/O scene boundary differs")
_source = _source.replace('"hybench_xp_cpu"', '"hybench_xp_io"')
if _source.count("!= 7") != 1 or _source.count('"completed_outer_attempts_preserved": 7') != 1:
    raise RuntimeError("v116 XP-I/O checkpoint boundary differs")
_source = _source.replace("!= 7", "!= 10")
_source = _source.replace('"completed_outer_attempts_preserved": 7', '"completed_outer_attempts_preserved": 10')
exec(compile(_source, str(Path(__file__).resolve()), "exec"), globals())


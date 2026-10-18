#!/usr/bin/env python3
"""Algorithm C v43 outer flow with the kernel/cgroup coordination gate."""

from __future__ import annotations

from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v43 as v43


LOCAL_REAL_REPLAY_LIMIT = v43.LOCAL_REAL_REPLAY_LIMIT
GLOBAL_COVERAGE_REAL_REPLAY_LIMIT = v43.GLOBAL_COVERAGE_REAL_REPLAY_LIMIT
GLOBAL_COVERAGE_SAMPLE_COUNT = v43.GLOBAL_COVERAGE_SAMPLE_COUNT
_admit_coverage_top10 = v43._admit_coverage_top10
_replay_coverage_top10 = v43._replay_coverage_top10
scene_checkpoint = v43.scene_checkpoint

_source = v43._source
_old_import = (
    "from scripts import mysql_cross_project_wave_coordination_v1 as coordination"
)
_new_import = (
    "from scripts import mysql_cross_project_wave_coordination_v27 as coordination"
)
if _source.count(_old_import) != 1:
    raise RuntimeError("Algorithm C v43 coordination import boundary differs")
_source = _source.replace(_old_import, _new_import)

# The old 20-second ceiling protected an exclusive cross-project lease.  The
# current successor has no lease and needs enough slack to obtain a continuous
# 15-second rolling window without creating a sibling-scene dependency.
_old_wait = (
    'or float(contract.get("maximum_quiet_wait_while_held_seconds", 0)) > 20.0'
)
_new_wait = (
    'or float(contract.get("maximum_quiet_wait_while_held_seconds", 0)) > 60.0'
)
if _source.count(_old_wait) != 1:
    raise RuntimeError("Algorithm C v43 quiet-wait validation boundary differs")
_source = _source.replace(_old_wait, _new_wait)

exec(compile(_source, str(__file__), "exec"), globals())


#!/usr/bin/env python3
"""Keep an accepted first-miss expansion, restoring only rejected expansions.

PostgreSQL Algorithm C temporarily opens one retained region after the first
complete valid miss.  If the expanded batch is rejected, it restores the
parent interval before the due incumbent bisection.  If the expanded batch is
accepted, the accepted candidate's binary shrink is the new current interval.
"""

from __future__ import annotations

from pathlib import Path

from scripts import mysql_algorithm_c_interval_control_v2 as interval_control
from scripts import mysql_outer_rejection_cycle_v3 as rejection_cycle
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v54 as v54


scene_checkpoint = v54.scene_checkpoint
_source = v54._source

_old = '''                    parent_ranges = state.setdefault(
                        "algorithm_c_first_miss_parent_ranges", {}
                    ).pop(scene, None)
                    if parent_ranges is not None:
                        ranges_by_scene[scene] = parent_ranges
                    ranges_before_rejection = copy.deepcopy(ranges_by_scene[scene])'''
_new = '''                    parent_ranges = state.setdefault(
                        "algorithm_c_first_miss_parent_ranges", {}
                    ).pop(scene, None)
                    if not accepted and parent_ranges is not None:
                        ranges_by_scene[scene] = parent_ranges
                    ranges_before_rejection = copy.deepcopy(ranges_by_scene[scene])'''

if _source.count(_old) != 1:
    raise RuntimeError("Algorithm C v55 first-miss parent restore boundary differs")
_source = _source.replace(_old, _new)

exec(compile(_source, str(Path(__file__).resolve()), "exec"), globals())

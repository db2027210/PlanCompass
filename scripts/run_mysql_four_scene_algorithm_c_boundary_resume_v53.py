#!/usr/bin/env python3
"""Restore interval and paper-gate globals after the v51/v52 source rebuild."""

from __future__ import annotations

from scripts import mysql_algorithm_c_interval_control_v1 as interval_control
from scripts import mysql_algorithm_c_paper_baseline_gate_v1 as paper_baseline_gate
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v52 as v52


scene_checkpoint = v52.scene_checkpoint
_source = v52._source
exec(compile(_source, str(__file__), "exec"), globals())

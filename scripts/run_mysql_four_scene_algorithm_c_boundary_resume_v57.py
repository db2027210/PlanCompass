#!/usr/bin/env python3
"""Bind the PG interval helper in the v56 expanded runtime namespace."""

from __future__ import annotations

from pathlib import Path

from scripts import mysql_algorithm_c_interval_control_v2 as interval_control
from scripts import mysql_algorithm_c_pg_recovery_v1 as pg_recovery
from scripts import mysql_outer_rejection_cycle_v3 as rejection_cycle
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v56 as v56


scene_checkpoint = v56.scene_checkpoint
_source = v56._source

exec(compile(_source, str(Path(__file__).resolve()), "exec"), globals())

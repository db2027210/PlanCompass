#!/usr/bin/env python3
"""Bind all current PG-C helpers for the external-inventory-only runtime."""

from __future__ import annotations

from pathlib import Path

from scripts import mysql_algorithm_c_interval_control_v3 as interval_control
from scripts import mysql_external_process_inventory_only_gate_v1 as external_gate
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v60 as v60


scene_checkpoint = v60.scene_checkpoint
_source = v60._source
external_process_inventory_only_validate_authority = (
    v60.external_process_inventory_only_validate_authority
)

exec(compile(_source, str(Path(__file__).resolve()), "exec"), globals())

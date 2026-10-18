#!/usr/bin/env python3
"""Refresh scene authority with the storage-scoped rolling gate."""

from scripts import mysql_cross_project_wave_coordination_v9 as gate_v9
from scripts import refresh_mysql_two_worker_scene_authority_v1 as predecessor


predecessor.dynamic_gate = gate_v9


if __name__ == "__main__":
    raise SystemExit(predecessor.main())

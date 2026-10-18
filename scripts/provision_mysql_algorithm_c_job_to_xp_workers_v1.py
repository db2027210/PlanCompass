#!/usr/bin/env python3
"""Provision Algorithm C XP workers on completed JOB physical positions."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from scripts import mysql_algorithm_c_job_to_xp_slots_v1 as slots
from scripts import provision_mysql_algorithm_a_xp_slot_workers_v1 as physical


def provision_worker(
    freeze: Mapping[str, Any], worker: Mapping[str, Any], output: Path,
) -> dict[str, Any]:
    """Use the sealed full-copy implementation with Algorithm C identities."""
    physical.slots = slots
    return physical.provision_worker(freeze, worker, output)

#!/usr/bin/env python3
"""Provision XP workers on positions released by completed four-worker JOB."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from scripts import mysql_algorithm_a_job_to_xp_slots_v1 as slots
from scripts import provision_mysql_algorithm_a_xp_slot_workers_v1 as legacy


def provision_worker(
    freeze: Mapping[str, Any], worker: Mapping[str, Any], output: Path,
) -> dict[str, Any]:
    # The physical clone/readback implementation is generic.  Bind its
    # identity guard to this successor's frozen mapping for this call.
    legacy.slots = slots
    return legacy.provision_worker(freeze, worker, output)

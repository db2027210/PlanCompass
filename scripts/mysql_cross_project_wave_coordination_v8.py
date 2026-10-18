#!/usr/bin/env python3
"""Allow a longer observation budget for the unchanged rolling gate."""

from __future__ import annotations

from typing import Any

from scripts import mysql_cross_project_wave_coordination_v7 as predecessor


def rolling_quiet_gate(
    *, run_id: str, required_seconds: float, maximum_wait_seconds: float,
    maximum_load1: float = 16.0, interval_seconds: float = 1.0,
    target_cpus: set[int] | None = None,
) -> dict[str, Any]:
    effective_maximum_wait = max(180.0, maximum_wait_seconds)
    result = predecessor.rolling_quiet_gate(
        run_id=run_id,
        required_seconds=required_seconds,
        maximum_wait_seconds=effective_maximum_wait,
        maximum_load1=maximum_load1,
        interval_seconds=interval_seconds,
        target_cpus=target_cpus,
    )
    result["release_version"] = "v63"
    result["observation_budget_successor"] = {
        "requested_maximum_wait_seconds": maximum_wait_seconds,
        "effective_maximum_wait_seconds": effective_maximum_wait,
        "required_clean_seconds_unchanged": required_seconds,
        "isolation_acceptance_conditions_unchanged": True,
        "external_process_mutation_performed": False,
    }
    return result

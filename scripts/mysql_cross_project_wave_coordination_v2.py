#!/usr/bin/env python3
"""Device- and envelope-scoped rolling quiet gate for formal MySQL waves.

The predecessor conservatively treated every persistent user-space D-state as
host-wide interference.  That is too broad once the same sample has already
proved that a frozen co-resident PostgreSQL workload is CPU-disjoint, does not
mount the MySQL disk2 data plane, has bounded memory, runs with swap disabled,
and leaves the required memory/PSI headroom.  This successor keeps those
D-state PIDs visible but exempts only the exactly inventoried workload PIDs
covered by that successful isolation proof.  Unknown, same-envelope, or
otherwise unproved D-state remains fail-closed.
"""

from __future__ import annotations

import os
from pathlib import Path
import time
from typing import Any

from scripts import mysql_cross_project_wave_coordination_v1 as predecessor

ROOT = Path(__file__).resolve().parents[1]


def _proved_isolated_workload_pids(
    isolation: dict[str, Any],
) -> set[int]:
    """Return only PIDs covered by the current sample's complete proof."""

    if isolation.get("passed") is not True:
        return set()
    readback = isolation.get("container_readback")
    if not isinstance(readback, dict) or readback.get("passed") is not True:
        return set()
    result: set[int] = set()
    for row in isolation.get("workloads") or []:
        if not isinstance(row, dict):
            continue
        if row.get("errors") not in ([], None):
            continue
        if row.get("overlap_target_cpus") not in ([], None):
            continue
        try:
            result.add(int(row["pid"]))
        except (KeyError, TypeError, ValueError):
            continue
    return result


def rolling_quiet_gate(
    *, run_id: str, required_seconds: float, maximum_wait_seconds: float,
    maximum_load1: float = 16.0, interval_seconds: float = 1.0,
    target_cpus: set[int] | None = None,
) -> dict[str, Any]:
    """Require a clean target envelope while retaining scoped D-state proof."""

    if required_seconds < 15 or maximum_wait_seconds < required_seconds:
        raise ValueError("invalid coordinated rolling-window bounds")
    from scripts.preflight_mysql_four_scene_live_v8 import host_pressure

    started = time.monotonic()
    clean_since: float | None = None
    samples: list[dict[str, Any]] = []
    terminal_errors: list[str] = []
    d_state_streaks: dict[int, int] = {}
    persistent_required = 3
    while True:
        inventory = predecessor.external_process_inventory(run_id, {0})
        raw_workloads = inventory["workloads"]
        scoped_target_cpus = set(target_cpus or ())
        allowed_sibling_mysql = [
            row for row in raw_workloads
            if scoped_target_cpus
            and str(ROOT) in str(row.get("command") or "")
            and predecessor.cpus(str(row.get("cpus_allowed_list") or ""))
            .isdisjoint(scoped_target_cpus)
        ]
        evaluated_workloads = [
            row for row in raw_workloads if row not in allowed_sibling_mysql
        ]
        workloads = predecessor.sanitized_rows(raw_workloads)
        isolation = predecessor.evaluate_co_resident_pg_isolation(
            evaluated_workloads
        )
        pressure = host_pressure()
        same_run_pids = predecessor.same_run_process_pids(run_id)
        raw = {int(pid) for pid in pressure["blocking_d_state_pids"]}
        same_run = raw & same_run_pids
        proved_isolated = _proved_isolated_workload_pids(isolation)
        isolated_d_state = raw & proved_isolated
        allowed_sibling_pids = {
            int(row["pid"]) for row in allowed_sibling_mysql
        }
        allowed_sibling_d_state = raw & allowed_sibling_pids
        blocking = raw - same_run - isolated_d_state - allowed_sibling_d_state
        pressure["raw_blocking_d_state_pids"] = sorted(raw)
        pressure["same_run_exempted_d_state_pids"] = sorted(same_run)
        pressure["proved_isolated_external_d_state_pids"] = sorted(
            isolated_d_state
        )
        pressure["proved_isolated_external_workload_pids"] = sorted(
            proved_isolated
        )
        pressure["cpu_disjoint_sibling_mysql_d_state_pids"] = sorted(
            allowed_sibling_d_state
        )
        pressure["blocking_d_state_pids"] = sorted(blocking)

        errors: list[str] = []
        if evaluated_workloads and isolation.get("passed") is not True:
            errors.append(
                "external_database_physical_workload_present_while_exclusive"
            )
            terminal_errors = list(errors)
        d_state_streaks = {
            pid: d_state_streaks.get(pid, 0) + 1 for pid in blocking
        }
        persistent = sorted(
            pid for pid, streak in d_state_streaks.items()
            if streak >= persistent_required
        )
        if persistent:
            errors.append("host_uninterruptible_sleep_present")

        normalized_load1 = float(pressure["load1"]) / max(
            1, os.cpu_count() or 1
        )
        current = time.monotonic()
        if errors:
            clean_since = None
        elif clean_since is None:
            clean_since = current
        clean_elapsed = 0.0 if clean_since is None else current - clean_since
        samples.append({
            "captured_at": predecessor.now(),
            "errors": errors,
            "host_pressure": pressure,
            "transient_d_state_pids": sorted(blocking),
            "persistent_d_state_pids": persistent,
            "d_state_consecutive_sample_streaks": {
                str(pid): streak
                for pid, streak in sorted(d_state_streaks.items())
            },
            "external_workloads": workloads,
            "allowed_cpu_disjoint_sibling_mysql_workloads": (
                predecessor.sanitized_rows(allowed_sibling_mysql)
            ),
            "co_resident_pg_isolation": isolation,
            "external_controller_count": len(inventory["controllers"]),
            "normalized_load1": normalized_load1,
            "load1_diagnostic_only": True,
            "clean_elapsed_seconds": clean_elapsed,
        })
        if terminal_errors:
            break
        if clean_elapsed >= required_seconds:
            break
        if current - started >= maximum_wait_seconds:
            terminal_errors = ["coordinated_quiet_window_timeout"]
            break
        time.sleep(max(0.2, interval_seconds))

    passed = (
        not terminal_errors
        and samples[-1]["clean_elapsed_seconds"] >= required_seconds
    )
    return {
        "schema_version": "mysql_cross_project_wave_quiet_gate_v2",
        "status": "PASS" if passed else "BLOCKED_FAIL_CLOSED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "coordination_contract_path": str(predecessor.CONTRACT_PATH),
        "coordination_contract_sha256": predecessor.sha256(
            predecessor.CONTRACT_PATH
        ),
        "lock_path": str(predecessor.LOCK_PATH),
        "required_clean_seconds": required_seconds,
        "maximum_wait_seconds": maximum_wait_seconds,
        "maximum_load1": maximum_load1,
        "maximum_normalized_load1": 0.10,
        "global_load1_policy": (
            "diagnostic_only_direct_isolation_is_authoritative"
        ),
        "persistent_d_state_required_consecutive_samples": persistent_required,
        "isolated_external_d_state_policy": (
            "record_nonblocking_only_with_same_sample_complete_isolation_proof"
        ),
        "cpu_disjoint_sibling_mysql_wait_required": False,
        "observed_seconds": time.monotonic() - started,
        "errors": terminal_errors,
        "samples": samples,
        "completed_at": predecessor.now(),
    }

#!/usr/bin/env python3
"""Wait under direct-isolation authority, then launch rank-1 recovery."""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_co_resident_pg_isolation_v3 as isolation
from scripts import wait_launch_mysql_algorithm_c_boundary_resume_v1 as waiter


ORIGINAL_HOST_PRESSURE = waiter.host_pressure
ORIGINAL_APPEND = waiter.append
_streaks: dict[int, int] = {}


def v56_host_pressure() -> dict[str, Any]:
    row = copy.deepcopy(ORIGINAL_HOST_PRESSURE())
    observed = {int(pid) for pid in row.get("blocking_d_state_pids") or []}
    for pid in list(_streaks):
        if pid not in observed:
            del _streaks[pid]
    for pid in observed:
        _streaks[pid] = _streaks.get(pid, 0) + 1
    row["observed_single_sample_d_state_pids"] = sorted(observed)
    row["d_state_consecutive_samples"] = {
        str(pid): _streaks[pid] for pid in sorted(_streaks)
    }
    row["blocking_d_state_pids"] = sorted(
        pid for pid, count in _streaks.items() if count >= 3
    )
    return row


def direct_isolation_load1_blockers(
    pressure: dict, workloads: list[dict], workload_isolation: dict,
) -> list[str]:
    # Linux load1 is host-wide and cannot identify pressure inside the frozen
    # MySQL CPU/NUMA envelope.  The versioned direct overlap/resource readback
    # and persistent D-state checks remain fail-closed and authoritative.
    return []


def append_with_contract(path: Path, event: dict[str, Any]) -> None:
    enriched = dict(event)
    if event.get("event") == "formal_boundary_resume_wait_started":
        source = Path(__file__).resolve()
        enriched.update({
            "waiter_runtime_source_path": str(source),
            "waiter_runtime_source_sha256": hashlib.sha256(
                source.read_bytes()
            ).hexdigest(),
            "d_state_policy": "same_pid_three_consecutive_samples_fail_closed",
            "single_sample_d_state_action": "record_without_resetting_clean_window",
            "persistent_d_state_required_consecutive_samples": 3,
            "global_load1_policy":
                "diagnostic_only_direct_isolation_is_authoritative",
            "direct_isolation_fail_closed": True,
        })
    ORIGINAL_APPEND(path, enriched)


def main() -> int:
    isolation.install()
    waiter.LAUNCHER = (
        ROOT / "scripts/launch_mysql_algorithm_c_boundary_resume_v7.py"
    )
    waiter.host_pressure = v56_host_pressure
    waiter.load1_blockers = direct_isolation_load1_blockers
    waiter.append = append_with_contract
    return waiter.main()


if __name__ == "__main__":
    raise SystemExit(main())

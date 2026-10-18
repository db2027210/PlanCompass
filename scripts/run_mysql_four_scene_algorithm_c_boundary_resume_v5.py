#!/usr/bin/env python3
"""Resume Algorithm C after a pre-measurement persistent-D-state timeout."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v4 as runtime


ORIGINAL_CLEAN = runtime.clean_quiet_timeout_partial_boundary
ORIGINAL_STATE_DOCUMENT = runtime.state_document


def clean_premeasurement_dstate_timeout_boundary(
    *, source_root: Path, reconstructed: Mapping[str, Any], start_rank: int,
) -> bool:
    """Bind an exhausted D-state gate even when its final sample stayed blocked.

    A new launch still has to establish a fresh 15-second clean window.  This
    function proves only that the old run stopped before the requested rank
    could reset XP, start a workload, or receive formal state credit.
    """

    if ORIGINAL_CLEAN(
        source_root=source_root,
        reconstructed=reconstructed,
        start_rank=start_rank,
    ):
        return True
    if start_rank not in range(2, 6):
        return False
    expected_outer = int(reconstructed["outer_attempt"]) + 1
    outer_root = source_root / f"outer_{expected_outer:03d}"
    state_path = source_root / "run_state.json"
    gate_path = outer_root / (
        f"top5_batch_{start_rank}_cross_project_wave_quiet_gate_attempt_001.json"
    )
    watchdog_path = source_root / "runtime_external_interference_watchdog.jsonl"
    try:
        state = runtime.runtime.alg.load(state_path)
        gate = runtime.runtime.alg.load(gate_path)
        rows = runtime._watchdog_rows(watchdog_path)
    except (OSError, ValueError, TypeError, KeyError):
        return False
    samples = gate.get("samples") or []
    completed = outer_root / f"top5_batch_{start_rank - 1}/batch_result.json"
    current = outer_root / f"top5_batch_{start_rank}/batch_result.json"
    current_reset = (
        source_root / "xp_resets"
        / f"outer_{expected_outer:03d}_batch_{start_rank}"
    )
    forbidden_events = {
        "runtime_interference_detected_under_exclusive_wave_lease",
        "cross_project_workload_lease_lost_during_formal_wave",
    }
    return bool(
        state.get("status") == "FAILED_CLOSED"
        and state.get("stage") == "FAILED"
        and state.get("error") == runtime.QUIET_GATE_ERROR
        and int(state.get("outer_attempt") or -1)
        == int(reconstructed["outer_attempt"])
        and gate.get("status") == "BLOCKED_FAIL_CLOSED"
        and gate.get("errors") == ["coordinated_quiet_window_timeout"]
        and float(gate.get("required_clean_seconds") or 0) >= 15.0
        and float(gate.get("observed_seconds") or 0)
        >= float(gate.get("maximum_wait_seconds") or 0)
        and samples
        and any(sample.get("persistent_d_state_pids") for sample in samples)
        and all(
            set(sample.get("errors") or [])
            <= {"host_uninterruptible_sleep_present"}
            and (sample.get("co_resident_pg_isolation") or {}).get("passed")
            is True
            for sample in samples
        )
        and completed.is_file()
        and not current.exists()
        and not current_reset.exists()
        and not list(outer_root.glob("*.decision.json"))
        and rows
        and rows[0].get("event") == "watchdog_started"
        and not any(row.get("event") in forbidden_events for row in rows)
    )


def state_document(**kwargs: Any) -> dict[str, Any]:
    document = ORIGINAL_STATE_DOCUMENT(**kwargs)
    document["premeasurement_dstate_timeout_recovery"] = {
        "source_path": str(Path(__file__).resolve()),
        "source_sha256": runtime.runtime.alg.sha(Path(__file__).resolve()),
        "old_gate_may_end_blocked": True,
        "new_launch_fresh_clean_window_required_seconds": 15,
        "candidate_state_mutation_before_continuation": False,
        "accepted_state_credit_before_continuation": 0,
    }
    return document


def main() -> int:
    runtime.clean_quiet_timeout_partial_boundary = (
        clean_premeasurement_dstate_timeout_boundary
    )
    runtime.state_document = state_document
    return runtime.main()


if __name__ == "__main__":
    raise SystemExit(main())

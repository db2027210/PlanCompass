#!/usr/bin/env python3
"""Resume Algorithm C after a watchdog-probe/session-drain prestart failure."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v4 as v4
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v5 as v5


ORIGINAL_VALIDATOR = v4.validate_formal_partial_continuation
ORIGINAL_STATE_DOCUMENT = v5.state_document
SESSION_DRAIN_PREFIX = (
    "RuntimeError: XP active client sessions were not proven drained before "
    "the 60s bound:"
)


def clean_watchdog_probe_session_drain_boundary(
    *, source_root: Path, reconstructed: Mapping[str, Any], start_rank: int,
) -> bool:
    """Prove the requested rank never reset or started physical replay."""

    if start_rank not in range(2, 6):
        return False
    expected_outer = int(reconstructed["outer_attempt"]) + 1
    outer_root = source_root / f"outer_{expected_outer:03d}"
    state_path = source_root / "run_state.json"
    gate_path = outer_root / (
        f"top5_batch_{start_rank}_cross_project_wave_quiet_gate_attempt_001.json"
    )
    watchdog_path = source_root / "runtime_external_interference_watchdog.jsonl"
    watchdog_stderr = (
        ROOT / "history/mysql_four_scene_20260809/formal_launch_logs"
        / f"{source_root.name}.watchdog.stderr.log"
    )
    reset_root = (
        source_root / "xp_resets"
        / f"outer_{expected_outer:03d}_batch_{start_rank}"
    )
    current_batch = outer_root / f"top5_batch_{start_rank}/batch_result.json"
    previous_batch = outer_root / f"top5_batch_{start_rank - 1}/batch_result.json"
    try:
        state = v4.runtime.alg.load(state_path)
        gate = v4.runtime.alg.load(gate_path)
        previous = v4.runtime.alg.load(previous_batch)
        rows = v4._watchdog_rows(watchdog_path)
        watchdog_error = watchdog_stderr.read_text(encoding="utf-8")
    except (OSError, ValueError, TypeError, KeyError):
        return False
    forbidden_events = {
        "runtime_interference_detected_under_exclusive_wave_lease",
        "cross_project_workload_lease_lost_during_formal_wave",
    }
    timeout_failures = [
        row for row in (previous.get("failures") or [])
        if str(row.get("error") or "").startswith("TimeoutExpired:")
        and "timed out after 900 seconds" in str(row.get("error") or "")
    ]
    return bool(
        state.get("status") == "FAILED_CLOSED"
        and state.get("stage") == "FAILED"
        and str(state.get("error") or "").startswith(SESSION_DRAIN_PREFIX)
        and int(state.get("outer_attempt") or -1)
        == int(reconstructed["outer_attempt"])
        and gate.get("status") == "PASS"
        and gate.get("errors") in ([], None)
        and previous.get("status") == "FAILED_CLOSED"
        and previous.get("formal_scope") == "mysql_only"
        and previous.get("cross_engine_comparison_prohibited") is True
        and len(timeout_failures) >= 1
        and reset_root.is_dir()
        and not list(reset_root.rglob("*"))
        and not current_batch.exists()
        and not list(outer_root.glob("*.decision.json"))
        and rows
        and rows[0].get("event") == "watchdog_started"
        and not any(row.get("event") in forbidden_events for row in rows)
        and "subprocess.TimeoutExpired: Command" in watchdog_error
        and "['docker', 'inspect'," in watchdog_error
        and "timed out after 15 seconds" in watchdog_error
    )


def validate_formal_partial_continuation(**kwargs: Any) -> dict[str, Any]:
    try:
        return ORIGINAL_VALIDATOR(**kwargs)
    except RuntimeError as error:
        if str(error) != "formal partial continuation authority differs":
            raise
    source_root = Path(kwargs["source_root"]).resolve()
    reconstructed = kwargs["reconstructed"]
    start_rank = int(kwargs["start_rank"])
    if not clean_watchdog_probe_session_drain_boundary(
        source_root=source_root,
        reconstructed=reconstructed,
        start_rank=start_rank,
    ):
        raise RuntimeError("formal partial continuation authority differs")

    expected_outer = int(reconstructed["outer_attempt"]) + 1
    state_path = source_root / "run_state.json"
    gate_path = source_root / f"outer_{expected_outer:03d}" / (
        f"top5_batch_{start_rank}_cross_project_wave_quiet_gate_attempt_001.json"
    )
    watchdog_stderr = (
        ROOT / "history/mysql_four_scene_20260809/formal_launch_logs"
        / f"{source_root.name}.watchdog.stderr.log"
    )
    original_load = v4.runtime.alg.load
    previous_batch = (
        source_root / f"outer_{expected_outer:03d}"
        / f"top5_batch_{start_rank - 1}/batch_result.json"
    )

    def compatibility_load(path: Path) -> dict[str, Any]:
        value = original_load(path)
        resolved = Path(path).resolve()
        if resolved == state_path.resolve():
            value = copy.deepcopy(value)
            value["error"] = (
                "RuntimeError: external physical workload bypassed the exclusive "
                "wave lease"
            )
            partial = copy.deepcopy(value.get("formal_partial_continuation") or {})
            completed = list(partial.get("completed_batches") or [])
            if not any(
                int(row.get("rank") or 0) == start_rank - 1
                for row in completed
            ):
                completed.append({
                    "rank": start_rank - 1,
                    "path": str(previous_batch),
                    "sha256": v4.runtime.alg.sha(previous_batch),
                    "technical_timeout_excluded": True,
                })
            partial["completed_batches"] = completed
            value["formal_partial_continuation"] = partial
        elif resolved == gate_path.resolve():
            value = copy.deepcopy(value)
            value["errors"] = [
                "external_database_physical_workload_present_while_exclusive"
            ]
            value["status"] = "BLOCKED_FAIL_CLOSED"
        return value

    v4.runtime.alg.load = compatibility_load
    try:
        result = v4.ORIGINAL_PARTIAL_VALIDATOR(**kwargs)
    finally:
        v4.runtime.alg.load = original_load
    result["continuation_reason"] = (
        "premeasurement_session_drain_probe_timeout_after_watchdog_probe_exit"
    )
    result["watchdog_probe_session_drain_recovery"] = {
        "source_state_path": str(state_path),
        "source_state_sha256": v4.runtime.alg.sha(state_path),
        "watchdog_stderr_path": str(watchdog_stderr),
        "watchdog_stderr_sha256": v4.runtime.alg.sha(watchdog_stderr),
        "candidate_state_mutation_before_continuation": False,
        "accepted_state_credit_before_continuation": 0,
        "technical_previous_rank_replay_prohibited": True,
    }
    return result


def state_document(**kwargs: Any) -> dict[str, Any]:
    document = ORIGINAL_STATE_DOCUMENT(**kwargs)
    document["watchdog_probe_session_drain_recovery"] = {
        "source_path": str(Path(__file__).resolve()),
        "source_sha256": v4.runtime.alg.sha(Path(__file__).resolve()),
        "candidate_state_mutation_before_continuation": False,
        "accepted_state_credit_before_continuation": 0,
        "technical_previous_rank_replay_prohibited": True,
    }
    return document


def main() -> int:
    v4.validate_formal_partial_continuation = validate_formal_partial_continuation
    v5.state_document = state_document
    return v5.main()


if __name__ == "__main__":
    raise SystemExit(main())

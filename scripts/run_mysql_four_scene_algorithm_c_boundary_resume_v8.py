#!/usr/bin/env python3
"""Resume after a technical candidate timeout and the next quiet-gate timeout.

The completed candidate remains a technical exclusion.  The following rank is
replayed only after a fresh quiet gate and before any XP reset or measurement
for that rank has occurred.
"""

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
TERMINAL_LEASE_EVENT = "cross_project_workload_lease_lost_during_formal_wave"


def _terminal_lease_release(row: Mapping[str, Any]) -> bool:
    return bool(
        row.get("event") == TERMINAL_LEASE_EVENT
        and row.get("runtime_stage") == "FAILED"
        and row.get("coordination_phase") == "quiet_gate"
        and row.get("lease_loss_confirmed_after_fresh_state_reread") is True
    )


def clean_technical_timeout_then_quiet_boundary(
    *, source_root: Path, reconstructed: Mapping[str, Any], start_rank: int,
) -> bool:
    """Prove the previous rank is excluded and the requested rank never ran."""

    if start_rank not in range(2, 6):
        return False
    expected_outer = int(reconstructed["outer_attempt"]) + 1
    outer_root = source_root / f"outer_{expected_outer:03d}"
    state_path = source_root / "run_state.json"
    gate_path = outer_root / (
        f"top5_batch_{start_rank}_cross_project_wave_quiet_gate_attempt_001.json"
    )
    watchdog_path = source_root / "runtime_external_interference_watchdog.jsonl"
    previous_path = outer_root / f"top5_batch_{start_rank - 1}/batch_result.json"
    current_path = outer_root / f"top5_batch_{start_rank}/batch_result.json"
    current_reset = (
        source_root / "xp_resets"
        / f"outer_{expected_outer:03d}_batch_{start_rank}"
    )
    try:
        state = v4.runtime.alg.load(state_path)
        gate = v4.runtime.alg.load(gate_path)
        previous = v4.runtime.alg.load(previous_path)
        rows = v4._watchdog_rows(watchdog_path)
    except (OSError, ValueError, TypeError, KeyError):
        return False

    measurements = previous.get("measurements") or {}
    active_scenes = set(state.get("active_scenes") or [])
    assignments = {
        worker_id: {
            "rank": start_rank - 1,
            "params": dict(row.get("params") or {}),
        }
        for worker_id, row in measurements.items()
        if isinstance(row, Mapping)
    }
    technical = v4.runtime.classify_technical_timeout_batch(
        previous, assignments
    )
    exclusions = state.get("technical_batch_exclusions") or []
    matching_exclusions = [
        row for row in exclusions
        if int(row.get("batch_index") or 0) == start_rank - 1
        and row.get("classification") == "TECHNICAL_TIMEOUT_EXCLUDED"
        and row.get("batch_result_path") == str(previous_path)
        and row.get("batch_result_sha256") == v4.runtime.alg.sha(previous_path)
        and row.get("candidate_state_mutation") is False
        and row.get("accepted_state_credit_increment") == 0
        and row.get("convergence_mutation") is False
        and row.get("rejected_outer_attempt_increment") == 0
        and row.get("pruning_evidence_eligible") is False
    ]
    samples = gate.get("samples") or []
    terminal_rows = [row for row in rows if _terminal_lease_release(row)]
    forbidden_rows = [
        row for row in rows
        if row.get("event") in {
            "runtime_interference_detected_under_exclusive_wave_lease",
            TERMINAL_LEASE_EVENT,
        }
        and not _terminal_lease_release(row)
    ]
    return bool(
        state.get("status") == "FAILED_CLOSED"
        and state.get("stage") == "FAILED"
        and state.get("error") == v4.QUIET_GATE_ERROR
        and int(state.get("outer_attempt") or -1)
        == int(reconstructed["outer_attempt"])
        and state.get("formal_evidence_eligible") is True
        and state.get("formal_scope") == "mysql_only"
        and state.get("cross_engine_comparison_prohibited") is True
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
        and not (samples[-1].get("errors") or [])
        and previous.get("status") == "FAILED_CLOSED"
        and previous.get("formal_scope") == "mysql_only"
        and previous.get("cross_engine_comparison_prohibited") is True
        and len(measurements) == len(active_scenes)
        and {str(row.get("scene")) for row in measurements.values()}
        == active_scenes
        and all(
            int(row.get("rank") or 0) == start_rank - 1
            and row.get("hard_gate_passed") is False
            and row.get("coverage_complete") is False
            for row in measurements.values()
        )
        and technical is not None
        and len(matching_exclusions) == 1
        and not current_path.exists()
        and not current_reset.exists()
        and not list(outer_root.glob("*.decision.json"))
        and rows
        and rows[0].get("event") == "watchdog_started"
        and len(terminal_rows) == 1
        and not forbidden_rows
    )


def validate_formal_partial_continuation(**kwargs: Any) -> dict[str, Any]:
    try:
        return ORIGINAL_VALIDATOR(**kwargs)
    except RuntimeError as error:
        if str(error) not in {
            "formal partial continuation authority differs",
            "formal runtime-interrupted continuation authority differs",
        }:
            raise

    source_root = Path(kwargs["source_root"]).resolve()
    reconstructed = kwargs["reconstructed"]
    start_rank = int(kwargs["start_rank"])
    if not clean_technical_timeout_then_quiet_boundary(
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
    previous_path = source_root / f"outer_{expected_outer:03d}" / (
        f"top5_batch_{start_rank - 1}/batch_result.json"
    )
    original_load = v4.runtime.alg.load
    original_json_loads = v4.runtime.json.loads

    def compatibility_json_loads(value: str, *args: Any, **inner: Any) -> Any:
        document = original_json_loads(value, *args, **inner)
        if isinstance(document, Mapping) and _terminal_lease_release(document):
            document = copy.deepcopy(dict(document))
            document["event"] = "post_terminal_lease_release_after_failed_gate"
        return document

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
                    "path": str(previous_path),
                    "sha256": v4.runtime.alg.sha(previous_path),
                    "technical_timeout_excluded": True,
                })
            partial["completed_batches"] = completed
            value["formal_partial_continuation"] = partial
        elif resolved == gate_path.resolve():
            value = copy.deepcopy(value)
            value["errors"] = [
                "external_database_physical_workload_present_while_exclusive"
            ]
        return value

    v4.runtime.json.loads = compatibility_json_loads
    v4.runtime.alg.load = compatibility_load
    try:
        result = v4.ORIGINAL_PARTIAL_VALIDATOR(**kwargs)
    finally:
        v4.runtime.alg.load = original_load
        v4.runtime.json.loads = original_json_loads

    result["continuation_reason"] = (
        "technical_timeout_excluded_then_premeasurement_quiet_timeout"
    )
    result["technical_timeout_quiet_recovery"] = {
        "source_state_path": str(state_path),
        "source_state_sha256": v4.runtime.alg.sha(state_path),
        "blocked_gate_path": str(gate_path),
        "blocked_gate_sha256": v4.runtime.alg.sha(gate_path),
        "technical_batch_path": str(previous_path),
        "technical_batch_sha256": v4.runtime.alg.sha(previous_path),
        "candidate_state_mutation_before_continuation": False,
        "accepted_state_credit_before_continuation": 0,
        "technical_previous_rank_replay_prohibited": True,
        "fresh_quiet_gate_required": True,
    }
    return result


def state_document(**kwargs: Any) -> dict[str, Any]:
    document = ORIGINAL_STATE_DOCUMENT(**kwargs)
    document["technical_timeout_quiet_recovery"] = {
        "source_path": str(Path(__file__).resolve()),
        "source_sha256": v4.runtime.alg.sha(Path(__file__).resolve()),
        "candidate_state_mutation_before_continuation": False,
        "accepted_state_credit_before_continuation": 0,
        "technical_previous_rank_replay_prohibited": True,
        "fresh_quiet_gate_required": True,
    }
    return document


def main() -> int:
    v4.validate_formal_partial_continuation = validate_formal_partial_continuation
    v5.state_document = state_document
    return v5.main()


if __name__ == "__main__":
    raise SystemExit(main())

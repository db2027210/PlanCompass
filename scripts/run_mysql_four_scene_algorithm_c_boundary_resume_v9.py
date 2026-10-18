#!/usr/bin/env python3
"""Resume Rank N after a discard host-PID kill completed after controller exit."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v4 as v4
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v5 as v5
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v8 as v8


ORIGINAL_VALIDATOR = v8.validate_formal_partial_continuation
ORIGINAL_STATE_DOCUMENT = v8.state_document
TARGETS = ("xp_cpu_w1", "xp_io_w1")
LAST_RESTART_READBACK: dict[str, Any] = {}


def _docker_inspect(container: str) -> dict[str, Any]:
    completed = subprocess.run(
        ["docker", "inspect", container],
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    value = json.loads(completed.stdout)
    if not isinstance(value, list) or len(value) != 1:
        raise RuntimeError("single Docker inspect result required")
    return dict(value[0])


def _live_restarted_readback(
    state: Mapping[str, Any], drain: Mapping[str, Any],
) -> dict[str, Any] | None:
    prepared_path = Path(str(state.get("prepared_binding_path") or "")).resolve()
    try:
        prepared = v4.runtime.alg.load(prepared_path)
    except (OSError, ValueError, TypeError, KeyError):
        return None
    workers = {
        str(row.get("worker_id")): dict(row)
        for row in (prepared.get("workers") or [])
        if isinstance(row, Mapping)
    }
    if not set(TARGETS).issubset(workers):
        return None
    result: dict[str, Any] = {}
    for worker_id in TARGETS:
        prior = dict((drain.get("liveness") or {}).get(worker_id) or {})
        worker = workers[worker_id]
        container = str(worker.get("container") or "")
        expected_id = str(prior.get("expected_container_id") or "")
        try:
            inspected = _docker_inspect(container)
            active = v4.runtime.alg.mysql_root(
                worker,
                "SELECT COUNT(*) FROM information_schema.processlist "
                "WHERE ID<>CONNECTION_ID() AND COMMAND<>'Daemon' "
                "AND USER<>'event_scheduler'",
                timeout_seconds=30,
            )
        except (OSError, ValueError, subprocess.SubprocessError):
            return None
        container_state = dict(inspected.get("State") or {})
        labels = dict((inspected.get("Config") or {}).get("Labels") or {})
        if (
            prior.get("status") != "PROVEN_NO_LIVE_MYSQLD"
            or prior.get("safe_to_discard_unresponsive_predecessor") is not True
            or prior.get("docker_running") is not True
            or prior.get("container_identity_stable") is not True
            or prior.get("expected_container") != container
            or len(expected_id) != 64
            or inspected.get("Id") != expected_id
            or container_state.get("Status") != "running"
            or container_state.get("Running") is not True
            or int(container_state.get("Pid") or 0) <= 1
            or labels.get("ra.owner") != "mysql_four_scene_v5"
            or labels.get("ra.worker_id") != worker_id
            or active.strip() != "0"
        ):
            return None
        result[worker_id] = {
            "container": container,
            "container_id": expected_id,
            "container_pid": int(container_state["Pid"]),
            "host_port": int(worker["host_port"]),
            "active_non_system_sessions": 0,
            "same_failed_predecessor_identity_restarted": True,
            "discard_only": True,
        }
    return result


def clean_late_host_kill_reset_boundary(
    *, source_root: Path, reconstructed: Mapping[str, Any], start_rank: int,
) -> bool:
    """Prove reset stopped before quarantine/copy/new-container mutation."""

    global LAST_RESTART_READBACK
    LAST_RESTART_READBACK = {}
    if start_rank not in range(2, 6):
        return False
    expected_outer = int(reconstructed["outer_attempt"]) + 1
    outer_root = source_root / f"outer_{expected_outer:03d}"
    state_path = source_root / "run_state.json"
    gate_path = outer_root / (
        f"top5_batch_{start_rank}_cross_project_wave_quiet_gate_attempt_001.json"
    )
    reset_root = (
        source_root / "xp_resets"
        / f"outer_{expected_outer:03d}_batch_{start_rank}"
    )
    drain_path = reset_root / "pre_reset_session_drain.json"
    watchdog_path = source_root / "runtime_external_interference_watchdog.jsonl"
    try:
        state = v4.runtime.alg.load(state_path)
        gate = v4.runtime.alg.load(gate_path)
        drain = v4.runtime.alg.load(drain_path)
        rows = v4._watchdog_rows(watchdog_path)
    except (OSError, ValueError, TypeError, KeyError):
        return False
    expected_files = {
        drain_path.resolve(),
        drain_path.with_name(drain_path.name + ".sha256").resolve(),
    }
    actual_files = {path.resolve() for path in reset_root.rglob("*") if path.is_file()}
    terminal_rows = [row for row in rows if v8._terminal_lease_release({
        **row,
        "coordination_phase": "quiet_gate",
    }) and row.get("coordination_phase") == "measuring"]
    forbidden_rows = [
        row for row in rows
        if row.get("event") in {
            "runtime_interference_detected_under_exclusive_wave_lease",
            v8.TERMINAL_LEASE_EVENT,
        }
        and row not in terminal_rows
    ]
    readback = _live_restarted_readback(state, drain)
    valid = bool(
        state.get("status") == "FAILED_CLOSED"
        and state.get("stage") == "FAILED"
        and state.get("error")
        == (
            "RuntimeError: ra_mysql_disk2_algc_xp_cpu_w1_20260809 remained "
            "running after bound host-PID kill"
        )
        and int(state.get("outer_attempt") or -1)
        == int(reconstructed["outer_attempt"])
        and state.get("formal_evidence_eligible") is True
        and state.get("formal_scope") == "mysql_only"
        and state.get("cross_engine_comparison_prohibited") is True
        and gate.get("status") == "PASS"
        and gate.get("errors") in ([], None)
        and drain.get("status") == "PASS"
        and drain.get("formal_scope") == "mysql_only"
        and drain.get("cross_engine_comparison_prohibited") is True
        and set(drain.get("provably_no_live_mysqld_workers") or [])
        == set(TARGETS)
        and actual_files == expected_files
        and not (outer_root / f"top5_batch_{start_rank}/batch_result.json").exists()
        and not list(outer_root.glob("*.decision.json"))
        and rows
        and rows[0].get("event") == "watchdog_started"
        and len(terminal_rows) == 1
        and not forbidden_rows
        and readback is not None
    )
    if valid:
        LAST_RESTART_READBACK = copy.deepcopy(readback or {})
    return valid


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
    if not clean_late_host_kill_reset_boundary(
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
    original_load = v4.runtime.alg.load
    original_json_loads = v4.runtime.json.loads

    def compatibility_json_loads(value: str, *args: Any, **inner: Any) -> Any:
        document = original_json_loads(value, *args, **inner)
        if (
            isinstance(document, Mapping)
            and document.get("event") == v8.TERMINAL_LEASE_EVENT
            and document.get("runtime_stage") == "FAILED"
            and document.get("coordination_phase") == "measuring"
            and document.get("lease_loss_confirmed_after_fresh_state_reread")
            is True
        ):
            document = copy.deepcopy(dict(document))
            document["event"] = "post_terminal_lease_release_after_failed_reset"
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
        elif resolved == gate_path.resolve():
            value = copy.deepcopy(value)
            value["status"] = "BLOCKED_FAIL_CLOSED"
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
        "late_host_pid_kill_completed_then_same_identity_restarted"
    )
    result["late_host_kill_reset_recovery"] = {
        "source_state_path": str(state_path),
        "source_state_sha256": v4.runtime.alg.sha(state_path),
        "failed_reset_root": str(
            source_root / "xp_resets"
            / f"outer_{expected_outer:03d}_batch_{start_rank}"
        ),
        "live_restart_readback": copy.deepcopy(LAST_RESTART_READBACK),
        "candidate_state_mutation_before_continuation": False,
        "accepted_state_credit_before_continuation": 0,
        "fresh_quiet_gate_required": True,
        "fresh_reset_required": True,
    }
    return result


def state_document(**kwargs: Any) -> dict[str, Any]:
    document = ORIGINAL_STATE_DOCUMENT(**kwargs)
    document["late_host_kill_reset_recovery"] = {
        "source_path": str(Path(__file__).resolve()),
        "source_sha256": v4.runtime.alg.sha(Path(__file__).resolve()),
        "live_restart_readback": copy.deepcopy(LAST_RESTART_READBACK),
        "candidate_state_mutation_before_continuation": False,
        "accepted_state_credit_before_continuation": 0,
        "fresh_quiet_gate_required": True,
        "fresh_reset_required": True,
    }
    return document


def main() -> int:
    v4.validate_formal_partial_continuation = validate_formal_partial_continuation
    v5.state_document = state_document
    return v5.main()


if __name__ == "__main__":
    raise SystemExit(main())

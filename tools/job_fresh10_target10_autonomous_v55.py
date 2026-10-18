#!/usr/bin/env python3
"""JOB v55 fresh0 controller with bounded sealed successor discovery."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import subprocess
import time
from typing import Any, Callable, Sequence

import job_fresh10_target10_autonomous_v50 as implementation


EXPECTED_IMPLEMENTATION_SHA256 = (
    "2cf4ecd0e1816c6e46013ef2c093b7b3caf7ca5d945cd107ab0bd97696da9804"
)
observed = hashlib.sha256(Path(implementation.__file__).read_bytes()).hexdigest()
if observed != EXPECTED_IMPLEMENTATION_SHA256:
    raise implementation.v49.FailClosed("v55 implementation SHA mismatch")

implementation.CONTROLLER_VERSION = "v55"
implementation.FRESH_ROOT = implementation.v49.T / "autonomous_target10_v55_fresh0"
implementation.FRESH_STATE = implementation.FRESH_ROOT / "package/formal_state.json"
implementation.FRESH_ACCEPTED_COUNTER = (
    implementation.FRESH_ROOT / "formal_search_accepted_counter.txt"
)
implementation.CAMPAIGN_CONTRACT = (
    "job_v55_fresh0_v54_postrun_seed_technical_excluded_v1"
)
implementation.EXPECTED_REMOTE_BRIDGE_SHA256 = (
    "406e56aea7b148840a64dff88b190468ed78f6f3683468553cbe31ed57baef23"
)
implementation.BASE_RUNNER_V7 = (
    Path(__file__).resolve().parent
    / "run_job_round8_strict_serial_base_v10_sealed_explain.py"
)
implementation.EXPECTED_BASE_RUNNER_V7_SHA256 = (
    "6640148153dceee614f04a96096883a1ef8ce12b6eaa84a761c59593640985d7"
)
implementation.v49.ROOT = implementation.FRESH_ROOT
implementation.v49.STATE = implementation.FRESH_STATE
implementation.v49.ACCEPTED_COUNTER = implementation.FRESH_ACCEPTED_COUNTER
implementation.v49.BASE_RUNNER = implementation.BASE_RUNNER_V7


# v50 is logically finite, but each of its two deterministic generator runs had
# a 7200-second timeout.  A cycle could therefore spend four silent hours before
# its first diagnostic replay.  v55 seals a much smaller, explicit state machine:
# exactly two generator slots, 30 minutes per slot, and 62 minutes wall clock for
# generation plus deterministic conversion/validation.
SUCCESSOR_GENERATOR_SLOTS = ("historical_training_a.json", "historical_training_b.json")
SUCCESSOR_GENERATOR_TIMEOUT_SECONDS = 1800
SUCCESSOR_DISCOVERY_WALL_SECONDS = 3720
SUCCESSOR_BUDGET_SCHEMA = "job_v55_successor_discovery_budget_v1"
SUCCESSOR_HANDOFF_SCHEMA = "job_v55_successor_first_replay_handoff_v1"

_ORIGINAL_RUN_LOGGED = implementation.v49.run_logged
_ORIGINAL_GENERATE_FRESH_DISCOVERY = implementation.generate_fresh_discovery
_ORIGINAL_OBTAIN_CLEAN_DISCOVERY = implementation.obtain_clean_discovery_v50


def _write_successor_budget(path: Path, value: dict[str, Any]) -> None:
    implementation.v49.atomic_json(path, value)


def _new_successor_budget(fresh_root: Path, replay_attempt: int) -> dict[str, Any]:
    now = time.time()
    return {
        "schema_version": SUCCESSOR_BUDGET_SCHEMA,
        "status": "generating",
        "replay_attempt": replay_attempt,
        "fresh_root": str(fresh_root.resolve()),
        "generator_slots": list(SUCCESSOR_GENERATOR_SLOTS),
        "generator_invocation_limit": len(SUCCESSOR_GENERATOR_SLOTS),
        "per_generator_timeout_seconds": SUCCESSOR_GENERATOR_TIMEOUT_SECONDS,
        "wall_timeout_seconds": SUCCESSOR_DISCOVERY_WALL_SECONDS,
        "started_unix_seconds": now,
        "deadline_unix_seconds": now + SUCCESSOR_DISCOVERY_WALL_SECONDS,
        "invocations": [],
        "workload_calls": 0,
        "formal_state_updates": 0,
    }


def _validate_successor_budget(
    value: dict[str, Any],
    fresh_root: Path,
    replay_attempt: int,
) -> None:
    invocations = value.get("invocations")
    try:
        started_unix_seconds = float(value.get("started_unix_seconds", float("nan")))
        deadline_unix_seconds = float(value.get("deadline_unix_seconds", float("nan")))
    except (TypeError, ValueError) as error:
        raise implementation.v49.FailClosed("invalid v55 successor deadline") from error
    if (
        value.get("schema_version") != SUCCESSOR_BUDGET_SCHEMA
        or int(value.get("replay_attempt", -1)) != replay_attempt
        or value.get("fresh_root") != str(fresh_root.resolve())
        or value.get("generator_slots") != list(SUCCESSOR_GENERATOR_SLOTS)
        or int(value.get("generator_invocation_limit", -1))
        != len(SUCCESSOR_GENERATOR_SLOTS)
        or int(value.get("per_generator_timeout_seconds", -1))
        != SUCCESSOR_GENERATOR_TIMEOUT_SECONDS
        or int(value.get("wall_timeout_seconds", -1))
        != SUCCESSOR_DISCOVERY_WALL_SECONDS
        or not isinstance(invocations, list)
        or len(invocations) > len(SUCCESSOR_GENERATOR_SLOTS)
        or int(value.get("workload_calls", -1)) != 0
        or int(value.get("formal_state_updates", -1)) != 0
        or not math.isfinite(started_unix_seconds)
        or not math.isfinite(deadline_unix_seconds)
        or abs(
            deadline_unix_seconds
            - started_unix_seconds
            - SUCCESSOR_DISCOVERY_WALL_SECONDS
        )
        > 1e-6
        or value.get("status")
        not in {
            "generating",
            "ready_for_first_replay",
            "replay_handoff_started",
            "failed",
            "timeout",
        }
    ):
        raise implementation.v49.FailClosed("v55 successor budget contract mismatch")
    seen_slots: set[str] = set()
    for invocation in invocations:
        if not isinstance(invocation, dict):
            raise implementation.v49.FailClosed("invalid v55 successor invocation audit")
        slot = str(invocation.get("slot") or "")
        if slot not in SUCCESSOR_GENERATOR_SLOTS or slot in seen_slots:
            raise implementation.v49.FailClosed("duplicate/unknown v55 generator slot")
        seen_slots.add(slot)
        if invocation.get("status") not in {"started", "complete", "failed", "timeout"}:
            raise implementation.v49.FailClosed("invalid v55 generator slot status")


def _bounded_generate_fresh_discovery(
    cycle_root: Path,
    replay_attempt: int,
    **kwargs: Any,
) -> Path:
    fresh_root = cycle_root / f"fresh_discovery_{replay_attempt:02d}"
    fresh_root.mkdir(parents=True, exist_ok=True)
    budget_path = fresh_root / "v55_successor_budget.json"
    artifact_paths = [fresh_root / name for name in SUCCESSOR_GENERATOR_SLOTS]
    output = fresh_root / "top5.json"
    second = fresh_root / "top5.second.json"
    preexisting = [
        path
        for path in [*artifact_paths, output, second]
        if path.exists()
    ]
    if budget_path.exists():
        budget = implementation.v49.read_json(budget_path)
        _validate_successor_budget(budget, fresh_root, replay_attempt)
    else:
        if preexisting:
            raise implementation.v49.FailClosed(
                "unbudgeted v55 successor artifacts already exist"
            )
        budget = _new_successor_budget(fresh_root, replay_attempt)
        _write_successor_budget(budget_path, budget)

    if budget.get("status") in {"failed", "timeout"}:
        raise implementation.v49.FailClosed("sealed v55 successor failure cannot be retried")
    if any(item.get("status") in {"started", "failed", "timeout"} for item in budget["invocations"]):
        raise implementation.v49.FailClosed("incomplete v55 generator invocation is sealed")
    deadline = float(budget.get("deadline_unix_seconds", 0.0))
    if not math.isfinite(deadline) or time.time() >= deadline:
        budget["status"] = "timeout"
        budget["failure"] = "successor discovery wall-clock deadline expired"
        _write_successor_budget(budget_path, budget)
        raise implementation.v49.FailClosed("v55 successor wall-clock budget exhausted")
    session_started = time.monotonic()
    session_wall_remaining = deadline - time.time()

    def wall_remaining_seconds() -> float:
        return min(
            deadline - time.time(),
            session_wall_remaining - (time.monotonic() - session_started),
        )

    expected_missing = {
        path.name for path in artifact_paths if not path.is_file()
    }
    completed_slots = {
        str(item["slot"])
        for item in budget["invocations"]
        if item.get("status") == "complete"
    }
    if completed_slots != {path.name for path in artifact_paths if path.is_file()}:
        raise implementation.v49.FailClosed("v55 successor artifact/audit progress mismatch")
    if len(budget["invocations"]) + len(expected_missing) > len(SUCCESSOR_GENERATOR_SLOTS):
        raise implementation.v49.FailClosed("v55 successor invocation limit would be exceeded")

    def bounded_run_logged(
        command: Sequence[str],
        log_path: Path,
        *,
        timeout_seconds: int,
        allowed_returncodes: Sequence[int] = (0,),
        cwd: Path | None = None,
    ) -> int:
        del timeout_seconds
        command_list = list(command)
        try:
            output_index = command_list.index("--output") + 1
            target = Path(command_list[output_index]).resolve()
        except (ValueError, IndexError) as error:
            raise implementation.v49.FailClosed(
                "v55 generator command has no sealed output slot"
            ) from error
        allowed_targets = {path.resolve(): path.name for path in artifact_paths}
        slot = allowed_targets.get(target)
        if slot is None or slot not in expected_missing:
            raise implementation.v49.FailClosed("v55 generator targeted an invalid/reused slot")
        if len(budget["invocations"]) >= len(SUCCESSOR_GENERATOR_SLOTS):
            raise implementation.v49.FailClosed("v55 successor generator invocation cap reached")
        remaining = int(math.floor(wall_remaining_seconds()))
        if remaining <= 0:
            raise implementation.v49.FailClosed("v55 successor wall-clock budget exhausted")
        effective_timeout = min(SUCCESSOR_GENERATOR_TIMEOUT_SECONDS, remaining)
        invocation = {
            "ordinal": len(budget["invocations"]) + 1,
            "slot": slot,
            "status": "started",
            "timeout_seconds": effective_timeout,
            "log_path": str(log_path.resolve()),
        }
        budget["invocations"].append(invocation)
        _write_successor_budget(budget_path, budget)
        started = time.monotonic()
        try:
            result = _ORIGINAL_RUN_LOGGED(
                command_list,
                log_path,
                timeout_seconds=effective_timeout,
                allowed_returncodes=allowed_returncodes,
                cwd=cwd,
            )
        except subprocess.TimeoutExpired as error:
            invocation["status"] = "timeout"
            invocation["elapsed_seconds"] = time.monotonic() - started
            budget["status"] = "timeout"
            budget["failure"] = f"generator slot {slot} timed out"
            _write_successor_budget(budget_path, budget)
            if not log_path.exists():
                log_path.write_text(
                    json.dumps({"status": "timeout", "slot": slot}) + "\n",
                    encoding="utf-8",
                )
            raise implementation.v49.FailClosed(
                f"v55 successor generator timed out: {slot}"
            ) from error
        except Exception as error:
            invocation["status"] = "failed"
            invocation["elapsed_seconds"] = time.monotonic() - started
            budget["status"] = "failed"
            budget["failure"] = f"generator slot {slot} failed: {type(error).__name__}"
            _write_successor_budget(budget_path, budget)
            raise
        if not target.is_file() or target.stat().st_size <= 0:
            invocation["status"] = "failed"
            invocation["elapsed_seconds"] = time.monotonic() - started
            budget["status"] = "failed"
            budget["failure"] = f"generator slot {slot} produced no artifact"
            _write_successor_budget(budget_path, budget)
            raise implementation.v49.FailClosed("v55 generator made no artifact progress")
        invocation["status"] = "complete"
        invocation["elapsed_seconds"] = time.monotonic() - started
        invocation["artifact_sha256"] = implementation.v49.sha256_file(target)
        expected_missing.remove(slot)
        _write_successor_budget(budget_path, budget)
        return result

    previous_run_logged: Callable[..., int] = implementation.v49.run_logged
    implementation.v49.run_logged = bounded_run_logged
    try:
        try:
            result = _ORIGINAL_GENERATE_FRESH_DISCOVERY(
                cycle_root,
                replay_attempt,
                **kwargs,
            )
        except Exception as error:
            if budget.get("status") == "generating":
                budget["status"] = "failed"
                budget["failure"] = (
                    "successor generation/validation failed: "
                    f"{type(error).__name__}"
                )
                _write_successor_budget(budget_path, budget)
            raise
    finally:
        implementation.v49.run_logged = previous_run_logged
    if result.resolve() != output.resolve() or not output.is_file() or not second.is_file():
        budget["status"] = "failed"
        budget["failure"] = "deterministic successor output pair is incomplete"
        _write_successor_budget(budget_path, budget)
        raise implementation.v49.FailClosed("v55 successor output pair is incomplete")
    if wall_remaining_seconds() <= 0:
        budget["status"] = "timeout"
        budget["failure"] = "successor validation crossed wall-clock deadline"
        _write_successor_budget(budget_path, budget)
        raise implementation.v49.FailClosed("v55 successor validation exceeded wall budget")
    if len(budget["invocations"]) != len(SUCCESSOR_GENERATOR_SLOTS):
        raise implementation.v49.FailClosed("v55 successor did not seal exactly two generators")
    budget.update(
        {
            "status": "ready_for_first_replay",
            "selection_path": str(output.resolve()),
            "selection_sha256": implementation.v49.sha256_file(output),
            "second_selection_sha256": implementation.v49.sha256_file(second),
            "generation_elapsed_seconds": time.time()
            - float(budget["started_unix_seconds"]),
        }
    )
    _write_successor_budget(budget_path, budget)
    return result


def _guarded_obtain_clean_discovery(
    initial_discovery: Path,
    cycle_root: Path,
    **kwargs: Any,
) -> tuple[Path, Path, int]:
    budget_path = initial_discovery.parent / "v55_successor_budget.json"
    if not budget_path.is_file():
        raise implementation.v49.FailClosed("v55 first replay has no successor budget")
    budget = implementation.v49.read_json(budget_path)
    replay_attempt = int(initial_discovery.parent.name.rsplit("_", 1)[-1])
    _validate_successor_budget(budget, initial_discovery.parent, replay_attempt)
    if (
        budget.get("status") not in {"ready_for_first_replay", "replay_handoff_started"}
        or budget.get("selection_path") != str(initial_discovery.resolve())
        or budget.get("selection_sha256") != implementation.v49.sha256_file(initial_discovery)
        or len(budget["invocations"]) != len(SUCCESSOR_GENERATOR_SLOTS)
        or any(item.get("status") != "complete" for item in budget["invocations"])
    ):
        raise implementation.v49.FailClosed("v55 first replay handoff is not sealed")
    handoff = {
        "schema_version": SUCCESSOR_HANDOFF_SCHEMA,
        "status": "replay_handoff_started",
        "selection_path": str(initial_discovery.resolve()),
        "selection_sha256": implementation.v49.sha256_file(initial_discovery),
        "generator_invocation_count": len(budget["invocations"]),
        "generator_invocation_limit": len(SUCCESSOR_GENERATOR_SLOTS),
        "workload_calls_before_handoff": 0,
        "formal_state_updates_before_handoff": 0,
    }
    handoff_path = initial_discovery.parent / "v55_first_replay_handoff.json"
    if handoff_path.exists() and implementation.v49.read_json(handoff_path) != handoff:
        raise implementation.v49.FailClosed("v55 first replay handoff changed")
    if not handoff_path.exists():
        implementation.v49.atomic_json(handoff_path, handoff)
    budget["status"] = "replay_handoff_started"
    _write_successor_budget(budget_path, budget)
    return _ORIGINAL_OBTAIN_CLEAN_DISCOVERY(
        initial_discovery,
        cycle_root,
        **kwargs,
    )


implementation.generate_fresh_discovery = _bounded_generate_fresh_discovery
implementation.obtain_clean_discovery_v50 = _guarded_obtain_clean_discovery


if __name__ == "__main__":
    try:
        raise SystemExit(implementation.main())
    except implementation.v49.FailClosed as error:
        try:
            implementation.write_controller_state(status="fail_closed", error=str(error))
        except Exception:
            pass
        print(
            f"JOB target10 v55 fail closed: {error}",
            file=implementation.sys.stderr,
            flush=True,
        )
        raise SystemExit(2)

#!/usr/bin/env python3
"""JOB cache primer plus a true 900-second scored-suite deadline."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import threading
import time
from typing import Any, Mapping

from scripts import mysql_job_buffer_pool_preconditioned_measurement_v9 as v9


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    ROOT / "config/formal/frozen_mysql_job_cache_state_20260823_v10"
    / "cache_state_contract.json"
)
SCORED_SUITE_TIMEOUT_SECONDS = 900.0

legacy = v9.legacy
alg = v9.alg
predecessor = v9.predecessor
PRIMER_PARAMS = copy.deepcopy(v9.PRIMER_PARAMS)


class SuiteDeadlineRunner:
    """Clamp every subprocess to one shared suite-level monotonic deadline."""

    def __init__(
        self, delegate: Any, limit_seconds: float,
        *, clock: Any = time.monotonic,
    ) -> None:
        if limit_seconds <= 0:
            raise ValueError("suite deadline must be positive")
        self.delegate = delegate
        self.limit_seconds = float(limit_seconds)
        self.clock = clock
        self.started_monotonic = float(clock())
        self.deadline_monotonic = self.started_monotonic + self.limit_seconds
        self.call_count = 0
        self.deadline_trigger_count = 0
        self.minimum_effective_timeout_seconds: float | None = None
        self._lock = threading.Lock()

    def __call__(self, argv: Any, *args: Any, **kwargs: Any) -> Any:
        remaining = self.deadline_monotonic - float(self.clock())
        with self._lock:
            self.call_count += 1
        if remaining <= 0.0:
            with self._lock:
                self.deadline_trigger_count += 1
            raise subprocess.TimeoutExpired(argv, self.limit_seconds)
        requested = kwargs.get("timeout")
        effective = remaining if requested is None else min(float(requested), remaining)
        if effective <= 0.0:
            with self._lock:
                self.deadline_trigger_count += 1
            raise subprocess.TimeoutExpired(argv, self.limit_seconds)
        kwargs["timeout"] = effective
        with self._lock:
            if (
                self.minimum_effective_timeout_seconds is None
                or effective < self.minimum_effective_timeout_seconds
            ):
                self.minimum_effective_timeout_seconds = effective
        try:
            return self.delegate(argv, *args, **kwargs)
        except subprocess.TimeoutExpired:
            with self._lock:
                self.deadline_trigger_count += 1
            raise

    def evidence(self, *, output_dir: str, elapsed_seconds: float) -> dict[str, Any]:
        return {
            "output_dir": output_dir,
            "limit_seconds": self.limit_seconds,
            "elapsed_seconds": elapsed_seconds,
            "delegate_call_count": self.call_count,
            "deadline_trigger_count": self.deadline_trigger_count,
            "deadline_triggered": self.deadline_trigger_count > 0,
            "minimum_effective_timeout_seconds": self.minimum_effective_timeout_seconds,
            "deadline_scope": "one_complete_scored_job113_suite",
            "subprocess_timeout_clamped_to_remaining_suite_time": True,
        }


def _validate_contract() -> dict[str, Any]:
    if CONTRACT_PATH.is_symlink() or not CONTRACT_PATH.is_file():
        raise RuntimeError(f"JOB buffer-pool v10 contract absent: {CONTRACT_PATH}")
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    if (
        contract.get("schema_version") != "mysql_job_cache_state_contract_v10"
        or contract.get("release_version") != "v10"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("scene") != "job_memory"
        or contract.get("v9_cache_precondition_retained") is not True
        or contract.get("precondition_wall_time_used_for_validity") is not False
        or contract.get("formal_measurement_replays_per_candidate") != 1
        or float(contract.get("scored_suite_hard_timeout_seconds") or 0)
        != SCORED_SUITE_TIMEOUT_SECONDS
        or contract.get("timeout_scope") != "complete_scored_job113_suite"
        or contract.get("timeout_is_active_termination") is not True
        or contract.get("timed_out_candidate_is_technical_only") is not True
        or contract.get("outer_loop_semantics_changed") is not False
    ):
        raise RuntimeError("JOB buffer-pool v10 contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        source = ROOT / str(binding.get("path") or "")
        if (
            source.is_symlink() or not source.is_file()
            or legacy._sha(source) != binding.get("sha256")
        ):
            raise RuntimeError(f"JOB buffer-pool v10 source differs: {source}")
    v9._validate_contract()
    return contract


def measure_batch(
    freeze: Mapping[str, Any], workers: dict[str, dict[str, Any]],
    assignments: Mapping[str, Mapping[str, Any]], output: Path,
    reset_token: str, *, state_path: Path | None = None,
    stage_prefix: str = "FORMAL_POINT",
) -> dict[str, dict[str, Any]]:
    contract = _validate_contract()
    scenes = {str(workers[worker_id]["scene"]) for worker_id in assignments}
    precondition = None
    if scenes == {"job_memory"}:
        precondition = v9._precondition(
            workers, assignments, output,
            state_path=state_path, stage_prefix=stage_prefix,
        )

    original_run_job = predecessor.alg.run_job
    deadline_records: list[dict[str, Any]] = []

    def hard_deadline_run_job(*args: Any, **kwargs: Any) -> dict[str, Any]:
        delegate = kwargs.get("runner")
        if delegate is None:
            raise RuntimeError("JOB scored-suite runner is absent")
        runner = SuiteDeadlineRunner(delegate, SCORED_SUITE_TIMEOUT_SECONDS)
        kwargs["runner"] = runner
        suite_output = str(
            kwargs.get("output_dir")
            or (args[2] if len(args) >= 3 else output)
        )
        started = time.monotonic()
        try:
            return original_run_job(*args, **kwargs)
        finally:
            deadline_records.append(
                runner.evidence(
                    output_dir=suite_output,
                    elapsed_seconds=time.monotonic() - started,
                )
            )

    predecessor.alg.run_job = hard_deadline_run_job
    try:
        measured = predecessor.measure_batch(
            freeze, workers, assignments, output, reset_token,
            state_path=state_path, stage_prefix=stage_prefix,
        )
    finally:
        predecessor.alg.run_job = original_run_job

    batch_path = output / "batch_result.json"
    document = alg.load(batch_path)
    document["job_cache_state_contract"] = {
        "path": str(CONTRACT_PATH),
        "sha256": legacy._sha(CONTRACT_PATH),
        "release_version": contract["release_version"],
        "applied": precondition is not None,
    }
    document["job_buffer_pool_precondition"] = precondition
    document["precondition_metrics_used_for_scoring"] = False
    document["precondition_wall_time_used_for_validity"] = False
    document["scored_suite_hard_timeout"] = {
        "limit_seconds": SCORED_SUITE_TIMEOUT_SECONDS,
        "scope": "complete_scored_job113_suite",
        "active_termination": True,
        "timed_out_candidate_is_technical_only": True,
        "worker_suites": sorted(
            deadline_records, key=lambda row: row["output_dir"],
        ),
    }
    document["outer_loop_semantics_changed"] = False
    alg.atomic(batch_path, document)
    return measured


__all__ = [
    "CONTRACT_PATH", "PRIMER_PARAMS", "SCORED_SUITE_TIMEOUT_SECONDS",
    "SuiteDeadlineRunner", "measure_batch",
]

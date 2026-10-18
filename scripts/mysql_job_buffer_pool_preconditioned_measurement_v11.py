#!/usr/bin/env python3
"""Four-worker JOB measurement with suite-start staggering.

Each JOB suite still executes all 113 queries exactly once and retains its
own active 900-second scored-suite deadline.  Only the starts of the four
independent suites are offset so their longest-query bursts are not launched
in lockstep on shared storage.
"""

from __future__ import annotations

import json
from pathlib import Path
import threading
import time
from typing import Any, Mapping

from scripts import mysql_job_buffer_pool_preconditioned_measurement_v10 as v10


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    ROOT / "config/formal/frozen_mysql_job_cache_state_20260824_v11"
    / "cache_state_contract.json"
)
WORKER_START_STAGGER_SECONDS = 90.0

alg = v10.alg
legacy = v10.legacy
PRIMER_PARAMS = v10.PRIMER_PARAMS
SCORED_SUITE_TIMEOUT_SECONDS = v10.SCORED_SUITE_TIMEOUT_SECONDS


def _validate_contract() -> dict[str, Any]:
    if CONTRACT_PATH.is_symlink() or not CONTRACT_PATH.is_file():
        raise RuntimeError(f"JOB buffer-pool v11 contract absent: {CONTRACT_PATH}")
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    if (
        contract.get("schema_version") != "mysql_job_cache_state_contract_v11"
        or contract.get("release_version") != "v11"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("scene") != "job_memory"
        or float(contract.get("worker_start_stagger_seconds") or -1.0)
        != WORKER_START_STAGGER_SECONDS
        or contract.get("stagger_order")
        != ["job_w1", "job_w2", "job_w3", "job_w4"]
        or contract.get("full_job_113_once_per_measurement") is not True
        or contract.get("per_suite_hard_timeout_seconds") != 900
        or contract.get("per_suite_deadline_begins_after_start_offset") is not True
        or contract.get("physical_replay_parallelism") != 4
        or contract.get("candidate_set_changed") is not False
        or contract.get("outer_loop_semantics_changed") is not False
        or contract.get("resource_envelope_changed") is not False
    ):
        raise RuntimeError("JOB buffer-pool v11 contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        source = ROOT / str(binding.get("path") or "")
        if (
            source.is_symlink() or not source.is_file()
            or legacy._sha(source) != binding.get("sha256")
        ):
            raise RuntimeError(f"JOB buffer-pool v11 source differs: {source}")
    v10._validate_contract()
    return contract


def measure_batch(
    freeze: Mapping[str, Any], workers: dict[str, dict[str, Any]],
    assignments: Mapping[str, Mapping[str, Any]], output: Path,
    reset_token: str, *, state_path: Path | None = None,
    stage_prefix: str = "FORMAL_POINT",
) -> dict[str, dict[str, Any]]:
    _validate_contract()
    worker_ids = sorted(assignments)
    if worker_ids != ["job_w1", "job_w2", "job_w3", "job_w4"]:
        raise RuntimeError("JOB v11 requires the frozen four-worker cohort")

    original_job_measure = v10.predecessor.job_measure
    starts: list[dict[str, Any]] = []
    lock = threading.Lock()

    def staggered_job_measure(
        pair: int, runtime_workers: Mapping[str, dict[str, Any]],
        runtime_output: Path, runners: Mapping[str, Any],
    ) -> dict[str, Any]:
        worker_id = f"job_w{pair}"
        delay = (pair - 1) * WORKER_START_STAGGER_SECONDS
        phase = (
            "cache_precondition"
            if runtime_output.name.endswith("_job_cache_precondition")
            else "scored_measurement"
        )
        if delay:
            time.sleep(delay)
        with lock:
            starts.append({
                "worker_id": worker_id,
                "phase": phase,
                "configured_start_offset_seconds": delay,
                "started_monotonic": time.monotonic(),
            })
        return original_job_measure(pair, runtime_workers, runtime_output, runners)

    v10.predecessor.job_measure = staggered_job_measure
    try:
        measured = v10.measure_batch(
            freeze, workers, assignments, output, reset_token,
            state_path=state_path, stage_prefix=stage_prefix,
        )
    finally:
        v10.predecessor.job_measure = original_job_measure

    batch_path = output / "batch_result.json"
    document = alg.load(batch_path)
    document["job_four_worker_start_stagger"] = {
        "contract_path": str(CONTRACT_PATH),
        "contract_sha256": legacy._sha(CONTRACT_PATH),
        "worker_start_stagger_seconds": WORKER_START_STAGGER_SECONDS,
        "stagger_order": worker_ids,
        "per_suite_deadline_begins_after_start_offset": True,
        "starts": sorted(
            starts,
            key=lambda row: (row["phase"], row["configured_start_offset_seconds"]),
        ),
    }
    document["outer_loop_semantics_changed"] = False
    document["candidate_set_changed"] = False
    document["resource_envelope_changed"] = False
    alg.atomic(batch_path, document)
    return measured


__all__ = [
    "CONTRACT_PATH", "PRIMER_PARAMS", "SCORED_SUITE_TIMEOUT_SECONDS",
    "WORKER_START_STAGGER_SECONDS", "measure_batch",
]

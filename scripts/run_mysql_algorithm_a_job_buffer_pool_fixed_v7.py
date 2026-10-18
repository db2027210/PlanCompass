#!/usr/bin/env python3
"""Run Algorithm A JOB with the v7 InnoDB primer and read-only retry."""

from __future__ import annotations

from pathlib import Path
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_job_buffer_pool_preconditioned_measurement_v7 as measurement
from scripts import run_mysql_algorithm_a_async_scene_v3 as base

_ORIGINAL_VALIDATE = base.validate_fresh_authority
RETRY_SECONDS = 600.0
RETRY_INTERVAL_SECONDS = 0.5
BLOCKED_PREFIX = "fresh R0 point non-interference gate blocked:"


def _output_dir() -> Path:
    return Path(sys.argv[sys.argv.index("--output-dir") + 1]).expanduser().resolve()


def validate_fresh_authority(**kwargs: Any):
    started = time.monotonic()
    attempts = 0
    first_error = None
    while True:
        attempts += 1
        try:
            result = _ORIGINAL_VALIDATE(**kwargs)
            measurement._validate_contract()
            base.runtime.alg.measure_batch = measurement.measure_batch
            base.alg.atomic(_output_dir() / "prestart_read_only_retry.json", {
                "schema_version": "mysql_job_prestart_read_only_retry_v1",
                "status": "PASS", "formal_scope": "mysql_only",
                "cross_engine_comparison_prohibited": True,
                "attempts": attempts,
                "wait_seconds": time.monotonic() - started,
                "retry_interval_seconds": RETRY_INTERVAL_SECONDS,
                "maximum_wait_seconds": RETRY_SECONDS,
                "first_blocker": first_error,
                "external_process_mutation": False,
                "optimizer_or_measurement_state_mutated_while_waiting": False,
            })
            return result
        except RuntimeError as error:
            message = str(error)
            if not message.startswith(BLOCKED_PREFIX):
                raise
            if first_error is None:
                first_error = message
            if time.monotonic() - started >= RETRY_SECONDS:
                raise TimeoutError("JOB v7 read-only prestart retry timed out") from error
            time.sleep(RETRY_INTERVAL_SECONDS)


def main() -> int:
    if "--scene" not in sys.argv or sys.argv[sys.argv.index("--scene") + 1] != "job_memory":
        raise RuntimeError("JOB buffer-pool v7 is JOB-only")
    measurement._validate_contract()
    base.validate_fresh_authority = validate_fresh_authority
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())

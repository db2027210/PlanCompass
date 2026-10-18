#!/usr/bin/env python3
"""Run page-cache-fixed JOB with bounded read-only prestart retries."""

from __future__ import annotations

from pathlib import Path
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_job_page_cache_preconditioned_measurement_v6 as cache_measurement
from scripts import run_mysql_algorithm_a_async_scene_v3 as predecessor

_ORIGINAL_VALIDATE_FRESH_AUTHORITY = predecessor.validate_fresh_authority
RETRY_SECONDS = 600.0
RETRY_INTERVAL_SECONDS = 0.5
BLOCKED_PREFIX = "fresh R0 point non-interference gate blocked:"


def _output_dir() -> Path:
    index = sys.argv.index("--output-dir") + 1
    return Path(sys.argv[index]).expanduser().resolve()


def validate_fresh_authority(**kwargs: Any):
    started = time.monotonic()
    attempts = 0
    first_error = None
    while True:
        attempts += 1
        try:
            result = _ORIGINAL_VALIDATE_FRESH_AUTHORITY(**kwargs)
            cache_measurement._validate_contract()
            predecessor.runtime.alg.measure_batch = cache_measurement.measure_batch
            predecessor.alg.atomic(_output_dir() / "prestart_read_only_retry.json", {
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
                raise TimeoutError("JOB read-only prestart retry timed out") from error
            time.sleep(RETRY_INTERVAL_SECONDS)


def main() -> int:
    if "--scene" not in sys.argv or "--output-dir" not in sys.argv:
        raise RuntimeError("page-cache-fixed Algorithm A requires scene and output")
    index = sys.argv.index("--scene") + 1
    if index >= len(sys.argv) or sys.argv[index] != "job_memory":
        raise RuntimeError("page-cache-fixed Algorithm A is JOB-only")
    cache_measurement._validate_contract()
    predecessor.validate_fresh_authority = validate_fresh_authority
    return predecessor.main()


if __name__ == "__main__":
    raise SystemExit(main())

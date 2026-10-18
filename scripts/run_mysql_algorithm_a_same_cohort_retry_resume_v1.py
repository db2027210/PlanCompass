#!/usr/bin/env python3
"""Resume same-cohort Algorithm A with a bounded read-only prestart retry."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_a_async_scene_v3 as base
from scripts import run_mysql_algorithm_a_same_cohort_codefix_resume_v1 as resume

CONTRACT = (
    ROOT / "config/formal/frozen_mysql_algorithm_a_same_cohort_retry_20260823_v1"
    / "retry_contract.json"
)
RETRY_SECONDS = 600.0
RETRY_INTERVAL_SECONDS = 0.5
BLOCKED_PREFIX = "fresh R0 point non-interference gate blocked:"
_ORIGINAL_VALIDATE = base.validate_fresh_authority


def _sha(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_contract() -> None:
    value = json.loads(CONTRACT.read_text(encoding="utf-8"))
    if (
        CONTRACT.is_symlink()
        or value.get("schema_version")
        != "mysql_algorithm_a_same_cohort_prestart_retry_v1"
        or value.get("status") != "FROZEN"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("maximum_wait_seconds") != RETRY_SECONDS
        or value.get("retry_interval_seconds") != RETRY_INTERVAL_SECONDS
        or value.get("external_process_mutation") is not False
        or value.get("optimizer_or_measurement_state_mutation_while_waiting") is not False
    ):
        raise RuntimeError("same-cohort prestart retry contract differs")
    for binding in (value.get("source_bindings") or {}).values():
        path = ROOT / str(binding["path"])
        if path.is_symlink() or not path.is_file() or _sha(path) != binding["sha256"]:
            raise RuntimeError(f"same-cohort retry source differs: {path}")


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
            base.atomic(_output_dir() / "prestart_read_only_retry.json", {
                "schema_version": "mysql_algorithm_a_prestart_read_only_retry_v1",
                "status": "PASS",
                "formal_scope": "mysql_only",
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
                raise TimeoutError("same-cohort read-only prestart retry timed out") from error
            time.sleep(RETRY_INTERVAL_SECONDS)


def main() -> int:
    _validate_contract()
    base.validate_fresh_authority = validate_fresh_authority
    return resume.main()


if __name__ == "__main__":
    raise SystemExit(main())

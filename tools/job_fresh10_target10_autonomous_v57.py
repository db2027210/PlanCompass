#!/usr/bin/env python3
"""JOB v57 fresh0 controller with explicit binding and robust resource scoring."""

from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
from typing import Any

import job_fresh10_target10_autonomous_v55 as v55


implementation = v55.implementation
EXPECTED_V55_SHA256 = "cc9b19977c140a2d42d0c33abf11250e607afc65fbff0f89eb5d3d6c8a357dd0"
if hashlib.sha256(Path(v55.__file__).read_bytes()).hexdigest() != EXPECTED_V55_SHA256:
    raise implementation.v49.FailClosed("v57 sealed v55 controller SHA mismatch")

TOOLS_DIR = Path(__file__).resolve().parent
BASE_V11 = TOOLS_DIR / "run_job_round8_strict_serial_base_v11_explicit_selection.py"
BASE_V10 = TOOLS_DIR / "run_job_round8_strict_serial_base_v10_sealed_explain.py"
ROBUST_V10 = TOOLS_DIR / "run_job_round8_strict_serial_robust_v10_resource_median.py"
ROBUST_V9 = TOOLS_DIR / "run_job_round8_strict_serial_robust_v9_state_guard.py"
ROBUST_V8 = TOOLS_DIR / "run_job_round8_strict_serial_robust_v8_apply_failure.py"
EXPECTED = {
    BASE_V11: "13ad9a6a78c6438794c9c36688b91e0398f8ae5a8eae115c56e4161adc8c23ab",
    BASE_V10: "6640148153dceee614f04a96096883a1ef8ce12b6eaa84a761c59593640985d7",
    ROBUST_V10: "e4738c9eb95e123a38e31410bb067bf817bb8b54e234acbb2f5bc14ed89f2b03",
    ROBUST_V9: "e717449a109669d6ed274e29d5aaf9f4285af8b37c586aeb0ad768970ba02693",
    ROBUST_V8: "5d965cd9410fba78486856dc2d78bd594b9ebf1d17eda4a8f593a5f43d6fa857",
}

implementation.CONTROLLER_VERSION = "v57"
implementation.FRESH_ROOT = implementation.v49.T / "autonomous_target10_v57_fresh0"
implementation.FRESH_STATE = implementation.FRESH_ROOT / "package/formal_state.json"
implementation.FRESH_ACCEPTED_COUNTER = implementation.FRESH_ROOT / "formal_search_accepted_counter.txt"
implementation.CAMPAIGN_CONTRACT = "job_v57_fresh0_resource_median_no_retry_v1"
implementation.BASE_RUNNER_V7 = BASE_V11
implementation.EXPECTED_BASE_RUNNER_V7_SHA256 = EXPECTED[BASE_V11]
implementation.ROBUST_RUNNER_V9 = ROBUST_V10
implementation.EXPECTED_ROBUST_RUNNER_V9_SHA256 = EXPECTED[ROBUST_V10]
implementation.ROBUST_RUNNER_V8_MODULE = ROBUST_V8
implementation.v49.ROOT = implementation.FRESH_ROOT
implementation.v49.STATE = implementation.FRESH_STATE
implementation.v49.ACCEPTED_COUNTER = implementation.FRESH_ACCEPTED_COUNTER
implementation.v49.BASE_RUNNER = BASE_V11
implementation.v49.ROBUST_RUNNER = ROBUST_V10

_PREPARE_FORMAL_V55 = implementation.prepare_formal_v50


def _sealed_copy(source: Path, target: Path) -> None:
    expected = EXPECTED[source]
    if not source.is_file() or implementation.v49.sha256_file(source) != expected:
        raise implementation.v49.FailClosed(f"v57 dependency SHA mismatch: {source}")
    if not target.exists():
        shutil.copy2(source, target)
    if implementation.v49.sha256_file(target) != expected:
        raise implementation.v49.FailClosed(f"v57 packaged dependency mismatch: {target}")


def prepare_formal_v57(*args: Any, **kwargs: Any) -> Path:
    """Package every dependency needed by the v57 import adapters."""
    cycle_root = Path(args[0])
    package = cycle_root / "formal/package"
    package.mkdir(parents=True, exist_ok=True)
    # These must exist before the inherited prepare step executes its dry-run.
    _sealed_copy(BASE_V10, package / BASE_V10.name)
    _sealed_copy(ROBUST_V9, package / ROBUST_V9.name)
    _sealed_copy(ROBUST_V8, package / ROBUST_V8.name)
    formal = _PREPARE_FORMAL_V55(*args, **kwargs)
    return formal


implementation.prepare_formal_v50 = prepare_formal_v57


if __name__ == "__main__":
    try:
        raise SystemExit(implementation.main())
    except implementation.v49.FailClosed as error:
        try:
            implementation.write_controller_state(status="fail_closed", error=str(error))
        except Exception:
            pass
        print(f"JOB target10 v57 fail closed: {error}", file=implementation.sys.stderr, flush=True)
        raise SystemExit(2)

#!/usr/bin/env python3
"""JOB v54 fresh0 controller using one dedicated Docker network mode."""

from __future__ import annotations

import hashlib
from pathlib import Path

import job_fresh10_target10_autonomous_v50 as implementation


EXPECTED_IMPLEMENTATION_SHA256 = (
    "2cf4ecd0e1816c6e46013ef2c093b7b3caf7ca5d945cd107ab0bd97696da9804"
)
observed = hashlib.sha256(Path(implementation.__file__).read_bytes()).hexdigest()
if observed != EXPECTED_IMPLEMENTATION_SHA256:
    raise implementation.v49.FailClosed("v52 implementation SHA mismatch")

implementation.CONTROLLER_VERSION = "v54"
implementation.FRESH_ROOT = implementation.v49.T / "autonomous_target10_v54_fresh0"
implementation.FRESH_STATE = implementation.FRESH_ROOT / "package/formal_state.json"
implementation.FRESH_ACCEPTED_COUNTER = (
    implementation.FRESH_ROOT / "formal_search_accepted_counter.txt"
)
implementation.CAMPAIGN_CONTRACT = (
    "job_v54_fresh0_v53_network_conflict_excluded_v1"
)
implementation.v49.ROOT = implementation.FRESH_ROOT
implementation.v49.STATE = implementation.FRESH_STATE
implementation.v49.ACCEPTED_COUNTER = implementation.FRESH_ACCEPTED_COUNTER


if __name__ == "__main__":
    try:
        raise SystemExit(implementation.main())
    except implementation.v49.FailClosed as error:
        try:
            implementation.write_controller_state(status="fail_closed", error=str(error))
        except Exception:
            pass
        print(
            f"JOB target10 v54 fail closed: {error}",
            file=implementation.sys.stderr,
            flush=True,
        )
        raise SystemExit(2)

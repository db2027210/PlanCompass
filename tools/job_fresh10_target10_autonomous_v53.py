#!/usr/bin/env python3
"""JOB v53 fresh0 controller using the dedicated-network bridge."""

from __future__ import annotations

import hashlib
from pathlib import Path

import job_fresh10_target10_autonomous_v50 as implementation


EXPECTED_IMPLEMENTATION_SHA256 = (
    "b9dc4ca55d67c4a94938e7498e2359796fae6749d70bb11e704b81124ff43099"
)
observed = hashlib.sha256(Path(implementation.__file__).read_bytes()).hexdigest()
if observed != EXPECTED_IMPLEMENTATION_SHA256:
    raise implementation.v49.FailClosed("v52 implementation SHA mismatch")

implementation.CONTROLLER_VERSION = "v53"
implementation.FRESH_ROOT = implementation.v49.T / "autonomous_target10_v53_fresh0"
implementation.FRESH_STATE = implementation.FRESH_ROOT / "package/formal_state.json"
implementation.FRESH_ACCEPTED_COUNTER = (
    implementation.FRESH_ROOT / "formal_search_accepted_counter.txt"
)
implementation.CAMPAIGN_CONTRACT = (
    "job_v53_fresh0_v52r2_technical_excluded_dedicated_network_v1"
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
            f"JOB target10 v53 fail closed: {error}",
            file=implementation.sys.stderr,
            flush=True,
        )
        raise SystemExit(2)

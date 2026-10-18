#!/usr/bin/env python3
"""JOB v51 entry point: explicit costs, preclaim readiness and state guard."""

from __future__ import annotations

import hashlib
from pathlib import Path

import job_fresh10_target10_autonomous_v50 as implementation


EXPECTED_V50_SHA256 = (
    "41533a5f3dd26ed7e7b11323e76d15d1b73ec2b82d9f1202e41a1406de840b0a"
)
observed_v50_sha256 = hashlib.sha256(
    Path(implementation.__file__).read_bytes()
).hexdigest()
if observed_v50_sha256 != EXPECTED_V50_SHA256:
    raise implementation.v49.FailClosed("v51 implementation v50-base SHA mismatch")
implementation.CONTROLLER_VERSION = "v51"


if __name__ == "__main__":
    try:
        raise SystemExit(implementation.main())
    except implementation.v49.FailClosed as error:
        try:
            implementation.write_controller_state(
                status="fail_closed",
                error=str(error),
            )
        except Exception:
            pass
        print(
            f"JOB target10 v51 fail closed: {error}",
            file=implementation.sys.stderr,
            flush=True,
        )
        raise SystemExit(2)

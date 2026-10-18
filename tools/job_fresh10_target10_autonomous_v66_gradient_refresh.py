#!/usr/bin/env python3
"""JOB v66: current-round gradient refresh and zero-gradient step guard."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping, Sequence

import job_exact_regime_successor_expand as successor
import job_fresh10_target10_autonomous_v65 as v65


implementation = v65.implementation
TOOLS_DIR = Path(__file__).resolve().parent
GENERATOR = TOOLS_DIR / "job_exact_regime_successor_expand.py"
EXPECTED_V65_SHA256 = "4a7af296ada667463a2b49d63fae01c12038251492b50da4e4f7d45f3798e228"
EXPECTED_GENERATOR_SHA256 = "102ac03d65453f6cd8fa83c18bb4335f5d0947c69c332782149f9e79b4758dc5"
CONTROLLER_VERSION = "v66"

if hashlib.sha256(Path(v65.__file__).read_bytes()).hexdigest() != EXPECTED_V65_SHA256:
    raise implementation.v49.FailClosed("v66 sealed v65 controller SHA mismatch")

_VALIDATE_TOP5_V65 = implementation.v49.validate_top5
_REQUIRE_V65 = implementation.require_v50_hashes

# Rebind the legacy absolute /tmp generator dependency to the generator shipped
# beside this release, then pin its refreshed hash before any EXPLAIN or workload.
old_generator = implementation.v49.GENERATOR
implementation.v49.EXPECTED_SHA.pop(old_generator, None)
implementation.v49.GENERATOR = GENERATOR
implementation.v49.EXPECTED_SHA[GENERATOR] = EXPECTED_GENERATOR_SHA256
implementation.CONTROLLER_VERSION = CONTROLLER_VERSION


def validate_top5_v66(
    path: Path,
    *,
    roles: Sequence[str],
    diagnostic_only: bool,
) -> dict[str, Any]:
    selection = _VALIDATE_TOP5_V65(
        path,
        roles=roles,
        diagnostic_only=diagnostic_only,
    )
    candidates = implementation.v49.candidate_list(selection)
    invalid_ranks = [
        rank
        for rank, candidate in enumerate(candidates, 1)
        if not successor.current_round_gradient_fields_valid(candidate)
    ]
    if invalid_ranks:
        raise implementation.v49.FailClosed(
            "v66 stale/invalid current-round gradient fields at ranks "
            + ",".join(str(rank) for rank in invalid_ranks)
        )
    return selection


def require_v66_hashes() -> None:
    _REQUIRE_V65()
    if (
        not GENERATOR.is_file()
        or implementation.v49.sha256_file(GENERATOR) != EXPECTED_GENERATOR_SHA256
    ):
        raise implementation.v49.FailClosed("v66 refreshed generator SHA mismatch")


implementation.v49.validate_top5 = validate_top5_v66
implementation.require_v50_hashes = require_v66_hashes


if __name__ == "__main__":
    try:
        raise SystemExit(implementation.main())
    except implementation.v49.FailClosed as error:
        try:
            implementation.write_controller_state(status="fail_closed", error=str(error))
        except Exception:
            pass
        print(f"JOB target10 v66 fail closed: {error}", flush=True)
        raise SystemExit(2)

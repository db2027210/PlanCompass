#!/usr/bin/env python3
"""Resume Algorithm C with the v53 watchdog-confirmation release."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v11 as v11


OPTIMIZATION_CONTRACT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260817_v53"
    / "optimization_contract.json"
)
ORIGINAL_STATE_DOCUMENT = v11.state_document


def state_document(**kwargs: Any) -> dict[str, Any]:
    document = ORIGINAL_STATE_DOCUMENT(**kwargs)
    document["runtime_watchdog_confirmation_release"] = {
        "confirmation_seconds": 15.0,
        "same_pid_and_error_identity_required": True,
        "transient_docker_readback_failure_state_credit_increment": 0,
        "persistent_cpu_numa_memory_storage_process_or_profile_failure_action":
            "fail_closed",
        "workload_measurement_acceptance_and_convergence_changed": False,
        "optimization_contract_path": str(OPTIMIZATION_CONTRACT),
        "optimization_contract_sha256": v11.v10.v4.runtime.alg.sha(
            OPTIMIZATION_CONTRACT
        ),
    }
    return document


def main() -> int:
    v11.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    v11.state_document = state_document
    return v11.main()


if __name__ == "__main__":
    raise SystemExit(main())

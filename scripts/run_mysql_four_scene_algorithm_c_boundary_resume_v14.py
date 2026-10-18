#!/usr/bin/env python3
"""Resume Algorithm C with the v55 read-only external-affinity release."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v13 as v13


OPTIMIZATION_CONTRACT = (
    ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260817_v55"
    / "optimization_contract.json"
)
ORIGINAL_STATE_DOCUMENT = v13.state_document


def state_document(**kwargs: Any) -> dict[str, Any]:
    document = ORIGINAL_STATE_DOCUMENT(**kwargs)
    document["read_only_external_affinity_release"] = {
        "release_version": "v55",
        "optimization_contract_path": str(OPTIMIZATION_CONTRACT),
        "optimization_contract_sha256": v13.v11.v10.v4.runtime.alg.sha(
            OPTIMIZATION_CONTRACT
        ),
        "observer_source_path": str(
            ROOT / "scripts/observe_frozen_external_pg_client_affinity_v1.py"
        ),
        "external_process_affinity_mutation": False,
        "external_container_mutation": False,
        "mismatch_action": "record_and_delegate_fail_closed_to_runtime_watchdog",
        "workload_resource_top5_scoring_acceptance_and_convergence_changed": False,
    }
    return document


def main() -> int:
    v13.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    v13.state_document = state_document
    return v13.main()


if __name__ == "__main__":
    raise SystemExit(main())

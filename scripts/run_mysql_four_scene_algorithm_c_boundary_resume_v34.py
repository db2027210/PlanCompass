#!/usr/bin/env python3
"""Resume C with the v7 bounded Docker exit-state convergence wait."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import reset_mysql_xp_data_planes_v7 as reset_v7
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v10 as v10
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v18 as v18
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v32 as v32


runtime = v32.runtime
ORIGINAL_V32_STATE_DOCUMENT = v32.state_document
OPTIMIZATION_CONTRACT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260817_v60"
    / "optimization_contract.json"
)


def state_document(**kwargs: Any) -> dict[str, Any]:
    document = ORIGINAL_V32_STATE_DOCUMENT(**kwargs)
    document["docker_exit_state_convergence_release"] = {
        "release_version": "v34",
        "source_path": str(Path(__file__).resolve()),
        "source_sha256": runtime.alg.sha(Path(__file__).resolve()),
        "reset_source_path": str(Path(reset_v7.__file__).resolve()),
        "reset_source_sha256": runtime.alg.sha(Path(reset_v7.__file__).resolve()),
        "bound_host_pid_kill_exit_wait_seconds": 240,
        "trigger_attempt": 52,
        "observed_publish_lag_seconds": 5,
        "container_identity_checks_unchanged": True,
        "workload_contract_unchanged": True,
        "resource_envelope_unchanged": True,
        "accepted_state_credit_unchanged": True,
    }
    return document


def main() -> int:
    # v10 is the chain point that installs the reset authority.
    v10.reset_v6 = reset_v7
    # v18 is the chain point that propagates the current optimization release.
    v18.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    runtime.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    runtime.alg.reset = reset_v7
    v32.state_document = state_document
    return v32.main()


if __name__ == "__main__":
    raise SystemExit(main())

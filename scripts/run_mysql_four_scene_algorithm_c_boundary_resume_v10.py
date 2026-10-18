#!/usr/bin/env python3
"""Resume Algorithm C with the stopped-discard XP reset successor."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import reset_mysql_xp_data_planes_v6 as reset_v6
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v4 as v4
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v5 as v5


OPTIMIZATION_CONTRACT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260816_v51"
    / "optimization_contract.json"
)
ORIGINAL_STATE_DOCUMENT = v5.state_document


def state_document(**kwargs: Any) -> dict[str, Any]:
    document = ORIGINAL_STATE_DOCUMENT(**kwargs)
    document["stopped_discard_reset_successor"] = {
        "reset_source_path": str(Path(reset_v6.__file__).resolve()),
        "reset_source_sha256": v4.runtime.alg.sha(
            Path(reset_v6.__file__).resolve()
        ),
        "optimization_contract_path": str(OPTIMIZATION_CONTRACT),
        "optimization_contract_sha256": v4.runtime.alg.sha(
            OPTIMIZATION_CONTRACT
        ),
        "bound_host_pid_kill_exit_wait_seconds": (
            reset_v6.HOST_PID_KILL_EXIT_WAIT_SECONDS
        ),
        "zero_session_proof_required": True,
        "exact_stopped_container_identity_required": True,
        "discarded_datadir_never_reused": True,
    }
    return document


def main() -> int:
    v4.isolation.install()
    v4.runtime.alg.reset = reset_v6
    v4.runtime.WAVE_COORDINATION_CONTRACT = v4.isolation.CONTRACT_PATH
    v4.runtime.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    v4.clean_quiet_timeout_partial_boundary = (
        v5.clean_premeasurement_dstate_timeout_boundary
    )
    v4.runtime._validate_formal_partial_continuation = (
        v4.validate_formal_partial_continuation
    )
    v4.runtime._validate_wave_coordination_contract = (
        v4.validate_wave_coordination_contract
    )
    v4.runtime._state_document = state_document
    return v4.runtime.main()


if __name__ == "__main__":
    raise SystemExit(main())

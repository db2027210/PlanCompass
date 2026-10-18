#!/usr/bin/env python3
"""Run XP Top-4 Algorithm C with Docker-interruption checkpoint recovery."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_c_async_scene_v56 as v56


alg = v56.alg
ASYNC_CONTRACT = (
    ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_c_20260825_v5"
    / "algorithm_c_async_contract.json"
)


def validate_contract() -> dict[str, Any]:
    contract = alg.load(ASYNC_CONTRACT)
    docker_recovery = contract.get(
        "docker_daemon_interruption_checkpoint_release"
    ) or {}
    if (
        contract.get("release_version") != "v57"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or docker_recovery.get(
            "completed_outer_decisions_reconstructed_from_sha_bound_evidence"
        ) is not True
        or docker_recovery.get("interrupted_outer_decision_required_absent")
        is not True
        or docker_recovery.get("interrupted_outer_credit_increment") != 0
        or docker_recovery.get("source_controller_required_inactive") is not True
        or docker_recovery.get("candidate_scoring_changed") is not False
        or docker_recovery.get("outer_algorithm_changed") is not False
        or docker_recovery.get("accepted_or_rejected_credit_changed") is not False
    ):
        raise RuntimeError("v57 Docker-interruption contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        if not isinstance(binding, Mapping):
            raise RuntimeError("v57 source binding is not an object")
        path = ROOT / str(binding["path"])
        if (
            not path.is_file()
            or path.is_symlink()
            or alg.sha(path) != str(binding["sha256"])
        ):
            raise RuntimeError(f"v57 source binding differs: {path}")
    return contract


def main() -> int:
    validate_contract()
    # v57 changes only admissible checkpoint provenance.  Dynamic isolation,
    # empty-wave skipping, candidates, scoring, MySQL calls, and outer flow are
    # the already frozen v56 implementation.
    v56.install()
    return v56.v55.v54.v53.v52.v51.v50.v49.v45.v34.main()


if __name__ == "__main__":
    raise SystemExit(main())

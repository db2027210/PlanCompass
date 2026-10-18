#!/usr/bin/env python3
"""Algorithm C v117 with the v8 scene-checkpoint verifier bound at runtime."""

from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v8 as checkpoint_v8
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v26 as predecessor


CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_latest_outer_20260907_v117/"
    "algorithm_c_pg_latest_outer_contract.json"
)
OPTIMIZATION_CONTRACT = ROOT / (
    "config/formal/frozen_mysql_four_scene_algorithm_c_client_primary_20260907_v117/"
    "outer_runtime_optimization.json"
)
COORDINATION_CONTRACT = ROOT / (
    "config/formal/frozen_mysql_cross_project_wave_coordination_20260907_v117/"
    "coordination_contract.json"
)
runtime = predecessor.runtime
# v22 owns the checkpoint implementation that it installs into the runtime.
v22 = predecessor.predecessor.predecessor.predecessor.predecessor


def validate_contract() -> dict[str, Any]:
    contract = runtime.alg.load(CONTRACT)
    flow = dict(contract.get("outer_flow") or {})
    recovery = dict(contract.get("xp_io_scene_checkpoint_runtime_binding") or {})
    if (
        contract.get("release_version") != "v117-xp-io-checkpoint-runtime-binding"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or flow.get("strict_higher_qps_acceptance") is not True
        or flow.get("two_rejections_before_incumbent_shrink") is not True
        or recovery.get("checkpoint_verifier")
        != "build_mysql_algorithm_c_scene_resume_checkpoint_v8.py"
        or recovery.get("completed_outer_attempts_preserved") != 10
        or recovery.get("partial_outer_credit") != 0
    ):
        raise RuntimeError("Algorithm C v117 lifecycle contract differs")
    for name, raw in dict(contract.get("source_bindings") or {}).items():
        row = dict(raw or {})
        path = (ROOT / str(row.get("path") or "")).resolve()
        if not path.is_file() or path.is_symlink() or runtime.alg.sha(path) != row.get("sha256"):
            raise RuntimeError(f"Algorithm C v117 source differs: {name}: {path}")
    return contract


def main() -> int:
    old = {
        "contract": predecessor.CONTRACT,
        "optimization": predecessor.OPTIMIZATION_CONTRACT,
        "coordination": predecessor.COORDINATION_CONTRACT,
        "validate": predecessor.validate_contract,
        "checkpoint": v22.checkpoint_v5,
    }
    predecessor.CONTRACT = CONTRACT
    predecessor.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    predecessor.COORDINATION_CONTRACT = COORDINATION_CONTRACT
    predecessor.validate_contract = validate_contract
    v22.checkpoint_v5 = checkpoint_v8
    try:
        return int(predecessor.main())
    finally:
        v22.checkpoint_v5 = old["checkpoint"]
        predecessor.validate_contract = old["validate"]
        predecessor.COORDINATION_CONTRACT = old["coordination"]
        predecessor.OPTIMIZATION_CONTRACT = old["optimization"]
        predecessor.CONTRACT = old["contract"]


if __name__ == "__main__":
    raise SystemExit(main())

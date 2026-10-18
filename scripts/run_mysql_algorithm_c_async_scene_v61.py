#!/usr/bin/env python3
"""Run independent Algorithm C with strict-QPS and two-reject shrink."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_outer_qps_strict_progress_v1 as qps_policy
from scripts import mysql_outer_rejection_cycle_v1 as rejection_cycle
from scripts import run_mysql_algorithm_c_async_scene_v60 as predecessor


alg = predecessor.alg
runtime = predecessor.runtime
ASYNC_CONTRACT = (
    ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_c_20260826_v7"
    / "algorithm_c_async_contract.json"
)
OPTIMIZATION_CONTRACT = ASYNC_CONTRACT.parent / "outer_runtime_optimization.json"


def validate_contract() -> dict[str, Any]:
    contract = alg.load(ASYNC_CONTRACT)
    if (
        contract.get("release_version") != "v61"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("outer_acceptance_policy") != qps_policy.policy_contract()
        or contract.get("outer_rejection_cycle_policy")
        != rejection_cycle.policy_contract()
        or contract.get("candidate_batches") != [[1, 2], [3, 4], [5]]
        or contract.get("four_scene_wave_barrier") is not False
        or contract.get("per_scene_outer_state_machine") is not True
        or contract.get("post_search_stability_confirmation") is not False
    ):
        raise RuntimeError("v61 Algorithm C outer-loop contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        if not isinstance(binding, Mapping):
            raise RuntimeError("v61 Algorithm C source binding is not an object")
        path = ROOT / str(binding.get("path") or "")
        if (
            not path.is_file()
            or path.is_symlink()
            or alg.sha(path) != str(binding.get("sha256") or "")
        ):
            raise RuntimeError(f"v61 Algorithm C source binding differs: {path}")
    return contract


def install() -> None:
    # The successor contract rebinds the intentionally changed shared outer
    # runtime. Legacy wrappers still carry the predecessor runtime hash, so
    # route their validation calls through the complete successor binding.
    for name, module in tuple(sys.modules.items()):
        if (
            name.startswith("scripts.run_mysql_algorithm_c_async_scene_")
            and module is not None
            and hasattr(module, "validate_contract")
        ):
            module.validate_contract = validate_contract
    predecessor.ASYNC_CONTRACT = ASYNC_CONTRACT
    predecessor.validate_contract = validate_contract
    predecessor.install()
    for name, module in tuple(sys.modules.items()):
        if (
            name.startswith(
                "scripts.run_mysql_four_scene_algorithm_c_boundary_resume_v"
            )
            and module is not None
            and hasattr(module, "OPTIMIZATION_CONTRACT")
        ):
            module.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    runtime.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    runtime.pg_outer.outer_semantic_decision = qps_policy.pg_outer_qps_strict_progress
    runtime.pg_outer.policy_contract = qps_policy.policy_contract


def main() -> int:
    validate_contract()
    install()
    return predecessor.v59.v58.v57.v56.v55.v54.v53.v52.v51.v50.v49.v45.v34.main()


if __name__ == "__main__":
    raise SystemExit(main())

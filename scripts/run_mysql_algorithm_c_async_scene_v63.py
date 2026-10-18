#!/usr/bin/env python3
"""Run v62 no-lease Algorithm C with strict-QPS boundary recovery v63."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_outer_qps_strict_progress_v1 as qps_policy
from scripts import mysql_outer_rejection_cycle_v1 as rejection_cycle
from scripts import run_mysql_algorithm_c_async_scene_v62 as predecessor


alg = predecessor.alg
CONTRACT_ROOT = (
    ROOT / "config/formal/frozen_mysql_four_scene_algorithm_c_qps_20260826_v15"
)
ASYNC_CONTRACT = CONTRACT_ROOT / "algorithm_c_async_contract.json"
OPTIMIZATION_CONTRACT = CONTRACT_ROOT / "outer_runtime_optimization.json"


def validate_contract() -> dict[str, Any]:
    contract = alg.load(ASYNC_CONTRACT)
    if (
        contract.get("release_version") != "v63"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("outer_acceptance_policy") != qps_policy.policy_contract()
        or contract.get("outer_rejection_cycle_policy")
        != rejection_cycle.policy_contract()
        or contract.get("candidate_batches") != [[1, 2], [3, 4], [5]]
        or contract.get("physical_replay_parallelism_per_scene") != 2
        or contract.get("maximum_concurrent_mysql_workers") != 8
        or contract.get("four_scene_wave_barrier") is not False
        or contract.get("sibling_scene_wait") is not False
        or contract.get("lease_mode") != "none"
        or contract.get("post_search_stability_confirmation") is not False
    ):
        raise RuntimeError("v63 Algorithm C recovery contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        if not isinstance(binding, Mapping):
            raise RuntimeError("v63 Algorithm C source binding is not an object")
        path = ROOT / str(binding.get("path") or "")
        if (
            not path.is_file() or path.is_symlink()
            or alg.sha(path) != str(binding.get("sha256") or "")
        ):
            raise RuntimeError(f"v63 Algorithm C source binding differs: {path}")
    return contract


def main() -> int:
    predecessor.ASYNC_CONTRACT = ASYNC_CONTRACT
    predecessor.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    predecessor.validate_contract = validate_contract
    return predecessor.main()


if __name__ == "__main__":
    raise SystemExit(main())

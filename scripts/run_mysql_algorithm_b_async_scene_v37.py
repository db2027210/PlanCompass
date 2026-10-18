#!/usr/bin/env python3
"""Run one general Algorithm-B scene with QPS-only outer progress."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_outer_qps_strict_progress_v1 as qps_policy
from scripts import run_mysql_algorithm_b_async_scene_v36 as predecessor


CONTRACT = (
    ROOT / "config/formal/frozen_mysql_async_outer_20260826_v38"
    / "algorithm_b_async_contract.json"
)


def validate_contract() -> dict[str, Any]:
    contract = predecessor.alg.load(CONTRACT)
    if (
        contract.get("release_version") != "v38"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("outer_acceptance_policy") != qps_policy.policy_contract()
        or contract.get("four_scene_wave_barrier") is not False
        or contract.get("per_scene_outer_state_machine") is not True
        or contract.get("candidate_batches") != [[1, 2], [3, 4], [5]]
    ):
        raise RuntimeError("v38 Algorithm B QPS-only contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        if not isinstance(binding, Mapping):
            raise RuntimeError("v38 Algorithm B source binding is not an object")
        path = ROOT / str(binding.get("path") or "")
        if (
            not path.is_file()
            or path.is_symlink()
            or predecessor.alg.sha(path) != str(binding.get("sha256") or "")
        ):
            raise RuntimeError(f"v38 Algorithm B source binding differs: {path}")
    return contract


def install() -> None:
    predecessor.CONTRACT = CONTRACT
    predecessor.validate_contract = validate_contract
    # v35 and the dynamic core share this module object.  Replacing the
    # semantic predicate also updates cached-replay redecisions.
    predecessor.v35.pg_outer.outer_semantic_decision = (
        qps_policy.pg_outer_qps_strict_progress
    )
    predecessor.v35.pg_outer.policy_contract = qps_policy.policy_contract


def main() -> int:
    install()
    return predecessor.main()


if __name__ == "__main__":
    raise SystemExit(main())

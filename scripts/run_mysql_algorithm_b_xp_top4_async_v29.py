#!/usr/bin/env python3
"""Run Top4 Algorithm B with QPS-only outer progress semantics."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_outer_qps_strict_progress_v1 as qps_policy
from scripts import run_mysql_algorithm_b_xp_top4_async_v24 as predecessor


CONTRACT_ROOT = (
    ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_b_20260826_v36"
)


def validate_contract() -> dict[str, Any]:
    contract = predecessor.predecessor.successor.predecessor._impl["alg"].load(
        CONTRACT_ROOT / "algorithm_b_top4_contract.json"
    )
    if (
        contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("outer_acceptance_policy") != qps_policy.policy_contract()
        or contract.get("candidate_batches") != [[1, 2, 3, 4], [5]]
        or contract.get("physical_replay_parallelism") != 4
        or contract.get("four_scene_barrier") is not False
        or contract.get("sibling_scene_wait") is not False
    ):
        raise RuntimeError("Top4-B v36 QPS-only contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        if not isinstance(binding, Mapping):
            raise RuntimeError("Top4-B v36 source binding is not an object")
        path = ROOT / str(binding.get("path") or "")
        if (
            not path.is_file()
            or path.is_symlink()
            or predecessor.predecessor.successor.predecessor._impl["alg"].sha(path)
            != str(binding.get("sha256") or "")
        ):
            raise RuntimeError(f"Top4-B v36 source binding differs: {path}")
    return contract


def install_runtime() -> Any:
    predecessor.CONTRACT_ROOT = CONTRACT_ROOT
    core = predecessor.install_runtime()
    core._impl["decide_validation_acceptance"] = (
        qps_policy.decide_qps_strict_progress
    )
    core._impl["advance_acceptance_tier"] = qps_policy.keep_qps_only_mode
    return core


def main() -> int:
    validate_contract()
    core = install_runtime()
    return int(core._implementation_main())


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Run independent Algorithm C with QPS-only outer progress semantics."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_outer_qps_strict_progress_v1 as qps_policy
from scripts import run_mysql_algorithm_c_async_scene_v59 as v59


alg = v59.alg
runtime = v59.v58.v57.v56.runtime
ASYNC_CONTRACT = (
    ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_c_20260826_v6"
    / "algorithm_c_async_contract.json"
)


def validate_contract() -> dict[str, Any]:
    contract = alg.load(ASYNC_CONTRACT)
    outer = contract.get("outer_acceptance_policy") or {}
    if (
        contract.get("release_version") != "v60"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or outer != qps_policy.policy_contract()
        or contract.get("candidate_batches") != [[1, 2], [3, 4], [5]]
        or contract.get("four_scene_wave_barrier") is not False
        or contract.get("per_scene_outer_state_machine") is not True
        or contract.get("post_search_stability_confirmation") is not False
    ):
        raise RuntimeError("v60 Algorithm C QPS-only contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        if not isinstance(binding, Mapping):
            raise RuntimeError("v60 Algorithm C source binding is not an object")
        path = ROOT / str(binding.get("path") or "")
        if (
            not path.is_file()
            or path.is_symlink()
            or alg.sha(path) != str(binding.get("sha256") or "")
        ):
            raise RuntimeError(f"v60 Algorithm C source binding differs: {path}")
    return contract


def install() -> None:
    # Preserve the v59 workload, isolation, reset, candidate generation and
    # real-replay implementation.  Replace only the semantic outer predicate.
    v59.v58.v57.v56.install()
    runtime.coordination.rolling_quiet_gate = v59.gate_v4.rolling_quiet_gate
    alg.r0mod.PREFLIGHT_SOURCE = Path(v59.preflight_v7.__file__).resolve()
    runtime.pg_outer.outer_semantic_decision = (
        qps_policy.pg_outer_qps_strict_progress
    )
    runtime.pg_outer.policy_contract = qps_policy.policy_contract


def main() -> int:
    validate_contract()
    install()
    return v59.v58.v57.v56.v55.v54.v53.v52.v51.v50.v49.v45.v34.main()


if __name__ == "__main__":
    raise SystemExit(main())

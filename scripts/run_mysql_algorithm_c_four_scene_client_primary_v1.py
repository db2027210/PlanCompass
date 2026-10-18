#!/usr/bin/env python3
"""Run fresh two-worker Algorithm C with v20 client-primary semantics."""

from __future__ import annotations

import copy
import os
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_algorithm_c_measurement_client_primary_v1 as measurement
from scripts import mysql_xp_reset_run_scoped_token_v1 as reset_token
from scripts import reset_mysql_xp_data_planes_client_primary_v3 as xp_reset
from scripts import run_mysql_algorithm_c_async_scene_v74 as predecessor


HISTORY = ROOT / "history/mysql_four_scene_20260809"
FREEZE = ROOT / (
    "config/formal/frozen_mysql_four_scene_two_worker_20260903_v20/"
    "four_scene_effective_resource_successor.json"
)
FREEZE_SCHEMA = "mysql_four_scene_two_worker_client_primary_successor_v20"
CONTRACT_ROOT = ROOT / (
    "config/formal/frozen_mysql_four_scene_algorithm_c_client_primary_20260903_v90"
)
PARENT_CONTRACT = CONTRACT_ROOT / "algorithm_c_async_contract.json"
OPTIMIZATION_CONTRACT = CONTRACT_ROOT / "outer_runtime_optimization.json"
CONTRACT = CONTRACT_ROOT / "global_monotonic_incumbent_contract.json"


def mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} object required")
    return dict(value)


def authority_root() -> Path:
    raw = os.environ.get("MYSQL_TWO_WORKER_AUTHORITY_ROOT", "")
    value = Path(raw).expanduser().resolve()
    if (
        not raw or HISTORY.resolve() not in value.parents or value.is_symlink()
        or any(not (value / name).is_file() for name in (
            "transition_preflight.json", "prepared_binding.json",
            "benchmark_accounts_binding.json",
        ))
    ):
        raise RuntimeError("Algorithm C v20 authority root differs")
    return value


def validate_contract() -> dict[str, Any]:
    alg = predecessor.predecessor.alg
    parent = alg.load(PARENT_CONTRACT)
    optimization = alg.load(OPTIMIZATION_CONTRACT)
    contract = alg.load(CONTRACT)
    if (
        parent.get("release_version") != "v90-client-primary"
        or parent.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or parent.get("formal_scope") != "mysql_only"
        or parent.get("cross_engine_comparison_prohibited") is not True
        or parent.get("outer_acceptance_policy")
        != predecessor.predecessor.qps_policy.policy_contract()
        or parent.get("outer_rejection_cycle_policy")
        != predecessor.predecessor.rejection_cycle.policy_contract()
        or parent.get("candidate_batches") != [[1, 2], [3, 4], [5]]
        or parent.get("physical_replay_parallelism_per_scene") != 2
        or parent.get("maximum_concurrent_mysql_workers") != 8
        or parent.get("four_scene_wave_barrier") is not False
        or parent.get("sibling_scene_wait") is not False
        or parent.get("lease_mode") != "none"
        or parent.get("post_search_stability_confirmation") is not False
        or optimization.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or mapping(optimization.get("bindings"), "optimization bindings").get(
            "resource_freeze_sha256"
        ) != alg.sha(FREEZE)
        or contract.get("release_version") != "v90-client-primary"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("parent_contract", {}).get("sha256") != alg.sha(PARENT_CONTRACT)
        or contract.get("resume_invariant", {}).get(
            "next_reference_equals_previous_selected_incumbent"
        ) is not True
        or contract.get("xp_outer_primary_metric")
        != "native_hybench_xp_client_xap_qps"
    ):
        raise RuntimeError("Algorithm C v90 client-primary contract differs")
    for document, label in ((parent, "parent"), (contract, "successor")):
        for name, raw in mapping(
            document.get("source_bindings"), f"{label} source bindings",
        ).items():
            row = mapping(raw, f"{label} {name}")
            path = (ROOT / str(row.get("path") or "")).resolve()
            if (
                not path.is_file() or path.is_symlink()
                or alg.sha(path) != str(row.get("sha256") or "")
            ):
                raise RuntimeError(f"Algorithm C v90 source differs: {name}")
    return {"parent": parent, "optimization": optimization, "successor": contract}


def main() -> int:
    validate_contract()
    v74 = predecessor
    v73 = predecessor.predecessor
    v63 = v73.predecessor
    v62 = v63.predecessor
    alg = v73.alg
    runtime = v73.runtime
    old = {
        "v74_contract": v74.CONTRACT,
        "v74_parent": v74.PARENT_CONTRACT,
        "v74_validate": v74.validate_contract,
        "v73_async": v73.ASYNC_CONTRACT,
        "v73_optimization": v73.OPTIMIZATION_CONTRACT,
        "v62_freeze": v62.FREEZE,
        "v62_install": v62.install,
    }

    def install_client_primary_v20() -> None:
        old["v62_install"]()
        root = authority_root()
        prepared = alg.load(root / "prepared_binding.json")
        rows = {
            str(row["worker_id"]): copy.deepcopy(dict(row))
            for row in prepared.get("workers") or []
        }
        xp_rows = {
            worker_id: row for worker_id, row in rows.items()
            if row.get("scene") in {"hybench_xp_cpu", "hybench_xp_io"}
        }
        if len(rows) != 8 or set(xp_rows) != {
            "xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2",
        }:
            raise RuntimeError("Algorithm C v20 worker cohort differs")
        xp_reset.configure(
            freeze=FREEZE, prepared=root / "prepared_binding.json",
            xp_workers=xp_rows,
        )
        alg.FREEZE = FREEZE
        alg.reset = xp_reset
        alg.WORKER_IDS = tuple(sorted(rows))
        alg.XP_IDS = tuple(sorted(xp_rows))
        alg.XP_RESET_MAX_WORKERS = 2
        alg.measure_batch = measurement.measure_batch
        runtime.alg.FREEZE = FREEZE
        runtime.alg.reset = xp_reset
        runtime.alg.measure_batch = measurement.measure_batch
        r0 = alg.r0mod
        r0.FREEZE = FREEZE
        r0.FREEZE_SCHEMA = FREEZE_SCHEMA
        r0.PREFLIGHT = root / "transition_preflight.json"
        r0.RUNTIME_PREFLIGHT = r0.PREFLIGHT
        r0.PREPARED = root / "prepared_binding.json"
        r0.BENCHMARK_ACCOUNTS = root / "benchmark_accounts_binding.json"
        reset_token.install(alg)
        original_state = runtime._state_document

        def state_document(*args: Any, **kwargs: Any) -> dict[str, Any]:
            value = original_state(*args, **kwargs)
            value.update({
                "freeze_path": str(FREEZE),
                "freeze_sha256": alg.sha(FREEZE),
                "xp_outer_primary_metric": "native_hybench_xp_client_xap_qps",
                "xp_server_questions_qps_role": "audit_only",
                "xp_metric_policy": measurement.POLICY_ID,
                "all_four_scenes_started_in_same_formal_launch": True,
                "physical_workers_per_scene": 2,
                "maximum_concurrent_mysql_workers": 8,
            })
            return value

        runtime._state_document = state_document

    v74.CONTRACT = CONTRACT
    v74.PARENT_CONTRACT = PARENT_CONTRACT
    v74.validate_contract = validate_contract
    v73.ASYNC_CONTRACT = PARENT_CONTRACT
    v73.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    v62.FREEZE = FREEZE
    v62.install = install_client_primary_v20
    try:
        return int(v74.main())
    finally:
        v74.CONTRACT = old["v74_contract"]
        v74.PARENT_CONTRACT = old["v74_parent"]
        v74.validate_contract = old["v74_validate"]
        v73.ASYNC_CONTRACT = old["v73_async"]
        v73.OPTIMIZATION_CONTRACT = old["v73_optimization"]
        v62.FREEZE = old["v62_freeze"]
        v62.install = old["v62_install"]


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Run independent XP Top4-A with two workers and all 16 candidates."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_algorithm_b_no_lease_measurement_v1 as no_lease
from scripts import run_mysql_algorithm_a_xp_top4_async_v1 as predecessor


base = predecessor.base
resume = predecessor.resume
CONTRACT_ROOT = (
    ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_a_20260826_v10"
)
CONTRACT = CONTRACT_ROOT / "algorithm_a_top4_contract.json"
COORDINATION = CONTRACT_ROOT / "coordination_contract.json"
FREEZE = (
    ROOT / "config/formal/frozen_mysql_four_scene_two_worker_20260826_v19"
    / "four_scene_effective_resource_successor.json"
)
EXPECTED = {
    "hybench_xp_cpu": {"xp_cpu_w1", "xp_cpu_w2"},
    "hybench_xp_io": {"xp_io_w1", "xp_io_w2"},
}


def validate_contract(scene: str, active: Sequence[str]) -> dict[str, Any]:
    contract = predecessor.load(CONTRACT)
    scene_contract = (contract.get("scenes") or {}).get(scene) or {}
    if (
        contract.get("schema_version")
        != "mysql_algorithm_a_xp_top4_two_worker_contract_v1"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("algorithm") != "A"
        or contract.get("method") != base.METHOD
        or contract.get("candidate_search_contract") != base.SEARCH_CONTRACT
        or contract.get("accepted_target") != base.TARGET_ACCEPTED
        or contract.get("normalized_width_threshold") != base.NORMALIZED_WIDTH_THRESHOLD
        or contract.get("physical_replay_parallelism_per_scene") != 2
        or contract.get("candidate_batches")
        != [[1, 2], [3, 4], [5, 6], [7, 8], [9, 10], [11, 12], [13, 14], [15, 16]]
        or contract.get("four_scene_wave_barrier") is not False
        or contract.get("sibling_scene_wait") is not False
        or contract.get("lease_mode") != "none"
        or contract.get("post_convergence_stability_required") is not False
        or scene_contract.get("active_parameters") != list(active)
        or scene_contract.get("candidates_per_round") != 16
        or scene_contract.get("physical_waves_per_round") != 8
    ):
        raise RuntimeError("Top4-A two-worker contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        path = ROOT / str(binding.get("path") or "")
        if path.is_symlink() or not path.is_file() or predecessor.sha(path) != binding.get("sha256"):
            raise RuntimeError(f"Top4-A two-worker source binding differs: {path}")
    return contract


def validate_launch_authority(
    path: Path, scene: str,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Any]]:
    authority = predecessor.load(path)
    contract = validate_contract(scene, base.scene_parameter_space(scene)[0])
    freeze_path = Path(str(authority.get("freeze_path") or "")).resolve()
    if freeze_path != FREEZE.resolve() or authority.get("freeze_sha256") != predecessor.sha(FREEZE):
        raise RuntimeError("Top4-A two-worker freeze authority differs")
    freeze = predecessor.load(freeze_path)
    prepared_path = Path(str(authority.get("prepared") or "")).resolve()
    accounts_path = Path(str(authority.get("accounts") or "")).resolve()
    preflight_path = Path(str(authority.get("preflight") or "")).resolve()
    prepared = predecessor.load(prepared_path)
    accounts = predecessor.load(accounts_path)
    preflight = predecessor.load(preflight_path)
    prepared_rows = {
        str(row.get("worker_id")): copy.deepcopy(dict(row))
        for row in prepared.get("workers") or [] if row.get("worker_id") in EXPECTED[scene]
    }
    account_rows = {
        str(row.get("worker_id")): copy.deepcopy(dict(row))
        for row in accounts.get("workers") or [] if row.get("worker_id") in EXPECTED[scene]
    }
    workers = {
        worker_id: {**prepared_rows[worker_id], **account_rows[worker_id]}
        for worker_id in sorted(prepared_rows)
    }
    gate = preflight.get("rolling_dynamic_noninterference_gate") or {}
    if (
        authority.get("status") != "PASS"
        or authority.get("scene") != scene
        or prepared.get("status") != "PREPARED"
        or prepared.get("freeze_sha256") != predecessor.sha(FREEZE)
        or accounts.get("status") != "PASS"
        or accounts.get("prepared_binding_sha256") != predecessor.sha(prepared_path)
        or preflight.get("status") != "PASS"
        or float(preflight.get("observed_seconds") or 0) < 60.0
        or gate.get("status") != "PASS"
        or set(workers) != EXPECTED[scene]
        or set(account_rows) != EXPECTED[scene]
    ):
        raise RuntimeError("Top4-A two-worker current live authority differs")
    resume.configure_runtime(freeze_path=freeze_path, workers=workers, scene=scene)
    for worker_id, worker in workers.items():
        inspected = base.alg.reset._docker_json("inspect", str(worker["container"]))
        if inspected.get("Id") != worker.get("container_id") or (inspected.get("State") or {}).get("Running") is not True:
            raise RuntimeError(f"Top4-A two-worker live identity differs: {worker_id}")
        base.alg.reset.prep._kernel_resource_readback(inspected, worker)
        base.alg.reset.prep._enforce_blkio(inspected, freeze, worker)
    return freeze, workers, {
        "coordination_contract": predecessor.load(COORDINATION),
        "resource_freeze_path": str(freeze_path),
        "resource_freeze_sha256": predecessor.sha(freeze_path),
        "resource_freeze_schema": freeze["schema_version"],
        "launch_authority_path": str(path),
        "launch_authority_sha256": predecessor.sha(path),
        "fresh_baseline_worker_count": 2,
        "historical_accepted_rounds_reused": False,
        "algorithm_b_state_or_result_credit": False,
        "lease_mode": "none", "contract_sha256": predecessor.sha(CONTRACT),
        "contract": copy.deepcopy(contract),
    }


def no_lease_measure(**kwargs: Any):
    no_lease.alg = base.alg
    return no_lease.measure_batch(**kwargs)


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--scene", choices=sorted(EXPECTED), required=True)
    parser.add_argument("--launch-authority", type=predecessor.regular_file, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    base.REPLAY_PARALLELISM = 2
    base.A_CONTRACT = CONTRACT
    base.alg.TABLE_ROOT = predecessor.TOP4_ROOT
    base.alg.TABLES = copy.deepcopy(predecessor.TOP4_TABLES)
    base.validate_a_contract = validate_contract
    base.validate_fresh_authority = lambda **kwargs: validate_launch_authority(
        args.launch_authority, str(kwargs["scene"])
    )
    base.measure = no_lease_measure
    forwarded = SimpleNamespace(
        run_id=args.run_id, scene=args.scene,
        runtime_preflight=args.launch_authority,
        prepared_binding=args.launch_authority,
        benchmark_accounts=args.launch_authority,
        fresh_r0=args.launch_authority, output_dir=args.output_dir,
    )
    return base.run(forwarded)


if __name__ == "__main__":
    raise SystemExit(main())

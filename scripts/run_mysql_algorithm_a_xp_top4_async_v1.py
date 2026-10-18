#!/usr/bin/env python3
"""Run fresh per-scene Top4 Algorithm A on four current XP workers."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_algorithm_b_no_lease_measurement_v1 as no_lease
from scripts import run_mysql_algorithm_a_async_scene_v3 as base
from scripts import run_mysql_algorithm_a_job_to_xp_resume_v1 as resume


CONTRACT_ROOT = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_a_20260826_v1"
CONTRACT = CONTRACT_ROOT / "algorithm_a_top4_contract.json"
COORDINATION = CONTRACT_ROOT / "coordination_contract.json"
TOP4_ROOT = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_c_20260825_v1"
TOP4_TABLES = {
    "hybench_xp_cpu": "T.hybench_xp_cpu.top4.operator_physics_v1.json",
    "hybench_xp_io": "T.hybench_xp_io.top4.operator_physics_v1.json",
}
EXPECTED = {
    scene: {f"{'xp_cpu' if scene.endswith('cpu') else 'xp_io'}_w{index}"
            for index in range(1, 5)}
    for scene in TOP4_TABLES
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"regular JSON required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def regular_file(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if path.is_symlink() or not path.is_file():
        raise argparse.ArgumentTypeError(f"regular file required: {path}")
    return path


def validate_contract(scene: str, active: Sequence[str]) -> dict[str, Any]:
    contract = load(CONTRACT)
    scene_contract = (contract.get("scenes") or {}).get(scene) or {}
    if (
        contract.get("schema_version") != "mysql_algorithm_a_xp_top4_async_contract_v1"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("algorithm") != "A"
        or contract.get("method") != base.METHOD
        or contract.get("candidate_search_contract") != base.SEARCH_CONTRACT
        or contract.get("accepted_target") != base.TARGET_ACCEPTED
        or contract.get("normalized_width_threshold") != base.NORMALIZED_WIDTH_THRESHOLD
        or contract.get("physical_replay_parallelism_per_scene") != 4
        or contract.get("candidate_batches") != [[1, 2, 3, 4]]
        or contract.get("four_scene_wave_barrier") is not False
        or contract.get("sibling_scene_wait") is not False
        or contract.get("lease_mode") != "none"
        or contract.get("post_convergence_stability_required") is not False
        or scene_contract.get("active_parameters") != list(active)
        or scene_contract.get("candidates_per_round") != 16
        or scene_contract.get("physical_waves_per_round") != 4
    ):
        raise RuntimeError("Top4 Algorithm A contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        path = ROOT / str(binding.get("path") or "")
        if path.is_symlink() or not path.is_file() or sha(path) != binding.get("sha256"):
            raise RuntimeError(f"Top4 Algorithm A source binding differs: {path}")
    return contract


def validate_launch_authority(
    path: Path, scene: str,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Any]]:
    authority = load(path)
    contract = validate_contract(scene, base.scene_parameter_space(scene)[0])
    artifacts = authority.get("artifacts") or {}
    freeze_path = Path(str(authority.get("resource_freeze_path") or "")).resolve()
    freeze = load(freeze_path)
    prepared_path = Path(str(artifacts.get("prepared") or "")).resolve()
    accounts_path = Path(str(artifacts.get("accounts") or "")).resolve()
    preflight_path = Path(str(artifacts.get("preflight") or "")).resolve()
    prepared = load(prepared_path)
    accounts = load(accounts_path)
    preflight = load(preflight_path)
    workers = {
        str(row.get("worker_id")): copy.deepcopy(dict(row))
        for row in prepared.get("workers") or []
        if row.get("worker_id") in EXPECTED[scene]
    }
    account_workers = {
        str(row.get("worker_id")): copy.deepcopy(dict(row))
        for row in accounts.get("workers") or []
        if row.get("worker_id") in EXPECTED[scene]
    }
    workers = {
        worker_id: {**workers[worker_id], **account_workers[worker_id]}
        for worker_id in sorted(workers)
    }
    gate = preflight.get("rolling_dynamic_noninterference_gate") or {}
    if (
        authority.get("status") != "PASS"
        or authority.get("scene") != scene
        or authority.get("formal_scope") != "mysql_only"
        or authority.get("cross_engine_comparison_prohibited") is not True
        or authority.get("physical_replay_parallelism") != 4
        or authority.get("recalibration_performed") is not False
        or authority.get("accepted_state_credit_increment") != 0
        or authority.get("resource_freeze_sha256") != sha(freeze_path)
        or freeze.get("formal_scope") != "mysql_only"
        or freeze.get("cross_engine_comparison_prohibited") is not True
        or prepared.get("status") != "PREPARED"
        or prepared.get("freeze_sha256") != sha(freeze_path)
        or accounts.get("status") != "PASS"
        or accounts.get("prepared_binding_sha256") != sha(prepared_path)
        or preflight.get("status") != "PASS"
        or float(preflight.get("observed_seconds") or 0.0) < 60.0
        or gate.get("status") != "PASS"
        or set(workers) != EXPECTED[scene]
        or set(account_workers) != EXPECTED[scene]
    ):
        raise RuntimeError("Top4 Algorithm A current live authority differs")
    frozen = {
        str(row.get("worker_id")): row
        for row in freeze.get("workers") or [] if row.get("worker_id") in EXPECTED[scene]
    }
    if set(frozen) != EXPECTED[scene]:
        raise RuntimeError("Top4 Algorithm A frozen worker set differs")
    resume.configure_runtime(freeze_path=freeze_path, workers=workers, scene=scene)
    for worker_id, worker in workers.items():
        inspect = base.alg.reset._docker_json("inspect", str(worker["container"]))
        if (
            inspect.get("Id") != worker.get("container_id")
            or (inspect.get("State") or {}).get("Running") is not True
        ):
            raise RuntimeError(f"Top4 Algorithm A live worker differs: {worker_id}")
        base.alg.reset.prep._kernel_resource_readback(inspect, worker)
        base.alg.reset.prep._enforce_blkio(inspect, freeze, worker)
    return freeze, workers, {
        "coordination_contract": load(COORDINATION),
        "resource_freeze_path": str(freeze_path),
        "resource_freeze_sha256": sha(freeze_path),
        "resource_freeze_schema": freeze["schema_version"],
        "launch_authority_path": str(path),
        "launch_authority_sha256": sha(path),
        "fresh_baseline_worker_count": 4,
        "historical_accepted_rounds_reused": False,
        "algorithm_b_state_or_result_credit": False,
        "lease_mode": "none",
        "contract_sha256": sha(CONTRACT),
        "contract": copy.deepcopy(contract),
    }


def no_lease_measure(**kwargs: Any):
    no_lease.alg = base.alg
    return no_lease.measure_batch(**kwargs)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--scene", choices=sorted(EXPECTED), required=True)
    parser.add_argument("--launch-authority", type=regular_file, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    base.REPLAY_PARALLELISM = 4
    base.A_CONTRACT = CONTRACT
    base.alg.TABLE_ROOT = TOP4_ROOT
    base.alg.TABLES = copy.deepcopy(TOP4_TABLES)
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
        fresh_r0=args.launch_authority,
        output_dir=args.output_dir,
    )
    return base.run(forwarded)


if __name__ == "__main__":
    raise SystemExit(main())

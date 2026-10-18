#!/usr/bin/env python3
"""Run one fresh four-worker XP Algorithm A scene in the frozen 4+4 cohort."""

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
CONTRACT = (
    ROOT / "config/formal/frozen_mysql_algorithm_a_xp_four_plus_four_20260823_v1"
    / "transition_contract.json"
)

from scripts import mysql_algorithm_a_job_to_xp_slots_v1 as slots
from scripts import run_mysql_algorithm_a_async_scene_v3 as base
from scripts import run_mysql_algorithm_a_job_to_xp_resume_v1 as resume


EXPECTED = {
    "hybench_xp_cpu": {f"xp_cpu_w{index}" for index in range(1, 5)},
    "hybench_xp_io": {f"xp_io_w{index}" for index in range(1, 5)},
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
        contract.get("schema_version")
        != "mysql_algorithm_a_xp_four_plus_four_contract_v1"
        or contract.get("status") != "FROZEN"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("algorithm") != "A"
        or contract.get("memory_scenes_must_complete_before_launch") is not True
        or contract.get("xp_parallelism")
        != {"hybench_xp_cpu": 4, "hybench_xp_io": 4, "total": 8}
        or contract.get("xp_scenes_measure_concurrently") is not True
        or contract.get("fresh_xp_runs_required") is not True
        or contract.get("historical_accepted_rounds_reused") is not False
        or contract.get("post_convergence_stability_required") is not False
        or scene_contract.get("active_parameters") != list(active)
        or int(scene_contract.get("candidates_per_round") or 0)
        != 1 << len(active)
    ):
        raise RuntimeError("frozen XP 4+4 contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        path = ROOT / str(binding.get("path") or "")
        if path.is_symlink() or not path.is_file() or sha(path) != binding.get("sha256"):
            raise RuntimeError(f"XP 4+4 source binding differs: {path}")
    return contract


def _terminal_memory(row: Mapping[str, Any], scene: str) -> bool:
    state_path = Path(str(row.get("state_path") or ""))
    if not state_path.is_file() or state_path.is_symlink():
        return False
    state = load(state_path)
    return bool(
        row.get("scene") == scene
        and row.get("state_sha256") == sha(state_path)
        and state.get("algorithm") == "A"
        and state.get("async_scene") == scene
        and state.get("status") == "COMPLETE"
        and state.get("stage") == "COMPLETE"
        and state.get("converged") is True
        and int(state.get("accepted") or 0) == 10
    )


def validate_launch_authority(
    path: Path, scene: str,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Any]]:
    authority = load(path)
    validate_contract(scene, base.scene_parameter_space(scene)[0])
    freeze_path = Path(str(authority.get("successor_freeze_path") or "")).resolve()
    freeze = load(freeze_path)
    workers = {
        str(row.get("worker_id") or ""): copy.deepcopy(dict(row))
        for row in authority.get("workers") or []
    }
    memory = authority.get("memory_terminal_evidence") or {}
    gate = authority.get("dynamic_noninterference_gate") or {}
    r0_path = Path(str(authority.get("r0_path") or "")).resolve()
    r0 = load(r0_path)
    if (
        authority.get("schema_version")
        != "mysql_algorithm_a_xp_four_plus_four_launch_authority_v1"
        or authority.get("status") != "PASS"
        or authority.get("formal_scope") != "mysql_only"
        or authority.get("cross_engine_comparison_prohibited") is not True
        or authority.get("contract_path") != str(CONTRACT)
        or authority.get("contract_sha256") != sha(CONTRACT)
        or authority.get("scene") != scene
        or authority.get("successor_freeze_sha256") != sha(freeze_path)
        or freeze.get("schema_version")
        != "mysql_algorithm_a_xp_four_worker_after_job_resource_successor_v1"
        or freeze.get("formal_scope") != "mysql_only"
        or freeze.get("cross_engine_comparison_prohibited") is not True
        or set(workers) != EXPECTED[scene]
        or any(row.get("scene") != scene for row in workers.values())
        or set(memory) != {"job_memory", "tpch_sf4_memory"}
        or not all(_terminal_memory(memory[name], name) for name in memory)
        or gate.get("status") != "PASS"
        or float(gate.get("observed_seconds") or 0.0) < 60.0
        or gate.get("target_worker_ids") != sorted(workers)
        or authority.get("fresh_baseline_all_four_workers") is not True
        or authority.get("historical_accepted_rounds_reused") is not False
        or authority.get("native_xp_workload_unchanged") is not True
        or authority.get("concurrent_companion_scene")
        != ({"hybench_xp_cpu", "hybench_xp_io"} - {scene}).pop()
        or not r0_path.is_file()
        or authority.get("r0_sha256") != sha(r0_path)
        or r0.get("status") != "PASS"
        or r0.get("authority_scene_scope") != scene
        or (r0.get("target_bottleneck_gate") or {}).get("passed") is not True
    ):
        raise RuntimeError("XP 4+4 launch authority differs")

    frozen = {
        str(row.get("worker_id") or ""): row
        for row in freeze.get("workers") or []
        if row.get("scene") == scene
    }
    fields = (
        "scene", "container", "host_port", *slots.POSITION_FIELDS,
        *slots.RESOURCE_FIELDS,
    )
    if set(frozen) != set(workers) or any(
        any(workers[worker_id].get(name) != frozen[worker_id].get(name) for name in fields)
        for worker_id in workers
    ):
        raise RuntimeError("XP 4+4 worker rows differ from successor freeze")

    resume.base = base
    resume.configure_runtime(freeze_path=freeze_path, workers=workers, scene=scene)
    for worker_id, worker in workers.items():
        inspect = base.alg.reset._docker_json("inspect", str(worker["container"]))
        state = inspect.get("State") or {}
        mounts = {
            str(row.get("Destination") or ""): str(row.get("Source") or "")
            for row in inspect.get("Mounts") or []
        }
        if (
            state.get("Running") is not True
            or inspect.get("Id") != worker.get("container_id")
            or mounts.get("/var/lib/mysql") != worker.get("datadir")
            or not Path(str(worker.get("credential_defaults") or "")).is_file()
            or not Path(str(worker.get("benchmark_defaults") or "")).is_file()
        ):
            raise RuntimeError(f"XP 4+4 live worker differs: {worker_id}")
        base.alg.reset.prep._kernel_resource_readback(inspect, worker)
        base.alg.reset.prep._enforce_blkio(inspect, freeze, worker)

    return freeze, workers, {
        "coordination_contract": base.c49.validate_v65_wave_coordination_contract(),
        "resource_freeze_path": str(freeze_path),
        "resource_freeze_sha256": sha(freeze_path),
        "resource_freeze_schema": freeze["schema_version"],
        "launch_authority_path": str(path),
        "launch_authority_sha256": sha(path),
        "transition_contract_path": str(CONTRACT),
        "transition_contract_sha256": sha(CONTRACT),
        "fresh_r0_source_path": str(r0_path),
        "fresh_r0_source_sha256": sha(r0_path),
        "fresh_baseline_worker_count": 4,
        "concurrent_companion_scene": authority["concurrent_companion_scene"],
        "historical_accepted_rounds_reused": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--scene", choices=sorted(EXPECTED), required=True)
    parser.add_argument("--launch-authority", type=regular_file, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    base.REPLAY_PARALLELISM = 4
    base.A_CONTRACT = CONTRACT

    def contract_adapter(scene: str, active: Sequence[str]) -> dict[str, Any]:
        return validate_contract(scene, active)

    def authority_adapter(**kwargs: Any):
        paths = {
            Path(str(kwargs[name])).resolve()
            for name in (
                "runtime_preflight", "prepared_binding",
                "benchmark_accounts", "fresh_r0",
            )
        }
        if paths != {args.launch_authority}:
            raise RuntimeError("XP 4+4 controller requires one sealed launch authority")
        return validate_launch_authority(args.launch_authority, str(kwargs["scene"]))

    base.validate_a_contract = contract_adapter
    base.validate_fresh_authority = authority_adapter
    forwarded = SimpleNamespace(
        run_id=args.run_id,
        scene=args.scene,
        runtime_preflight=args.launch_authority,
        prepared_binding=args.launch_authority,
        benchmark_accounts=args.launch_authority,
        fresh_r0=args.launch_authority,
        output_dir=args.output_dir,
    )
    return base.run(forwarded)


if __name__ == "__main__":
    raise SystemExit(main())

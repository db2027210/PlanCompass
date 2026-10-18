#!/usr/bin/env python3
"""Prepare and launch the isolated 5-GiB TPC-H Memory Algorithm C lane."""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional, Sequence


ROOT = Path(__file__).resolve().parents[1]
BASE_LAUNCHER = ROOT / "shell/launch_pg_algorithm_c_four_experience_sgd_20260809.py"
SPEC = importlib.util.spec_from_file_location("algorithm_c_four_base", BASE_LAUNCHER)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load base launcher: {BASE_LAUNCHER}")
base = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(base)


MEMORY_GIB = 5.0
SOURCE_BASELINE = Path(
    "/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/"
    "20260811_algorithm_c_cache_evict_failclosed_v5/tpch_memory/"
    "calibration_replays.jsonl"
)


def prepare(tag: str) -> Path:
    run_root = base.prepare(tag, ("tpch_memory",))
    scene_dir = run_root / "tpch_memory"
    config_path = scene_dir / "optimizer_config.json"
    contract_path = scene_dir / "workload_contract.json"
    assignment_path = scene_dir / "worker_assignment.json"

    cfg = base.read_json(config_path)
    bpt = cfg["binary_plan_tuning"]
    workers = bpt["validation"]["docker_workers"]["workers"]
    for worker in workers:
        worker["memory"] = f"{MEMORY_GIB:g}g"
    capacity = {"cpu_cores": 8.0, "memory_gib": MEMORY_GIB, "io_mib_s": 1000.0}
    bpt["acceptance_policy"]["resource_capacity"] = dict(capacity)
    bpt["experiment_meta"]["machine_spec"]["mem_gb"] = MEMORY_GIB
    docker_runtime = bpt["validation"]["docker_runtime"]
    docker_runtime["evict_data_file_cache_host_sudo"] = True
    docker_runtime["evict_data_file_cache_postgres_program_fallback"] = False
    docker_runtime["evict_data_file_cache_container_fallback"] = False
    docker_runtime["evict_data_file_cache_require_success"] = True
    hybench = bpt["validation"]["hybench_xp"]
    hybench["timeout_s"] = 1800
    hybench["timeout_grace_s"] = 1800
    cfg["algorithm_c"]["resource_capacity"] = dict(capacity)
    cfg["algorithm_c"]["workload_contract"]["resource_capacity"] = dict(capacity)
    base.write_json(config_path, cfg)
    base.write_json(assignment_path, workers)

    contract = base.read_json(contract_path)
    contract["resource_capacity"] = dict(capacity)
    contract["target_requalification"] = {
        "contract": "algorithm_c_target_bottleneck_requalification_v1",
        "source_run": str(SOURCE_BASELINE.parent.parent),
        "source_baseline_sha256": base.sha256(SOURCE_BASELINE),
        "source_worker_memory_avg_gib": [4.367327528273779, 4.067978932727716],
        "source_worker_memory_p99_gib": [7.585029602050781, 7.559165954589844],
        "source_capacity_gib": 16.0,
        "source_average_utilization_pct": [27.29579705171112, 25.424868329548225],
        "new_container_and_capacity_gib": MEMORY_GIB,
        "expected_average_utilization_pct_before_replay": [
            87.34655056547558,
            81.35957865455432,
        ],
        "reason": "fresh cold-cache baseline proved the 16-GiB lane was not a memory bottleneck",
        "physical_workload_invocations": 0,
        "qps_used": False,
    }
    contract["cache_eviction_transport"] = {
        "contract": "algorithm_c_host_sudo_fadvise_failclosed_v1",
        "method": "sudo_root_posix_fadvise_DONTNEED",
        "require_success": True,
        "docker_exec_disabled": True,
        "reason": "Docker exec control plane timed out before workload in v9",
        "validated_files": 4001,
        "validated_bytes": 47153541465,
        "qps_used": False,
    }
    contract["workload_timeout_requalification"] = {
        "contract": "algorithm_c_tpch_sf5_memory_timeout_v1",
        "old_timeout_s": 660,
        "new_timeout_s": 1800,
        "source_run": (
            "/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/"
            "20260811_algorithm_c_tpch_memory5_hostsudo_v10"
        ),
        "source_physical_workload_invocations": 2,
        "source_technical_failures": 2,
        "source_valid_replays": 0,
        "source_failure": "complete SF5 Q1-Q22 exceeded 660 seconds at 5 GiB",
        "memory_limit_unchanged_gib": MEMORY_GIB,
        "qps_used": False,
    }
    base.write_json(contract_path, contract)

    manifest_path = run_root / "launch_manifest.json"
    manifest = base.read_json(manifest_path)
    manifest["source_provenance"]["files"][
        str(Path(__file__).resolve().relative_to(base.ROOT))
    ] = base.sha256(Path(__file__).resolve())
    manifest["tpch_memory_requalification"] = contract["target_requalification"]
    manifest["tpch_cache_eviction_transport"] = contract[
        "cache_eviction_transport"
    ]
    manifest["tpch_workload_timeout_requalification"] = contract[
        "workload_timeout_requalification"
    ]
    base.write_json(manifest_path, manifest)
    return run_root


def apply_tpch_memory_contract(
    run_root: Path,
    scene_names: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Apply TPC-H CPU/memory limits with timeout and fail-closed readback."""
    readbacks: Dict[str, Any] = {}
    selected = tuple(scene_names or base.configured_scenes(run_root))
    if selected != ("tpch_memory",):
        raise RuntimeError(f"isolated TPC-H launcher received scenes={selected}")

    cfg = base.read_json(run_root / "tpch_memory" / "optimizer_config.json")
    workers = cfg["binary_plan_tuning"]["validation"]["docker_workers"]["workers"]
    expected_memory = int(MEMORY_GIB * (1024 ** 3))
    for index, worker in enumerate(workers, start=1):
        container = str(worker["container"])
        cpuset = base.DB_CPUSETS["tpch_memory"][index - 1]
        cpu_quota = float(len(base.expand_cpuset(cpuset)))
        expected_nano_cpus = int(round(cpu_quota * 1_000_000_000))
        configured_memory = str(worker.get("memory") or "")
        if configured_memory != f"{MEMORY_GIB:g}g":
            raise RuntimeError(
                f"TPC-H worker memory contract mismatch: {container}={configured_memory}"
            )
        command = [
            "docker",
            "update",
            "--cpuset-cpus",
            cpuset,
            "--cpus",
            str(cpu_quota),
            "--memory",
            configured_memory,
            container,
        ]
        timed_out = False
        returncode: Optional[int] = None
        stderr = ""
        try:
            completed = subprocess.run(
                command,
                text=True,
                capture_output=True,
                check=False,
                timeout=30.0,
            )
            returncode = completed.returncode
            stderr = completed.stderr
        except subprocess.TimeoutExpired as exc:
            # On this host Docker can apply the cgroup update but leave the
            # CLI waiting for the daemon response.  subprocess.run terminates
            # that CLI at the deadline; only the authoritative inspect
            # readback below may admit the lane.
            timed_out = True
            stderr = str(exc)
        readback = base.docker_readback(container)
        readback_ok = bool(
            readback.get("cpuset_cpus") == cpuset
            and int(readback.get("nano_cpus") or 0) == expected_nano_cpus
            and int(readback.get("memory") or 0) == expected_memory
        )
        if not readback_ok:
            raise RuntimeError(
                "TPC-H Docker resource update/readback failed: "
                f"returncode={returncode} timed_out={timed_out} "
                f"stderr={stderr!r} readback={readback}"
            )
        readback["resource_update"] = {
            "command": command,
            "returncode": returncode,
            "timed_out": timed_out,
            "effective_readback_required": True,
        }
        readbacks[f"tpch_memory.w{index}"] = readback

    readback_path = run_root / "docker_resource_readback.json"
    base.write_json(readback_path, readbacks)
    return readbacks


def launch(tag: str) -> dict[str, int]:
    run_root = base.RUN_BASE / tag
    base.apply_resource_contract = apply_tpch_memory_contract
    return base.launch(run_root, ("tpch_memory",))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "launch"))
    parser.add_argument("--tag", required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        print(prepare(args.tag), flush=True)
        return 0
    pids = launch(args.tag)
    print(json.dumps({"run_root": str(base.RUN_BASE / args.tag), "pids": pids}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

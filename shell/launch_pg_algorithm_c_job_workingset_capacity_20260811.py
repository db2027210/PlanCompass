#!/usr/bin/env python3
"""Prepare and launch JOB with an empirical working-set score capacity."""

from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


ROOT = Path(__file__).resolve().parents[1]
BASE_LAUNCHER = ROOT / "shell/launch_pg_algorithm_c_four_experience_sgd_20260809.py"
SPEC = importlib.util.spec_from_file_location("algorithm_c_four_base", BASE_LAUNCHER)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load base launcher: {BASE_LAUNCHER}")
base = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(base)


PHYSICAL_MEMORY_GIB = 2.0
BASELINE_NORMALIZED_UTILIZATION = 0.90
SOURCE_BASELINE = Path(
    "/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/"
    "20260811_algorithm_c_cache_evict_failclosed_v5/job_memory/"
    "calibration_baseline_authority_2rows.jsonl"
)


def source_baseline_rows() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for line in SOURCE_BASELINE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not str(row.get("label") or "").startswith("calibration_baseline_"):
            continue
        if bool(row.get("technical_failure")) or str(row.get("status")) != "ok":
            raise RuntimeError(f"invalid source JOB baseline row: {row.get('label')}")
        memory_gib = float((row.get("resources") or {}).get("memory") or 0.0)
        completeness = row.get("workload_completeness") or {}
        if memory_gib <= 0.0 or not bool(completeness.get("passed")):
            raise RuntimeError(f"incomplete source JOB baseline row: {row.get('label')}")
        rows.append(row)
    if len(rows) != 2:
        raise RuntimeError(f"expected two authoritative JOB baseline rows, got {len(rows)}")
    return rows


def capacity_calibration() -> Dict[str, Any]:
    rows = source_baseline_rows()
    memory_gib = [float(row["resources"]["memory"]) for row in rows]
    effective_capacity = statistics.median(memory_gib) / BASELINE_NORMALIZED_UTILIZATION
    return {
        "contract": "algorithm_c_job_working_set_empirical_capacity_v1",
        "metric": "docker_cgroup_working_set",
        "physical_container_memory_gib": PHYSICAL_MEMORY_GIB,
        "physical_cgroup_limit_remains_unchanged": True,
        "source_replays": str(SOURCE_BASELINE),
        "source_replays_sha256": base.sha256(SOURCE_BASELINE),
        "source_labels": [str(row["label"]) for row in rows],
        "source_working_set_avg_gib": memory_gib,
        "source_technical_failures": 0,
        "source_complete_canonical_job_113": True,
        "baseline_normalized_utilization": BASELINE_NORMALIZED_UTILIZATION,
        "derived_working_set_capacity_gib": effective_capacity,
        "formula": "median(source_working_set_avg_gib) / 0.90",
        "reason": (
            "raw cgroup usage is pinned at the physical 2-GiB limit and cannot "
            "identify planner effects; working-set averages must be normalized "
            "so the target-resource score and strict outer decrease agree"
        ),
        "qps_used": False,
        "new_physical_workload_invocations": 0,
    }


def prepare(tag: str) -> Path:
    calibration = capacity_calibration()
    capacity_gib = float(calibration["derived_working_set_capacity_gib"])
    run_root = base.prepare(tag, ("job_memory",))
    scene_dir = run_root / "job_memory"
    config_path = scene_dir / "optimizer_config.json"
    contract_path = scene_dir / "workload_contract.json"

    cfg = base.read_json(config_path)
    bpt = cfg["binary_plan_tuning"]
    workers = bpt["validation"]["docker_workers"]["workers"]
    for worker in workers:
        if str(worker.get("memory") or "") != "2g":
            raise RuntimeError(f"JOB physical memory changed unexpectedly: {worker}")
    normalized_capacity = {
        "cpu_cores": 12.0,
        "memory_gib": capacity_gib,
        "io_mib_s": 1000.0,
    }
    bpt["acceptance_policy"]["resource_capacity"] = dict(normalized_capacity)
    bpt["working_set_capacity_calibration"] = dict(calibration)
    cfg["algorithm_c"]["resource_capacity"] = dict(normalized_capacity)
    cfg["algorithm_c"]["workload_contract"]["resource_capacity"] = dict(
        normalized_capacity
    )
    base.write_json(config_path, cfg)

    contract = base.read_json(contract_path)
    contract["resource_capacity"] = dict(normalized_capacity)
    contract["working_set_capacity_calibration"] = dict(calibration)
    contract["physical_container_memory_gib"] = PHYSICAL_MEMORY_GIB
    base.write_json(contract_path, contract)

    manifest_path = run_root / "launch_manifest.json"
    manifest = base.read_json(manifest_path)
    manifest["source_provenance"]["files"][
        str(Path(__file__).resolve().relative_to(base.ROOT))
    ] = base.sha256(Path(__file__).resolve())
    manifest["job_working_set_capacity_calibration"] = dict(calibration)
    base.write_json(manifest_path, manifest)
    return run_root


def apply_job_resource_contract(
    run_root: Path,
    scene_names: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Apply CPU isolation and verify the unchanged physical memory limit."""
    selected = tuple(scene_names or base.configured_scenes(run_root))
    if selected != ("job_memory",):
        raise RuntimeError(f"isolated JOB launcher received scenes={selected}")
    cfg = base.read_json(run_root / "job_memory" / "optimizer_config.json")
    workers = cfg["binary_plan_tuning"]["validation"]["docker_workers"]["workers"]
    expected_memory = int(PHYSICAL_MEMORY_GIB * (1024 ** 3))
    readbacks: Dict[str, Any] = {}
    for index, worker in enumerate(workers, start=1):
        container = str(worker["container"])
        cpuset = base.DB_CPUSETS["job_memory"][index - 1]
        cpu_quota = float(len(base.expand_cpuset(cpuset)))
        expected_nano_cpus = int(round(cpu_quota * 1_000_000_000))
        command = [
            "docker",
            "update",
            "--cpuset-cpus",
            cpuset,
            "--cpus",
            str(cpu_quota),
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
                "JOB Docker resource update/readback failed: "
                f"returncode={returncode} timed_out={timed_out} "
                f"stderr={stderr!r} readback={readback}"
            )
        readback["resource_update"] = {
            "command": command,
            "returncode": returncode,
            "timed_out": timed_out,
            "effective_readback_required": True,
        }
        readbacks[f"job_memory.w{index}"] = readback
    base.write_json(run_root / "docker_resource_readback.json", readbacks)
    return readbacks


def launch(tag: str) -> Dict[str, int]:
    run_root = base.RUN_BASE / tag
    base.apply_resource_contract = apply_job_resource_contract
    return base.launch(run_root, ("job_memory",))


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

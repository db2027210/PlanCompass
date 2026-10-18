#!/usr/bin/env python3
"""Build a new audited MySQL binding after a pre-workload affinity failure."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Dict, Mapping, Set


MYSQL_ROOT = Path("/home/anonymous/program/resource_adaptive_mysql")
FORMAL_TARGET_BINDING = (
    MYSQL_ROOT
    / "history/mysql_four_scene_20260809"
    / "v301_to_four_scene_two_worker_live_authority_20260826"
    / "prepared_binding.json"
)
CLIENT_CPUSETS = {
    "job_w1": "376-383",
    "job_w2": "400-407",
    "tpch_w1": "128-131,180-183",
    "tpch_w2": "408-415",
    "xp_cpu_w1": "288-295",
    "xp_cpu_w2": "112-119",
    "xp_io_w1": "304-311",
    "xp_io_w2": "312-319",
}
SEMANTIC_KEYS = (
    "worker_id",
    "scene",
    "container",
    "host_port",
    "database_cpuset",
    "database_cpuset_mems",
    "client_cpuset",
    "client_cpuset_mems",
    "cpu_quota_cores",
    "memory_bytes",
    "pids_max",
    "io_read_bps",
    "io_write_bps",
    "innodb_buffer_pool_bytes",
    "datadir",
)


def load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    checksum = sha256(path)
    path.with_name(path.name + ".sha256").write_text(
        f"{checksum}  {path.name}\n", encoding="ascii"
    )


def parse_cpuset(raw: str) -> Set[int]:
    cpus: Set[int] = set()
    for part in raw.split(","):
        token = part.strip()
        if "-" in token:
            start, end = (int(value) for value in token.split("-", 1))
            cpus.update(range(start, end + 1))
        elif token:
            cpus.add(int(token))
    if not cpus:
        raise RuntimeError(f"empty cpuset: {raw!r}")
    return cpus


def running_container_cpus(excluded_names: Set[str]) -> Dict[int, str]:
    listed = subprocess.run(
        ["docker", "ps", "-q"], check=True, text=True,
        capture_output=True, timeout=60,
    ).stdout.split()
    if not listed:
        return {}
    inspected = subprocess.run(
        ["docker", "inspect", *listed], check=True, text=True,
        capture_output=True, timeout=60,
    )
    occupied: Dict[int, str] = {}
    for row in json.loads(inspected.stdout):
        name = str(row.get("Name") or "").lstrip("/")
        if name in excluded_names:
            continue
        raw = str((row.get("HostConfig") or {}).get("CpusetCpus") or "")
        if not raw:
            continue
        for cpu in parse_cpuset(raw):
            occupied[cpu] = name
    return occupied


def zero_swap_authority(row: Mapping[str, Any]) -> Dict[str, Any]:
    name = str(row["container"])
    inspected = json.loads(subprocess.run(
        ["docker", "inspect", name], check=True, text=True,
        capture_output=True, timeout=60,
    ).stdout)[0]
    state = inspected.get("State") or {}
    host = inspected.get("HostConfig") or {}
    if state.get("Running") is not True:
        raise RuntimeError(f"{name}: container is not running")
    container_id = str(inspected.get("Id") or "")
    pid = int(state.get("Pid") or 0)
    memory_relative = ""
    for line in Path(f"/proc/{pid}/cgroup").read_text(encoding="ascii").splitlines():
        _hierarchy, controllers, relative = line.split(":", 2)
        if "memory" in controllers.split(","):
            memory_relative = relative.lstrip("/")
            break
    memory_cgroup = Path("/sys/fs/cgroup/memory") / memory_relative
    swappiness_path = memory_cgroup / "memory.swappiness"
    write = subprocess.run(
        ["sudo", "-n", "tee", str(swappiness_path)],
        input="0\n", check=False, text=True, stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE, timeout=30,
    )
    if write.returncode != 0:
        raise RuntimeError(
            f"{name}: zero-swap cgroup write failed: {write.stderr.strip()}"
        )
    kernel_swappiness = int(
        swappiness_path.read_text(encoding="ascii").strip()
    )
    docker_swappiness = host.get("MemorySwappiness")
    docker_memory_swap = int(host.get("MemorySwap") or 0)
    memory_bytes = int(row["memory_bytes"])
    if (
        not memory_relative
        or kernel_swappiness != 0
        or docker_memory_swap not in {-1, memory_bytes}
    ):
        raise RuntimeError(f"{name}: zero-swap authority differs")
    return {
        "container_id": container_id,
        "container_pid": pid,
        "docker_memory_swap": docker_memory_swap,
        "docker_memory_swappiness": docker_swappiness,
        "kernel_memory_swappiness": kernel_swappiness,
        "kernel_memory_swappiness_path": str(swappiness_path),
        "memory_cgroup": str(memory_cgroup),
        "host_swap_limit_supported": docker_memory_swap != -1,
        "effective_swap_bytes": 0,
        "status": "PASS",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-binding", type=Path, required=True)
    parser.add_argument("--failed-state", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    base = args.base_binding.resolve(strict=True)
    failed_state_path = args.failed_state.resolve(strict=True)
    output = args.output.resolve()
    if output.exists() or output.with_name(output.name + ".sha256").exists():
        raise RuntimeError("repair output already exists")

    binding = copy.deepcopy(load(base))
    failed_state = load(failed_state_path)
    if (
        binding.get("status") != "PASS"
        or binding.get("schema_version")
        != "mysql_external_baseline_independent_workers_v1"
        or binding.get("immutable_templates_only") is not True
    ):
        raise RuntimeError("base binding authority differs")
    if (
        failed_state.get("phase") != "FAILED_CLOSED"
        or int(failed_state.get("physical_workload_invocations", -1)) != 0
        or int(failed_state.get("candidate_physical_invocations", -1)) != 0
        or not (
            (failed_state.get("error") or {}).get("message")
            == "formal MySQL target CPU binding is missing"
            or "Docker/reset resource differs:"
            in str((failed_state.get("error") or {}).get("message") or "")
        )
    ):
        raise RuntimeError("failure is not a zero-workload affinity preflight")

    workers = {
        str(row["worker_id"]): row for row in binding.get("workers") or []
    }
    frozen_workers = {
        str(row["worker_id"]): row
        for row in (binding.get("freeze") or {}).get("workers") or []
    }
    if set(workers) != set(CLIENT_CPUSETS) or set(frozen_workers) != set(CLIENT_CPUSETS):
        raise RuntimeError("worker set differs")
    for worker_id, cpuset in CLIENT_CPUSETS.items():
        workers[worker_id]["client_cpuset"] = cpuset
        workers[worker_id]["client_cpuset_mems"] = "0"
        frozen_workers[worker_id]["client_cpuset"] = cpuset
        frozen_workers[worker_id]["client_cpuset_mems"] = "0"

    swap_authority = {
        worker_id: zero_swap_authority(worker)
        for worker_id, worker in workers.items()
    }
    for worker_id, authority in swap_authority.items():
        workers[worker_id]["container_id"] = authority["container_id"]
        workers[worker_id]["container_pid"] = authority["container_pid"]
        workers[worker_id]["zero_swap_authority"] = authority
        frozen_workers[worker_id]["zero_swap_authority"] = authority

    binding["workers"] = [workers[str(row["worker_id"])] for row in binding["workers"]]
    binding["freeze"]["workers"] = [
        frozen_workers[str(row["worker_id"])]
        for row in binding["freeze"]["workers"]
    ]
    domains: Dict[str, Set[int]] = {}
    for worker_id, worker in workers.items():
        domains[f"{worker_id}:database"] = parse_cpuset(str(worker["database_cpuset"]))
        domains[f"{worker_id}:client"] = parse_cpuset(str(worker["client_cpuset"]))
    owners: Dict[int, str] = {}
    for label, cpus in domains.items():
        for cpu in cpus:
            if cpu in owners:
                raise RuntimeError(f"CPU {cpu} overlaps {owners[cpu]} and {label}")
            owners[cpu] = label

    formal = load(FORMAL_TARGET_BINDING)
    formal_cpus = set(
        int(cpu)
        for cpu in formal["rolling_dynamic_noninterference_gate"]
        ["actual_target_cpu_scope_release"]["target_cpus"]
    )
    formal_overlap = sorted(set(owners) & formal_cpus)
    if formal_overlap:
        raise RuntimeError(f"formal MySQL CPU overlap: {formal_overlap}")
    own_containers = {str(worker["container"]) for worker in workers.values()}
    occupied = running_container_cpus(own_containers)
    running_overlap = {
        cpu: occupied[cpu] for cpu in sorted(set(owners) & set(occupied))
    }
    if running_overlap:
        raise RuntimeError(f"running container CPU overlap: {running_overlap}")

    binding["client_affinity_rebind"] = {
        "formal_target_binding": str(FORMAL_TARGET_BINDING),
        "formal_target_binding_sha256": sha256(FORMAL_TARGET_BINDING),
        "formal_target_cpu_scope_excluded": True,
        "running_container_logical_cpuset_overlap": False,
        "reason": (
            "repair zero-workload preflight binding to exclude the formal "
            "MySQL target and independently running container CPU scopes"
        ),
        "source_binding": str(base),
        "source_binding_sha256": sha256(base),
        "failed_state": str(failed_state_path),
        "failed_state_sha256": sha256(failed_state_path),
        "failed_physical_workload_invocations": 0,
    }
    binding["container_zero_swap_policy"] = {
        "status": "PASS",
        "policy": "memory_swappiness_zero_with_live_cgroup_readback",
        "host_memsw_accounting_required": False,
        "workers": swap_authority,
    }
    binding["worker_semantic_sha256"] = canonical_sha(
        [
            {key: worker[key] for key in SEMANTIC_KEYS}
            for worker in binding["workers"]
        ]
    )
    atomic(output, binding)
    print(output, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

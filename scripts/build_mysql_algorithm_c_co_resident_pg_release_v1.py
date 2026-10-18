#!/usr/bin/env python3
"""Publish runtime v20 / coordination v27 for frozen isolated disk1 PG work."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OLD_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260814_v19/optimization_contract.json"
NEW_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260814_v20/optimization_contract.json"
OLD_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260814_v26/coordination_contract.json"
NEW_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260814_v27/coordination_contract.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def rebind(bindings: dict) -> None:
    for key, value in list(bindings.items()):
        sha_key = key[:-5] + "_sha256" if key.endswith("_path") else None
        if sha_key is None or sha_key not in bindings:
            continue
        path = (ROOT / str(value)).resolve()
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"runtime release binding differs: {path}")
        bindings[sha_key] = sha(path)


def write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=False)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def main() -> int:
    coordination = json.loads(OLD_COORD.read_text(encoding="utf-8"))
    coordination["predecessor"] = {
        "path": rel(OLD_COORD), "sha256": sha(OLD_COORD),
    }
    coordination["successor_reason"] = (
        "allow only the live-read-back frozen disk1 PG cohort when CPU, storage, "
        "memory/swap and process-headroom isolation all pass dynamically"
    )
    coordination["maximum_normalized_load1"] = 0.10
    coordination["co_resident_process_policy"]["active_postgresql_physical_workloads"] = (
        "allowed only for the exact frozen disk1 cohort after every dynamic isolation check passes"
    )
    coordination["co_resident_isolated_pg_allowance"] = {
        "status": "FROZEN_LIVE_READBACK_REQUIRED",
        "allowed_external_cpu_union": "80-99,160-167",
        "mysql_target_cpu_overlap_required": 0,
        "allowed_postgres_ports": [59513, 59514, 59515, 59516],
        "maximum_external_workload_processes": 32,
        "maximum_external_workload_rss_bytes": 17179869184,
        "minimum_host_mem_available_bytes": 549755813888,
        "host_swap_total_bytes_required": 0,
        "maximum_memory_full_psi_avg10": 0.10,
        "external_container_pids_limit_readback": None,
        "process_isolation_evidence": (
            "all target MySQL containers retain frozen finite PidsLimit; external workload count is dynamically capped"
        ),
        "numa_memory_isolation_evidence": (
            "target cpuset_mems remains frozen; each PG database has a 12GiB memory cgroup cap; host headroom and full-memory PSI are gated"
        ),
        "storage_isolation_evidence": (
            "all frozen PG database mounts are exact /disk1 sources and no frozen PG container may mount /disk2"
        ),
        "containers": [
            {
                "name": "ra_fullgrid_hybench_cpu_20260718_w1",
                "container_id": "1ee9ca4ee76aba1f9cc764e3244d4b12a863f8593c98aec35c270000293c2ae1",
                "cpuset_cpus": "80-83", "memory_bytes": 12884901888,
                "disk1_mount_source": "/disk1/resource_adaptive/bruteforce_pg17_runs_20260718/workers/hybench_cpu/w1/pgdata",
            },
            {
                "name": "ra_fullgrid_hybench_cpu_20260718_w2",
                "container_id": "f34d0ca64b1e1016259aff7165026adcf025f4ee47e23efd2f9d36ec1f6d8119",
                "cpuset_cpus": "84-87", "memory_bytes": 12884901888,
                "disk1_mount_source": "/disk1/resource_adaptive/bruteforce_pg17_runs_20260718/workers/hybench_cpu/w2/pgdata",
            },
            {
                "name": "ra_fullgrid_hybench_none_20260718_w1",
                "container_id": "d3eaae0769da3d90079ec6e55ef3a309e26f2b2d189b232bb139effdddb1bf2b",
                "cpuset_cpus": "88-91", "memory_bytes": 12884901888,
                "disk1_mount_source": "/disk1/resource_adaptive/bruteforce_pg17_runs_20260718/workers/hybench_none/w1/pgdata",
            },
            {
                "name": "ra_fullgrid_hybench_none_20260718_w2",
                "container_id": "b447fc532eea4b079c0749e0cd28dd1becea748d72a73d86eb440fc41f53791f",
                "cpuset_cpus": "96-99", "memory_bytes": 12884901888,
                "disk1_mount_source": "/disk1/resource_adaptive/bruteforce_pg17_runs_20260718/workers/hybench_none/w2/pgdata",
            },
        ],
    }
    coordination["source_bindings"]["co_resident_isolation_test_path"] = (
        "tests/test_mysql_cross_project_wave_coordination.py"
    )
    coordination["source_bindings"]["co_resident_isolation_test_sha256"] = ""
    rebind(coordination["source_bindings"])
    write(NEW_COORD, coordination)

    optimization = json.loads(OLD_OPT.read_text(encoding="utf-8"))
    optimization["schema_version"] = "mysql_algorithm_c_outer_runtime_optimization_v20"
    optimization["predecessor"] = {
        "path": rel(OLD_OPT), "sha256": sha(OLD_OPT),
    }
    optimization["successor_reason"] = (
        "replace absence-only PG blocking with versioned live-read-back isolation proof"
    )
    optimization["co_resident_isolated_pg_allowance"] = {
        "coordination_contract_path": rel(NEW_COORD),
        "coordination_contract_sha256": None,
        "unknown_or_changed_external_workload_action": "fail_closed",
        "algorithm_acceptance_and_convergence_semantics_changed": False,
    }
    builder = Path(__file__).resolve()
    optimization["bindings"]["co_resident_pg_release_builder_path"] = rel(builder)
    optimization["bindings"]["co_resident_pg_release_builder_sha256"] = sha(builder)
    rebind(optimization["bindings"])
    optimization["co_resident_isolated_pg_allowance"]["coordination_contract_sha256"] = sha(NEW_COORD)
    write(NEW_OPT, optimization)
    print(json.dumps({
        "coordination_sha256": sha(NEW_COORD),
        "optimization_sha256": sha(NEW_OPT),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

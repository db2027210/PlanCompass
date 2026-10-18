#!/usr/bin/env python3
"""Publish v21/v28 after freezing every live 20260718 disk1 PG lane."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OLD_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260814_v20/optimization_contract.json"
NEW_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260814_v21/optimization_contract.json"
OLD_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260814_v27/coordination_contract.json"
NEW_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260814_v28/coordination_contract.json"


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
        "freeze all eight live 20260718 disk1 PG lanes so fixed CPU-disjoint "
        "generic and generic-memory work is proven rather than misclassified"
    )
    allowance = coordination["co_resident_isolated_pg_allowance"]
    allowance["allowed_external_cpu_union"] = (
        "48-91,96-99,136-143,160-175"
    )
    allowance["allowed_postgres_ports"] = [
        59513, 59514, 59515, 59516, 59521, 59522, 59523, 59524,
    ]
    allowance["containers"].extend([
        {
            "name": "ra_fullgrid_generic_20260718_w1",
            "container_id": "b6d4292d8a1522633a2218ced79729e66dbc465fe937c0726dd3cbe2b6e427d1",
            "cpuset_cpus": "64-71", "memory_bytes": 8589934592,
            "disk1_mount_source": "/disk1/resource_adaptive/bruteforce_pg17_runs_20260718/workers/generic/w1/pgdata",
        },
        {
            "name": "ra_fullgrid_generic_20260718_w2",
            "container_id": "b60d76aece750b4eb1379200f9b572a8370f0bc53d387388168232b306f17fef",
            "cpuset_cpus": "72-79", "memory_bytes": 8589934592,
            "disk1_mount_source": "/disk1/resource_adaptive/bruteforce_pg17_runs_20260718/workers/generic/w2/pgdata",
        },
        {
            "name": "ra_fullgrid_generic_mem_20260718_w1",
            "container_id": "c7be677c9df98ad4b5de2e8d8f5e07b8fc5caa3845e565a1c4b2fcd5bc876714",
            "cpuset_cpus": "48-55,136-139,160-163,168-171",
            "memory_bytes": 8589934592,
            "disk1_mount_source": "/disk1/resource_adaptive/bruteforce_pg17_runs_20260718/workers/generic_memory/w1/pgdata",
        },
        {
            "name": "ra_fullgrid_generic_mem_20260718_w2",
            "container_id": "60795cb17abf6d951c818f2a19e7ebd7392b0ccf847be792f1fbee9577de74e8",
            "cpuset_cpus": "56-63,140-143,164-167,172-175",
            "memory_bytes": 8589934592,
            "disk1_mount_source": "/disk1/resource_adaptive/bruteforce_pg17_runs_20260718/workers/generic_memory/w2/pgdata",
        },
    ])
    rebind(coordination["source_bindings"])
    write(NEW_COORD, coordination)

    optimization = json.loads(OLD_OPT.read_text(encoding="utf-8"))
    optimization["schema_version"] = "mysql_algorithm_c_outer_runtime_optimization_v21"
    optimization["predecessor"] = {
        "path": rel(OLD_OPT), "sha256": sha(OLD_OPT),
    }
    optimization["successor_reason"] = (
        "expand the exact frozen disk1 PG cohort without changing Algorithm C, "
        "measurement, acceptance, pruning or convergence semantics"
    )
    optimization["co_resident_isolated_pg_allowance"] = {
        "coordination_contract_path": rel(NEW_COORD),
        "coordination_contract_sha256": sha(NEW_COORD),
        "unknown_or_changed_external_workload_action": "fail_closed",
        "algorithm_acceptance_and_convergence_semantics_changed": False,
        "formal_completed_ranks_reusable_after_pre_measurement_interruption": True,
    }
    builder = Path(__file__).resolve()
    optimization["bindings"]["expanded_pg_cohort_release_builder_path"] = rel(builder)
    optimization["bindings"]["expanded_pg_cohort_release_builder_sha256"] = sha(builder)
    rebind(optimization["bindings"])
    write(NEW_OPT, optimization)
    print(json.dumps({
        "coordination_sha256": sha(NEW_COORD),
        "optimization_sha256": sha(NEW_OPT),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

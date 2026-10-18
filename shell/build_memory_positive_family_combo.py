#!/usr/bin/env python3
"""Build combined Memory workload from positive plan families (post causal pairs)."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "config/generated/bonect_phase2_vm_memory_v124ap_faithful6"
HOSTS = {
    "bonect-replay-v87-1": "192.0.2.12",
    "bonect-replay-v87-2": "192.0.2.11",
    "bonect-replay-v87-3": "192.0.2.13",
}

TEACHERS = {
    "v124": {
        "candidate_id": "v124_teacher_cand7",
        "params": {
            "seq_page_cost": 1.0,
            "random_page_cost": 4.5,
            "cpu_tuple_cost": 0.01,
            "cpu_index_tuple_cost": 0.005,
            "cpu_operator_cost": 0.0075,
            "parallel_setup_cost": 1000.0,
            "parallel_tuple_cost": 0.1,
        },
    },
    "v21": {
        "candidate_id": "v21_plan_teacher",
        "params": {
            "seq_page_cost": 75.0,
            "random_page_cost": 15.0,
            "cpu_tuple_cost": 0.01,
            "cpu_index_tuple_cost": 0.005,
            "cpu_operator_cost": 0.0025,
            "parallel_setup_cost": 1000.0,
            "parallel_tuple_cost": 0.1,
        },
    },
}


def sub_prop(text: str, key: str, value: str) -> str:
    if re.search(rf"(?m)^{key}=", text):
        return re.sub(rf"(?m)^{key}=.*$", f"{key}={value}", text)
    return text + f"\n{key}={value}\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ap-ids", required=True)
    parser.add_argument("--ap-weights", required=True)
    parser.add_argument("--teacher", choices=["v124", "v21"], default="v124")
    parser.add_argument("--work-mem", default="32MB")
    parser.add_argument("--shared-buffers", default="1536MB")
    parser.add_argument("--apclient", type=int, default=8)
    parser.add_argument("--tag", required=True)
    args = parser.parse_args()

    setup = BASE / f"v124ap_setup_{args.tag}.sql"
    setup.write_text(
        "\n".join(
            [
                f"ALTER DATABASE hybench_sf10x SET work_mem = '{args.work_mem}';",
                "ALTER DATABASE hybench_sf10x SET hash_mem_multiplier = 1.0;",
                "VACUUM (ANALYZE) transfer;",
                "VACUUM (ANALYZE) checking;",
                "VACUUM (ANALYZE) customer;",
                "VACUUM (ANALYZE) company;",
                "VACUUM (ANALYZE) savingaccount;",
                "VACUUM (ANALYZE) loanapps;",
                "VACUUM (ANALYZE) loantrans;",
                "",
            ]
        ),
        encoding="utf-8",
    )

    workers = []
    for vm, host in HOSTS.items():
        props = (BASE / "frozen_pC2_bonect-replay-v87-1.props").read_text(encoding="utf-8")
        props = re.sub(r"192\.168\.122\.\d+", host, props)
        props = sub_prop(props, "ap_query_ids", args.ap_ids)
        props = sub_prop(props, "ap_query_weights", args.ap_weights)
        props = sub_prop(props, "apclient", str(args.apclient))
        props_path = BASE / f"{args.tag}_{vm}.props"
        props_path.write_text(props, encoding="utf-8")

        vmj = json.loads((BASE / "frozen_pC2_bonect-replay-v87-1_vm.json").read_text(encoding="utf-8"))
        vmj["fixed_params"] = {
            "max_parallel_workers_per_gather": 4,
            "max_parallel_workers": 8,
            "work_mem": args.work_mem,
            "shared_buffers": args.shared_buffers,
            "hash_mem_multiplier": "1.0",
        }
        vmj["postgres"]["host"] = host
        if "vm_runtime" in (vmj.get("metrics") or {}):
            vmj["metrics"]["vm_runtime"]["host"] = host
            vmj["metrics"]["vm_runtime"]["ssh_target"] = f"anonymous@{host}"
        stmt = str(BASE / "v124ap_stmt_postgres.toml")
        vm_path = BASE / f"{args.tag}_{vm}_vm.json"
        vmj["benchmark"]["workload_cmd"] = (
            f"cd /home/anonymous/HyBench-2024-master && bash hybench -t runap -c {props_path} -f {stmt}"
        )
        vmj["benchmark"]["pre_workload_sql_files"] = [str(setup)]
        snap = ((vmj.get("pre_benchmark_recovery") or {}).get("snapshot_restore") or {})
        if snap and snap.get("command"):
            cmd = str(snap["command"])
            cmd = re.sub(r"OPTIMIZER_CONFIG_PATH=\S+", f"OPTIMIZER_CONFIG_PATH={vm_path}", cmd)
            cmd = re.sub(r"DB_HOST=\S+", f"DB_HOST={host}", cmd)
            snap["command"] = cmd
            vmj["pre_benchmark_recovery"]["snapshot_restore"] = snap
        vmj["_variant"] = args.tag
        vmj["_teacher_params"] = TEACHERS[args.teacher]["params"]
        vm_path.write_text(json.dumps(vmj, indent=2) + "\n", encoding="utf-8")

        workers.append(
            {
                "enabled": True,
                "worker_id": vm,
                "vm_name": vm,
                "scenarios": ["memory"],
                "scenario_configs": {"memory": str(vm_path)},
                "config_override": {
                    "postgres": {"host": host, "port": 25432, "password": "CHANGE_ME"},
                    "metrics": {
                        "vm_runtime": {
                            "host": host,
                            "ssh_target": f"anonymous@{host}",
                            "cpu_mode": "vm_busy",
                            "io_mode": "disk_device",
                            "memory_mode": "vm_used",
                            "fixed_horizon_success": True,
                            "fixed_horizon_grace_s": 5,
                        }
                    },
                },
                "vm_resource": {
                    "vm_name": vm,
                    "cpu": 13,
                    "memory_gb": 3.0,
                    "memory_size": "3072M",
                    "read_mb": 800,
                    "write_mb": 800,
                    "disk_dev": "vda",
                },
                "disk_isolation_id": "",
                "disk_isolation_verified": False,
            }
        )

    pool_path = BASE / f"{args.tag}_worker3_pool.json"
    pool_path.write_text(json.dumps({"workers": workers}, indent=2) + "\n", encoding="utf-8")
    cand_path = BASE / f"{args.tag}_teacher_manifest.json"
    cand_path.write_text(
        json.dumps({"candidates": [TEACHERS[args.teacher]]}, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {"pool": str(pool_path), "candidates": str(cand_path), "setup": str(setup)},
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

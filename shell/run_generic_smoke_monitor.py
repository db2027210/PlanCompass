#!/usr/bin/env python3
"""Run one generic benchmark smoke test with the production cgroup monitor."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import shlex
from typing import Any, Dict


def load_monitor(module_path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("fullgrid_system_resource_monitor", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load resource monitor: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--framework", required=True, type=Path)
    parser.add_argument("--container", required=True)
    parser.add_argument("--props", required=True, type=Path)
    parser.add_argument(
        "--workload",
        required=True,
        choices=["tpcc", "tpch", "job", "hybench_ap", "hybench_tp", "hybench_xp"],
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--runtime-root", required=True, type=Path)
    parser.add_argument("--benchbase-source", type=Path, default=Path("/home/anonymous/benchbase-postgres"))
    parser.add_argument("--job-sql-dir", type=Path, default=Path(
        "/home/anonymous/dataset/pg_data/join-order-benchmark"
    ))
    parser.add_argument("--run-seconds", type=int, default=60)
    parser.add_argument("--terminals", type=int, default=1)
    parser.add_argument("--warmup-seconds", type=float, default=5.0)
    parser.add_argument("--hybench-launcher", type=Path, default=Path(
        "/disk2/resource_adaptive/binary_plan_tuning_hybenchxp/hybench_runtime/HyBench-2024-master/"
        "hybench_unblocked"
    ))
    parser.add_argument("--hybench-dir", type=Path, default=Path(
        "/disk2/resource_adaptive/binary_plan_tuning_hybenchxp/hybench_runtime/HyBench-2024-master"
    ))
    parser.add_argument("--hybench-statements", type=Path, default=Path(
        "/disk2/resource_adaptive/binary_plan_tuning_hybenchxp/hybench_runtime/HyBench-2024-master/"
        "conf/stmt_postgres.toml"
    ))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    framework = args.framework.resolve()
    monitor = load_monitor(framework / "system_resource_monitor.py")
    shim = framework / "shell/generic_workload_shim.sh"
    if not shim.is_file():
        raise FileNotFoundError(shim)

    if args.workload.startswith("hybench_"):
        mode = {
            "hybench_ap": "runap",
            "hybench_tp": "runtp",
            "hybench_xp": "runxp",
        }[args.workload]
        invocation = shlex.join(
            [
                str(args.hybench_launcher.resolve()),
                "-t", mode,
                "-c", str(args.props.resolve()),
                "-f", str(args.hybench_statements.resolve()),
            ]
        )
        command = f"cd {shlex.quote(str(args.hybench_dir.resolve()))} && {invocation}"
    else:
        os.environ.update(
            {
                "GENERIC_WORKLOAD": args.workload,
                "GENERIC_RUN_SECONDS": str(max(1, args.run_seconds)),
                "BENCHBASE_SOURCE_ROOT": str(args.benchbase_source.resolve()),
                "BENCHBASE_RUNTIME_ROOT": str(args.runtime_root.resolve()),
                "BENCHBASE_SCALEFACTOR": "10" if args.workload == "tpcc" else "1",
                "BENCHBASE_TERMINALS": str(max(1, args.terminals)),
                "BENCHBASE_JAVA_XMX": "-Xmx2g",
                "JOB_SQL_DIR": str(args.job_sql_dir.resolve()),
            }
        )
        command = shlex.join([str(shim), "-t", "generic", "-c", str(args.props.resolve())])
    resources, stats = monitor.monitor_resources_from_docker_runtime_workload(
        command,
        args.container,
        interval_s=1.0,
        warmup_s=max(0.0, args.warmup_seconds),
        memory_mode="working_set",
    )
    document: Dict[str, Any] = {
        "container": args.container,
        "workload": args.workload,
        "resources": resources,
        "resources_human": {
            "cpu_cores": resources.get("cpu"),
            "memory_gib": float(resources.get("memory", 0.0)) / (1024.0 ** 3),
            "io_mib_s": float(resources.get("io", 0.0)) / (1024.0 ** 2),
        },
        "stats": stats,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(args.output, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

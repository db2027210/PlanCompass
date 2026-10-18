#!/usr/bin/env python3
"""Bounded native HyBench configuration sweep with exact parameter restore."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from mysql_cost_adapter import MySQLCLIClient, MySQLCostAdapter
from mysql_live_measurement import measure_hybench
from mysql_workload_adapter import MySQLEndpoint


def write_json(path: Path, payload: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("ap", "tp", "xp"))
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--mysqld-pid", type=int, required=True)
    config_source = parser.add_mutually_exclusive_group(required=True)
    config_source.add_argument("--configs", help="JSON list of {name, params}")
    config_source.add_argument("--configs-file", type=Path,
                               help="path to a JSON list of {name, params}")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--duration-s", type=int, default=60)
    parser.add_argument("--clients", type=int, default=2)
    parser.add_argument("--extra-properties", default="{}")
    parser.add_argument("--timeout-s", type=float, default=360.0)
    args = parser.parse_args()

    configs_text = (
        args.configs_file.read_text(encoding="utf-8")
        if args.configs_file is not None else args.configs
    )
    configs = json.loads(configs_text)
    if not isinstance(configs, list) or not configs:
        raise ValueError("--configs must be a non-empty JSON list")
    extra = json.loads(args.extra_properties)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    endpoint = MySQLEndpoint("127.0.0.1", args.port, "hybench_10x", "root", "CHANGE_ME")
    controller = MySQLCostAdapter(MySQLCLIClient({
        "host": "127.0.0.1", "port": args.port, "user": "root",
        "password": "CHANGE_ME", "database": "hybench_10x", "query_timeout_sec": 30,
    }))
    snapshot = controller.snapshot()
    rows: list[dict[str, Any]] = []
    try:
        for index, config in enumerate(configs, start=1):
            name = str(config["name"])
            params = dict(config["params"])
            applied = controller.apply(params)
            measurement = measure_hybench(
                args.mode, endpoint, args.output_dir / f"{index:02d}_{name}",
                mysqld_pid=args.mysqld_pid,
                hybench_home=Path(__file__).resolve().parent / "hybench_runtime",
                duration_s=args.duration_s, clients=args.clients, scale_factor="10x",
                interval_s=1, timeout_s=args.timeout_s,
                extra_properties=extra, memory_mode="sql_dynamic",
            )
            rows.append({
                "name": name, "params": params, "applied": applied,
                "measurement": measurement,
            })
            write_json(args.output_dir / "sweep.json", {"rows": rows, "complete": False})
    finally:
        controller.restore(snapshot)
    write_json(args.output_dir / "sweep.json", {"rows": rows, "complete": True})
    print(json.dumps({"path": str(args.output_dir / "sweep.json"), "rows": len(rows)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Run one native HyBench mode with owned-mysqld resource sampling."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mysql_live_measurement import measure_hybench
from mysql_workload_adapter import MySQLEndpoint


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("ap", "tp", "xp"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=13363)
    parser.add_argument("--mysqld-pid", type=int, default=1257905)
    parser.add_argument("--clients", type=int, default=2)
    parser.add_argument("--ap-query-ids", default="")
    parser.add_argument("--ap-query-weights", default="")
    parser.add_argument("--ap-query-sequence", default="")
    parser.add_argument("--ap-query-sequence-limit", type=int, default=0)
    parser.add_argument(
        "--extra-properties", default="{}",
        help="JSON object merged into native HyBench workload properties",
    )
    args = parser.parse_args()
    extras = json.loads(args.extra_properties)
    if not isinstance(extras, dict):
        raise ValueError("--extra-properties must decode to a JSON object")
    for option, key in ((args.ap_query_ids, "ap_query_ids"),
                        (args.ap_query_weights, "ap_query_weights"),
                        (args.ap_query_sequence, "ap_query_sequence")):
        if option:
            extras[key] = option
    if args.ap_query_sequence_limit > 0:
        extras["ap_query_sequence_limit"] = args.ap_query_sequence_limit
    result = measure_hybench(
        args.mode,
        MySQLEndpoint("127.0.0.1", args.port, "hybench_10x", "root", "CHANGE_ME"),
        args.output_dir,
        mysqld_pid=args.mysqld_pid,
        hybench_home=Path(__file__).resolve().parent / "hybench_runtime",
        duration_s=60,
        clients=args.clients,
        scale_factor="10x",
        interval_s=1,
        timeout_s=300,
        extra_properties=extras,
        memory_mode="sql_dynamic",
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

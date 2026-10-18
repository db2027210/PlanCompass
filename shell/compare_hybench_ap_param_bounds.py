#!/usr/bin/env python3
from __future__ import annotations

from typing import Dict, List

from compare_hybench_ap_random_page_cost import AP_IDS, extract_ap_sql, run_explain
from pathlib import Path


PARAM_BOUNDS = {
    "seq_page_cost": (0.0001, 100.0),
    "random_page_cost": (0.0001, 20.0),
    "cpu_tuple_cost": (0.000001, 1.0),
    "cpu_index_tuple_cost": (0.000001, 1.0),
    "cpu_operator_cost": (0.000001, 1.0),
    "parallel_setup_cost": (0.0, 10000.0),
    "parallel_tuple_cost": (0.000001, 10.0),
}

DEFAULTS = {
    "seq_page_cost": 1.0,
    "random_page_cost": 4.0,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
}


def main() -> None:
    stmt_file = Path("/home/anonymous/HyBench-2024-master/conf/stmt_postgres.toml")
    host = "192.0.2.8"
    port = 25432
    user = "postgres"
    dbname = "hybench_sf10x"

    results: Dict[str, Dict[int, Dict[str, str]]] = {}
    node_prefixes: Dict[str, Dict[int, Dict[str, str]]] = {}

    for param, (low, high) in PARAM_BOUNDS.items():
        results[param] = {}
        node_prefixes[param] = {}
        for ap_id in AP_IDS:
            sql = extract_ap_sql(stmt_file, ap_id)
            variants = {
                "default": DEFAULTS,
                "min": {**DEFAULTS, param: low},
                "max": {**DEFAULTS, param: high},
            }
            results[param][ap_id] = {}
            node_prefixes[param][ap_id] = {}
            for variant, settings in variants.items():
                try:
                    plan_hash, _top, prefix = run_explain(
                        host=host,
                        port=port,
                        user=user,
                        dbname=dbname,
                        sql=sql,
                        settings=settings,
                    )
                except Exception as exc:
                    plan_hash = "ERROR"
                    prefix = str(exc).replace("\n", " ")[:160]
                results[param][ap_id][variant] = plan_hash
                node_prefixes[param][ap_id][variant] = prefix

    print("param\tAP\tdefault_hash\tmin_hash\tmax_hash\tmin_changed\tmax_changed", flush=True)
    for param in PARAM_BOUNDS:
        for ap_id in AP_IDS:
            row = results[param][ap_id]
            default_hash = row["default"]
            min_changed = row["min"] != default_hash
            max_changed = row["max"] != default_hash
            print(
                f"{param}\tAP-{ap_id}\t{default_hash}\t{row['min']}\t{row['max']}\t"
                f"{min_changed}\t{max_changed}",
                flush=True,
            )

    print("\nsummary_by_param", flush=True)
    for param in PARAM_BOUNDS:
        min_changed: List[str] = []
        max_changed: List[str] = []
        for ap_id in AP_IDS:
            row = results[param][ap_id]
            if row["min"] != row["default"]:
                min_changed.append(f"AP-{ap_id}")
            if row["max"] != row["default"]:
                max_changed.append(f"AP-{ap_id}")
        print(
            f"{param}: min_changes={','.join(min_changed) or '-'}; "
            f"max_changes={','.join(max_changed) or '-'}",
            flush=True,
        )

    print("\nchanged_plan_prefixes", flush=True)
    for param in PARAM_BOUNDS:
        for ap_id in AP_IDS:
            row = results[param][ap_id]
            base = row["default"]
            for variant in ("min", "max"):
                if row[variant] != base:
                    print(
                        f"{param} {variant} AP-{ap_id}: "
                        f"default=[{node_prefixes[param][ap_id]['default']}] "
                        f"{variant}=[{node_prefixes[param][ap_id][variant]}]",
                        flush=True,
                    )


if __name__ == "__main__":
    main()

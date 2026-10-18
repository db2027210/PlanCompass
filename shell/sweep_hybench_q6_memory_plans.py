#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import os
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Tuple


Q6_VARIANTS: Dict[str, Dict[str, float]] = {
    "default": {
        "seq_page_cost": 1.0,
        "random_page_cost": 4.0,
        "cpu_tuple_cost": 0.01,
        "cpu_index_tuple_cost": 0.005,
        "cpu_operator_cost": 0.0025,
        "parallel_setup_cost": 1000.0,
        "parallel_tuple_cost": 0.1,
    },
    "current_memory_once": {
        "seq_page_cost": 1.2,
        "random_page_cost": 5.0,
        "cpu_tuple_cost": 0.006,
        "cpu_index_tuple_cost": 0.005,
        "cpu_operator_cost": 0.0015,
        "parallel_setup_cost": 500.0,
        "parallel_tuple_cost": 0.05,
    },
    "force_hash_high_random": {
        "seq_page_cost": 0.5,
        "random_page_cost": 20.0,
        "cpu_tuple_cost": 0.001,
        "cpu_index_tuple_cost": 0.005,
        "cpu_operator_cost": 0.00025,
        "parallel_setup_cost": 1000.0,
        "parallel_tuple_cost": 0.01,
    },
    "force_hash_parallel": {
        "seq_page_cost": 1.0,
        "random_page_cost": 12.0,
        "cpu_tuple_cost": 0.002,
        "cpu_index_tuple_cost": 0.005,
        "cpu_operator_cost": 0.0005,
        "parallel_setup_cost": 300.0,
        "parallel_tuple_cost": 0.02,
    },
    "force_hash_seq_ok": {
        "seq_page_cost": 1.2,
        "random_page_cost": 10.0,
        "cpu_tuple_cost": 0.002,
        "cpu_index_tuple_cost": 0.005,
        "cpu_operator_cost": 0.0005,
        "parallel_setup_cost": 500.0,
        "parallel_tuple_cost": 0.03,
    },
    "force_bitmap_hash": {
        "seq_page_cost": 1.0,
        "random_page_cost": 8.0,
        "cpu_tuple_cost": 0.003,
        "cpu_index_tuple_cost": 0.005,
        "cpu_operator_cost": 0.00075,
        "parallel_setup_cost": 500.0,
        "parallel_tuple_cost": 0.03,
    },
    "moderate_memory": {
        "seq_page_cost": 1.5,
        "random_page_cost": 6.0,
        "cpu_tuple_cost": 0.004,
        "cpu_index_tuple_cost": 0.005,
        "cpu_operator_cost": 0.001,
        "parallel_setup_cost": 700.0,
        "parallel_tuple_cost": 0.05,
    },
    "nl_suppression": {
        "seq_page_cost": 1.0,
        "random_page_cost": 30.0,
        "cpu_tuple_cost": 0.0005,
        "cpu_index_tuple_cost": 0.005,
        "cpu_operator_cost": 0.0001,
        "parallel_setup_cost": 1000.0,
        "parallel_tuple_cost": 0.01,
    },
}


def extract_q6_sql(stmt_file: Path) -> Tuple[str, bool]:
    text = stmt_file.read_text(encoding="utf-8")
    match = re.search(r'^\[AP-6\]\s*\nsql\s*=\s*("""(.*?)"""|"(.*?)")', text, flags=re.M | re.S)
    if not match:
        raise RuntimeError(f"AP-6 not found in {stmt_file}")
    sql = (match.group(2) or match.group(3) or "").strip()
    if "?" in sql:
        return sql.replace("?", "$1", 1), True
    return sql, False


def collect_nodes(plan: Dict[str, Any]) -> List[str]:
    nodes = [str(plan.get("Node Type", "?"))]
    for child in plan.get("Plans", []) or []:
        if isinstance(child, dict):
            nodes.extend(collect_nodes(child))
    return nodes


def collect_runtime_evidence(plan: Dict[str, Any]) -> List[str]:
    evidence: List[str] = []
    node_type = str(plan.get("Node Type", "?"))
    if node_type in {"Sort", "Incremental Sort"}:
        method = plan.get("Sort Method")
        space_type = plan.get("Sort Space Type")
        space_used = plan.get("Sort Space Used")
        if method or space_type or space_used is not None:
            evidence.append(f"{node_type}({method}, {space_used}{space_type or ''})")
    if node_type == "Hash":
        evidence.append(f"Hash(batches={plan.get('Hash Batches')}, mem={plan.get('Peak Memory Usage')}kB)")
    if node_type in {"Aggregate", "HashAggregate"}:
        peak_mem = plan.get("Peak Memory Usage")
        disk = plan.get("Disk Usage")
        if peak_mem is not None or disk is not None:
            evidence.append(f"{node_type}(mem={peak_mem}kB, disk={disk}kB)")
    for child in plan.get("Plans", []) or []:
        if isinstance(child, dict):
            evidence.extend(collect_runtime_evidence(child))
    return evidence


def strip_costs(obj: Any) -> Any:
    ignored = {"Startup Cost", "Total Cost", "Plan Rows", "Plan Width", "Actual Startup Time",
               "Actual Total Time", "Actual Rows", "Actual Loops"}
    if isinstance(obj, dict):
        return {k: strip_costs(v) for k, v in obj.items() if k not in ignored}
    if isinstance(obj, list):
        return [strip_costs(v) for v in obj]
    return obj


def run_plan(
    *,
    host: str,
    port: int,
    user: str,
    dbname: str,
    password: str,
    sql: str,
    has_param: bool,
    variant: Dict[str, float],
    mode: str,
    analyze: bool,
) -> Dict[str, Any]:
    set_lines = [f"SET {name} = {value};" for name, value in variant.items()]
    explain_opts = "ANALYZE true, BUFFERS true, FORMAT JSON" if analyze else "FORMAT JSON, COSTS true"
    command_sql = "\n".join(
        [
            "SET statement_timeout = 600000;",
            f"SET plan_cache_mode = {mode};",
            *set_lines,
            "DEALLOCATE ALL;",
            f"PREPARE q6{'(text)' if has_param else ''} AS {sql};",
            f"EXPLAIN ({explain_opts}) EXECUTE q6{'(''software%'')' if has_param else ''};",
        ]
    )
    env = os.environ.copy()
    env["PGPASSWORD"] = password
    proc = subprocess.run(
        ["psql", "-h", host, "-p", str(port), "-U", user, "-d", dbname, "-X", "-q", "-t", "-A", "-c", command_sql],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"psql failed rc={proc.returncode} stdout={proc.stdout!r} stderr={proc.stderr!r}"
        )
    payload = proc.stdout.strip()
    data = json.loads(payload)
    plan = data[0]["Plan"]
    nodes = collect_nodes(plan)
    counts = Counter(nodes)
    normalized = strip_costs(plan)
    plan_hash = hashlib.sha256(json.dumps(normalized, sort_keys=True).encode("utf-8")).hexdigest()[:12]
    has_memory = any(n in nodes for n in ["Hash Join", "HashAggregate", "Hash", "Materialize", "Memoize"])
    has_parallel = any(n in nodes for n in ["Gather", "Gather Merge"])
    has_nested_index = counts["Nested Loop"] > 0 and counts["Index Scan"] >= 2
    result = {
        "hash": plan_hash,
        "root": plan.get("Node Type", "?"),
        "prefix": " -> ".join(nodes[:14]),
        "counts": dict(counts),
        "memory_like": has_memory,
        "parallel": has_parallel,
        "nested_index": has_nested_index,
    }
    if analyze:
        result["execution_ms"] = data[0].get("Execution Time")
        result["evidence"] = "; ".join(collect_runtime_evidence(plan)[:20])
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stmt-file", default="/home/anonymous/HyBench-2024-master/conf/stmt_postgres.toml")
    parser.add_argument("--host", default="192.0.2.8")
    parser.add_argument("--port", type=int, default=25432)
    parser.add_argument("--user", default="postgres")
    parser.add_argument("--password", default='CHANGE_ME')
    parser.add_argument("--dbname", default="hybench_sf10x")
    parser.add_argument("--analyze", action="store_true")
    parser.add_argument("--variants", default=",".join(Q6_VARIANTS.keys()))
    args = parser.parse_args()

    sql, has_param = extract_q6_sql(Path(args.stmt_file))
    selected = [name.strip() for name in args.variants.split(",") if name.strip()]

    print(
        "\t".join(
            [
                "variant",
                "mode",
                "hash",
                "root",
                "memory_like",
                "parallel",
                "nested_index",
                "counts",
                "execution_ms",
                "prefix",
                "evidence",
            ]
        ),
        flush=True,
    )
    for name in selected:
        variant = Q6_VARIANTS[name]
        for mode in ["force_generic_plan", "force_custom_plan"]:
            result = run_plan(
                host=args.host,
                port=args.port,
                user=args.user,
                dbname=args.dbname,
                password=args.password,
                sql=sql,
                has_param=has_param,
                variant=variant,
                mode=mode,
                analyze=args.analyze,
            )
            counts = result["counts"]
            compact_counts = ",".join(
                f"{k}={counts.get(k, 0)}"
                for k in [
                    "Seq Scan",
                    "Index Scan",
                    "Bitmap Heap Scan",
                    "Bitmap Index Scan",
                    "Nested Loop",
                    "Hash Join",
                    "Hash",
                    "Aggregate",
                    "Sort",
                    "Incremental Sort",
                    "Gather Merge",
                ]
                if counts.get(k, 0)
            )
            print(
                "\t".join(
                    [
                        name,
                        mode.replace("force_", "").replace("_plan", ""),
                        str(result["hash"]),
                        str(result["root"]),
                        str(result["memory_like"]),
                        str(result["parallel"]),
                        str(result["nested_index"]),
                        compact_counts,
                        str(result.get("execution_ms", "")),
                        str(result["prefix"]),
                        str(result.get("evidence", "")),
                    ]
                ),
                flush=True,
            )


if __name__ == "__main__":
    main()

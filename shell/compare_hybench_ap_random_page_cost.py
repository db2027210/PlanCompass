#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Tuple


AP_IDS = [6, 8, 9, 10, 11, 12, 13]


def extract_ap_sql(stmt_file: Path, ap_id: int) -> str:
    text = stmt_file.read_text(encoding="utf-8")
    pattern = rf"^\[AP-{ap_id}\]\s*\nsql\s*=\s*(\"\"\".*?\"\"\"|\".*?\")"
    match = re.search(pattern, text, flags=re.M | re.S)
    if not match:
        raise RuntimeError(f"AP-{ap_id} not found in {stmt_file}")
    raw = match.group(1)
    if raw.startswith('"""'):
        sql = raw[3:-3]
    else:
        sql = raw[1:-1]
    sql = sql.strip()
    if ap_id == 12:
        sql = sql.replace("?", "'software_IT'", 1)
    elif ap_id == 13:
        sql = sql.replace("?", "1", 1)
    if not sql.endswith(";"):
        sql += ";"
    return sql


def strip_plan_noise(obj: Any) -> Any:
    ignored = {
        "Startup Cost",
        "Total Cost",
        "Plan Rows",
        "Plan Width",
        "Parallel Aware",
        "Async Capable",
    }
    if isinstance(obj, dict):
        return {k: strip_plan_noise(v) for k, v in obj.items() if k not in ignored}
    if isinstance(obj, list):
        return [strip_plan_noise(v) for v in obj]
    return obj


def collect_node_types(plan: Dict[str, Any]) -> List[str]:
    nodes = [str(plan.get("Node Type", "?"))]
    for child in plan.get("Plans", []) or []:
        if isinstance(child, dict):
            nodes.extend(collect_node_types(child))
    return nodes


def collect_runtime_evidence(plan: Dict[str, Any]) -> List[str]:
    evidence: List[str] = []
    node_type = str(plan.get("Node Type", "?"))
    if node_type in {"Sort", "Incremental Sort"}:
        method = plan.get("Sort Method")
        space_type = plan.get("Sort Space Type")
        space_used = plan.get("Sort Space Used")
        if method or space_type or space_used is not None:
            evidence.append(
                f"{node_type}(method={method}, space={space_used}{space_type or ''})"
            )
    if node_type == "Hash":
        batches = plan.get("Hash Batches")
        peak_mem = plan.get("Peak Memory Usage")
        if batches is not None or peak_mem is not None:
            evidence.append(f"Hash(batches={batches}, peak_mem_kB={peak_mem})")
    if node_type in {"Hash Join", "HashAggregate", "Aggregate"}:
        peak_mem = plan.get("Peak Memory Usage")
        disk = plan.get("Disk Usage")
        if peak_mem is not None or disk is not None:
            evidence.append(f"{node_type}(peak_mem_kB={peak_mem}, disk_kB={disk})")
    for child in plan.get("Plans", []) or []:
        if isinstance(child, dict):
            evidence.extend(collect_runtime_evidence(child))
    return evidence


def run_explain(
    *,
    host: str,
    port: int,
    user: str,
    dbname: str,
    sql: str,
    settings: Dict[str, float],
) -> Tuple[str, str, str]:
    set_sql = "\n".join(f"SET {name} = {value};" for name, value in settings.items())
    explain_sql = f"{set_sql}\nEXPLAIN (FORMAT JSON, COSTS true, VERBOSE false) {sql}"
    cmd = [
        "psql",
        "-h",
        host,
        "-p",
        str(port),
        "-U",
        user,
        "-d",
        dbname,
        "-X",
        "-q",
        "-t",
        "-A",
        "-c",
        explain_sql,
    ]
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise RuntimeError(detail)
    payload = proc.stdout.strip()
    data = json.loads(payload)
    plan = data[0]["Plan"]
    normalized = strip_plan_noise(plan)
    plan_hash = hashlib.sha256(json.dumps(normalized, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    nodes = collect_node_types(plan)
    return plan_hash, plan.get("Node Type", "?"), " -> ".join(nodes[:12])


def run_explain_analyze(
    *,
    host: str,
    port: int,
    user: str,
    dbname: str,
    sql: str,
    settings: Dict[str, float],
) -> Tuple[str, str, str]:
    set_sql = "\n".join(f"SET {name} = {value};" for name, value in settings.items())
    explain_sql = (
        f"{set_sql}\n"
        "SET statement_timeout = 600000;\n"
        "EXPLAIN (ANALYZE true, BUFFERS true, FORMAT JSON, COSTS true, VERBOSE false) "
        f"{sql}"
    )
    cmd = [
        "psql",
        "-h",
        host,
        "-p",
        str(port),
        "-U",
        user,
        "-d",
        dbname,
        "-X",
        "-q",
        "-t",
        "-A",
        "-c",
        explain_sql,
    ]
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise RuntimeError(detail)
    payload = proc.stdout.strip()
    data = json.loads(payload)
    plan = data[0]["Plan"]
    nodes = collect_node_types(plan)
    evidence = collect_runtime_evidence(plan)
    total_ms = data[0].get("Execution Time")
    return str(total_ms), " -> ".join(nodes[:12]), "; ".join(evidence[:20])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stmt-file", default="/home/anonymous/HyBench-2024-master/conf/stmt_postgres.toml")
    parser.add_argument("--host", default="192.0.2.8")
    parser.add_argument("--port", type=int, default=25432)
    parser.add_argument("--user", default="postgres")
    parser.add_argument("--dbname", default="hybench_sf10x")
    parser.add_argument("--analyze", action="store_true")
    parser.add_argument("--ap-ids", default=",".join(str(x) for x in AP_IDS))
    parser.add_argument("--variant", default="")
    args = parser.parse_args()

    variants = {
        "default_rpc_4": {"seq_page_cost": 1.0, "random_page_cost": 4.0},
        "low_rpc_0.0001": {"seq_page_cost": 1.0, "random_page_cost": 0.0001},
        "high_rpc_20": {"seq_page_cost": 1.0, "random_page_cost": 20.0},
        "io_to_memory_trial": {
            "seq_page_cost": 0.5,
            "random_page_cost": 20.0,
            "cpu_tuple_cost": 0.001,
            "cpu_index_tuple_cost": 0.005,
            "cpu_operator_cost": 0.00025,
            "parallel_setup_cost": 1000.0,
            "parallel_tuple_cost": 0.01,
        },
        "bitmap_hash_memoize_moderate_parallel": {
            "seq_page_cost": 1.5,
            "random_page_cost": 2.5,
            "cpu_tuple_cost": 0.008,
            "cpu_index_tuple_cost": 0.005,
            "cpu_operator_cost": 0.002,
            "parallel_setup_cost": 500.0,
            "parallel_tuple_cost": 0.07,
        },
        "bad_io_151631_iter1": {
            "seq_page_cost": 1.375,
            "random_page_cost": 14.459180714809381,
            "cpu_tuple_cost": 0.008234749535883896,
            "cpu_index_tuple_cost": 0.005,
            "cpu_operator_cost": 0.002058687383970974,
            "parallel_setup_cost": 1000.0,
            "parallel_tuple_cost": 0.3858270608479032,
        },
        "memory_shift_v2": {
            "seq_page_cost": 1.2,
            "random_page_cost": 3.0,
            "cpu_tuple_cost": 0.006,
            "cpu_index_tuple_cost": 0.005,
            "cpu_operator_cost": 0.0015,
            "parallel_setup_cost": 300.0,
            "parallel_tuple_cost": 0.05,
        },
        "memory_shift_v3": {
            "seq_page_cost": 1.0,
            "random_page_cost": 4.0,
            "cpu_tuple_cost": 0.006,
            "cpu_index_tuple_cost": 0.005,
            "cpu_operator_cost": 0.0015,
            "parallel_setup_cost": 500.0,
            "parallel_tuple_cost": 0.05,
        },
        "memory_shift_v4": {
            "seq_page_cost": 1.3,
            "random_page_cost": 3.0,
            "cpu_tuple_cost": 0.007,
            "cpu_index_tuple_cost": 0.005,
            "cpu_operator_cost": 0.0018,
            "parallel_setup_cost": 400.0,
            "parallel_tuple_cost": 0.06,
        },
        "memory_shift_v5": {
            "seq_page_cost": 1.5,
            "random_page_cost": 3.0,
            "cpu_tuple_cost": 0.008,
            "cpu_index_tuple_cost": 0.005,
            "cpu_operator_cost": 0.002,
            "parallel_setup_cost": 500.0,
            "parallel_tuple_cost": 0.07,
        },
        "balanced_memory_1": {
            "seq_page_cost": 1.2,
            "random_page_cost": 5.0,
            "cpu_tuple_cost": 0.006,
            "cpu_index_tuple_cost": 0.005,
            "cpu_operator_cost": 0.0015,
            "parallel_setup_cost": 500.0,
            "parallel_tuple_cost": 0.05,
        },
        "balanced_memory_2": {
            "seq_page_cost": 1.5,
            "random_page_cost": 4.0,
            "cpu_tuple_cost": 0.004,
            "cpu_index_tuple_cost": 0.005,
            "cpu_operator_cost": 0.001,
            "parallel_setup_cost": 700.0,
            "parallel_tuple_cost": 0.06,
        },
        "io_oriented_1": {
            "seq_page_cost": 1.2,
            "random_page_cost": 3.5,
            "cpu_tuple_cost": 0.01,
            "cpu_index_tuple_cost": 0.006,
            "cpu_operator_cost": 0.0025,
            "parallel_setup_cost": 1000.0,
            "parallel_tuple_cost": 0.10,
        },
        "memory_oriented_1": {
            "seq_page_cost": 1.5,
            "random_page_cost": 2.5,
            "cpu_tuple_cost": 0.008,
            "cpu_index_tuple_cost": 0.005,
            "cpu_operator_cost": 0.002,
            "parallel_setup_cost": 700.0,
            "parallel_tuple_cost": 0.08,
        },
        "optimizer_best": {
            "seq_page_cost": 0.0001,
            "random_page_cost": 13.999949999999998,
            "cpu_tuple_cost": 0.5099994999999999,
            "cpu_index_tuple_cost": 0.07063907794109028,
            "cpu_operator_cost": 0.4410062858827704,
            "parallel_setup_cost": 0.0,
            "parallel_tuple_cost": 0.000001,
        },
    }

    ap_ids = [int(x.strip()) for x in str(args.ap_ids).split(",") if x.strip()]
    selected_variants = variants
    if args.variant:
        selected_variants = {args.variant: variants[args.variant]}

    if args.analyze:
        print("query\tvariant\texecution_ms\tnode_prefix\truntime_evidence", flush=True)
        for ap_id in ap_ids:
            sql = extract_ap_sql(Path(args.stmt_file), ap_id)
            for name, settings in selected_variants.items():
                try:
                    execution_ms, node_prefix, evidence = run_explain_analyze(
                        host=args.host,
                        port=args.port,
                        user=args.user,
                        dbname=args.dbname,
                        sql=sql,
                        settings=settings,
                    )
                    print(f"AP-{ap_id}\t{name}\t{execution_ms}\t{node_prefix}\t{evidence}", flush=True)
                except Exception as exc:
                    print(f"AP-{ap_id}\t{name}\tERROR\tERROR\t{exc}", flush=True)
        return

    print("query\tvariant\tplan_hash\ttop_node\tnode_prefix", flush=True)
    per_query: Dict[int, Dict[str, str]] = {}
    for ap_id in ap_ids:
        sql = extract_ap_sql(Path(args.stmt_file), ap_id)
        per_query[ap_id] = {}
        for name, settings in selected_variants.items():
            try:
                plan_hash, top_node, node_prefix = run_explain(
                    host=args.host,
                    port=args.port,
                    user=args.user,
                    dbname=args.dbname,
                    sql=sql,
                    settings=settings,
                )
                per_query[ap_id][name] = plan_hash
                print(f"AP-{ap_id}\t{name}\t{plan_hash}\t{top_node}\t{node_prefix}", flush=True)
            except Exception as exc:
                per_query[ap_id][name] = "ERROR"
                print(f"AP-{ap_id}\t{name}\tERROR\tERROR\t{exc}", flush=True)

    print("\nsummary", flush=True)
    for ap_id in ap_ids:
        hashes = per_query[ap_id]
        changed = len(set(hashes.values())) > 1
        print(f"AP-{ap_id}: changed={changed} hashes={hashes}", flush=True)


if __name__ == "__main__":
    main()

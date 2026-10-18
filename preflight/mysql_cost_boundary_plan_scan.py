#!/usr/bin/env python3
"""Screen benchmark SQL for structural plan changes at eight cost bounds.

Only the eight rows exposed by MySQL's optimizer cost tables are modified.
Every benchmark is checked at the compiled defaults, each one-factor lower
and upper bound, and the two uniform corners.  Cost estimates are removed
before plans are compared, so a query is reported only when plan structure
changes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tomllib
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mysql_cost_adapter import MYSQL_COST_KEYS, MySQLCLIClient, MySQLCostAdapter


DEFAULTS = {
    "io_block_read_cost": 1.0,
    "memory_block_read_cost": 0.25,
    "row_evaluate_cost": 0.1,
    "key_compare_cost": 0.05,
    "disk_temptable_create_cost": 20.0,
    "disk_temptable_row_cost": 0.5,
    "memory_temptable_create_cost": 1.0,
    "memory_temptable_row_cost": 0.1,
}
BOUNDS = {
    "io_block_read_cost": (0.01, 100.0),
    "memory_block_read_cost": (0.01, 100.0),
    "row_evaluate_cost": (0.01, 10.0),
    "key_compare_cost": (0.01, 10.0),
    "disk_temptable_create_cost": (0.01, 100.0),
    "disk_temptable_row_cost": (0.01, 100.0),
    "memory_temptable_create_cost": (0.01, 100.0),
    "memory_temptable_row_cost": (0.01, 100.0),
}
CONNECTIONS = {
    "hybench_ap": dict(port=13365, password="CHANGE_ME", database="hybench_10x"),
    "job": dict(port=13412, password="CHANGE_ME", database="imdb_job"),
    "tpch": dict(port=13307, password="CHANGE_ME", database="tpch"),
    "hybench_tp": dict(port=13365, password="CHANGE_ME", database="hybench_10x"),
    "tpcc": dict(port=57652, password="CHANGE_ME", database="tpcc"),
    "hybench_xp": dict(port=14368, password="CHANGE_ME", database="hybench_10x"),
}


def oat_profiles() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = [
        {"profile_id": "default", "changed": {}, "values": dict(DEFAULTS)}
    ]
    for name in MYSQL_COST_KEYS:
        for side, value in zip(("lower", "upper"), BOUNDS[name]):
            values = dict(DEFAULTS)
            values[name] = value
            rows.append({
                "profile_id": f"{name}__{side}",
                "changed": {name: value},
                "values": values,
            })
    rows.extend((
        {
            "profile_id": "all_lower",
            "changed": {name: limits[0] for name, limits in BOUNDS.items()},
            "values": {name: limits[0] for name, limits in BOUNDS.items()},
        },
        {
            "profile_id": "all_upper",
            "changed": {name: limits[1] for name, limits in BOUNDS.items()},
            "values": {name: limits[1] for name, limits in BOUNDS.items()},
        },
    ))
    return rows


def corner_profiles() -> list[dict[str, Any]]:
    """Return default plus the complete 2^8 lower/upper factorial."""
    names = list(MYSQL_COST_KEYS)
    rows: list[dict[str, Any]] = [
        {"profile_id": "default", "changed": {}, "values": dict(DEFAULTS)}
    ]
    for mask in range(1 << len(names)):
        values = {
            name: BOUNDS[name][(mask >> index) & 1]
            for index, name in enumerate(names)
        }
        rows.append({
            "profile_id": f"corner_{mask:03d}",
            "changed": dict(values),
            "values": values,
        })
    return rows


def profiles(design: str = "oat") -> list[dict[str, Any]]:
    if design == "oat":
        return oat_profiles()
    if design == "corners":
        return corner_profiles()
    raise ValueError(f"unknown profile design: {design}")


def bind_placeholders(sql: str, values: Iterable[str] | None = None) -> str:
    replacements = iter(values or ())

    def replace(_: re.Match[str]) -> str:
        return next(replacements, "1")

    return re.sub(r"\?", replace, sql).strip().rstrip(";")


def natural_key(name: str) -> tuple[Any, ...]:
    return tuple(int(part) if part.isdigit() else part for part in re.split(r"(\d+)", name))


def load_hybench(
    section_prefixes: tuple[str, ...], *, include_dml: bool = False,
    duplicate_bindings: bool = False,
) -> list[dict[str, str]]:
    source = Path("/home/anonymous/HyBench-2024-master/conf/stmt_mysql.toml")
    with source.open("rb") as handle:
        document = tomllib.load(handle)
    queries: list[dict[str, str]] = []
    for section in sorted(document, key=natural_key):
        if not any(re.fullmatch(prefix + r"-\d+", section) for prefix in section_prefixes):
            continue
        statements = document[section].get("sql")
        if isinstance(statements, str):
            statements = [statements]
        if not isinstance(statements, list):
            continue
        for index, statement in enumerate(statements, 1):
            if not isinstance(statement, str):
                continue
            normalized = statement.lstrip().lower()
            allowed_prefixes = ("select", "with", "(", "update", "delete") if include_dml else ("select", "with", "(")
            if section.startswith("TP-") and not normalized.startswith(allowed_prefixes):
                continue
            values = ["'software_IT'"] if section == "AP-12" else None
            base_name = section if len(statements) == 1 else f"{section}/s{index}"
            binding_rows = [("", values)]
            if duplicate_bindings and "?" in statement and values is None:
                binding_rows = (("/bind-low", ["1"] * statement.count("?")),
                                ("/bind-high", ["2000000"] * statement.count("?")))
            for suffix, binding_values in binding_rows:
                queries.append({
                    "query": base_name + suffix,
                    "sql": bind_placeholders(statement, binding_values),
                    "source": str(source),
                })
    return queries


def load_job() -> list[dict[str, str]]:
    source = Path("/home/anonymous/dataset/pg_data/join-order-benchmark")
    return [
        {"query": path.name, "sql": path.read_text(encoding="utf-8").strip().rstrip(";"), "source": str(path)}
        for path in sorted(source.glob("*.sql"), key=lambda item: natural_key(item.name))
        if re.fullmatch(r"\d+[a-z]\.sql", path.name)
    ]


def load_tpch() -> list[dict[str, str]]:
    # These are the 22 generated TPC-H query templates with fixed legal seed
    # values.  They are semantically the same SQL used by BenchBase, while
    # being directly EXPLAINable without JDBC bind parameters.
    source = Path("/home/anonymous/program/duckdb/extension/tpch/dbgen/queries")
    return [
        {
            "query": f"Q{int(path.stem[1:])}",
            "sql": path.read_text(encoding="utf-8").strip().rstrip(";"),
            "source": str(path),
        }
        for path in sorted(source.glob("q*.sql"), key=lambda item: natural_key(item.name))
        # BenchBase Q15 creates and drops a parameterized view around its main
        # SELECT.  The standalone CTE rendering here is not the same statement
        # and its MySQL optimization can take minutes, so it is not silently
        # substituted into this cost-only comparison.
        if path.stem != "q15"
    ]


def load_tpcc(*, include_dml: bool = False) -> list[dict[str, str]]:
    # Representative read statements from the five official TPC-C
    # transactions.  The labels retain the transaction whose weight can be
    # changed later if a statement is plan-sensitive.
    statements = {
        "StockLevel/district": "SELECT d_next_o_id FROM district WHERE d_w_id=1 AND d_id=1",
        "StockLevel/count": (
            "SELECT COUNT(DISTINCT(s_i_id)) FROM order_line, stock "
            "WHERE ol_w_id=1 AND ol_d_id=1 AND ol_o_id<3001 AND ol_o_id>=2981 "
            "AND s_w_id=1 AND s_i_id=ol_i_id AND s_quantity<15"
        ),
        "OrderStatus/customer_id": "SELECT * FROM customer WHERE c_w_id=1 AND c_d_id=1 AND c_id=1",
        "OrderStatus/customer_last": (
            "SELECT * FROM customer WHERE c_w_id=1 AND c_d_id=1 AND c_last='BARBARBAR' "
            "ORDER BY c_first"
        ),
        "OrderStatus/last_order": (
            "SELECT o_id,o_carrier_id,o_entry_d FROM oorder WHERE o_w_id=1 AND o_d_id=1 "
            "AND o_c_id=1 ORDER BY o_id DESC LIMIT 1"
        ),
        "OrderStatus/order_lines": "SELECT * FROM order_line WHERE ol_w_id=1 AND ol_d_id=1 AND ol_o_id=1",
        "NewOrder/item": "SELECT i_price,i_name,i_data FROM item WHERE i_id=1",
        "NewOrder/stock": "SELECT * FROM stock WHERE s_i_id=1 AND s_w_id=1",
        "Payment/warehouse": "SELECT * FROM warehouse WHERE w_id=1",
        "Payment/district": "SELECT * FROM district WHERE d_w_id=1 AND d_id=1",
        "Delivery/new_order": (
            "SELECT no_o_id FROM new_order WHERE no_d_id=1 AND no_w_id=1 "
            "ORDER BY no_o_id ASC LIMIT 1"
        ),
    }
    if include_dml:
        statements.update({
            "NewOrder/customer": (
                "SELECT c_discount,c_last,c_credit FROM customer "
                "WHERE c_w_id=1 AND c_d_id=1 AND c_id=1"
            ),
            "NewOrder/warehouse": "SELECT w_tax FROM warehouse WHERE w_id=1",
            "NewOrder/district": (
                "SELECT d_next_o_id,d_tax FROM district "
                "WHERE d_w_id=1 AND d_id=1 FOR UPDATE"
            ),
            "NewOrder/update_district": (
                "UPDATE district SET d_next_o_id=d_next_o_id+1 "
                "WHERE d_w_id=1 AND d_id=1"
            ),
            "NewOrder/update_stock": (
                "UPDATE stock SET s_quantity=10,s_ytd=s_ytd+1,"
                "s_order_cnt=s_order_cnt+1,s_remote_cnt=s_remote_cnt+0 "
                "WHERE s_i_id=1 AND s_w_id=1"
            ),
            "NewOrder/update_stock_high_binding": (
                "UPDATE stock SET s_quantity=10,s_ytd=s_ytd+1,"
                "s_order_cnt=s_order_cnt+1,s_remote_cnt=s_remote_cnt+0 "
                "WHERE s_i_id=50000 AND s_w_id=5"
            ),
            "Payment/update_warehouse": (
                "UPDATE warehouse SET w_ytd=w_ytd+1 WHERE w_id=1"
            ),
            "Payment/update_district": (
                "UPDATE district SET d_ytd=d_ytd+1 WHERE d_w_id=1 AND d_id=1"
            ),
            "Payment/customer_data": (
                "SELECT c_data FROM customer WHERE c_w_id=1 AND c_d_id=1 AND c_id=1"
            ),
            "Payment/update_customer": (
                "UPDATE customer SET c_balance=1,c_ytd_payment=1,c_payment_cnt=1 "
                "WHERE c_w_id=1 AND c_d_id=1 AND c_id=1"
            ),
            "Delivery/delete_new_order": (
                "DELETE FROM new_order WHERE no_o_id=1 AND no_d_id=1 AND no_w_id=1"
            ),
            "Delivery/order_customer": (
                "SELECT o_c_id FROM oorder WHERE o_id=1 AND o_d_id=1 AND o_w_id=1"
            ),
            "Delivery/update_order": (
                "UPDATE oorder SET o_carrier_id=1 WHERE o_id=1 AND o_d_id=1 AND o_w_id=1"
            ),
            "Delivery/update_order_lines": (
                "UPDATE order_line SET ol_delivery_d='2026-01-01' "
                "WHERE ol_o_id=1 AND ol_d_id=1 AND ol_w_id=1"
            ),
            "Delivery/sum_order": (
                "SELECT SUM(ol_amount) FROM order_line "
                "WHERE ol_o_id=1 AND ol_d_id=1 AND ol_w_id=1"
            ),
            "Delivery/update_customer": (
                "UPDATE customer SET c_balance=c_balance+1,c_delivery_cnt=c_delivery_cnt+1 "
                "WHERE c_w_id=1 AND c_d_id=1 AND c_id=1"
            ),
        })
    return [
        {"query": name, "sql": sql, "source": "BenchBase TPC-C transaction SQL"}
        for name, sql in statements.items()
    ]


def load_queries(benchmark: str) -> list[dict[str, str]]:
    if benchmark == "job":
        return load_job()
    if benchmark == "tpch":
        return load_tpch()
    if benchmark == "tpcc":
        return load_tpcc()
    if benchmark == "hybench_ap":
        return load_hybench(("AP",))
    if benchmark == "hybench_tp":
        return load_hybench(("TP",))
    if benchmark == "hybench_xp":
        return load_hybench(("AP", "TP"))
    raise ValueError(benchmark)


VOLATILE_PLAN_FIELDS = {
    "cost_info", "rows_examined_per_scan", "rows_produced_per_join",
    "filtered", "data_read_per_join",
}


def structural_plan(plan: Any) -> Any:
    if isinstance(plan, Mapping):
        return {
            key: structural_plan(value)
            for key, value in plan.items()
            if key not in VOLATILE_PLAN_FIELDS
        }
    if isinstance(plan, list):
        return [structural_plan(value) for value in plan]
    return plan


def fingerprint(plan: Any) -> str:
    encoded = json.dumps(structural_plan(plan), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def access_path(plan: Any) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []

    def visit(node: Any) -> None:
        if isinstance(node, Mapping):
            table = node.get("table")
            if isinstance(table, Mapping):
                rows.append({
                    "table": str(table.get("table_name", "")),
                    "access": str(table.get("access_type", "")),
                    "key": str(table.get("key", "")),
                })
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(plan)
    return rows


def explain_one(client: MySQLCLIClient, sql: str) -> Any:
    return json.loads(client.execute("EXPLAIN FORMAT=JSON " + sql).strip())


def validate_queries(client: MySQLCLIClient, queries: list[dict[str, str]]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    valid: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    for query in queries:
        try:
            explain_one(client, query["sql"])
            valid.append(query)
        except Exception as exc:
            errors.append({"query": query["query"], "error": str(exc)})
    return valid, errors


def explain_batch(client: MySQLCLIClient, queries: list[dict[str, str]]) -> dict[str, Any]:
    marker = "__RA_COST_PLAN_QUERY__"
    statements: list[str] = []
    marker_to_name: dict[str, str] = {}
    for index, query in enumerate(queries):
        token = f"{marker}{index:04d}"
        marker_to_name[token] = query["query"]
        statements.extend((f"SELECT '{token}'", "EXPLAIN FORMAT=JSON " + query["sql"]))
    output = client.execute(";\n".join(statements) + ";")
    chunks: dict[str, list[str]] = {}
    active: str | None = None
    for line in output.splitlines():
        if line in marker_to_name:
            active = line
            chunks[active] = []
        elif active is not None:
            chunks[active].append(line)
    plans: dict[str, Any] = {}
    for token, name in marker_to_name.items():
        if token not in chunks:
            raise RuntimeError(f"missing EXPLAIN result marker for {name}")
        plans[name] = json.loads("\n".join(chunks[token]).strip())
    return plans


def explain_in_chunks(
    client: MySQLCLIClient, queries: list[dict[str, str]], chunk_size: int = 4,
) -> dict[str, Any]:
    """Bound optimizer work and command size in each mysql client process."""
    plans: dict[str, Any] = {}
    for offset in range(0, len(queries), chunk_size):
        plans.update(explain_batch(client, queries[offset:offset + chunk_size]))
    return plans


def scan_benchmark(
    benchmark: str, output_dir: Path, design: str = "oat",
    complete_transactions: bool = False,
) -> dict[str, Any]:
    config = dict(CONNECTIONS[benchmark])
    config.update(host="127.0.0.1", user="root", query_timeout_sec=600)
    client = MySQLCLIClient(config)
    adapter = MySQLCostAdapter(client)
    snapshot = adapter.snapshot()
    before = {
        item.name: {"raw_value": item.raw_value, "effective_value": item.effective_value}
        for item in snapshot if item.name in MYSQL_COST_KEYS
    }
    if complete_transactions and benchmark == "hybench_tp":
        discovered_queries = load_hybench(
            ("TP",), include_dml=True, duplicate_bindings=True,
        )
    elif complete_transactions and benchmark == "tpcc":
        discovered_queries = load_tpcc(include_dml=True)
    else:
        discovered_queries = load_queries(benchmark)
    queries, validation_errors = validate_queries(client, discovered_queries)
    by_query: dict[str, dict[str, Any]] = {
        query["query"]: {"query": query["query"], "sql": query["sql"], "source": query["source"], "profiles": {}}
        for query in queries
    }
    scan_error: str | None = None
    try:
        scan_profiles = profiles(design)
        for profile in scan_profiles:
            adapter.apply(profile["values"])
            plans = explain_in_chunks(client, queries)
            for name, plan in plans.items():
                by_query[name]["profiles"][profile["profile_id"]] = {
                    "fingerprint": fingerprint(plan),
                    "access_path": access_path(plan),
                }
    except Exception as exc:
        scan_error = str(exc)
        raise
    finally:
        adapter.restore(snapshot)

    changed: list[dict[str, Any]] = []
    unchanged: list[str] = []
    for item in by_query.values():
        variants: dict[str, dict[str, Any]] = {}
        for profile_id, result in item["profiles"].items():
            row = variants.setdefault(result["fingerprint"], {
                "fingerprint": result["fingerprint"],
                "access_path": result["access_path"],
                "profile_ids": [],
            })
            row["profile_ids"].append(profile_id)
        item["distinct_structural_plans"] = len(variants)
        item["variants"] = list(variants.values())
        del item["profiles"]
        if len(variants) > 1:
            changed.append(item)
        else:
            unchanged.append(item["query"])

    payload = {
        "version": 1,
        "benchmark": benchmark,
        "connection": {key: value for key, value in config.items() if key != "password"},
        "method": {
            "cost_parameters_only": list(MYSQL_COST_KEYS),
            "design": design,
            "complete_transactions": complete_transactions,
            "profile_count": len(scan_profiles),
            "profiles": scan_profiles,
            "comparison": "EXPLAIN FORMAT=JSON with optimizer estimates/cost_info removed",
        },
        "cost_snapshot_before": before,
        "query_count_discovered": len(queries) + len(validation_errors),
        "query_count_scanned": len(queries),
        "validation_errors": validation_errors,
        "changed_query_count": len(changed),
        "changed_queries": changed,
        "unchanged_queries": unchanged,
        "scan_error": scan_error,
        "restored_after_scan": True,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = "_transactions" if complete_transactions else ""
    if design != "oat":
        suffix += f"_{design}"
    target = output_dir / f"{benchmark}{suffix}.json"
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, target)
    return {
        "benchmark": benchmark,
        "output": str(target),
        "queries": len(queries),
        "validation_errors": len(validation_errors),
        "changed": len(changed),
        "changed_queries": [item["query"] for item in changed],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--benchmarks",
        default="hybench_ap,job,tpch,hybench_tp,tpcc,hybench_xp",
        help="comma-separated benchmark names",
    )
    parser.add_argument(
        "--output-dir", type=Path,
        default=ROOT / "preflight" / "cost_boundary_20260721",
    )
    parser.add_argument("--design", choices=("oat", "corners"), default="oat")
    parser.add_argument("--complete-transactions", action="store_true")
    args = parser.parse_args()
    wanted = [item.strip() for item in args.benchmarks.split(",") if item.strip()]
    unknown = sorted(set(wanted) - set(CONNECTIONS))
    if unknown:
        parser.error(f"unknown benchmarks: {unknown}")
    summaries = []
    for benchmark in wanted:
        result = scan_benchmark(
            benchmark, args.output_dir, args.design, args.complete_transactions,
        )
        summaries.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    print(json.dumps({"completed": summaries}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

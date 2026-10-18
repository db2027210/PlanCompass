#!/usr/bin/env python3
"""Capture and compare PostgreSQL plan trees for top SQL from pg_stat_statements."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def load_config(config_path: str) -> Dict[str, Any]:
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def pg_conn_from_config(cfg: Dict[str, Any]) -> Dict[str, str]:
    postgres_cfg = cfg.get("postgres") or {}
    return {
        "host": str(os.environ.get("PGHOST", postgres_cfg.get("host", "127.0.0.1"))),
        "port": str(os.environ.get("PGPORT", postgres_cfg.get("port", "5432"))),
        "user": str(os.environ.get("PGUSER", postgres_cfg.get("user", "postgres"))),
        "dbname": str(os.environ.get("PG_DBNAME", postgres_cfg.get("dbname", "postgres"))),
    }


def psql_args(sql: str, conn: Dict[str, str], *, dbname: Optional[str] = None) -> List[str]:
    return [
        "psql",
        "-h",
        conn["host"],
        "-p",
        conn["port"],
        "-U",
        conn["user"],
        "-d",
        dbname or conn["dbname"],
        "-v",
        "ON_ERROR_STOP=1",
        "-At",
        "-F",
        "\t",
        "-c",
        sql,
    ]


def psql_query(sql: str, conn: Dict[str, str], *, dbname: Optional[str] = None) -> str:
    proc = subprocess.run(psql_args(sql, conn, dbname=dbname), capture_output=True, text=True)
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip() or (proc.stdout or "").strip()
        raise RuntimeError(f"psql query failed: {detail}")
    return (proc.stdout or "").strip()


def pgss_columns(conn: Dict[str, str]) -> List[str]:
    out = psql_query(
        "SELECT attname "
        "FROM pg_attribute "
        "WHERE attrelid = 'pg_stat_statements'::regclass "
        "AND attnum > 0 AND NOT attisdropped "
        "ORDER BY attnum;",
        conn,
    )
    return [line.strip() for line in out.splitlines() if line.strip()]


def fetch_top_slowest_sql(conn: Dict[str, str], *, limit: int) -> List[Dict[str, Any]]:
    cols = set(pgss_columns(conn))
    total_col = "total_exec_time" if "total_exec_time" in cols else "total_time"
    mean_col = "mean_exec_time" if "mean_exec_time" in cols else "mean_time"
    queryid_col = "queryid" if "queryid" in cols else None
    rows_col = "rows" if "rows" in cols else None

    select_cols: List[str] = []
    if queryid_col:
        select_cols.append(queryid_col)
    select_cols.extend(["calls", total_col, mean_col])
    if rows_col:
        select_cols.append(rows_col)
    select_cols.append(r"replace(replace(query, E'\t', ' '), E'\n', ' ') AS query")

    filter_clauses = [
        "dbid = (SELECT oid FROM pg_database WHERE datname = current_database())",
        "query NOT ILIKE 'SELECT attname FROM pg_attribute%'",
        "query NOT ILIKE 'SELECT pg_is_in_recovery()%'",
        "query NOT ILIKE 'SELECT $1%'",
        "query NOT ILIKE 'EXPLAIN (FORMAT JSON%'",
        "query NOT ILIKE 'DISCARD ALL%'",
        "query NOT ILIKE 'SELECT pg_stat_statements_reset()%'",
        "query NOT ILIKE '%pg_catalog%'",
        "query NOT ILIKE '%pg_stat_statements%'",
    ]

    sql = (
        "SELECT "
        + ", ".join(select_cols)
        + " FROM pg_stat_statements "
        + "WHERE " + " AND ".join(filter_clauses) + " "
        + f"ORDER BY {total_col} DESC LIMIT {int(limit)};"
    )
    out = psql_query(sql, conn)
    rows: List[Dict[str, Any]] = []
    for line in out.splitlines():
        parts = line.split("\t")
        idx = 0
        item: Dict[str, Any] = {}
        if queryid_col:
            item["queryid"] = parts[idx]
            idx += 1
        item["calls"] = float(parts[idx]); idx += 1
        item["total_time_ms"] = float(parts[idx]); idx += 1
        item["mean_time_ms"] = float(parts[idx]); idx += 1
        if rows_col:
            item["rows"] = float(parts[idx]); idx += 1
        item["query"] = parts[idx] if idx < len(parts) else ""
        rows.append(item)
    return rows


def build_explain_sql(query_text: str) -> Tuple[str, str]:
    has_params = bool(re.search(r"\$\d+\b", query_text))
    if has_params:
        return (
            "EXPLAIN (FORMAT JSON, GENERIC_PLAN true, COSTS true, VERBOSE false, BUFFERS false) " + query_text,
            "generic",
        )
    return (
        "EXPLAIN (FORMAT JSON, COSTS true, VERBOSE false, BUFFERS false) " + query_text,
        "plain",
    )


def explain_query_json(query_text: str, conn: Dict[str, str]) -> Tuple[Any, str]:
    sql, mode = build_explain_sql(query_text)
    raw = psql_query(sql, conn)
    return json.loads(raw), mode


def normalize_plan_structure(plan_obj: Any) -> Any:
    if isinstance(plan_obj, list):
        return [normalize_plan_structure(item) for item in plan_obj]
    if not isinstance(plan_obj, dict):
        return plan_obj

    kept_fields = [
        "Node Type",
        "Parent Relationship",
        "Subplan Name",
        "Parallel Aware",
        "Async Capable",
        "Join Type",
        "Strategy",
        "Operation",
        "Relation Name",
        "Alias",
        "Index Name",
        "Index Cond",
        "Recheck Cond",
        "Filter",
        "Join Filter",
        "Hash Cond",
        "Merge Cond",
        "TID Cond",
        "Sort Key",
        "Group Key",
        "Grouping Sets",
        "Output",
        "CTE Name",
        "Workers Planned",
        "Plans",
    ]
    normalized: Dict[str, Any] = {}
    for key in kept_fields:
        if key not in plan_obj:
            continue
        value = plan_obj[key]
        if key == "Plans":
            normalized[key] = [normalize_plan_structure(child) for child in value] if isinstance(value, list) else []
        else:
            normalized[key] = normalize_plan_structure(value)
    return normalized


def plan_root(plan_json: Any) -> Any:
    root = plan_json[0] if isinstance(plan_json, list) and plan_json else plan_json
    if isinstance(root, dict) and "Plan" in root:
        return root["Plan"]
    return root


def key_of(item: Dict[str, Any]) -> str:
    queryid = item.get("queryid")
    if queryid not in (None, ""):
        return f"queryid:{queryid}"
    return f"query:{item.get('query', '')}"


def capture(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    conn = pg_conn_from_config(cfg)
    top_sql = fetch_top_slowest_sql(conn, limit=args.limit)
    items: List[Dict[str, Any]] = []
    for idx, item in enumerate(top_sql, start=1):
        query_text = str(item.get("query") or "").strip().rstrip(";")
        if not query_text:
            continue
        entry: Dict[str, Any] = {
            "rank": idx,
            "queryid": item.get("queryid"),
            "query": query_text,
            "calls": item.get("calls"),
            "total_time_ms": item.get("total_time_ms"),
            "mean_time_ms": item.get("mean_time_ms"),
            "rows": item.get("rows"),
        }
        try:
            plan_json, mode = explain_query_json(query_text, conn)
            root = plan_root(plan_json)
            entry["plan_mode"] = mode
            entry["plan_summary"] = root.get("Node Type") if isinstance(root, dict) else None
            entry["normalized_plan"] = normalize_plan_structure(root)
            entry["raw_plan_json"] = plan_json
        except Exception as exc:
            entry["error"] = str(exc)
        items.append(entry)

    payload = {
        "captured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "config": args.config,
        "dbname": conn["dbname"],
        "limit": args.limit,
        "items": items,
    }
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {len(items)} plan trees to {out_path}")
    return 0


def compare(args: argparse.Namespace) -> int:
    left = json.loads(Path(args.left).read_text(encoding="utf-8"))
    right = json.loads(Path(args.right).read_text(encoding="utf-8"))
    left_items = {key_of(item): item for item in left.get("items", [])}
    right_items = {key_of(item): item for item in right.get("items", [])}

    changes: List[Dict[str, Any]] = []
    for key, left_item in left_items.items():
        right_item = right_items.get(key)
        if not right_item:
            continue
        left_plan = left_item.get("normalized_plan")
        right_plan = right_item.get("normalized_plan")
        if left_plan != right_plan:
            changes.append(
                {
                    "key": key,
                    "queryid": left_item.get("queryid"),
                    "query": left_item.get("query"),
                    "left_plan_summary": left_item.get("plan_summary"),
                    "right_plan_summary": right_item.get("plan_summary"),
                    "left_mean_time_ms": left_item.get("mean_time_ms"),
                    "right_mean_time_ms": right_item.get("mean_time_ms"),
                    "left_plan": left_plan,
                    "right_plan": right_plan,
                }
            )

    report = {
        "left": args.left,
        "right": args.right,
        "compared_count": len(set(left_items) & set(right_items)),
        "changed_count": len(changes),
        "changes": changes,
    }
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote comparison report to {out_path}")
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_cap = sub.add_parser("capture", help="capture top SQL plan trees from pg_stat_statements")
    p_cap.add_argument("--config", default="config/optimizer_config.json")
    p_cap.add_argument("--limit", type=int, default=100)
    p_cap.add_argument("--output", required=True)
    p_cap.set_defaults(func=capture)

    p_cmp = sub.add_parser("compare", help="compare two captured plan-tree JSON files")
    p_cmp.add_argument("--left", required=True)
    p_cmp.add_argument("--right", required=True)
    p_cmp.add_argument("--output")
    p_cmp.set_defaults(func=compare)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())

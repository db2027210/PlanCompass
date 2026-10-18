#!/usr/bin/env python3
"""Append real per-round EXPLAIN samples to a completed full-grid JSONL log."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Dict, List, Optional


COST_PARAMS = (
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
)


def read_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            if not raw.strip():
                continue
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: expected JSON object")
            rows.append(value)
    return rows


def resolve_config_path(config_path: Path, raw: str) -> Path:
    candidate = Path(raw)
    return candidate if candidate.is_absolute() else (config_path.parent / candidate).resolve()


def sql_queries(path: Path) -> List[str]:
    text = "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("--")
    )
    return [query.strip() for query in text.split(";") if query.strip()]


def round_number(row: Dict[str, Any]) -> Optional[int]:
    try:
        return int(row.get("round"))
    except (TypeError, ValueError):
        return None


def anchor_for_round(
    summary: Dict[str, Any],
    candidates: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    label = summary.get("range_anchor_label")
    if isinstance(label, str):
        for candidate in candidates:
            if candidate.get("label") == label:
                return candidate
    current_round = round_number(summary)
    same_round = [row for row in candidates if round_number(row) == current_round]
    accepted = [row for row in same_round if row.get("accepted") is True]
    pool = accepted or same_round
    return max(pool, key=lambda row: float(row.get("db_qps") or float("-inf"))) if pool else None


def endpoint(config: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
    postgres = config.get("postgres") if isinstance(config.get("postgres"), dict) else {}
    worker = candidate.get("worker") if isinstance(candidate.get("worker"), dict) else {}
    return {
        "host": worker.get("host") or postgres.get("host") or "127.0.0.1",
        "port": int(worker.get("port") or postgres.get("port") or 5432),
        "user": worker.get("user") or postgres.get("user") or "postgres",
        "password": worker.get("password") or postgres.get("password") or "",
        "dbname": worker.get("dbname") or postgres.get("dbname") or "postgres",
        "worker": worker.get("name") or worker.get("container") or "",
    }


def capture_explain(
    query: str,
    params: Dict[str, Any],
    pg: Dict[str, Any],
) -> str:
    settings = []
    for name in COST_PARAMS:
        if name not in params:
            raise ValueError(f"anchor params missing {name}")
        settings.append(f"SET LOCAL {name} = {float(params[name]):.17g};")
    script = "\n".join(["BEGIN;", *settings, f"EXPLAIN (FORMAT JSON) {query};", "ROLLBACK;"])
    env = os.environ.copy()
    if pg.get("password"):
        env["PGPASSWORD"] = str(pg["password"])
    command = [
        "psql", "-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1",
        "-h", str(pg["host"]), "-p", str(pg["port"]),
        "-U", str(pg["user"]), "-d", str(pg["dbname"]),
    ]
    proc = subprocess.run(command, input=script, text=True, capture_output=True, env=env, check=False)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "psql EXPLAIN failed").strip())
    output = proc.stdout.strip()
    start = output.find("[")
    end = output.rfind("]")
    if start < 0 or end < start:
        raise RuntimeError(f"EXPLAIN returned no JSON plan: {output[:500]}")
    plan = json.loads(output[start:end + 1])
    return json.dumps({"sql": query, "plan": plan}, ensure_ascii=False, separators=(",", ":"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--max-queries", type=int, default=3)
    parser.add_argument("--allow-incomplete", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = args.config.resolve()
    log_path = args.log.resolve()
    config = read_json(config_path)
    rows = read_jsonl(log_path)
    if not args.allow_incomplete and not any(row.get("event") == "search_complete" for row in rows):
        raise RuntimeError("search_complete is missing")

    tuning = config.get("binary_plan_tuning") if isinstance(config.get("binary_plan_tuning"), dict) else {}
    workload = tuning.get("workload") if isinstance(tuning.get("workload"), dict) else {}
    raw_plan_path = str(workload.get("plan_sql_file") or "").strip()
    if not raw_plan_path:
        raise RuntimeError("binary_plan_tuning.workload.plan_sql_file is missing")
    queries = sql_queries(resolve_config_path(config_path, raw_plan_path))[:max(1, args.max_queries)]
    if not queries:
        raise RuntimeError("plan SQL file contains no queries")

    summaries = sorted(
        (row for row in rows if row.get("event") == "round_summary"),
        key=lambda row: round_number(row) or -1,
    )
    candidates = [
        row for row in rows
        if row.get("event") == "candidate_validation" and row.get("status") == "ok"
    ]
    existing = {
        round_number(row)
        for row in rows
        if row.get("event") == "round_explain_backfill"
    }
    additions: List[Dict[str, Any]] = []
    for summary in summaries:
        current_round = round_number(summary)
        if current_round is None or current_round in existing:
            continue
        anchor = anchor_for_round(summary, candidates)
        if anchor is None:
            raise RuntimeError(f"round {current_round}: anchor candidate is missing")
        params = anchor.get("params") if isinstance(anchor.get("params"), dict) else {}
        pg = endpoint(config, anchor)
        samples = [capture_explain(query, params, pg) for query in queries]
        additions.append(
            {
                "event": "round_explain_backfill",
                "round": current_round,
                "anchor_label": anchor.get("label"),
                "params": params,
                "explain_samples": samples,
                "capture_backend": "postgres_explain_format_json",
                "endpoint": {key: value for key, value in pg.items() if key != "password"},
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )

    if additions:
        with log_path.open("a", encoding="utf-8") as handle:
            for row in additions:
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(json.dumps({"log": str(log_path), "added_rounds": [row["round"] for row in additions]}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

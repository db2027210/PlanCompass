#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple


DEFAULT_STMT_FILE = Path(
    "/disk2/resource_adaptive/binary_plan_tuning_hybenchxp/"
    "hybench_runtime/HyBench-2024-master/conf/stmt_postgres.toml"
)
DEFAULT_OUT_PREFIX = Path("report/bonect/hybenchxp_plan_sensitive_sql_scan_20260709")

DEFAULTS: Dict[str, float] = {
    "seq_page_cost": 1.0,
    "random_page_cost": 4.0,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
}

BOUNDS: Dict[str, Tuple[float, float]] = {
    "seq_page_cost": (0.0001, 100.0),
    "random_page_cost": (0.0001, 20.0),
    "cpu_tuple_cost": (0.000001, 1.0),
    "cpu_index_tuple_cost": (0.000001, 1.0),
    "cpu_operator_cost": (0.000001, 1.0),
    "parallel_setup_cost": (0.0, 10000.0),
    "parallel_tuple_cost": (0.000001, 10.0),
}

EXTRA_PROFILES: Dict[str, Dict[str, float]] = {
    "cpu_round3_sweet_low_qps_c022": {
        "seq_page_cost": 6.2500937500000004,
        "random_page_cost": 1.25009375,
        "cpu_tuple_cost": 0.43750056249999997,
        "cpu_index_tuple_cost": 0.3125006875,
        "cpu_operator_cost": 0.6875003125,
        "parallel_setup_cost": 625.0,
        "parallel_tuple_cost": 9.3750000625,
    },
    "cpu_round3_high_qps_fallback_c118": {
        "seq_page_cost": 18.75008125,
        "random_page_cost": 3.75008125,
        "cpu_tuple_cost": 0.43750056249999997,
        "cpu_index_tuple_cost": 0.3125006875,
        "cpu_operator_cost": 0.6875003125,
        "parallel_setup_cost": 625.0,
        "parallel_tuple_cost": 9.3750000625,
    },
    "low_parallel_cost": {
        **DEFAULTS,
        "parallel_setup_cost": 0.0,
        "parallel_tuple_cost": 0.000001,
    },
    "high_parallel_cost": {
        **DEFAULTS,
        "parallel_setup_cost": 10000.0,
        "parallel_tuple_cost": 10.0,
    },
    "high_seq_low_random": {
        **DEFAULTS,
        "seq_page_cost": 30.0,
        "random_page_cost": 0.01,
    },
    "low_seq_high_random": {
        **DEFAULTS,
        "seq_page_cost": 0.01,
        "random_page_cost": 20.0,
    },
}

FINAL_AUDIT_PROFILES: Dict[str, Dict[str, float]] = {
    "final_audit_cpu": {
        "seq_page_cost": 75.000025,
        "random_page_cost": 15.000025,
        "cpu_tuple_cost": 0.25000075,
        "cpu_index_tuple_cost": 0.25000075,
        "cpu_operator_cost": 0.25000075,
        "parallel_setup_cost": 2500.0,
        "parallel_tuple_cost": 7.50000025,
    },
    "final_audit_memory_io": {
        "seq_page_cost": 75.0,
        "random_page_cost": 15.0,
        "cpu_tuple_cost": 0.01,
        "cpu_index_tuple_cost": 0.005,
        "cpu_operator_cost": 0.0025,
        "parallel_setup_cost": 1000.0,
        "parallel_tuple_cost": 10.0,
    },
    "final_audit_none": {
        "seq_page_cost": 87.5000125,
        "random_page_cost": 2.5000875,
        "cpu_tuple_cost": 0.625000375,
        "cpu_index_tuple_cost": 0.875000125,
        "cpu_operator_cost": 0.375000625,
        "parallel_setup_cost": 8750.0,
        "parallel_tuple_cost": 1.250000875,
    },
}

SAMPLE_CUSTOMERS = [33757, 136446, 2757915, 1551003, 3000000]
SAMPLE_COMPANIES = [3000033, 3005000, 3019988, 3000001]
SAMPLE_CATEGORIES = [
    "software_IT",
    "internet_service",
    "telecommunication",
    "agriculture",
    "coal_mining",
]


def normalize_sql(sql: str) -> str:
    sql = sql.strip()
    sql = re.sub(r"\s+", " ", sql)
    if not sql.endswith(";"):
        sql += ";"
    return sql


def sql_literal(value: Any) -> str:
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return str(value)


def replace_qmarks(sql: str, values: Sequence[Any]) -> str:
    out = sql
    for value in values:
        out = out.replace("?", sql_literal(value), 1)
    if "?" in out:
        raise ValueError(f"not enough placeholder values for SQL: {sql[:120]}")
    return normalize_sql(out)


def extract_ap_sql(stmt_file: Path) -> Dict[str, str]:
    text = stmt_file.read_text(encoding="utf-8")
    pattern = re.compile(r"^\[(AP-[^\]]+)\]\s*\nsql\s*=\s*(\"\"\".*?\"\"\"|\".*?\")", re.M | re.S)
    result: Dict[str, str] = {}
    for match in pattern.finditer(text):
        section = match.group(1)
        raw = match.group(2)
        sql = raw[3:-3] if raw.startswith('"""') else raw[1:-1]
        result[section] = sql.strip()
    return result


def ap_value_sets(section: str) -> List[Sequence[Any]]:
    customers = SAMPLE_CUSTOMERS[:3]
    companies = SAMPLE_COMPANIES[:3]
    categories = SAMPLE_CATEGORIES[:3]
    if section == "AP-1":
        return [[c, c, c, c] for c in customers]
    if section == "AP-2":
        return [[c] for c in customers]
    if section == "AP-2.1":
        return [[1000000], [2500000], [3000001]]
    if section in {"AP-3", "AP-4"}:
        return [[c, c] for c in customers]
    if section == "AP-5":
        return [[c] for c in companies]
    if section == "AP-7":
        return [[c, c, c, c] for c in companies]
    if section == "AP-12":
        return [[c] for c in categories]
    if section == "AP-13":
        return [[c] for c in customers]
    return [[]]


def synthetic_candidates() -> List[Tuple[str, str]]:
    return [
        (
            "SYN-transfer-target-group-top100",
            """
            SELECT t.targetid, sum(t.amount) AS total_amount
            FROM transfer t
            GROUP BY t.targetid
            ORDER BY total_amount DESC
            LIMIT 100
            """,
        ),
        (
            "SYN-transfer-source-group-top100",
            """
            SELECT t.sourceid, count(*) AS transfer_cnt, sum(t.amount) AS total_amount
            FROM transfer t
            GROUP BY t.sourceid
            ORDER BY total_amount DESC
            LIMIT 100
            """,
        ),
        (
            "SYN-transfer-customer-company-group",
            """
            SELECT cu.companyid, count(*) AS transfer_cnt, sum(t.amount) AS total_amount
            FROM customer cu
            JOIN transfer t ON t.sourceid = cu.custid
            GROUP BY cu.companyid
            ORDER BY total_amount DESC
            LIMIT 100
            """,
        ),
        (
            "SYN-transfer-target-company-group",
            """
            SELECT cu.companyid, count(*) AS inbound_cnt, sum(t.amount) AS total_amount
            FROM customer cu
            JOIN transfer t ON t.targetid = cu.custid
            GROUP BY cu.companyid
            ORDER BY inbound_cnt DESC
            LIMIT 100
            """,
        ),
        (
            "SYN-checking-customer-company-group",
            """
            SELECT cu.companyid, count(*) AS checking_cnt, sum(ch.amount) AS total_amount
            FROM customer cu
            JOIN checking ch ON ch.sourceid = cu.custid
            GROUP BY cu.companyid
            ORDER BY total_amount DESC
            LIMIT 100
            """,
        ),
        (
            "SYN-loanapps-trans-status-group",
            """
            SELECT la.status, lt.status AS trans_status, count(*) AS cnt, sum(la.amount) AS amount
            FROM loanapps la
            JOIN loantrans lt ON lt.appid = la.id
            GROUP BY la.status, lt.status
            ORDER BY amount DESC
            """,
        ),
        (
            "SYN-customer-saving-checking-filter",
            """
            SELECT cu.companyid, count(*) AS cnt, avg(sa.balance) AS avg_saving, avg(ca.balance) AS avg_checking
            FROM customer cu
            JOIN savingaccount sa ON sa.userid = cu.custid
            JOIN checkingaccount ca ON ca.userid = cu.custid
            WHERE sa.balance > 1000 AND ca.balance > 1000
            GROUP BY cu.companyid
            ORDER BY cnt DESC
            LIMIT 100
            """,
        ),
        (
            "SYN-transfer-range-source-order",
            """
            SELECT sourceid, targetid, amount, timestamp
            FROM transfer
            WHERE sourceid BETWEEN 100000 AND 400000
            ORDER BY amount DESC
            LIMIT 200
            """,
        ),
        (
            "SYN-transfer-range-target-order",
            """
            SELECT sourceid, targetid, amount, timestamp
            FROM transfer
            WHERE targetid BETWEEN 1500000 AND 1800000
            ORDER BY timestamp DESC
            LIMIT 200
            """,
        ),
        (
            "SYN-company-category-transfer",
            """
            SELECT co.category, count(*) AS cnt, sum(t.amount) AS total_amount
            FROM company co
            JOIN transfer t ON t.targetid = co.companyid
            WHERE co.category IN ('software_IT','internet_service','telecommunication','agriculture')
            GROUP BY co.category
            ORDER BY total_amount DESC
            """,
        ),
        (
            "SYN-exists-loan-no-trans",
            """
            SELECT cu.companyid, count(*) AS cnt
            FROM customer cu
            WHERE EXISTS (
              SELECT 1 FROM loanapps la
              WHERE la.applicantid = cu.custid AND la.amount > 1000
            )
            AND NOT EXISTS (
              SELECT 1 FROM loantrans lt
              WHERE lt.applicantid = cu.custid AND lt.status = 'accept'
            )
            GROUP BY cu.companyid
            ORDER BY cnt DESC
            LIMIT 100
            """,
        ),
        (
            "SYN-union-transfer-checking-hot-customer",
            """
            SELECT sourceid, targetid, sum(amount) AS amount
            FROM (
              SELECT sourceid, targetid, amount FROM transfer WHERE sourceid = 2757915 OR targetid = 2757915
              UNION ALL
              SELECT sourceid, targetid, amount FROM checking WHERE sourceid = 2757915 OR targetid = 2757915
            ) s
            GROUP BY sourceid, targetid
            ORDER BY amount DESC
            LIMIT 100
            """,
        ),
    ]


def build_candidates(stmt_file: Path) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for section, sql in sorted(extract_ap_sql(stmt_file).items()):
        value_sets = ap_value_sets(section)
        for idx, values in enumerate(value_sets, start=1):
            try:
                concrete = replace_qmarks(sql, values)
            except ValueError as exc:
                rows.append({"label": f"{section}-variant{idx}", "sql": "", "error": str(exc)})
                continue
            suffix = "" if len(value_sets) == 1 else f"-v{idx}"
            rows.append({"label": f"{section}{suffix}", "sql": concrete, "source": "hybench_stmt"})
    for label, sql in synthetic_candidates():
        rows.append({"label": label, "sql": normalize_sql(sql), "source": "synthetic"})
    return rows


def build_capture_candidates(capture_file: Path) -> List[Dict[str, str]]:
    payload = json.loads(capture_file.read_text(encoding="utf-8"))
    items = payload.get("items") if isinstance(payload, dict) else []
    rows: List[Dict[str, str]] = []
    seen_sql: Set[str] = set()
    for index, item in enumerate(items or [], start=1):
        if not isinstance(item, dict) or item.get("error") or not item.get("plan_hash"):
            continue
        sql = str(item.get("query") or "").strip()
        if not sql:
            continue
        normalized = normalize_sql(sql)
        dedup_key = re.sub(r"\s+", " ", normalized).strip().lower()
        if dedup_key in seen_sql:
            continue
        seen_sql.add(dedup_key)
        queryid = str(item.get("queryid") or index)
        rows.append({
            "label": f"capture-{queryid}",
            "sql": normalized,
            "source": "vm_plan_capture",
        })
    return rows


def build_profiles(
    include_bounds: bool,
    include_final_audit: bool = False,
) -> Dict[str, Dict[str, float]]:
    profiles: Dict[str, Dict[str, float]] = {"baseline": dict(DEFAULTS)}
    profiles.update(EXTRA_PROFILES)
    if include_final_audit:
        profiles.update(FINAL_AUDIT_PROFILES)
    if include_bounds:
        for param, (low, high) in BOUNDS.items():
            profiles[f"{param}_min"] = {**DEFAULTS, param: low}
            profiles[f"{param}_max"] = {**DEFAULTS, param: high}
    return profiles


def load_profiles_manifest(path: Path) -> Dict[str, Dict[str, float]]:
    """Load named exactly-seven-cost profiles from a JSON object or replay manifest."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_profiles: List[Tuple[str, Any]] = []
    if isinstance(payload, dict):
        raw_profiles = [(str(name), params) for name, params in payload.items()]
    elif isinstance(payload, list):
        for index, item in enumerate(payload, start=1):
            if not isinstance(item, dict):
                raise ValueError(f"profile row {index} must be an object")
            name = str(item.get("candidate_id") or item.get("name") or f"profile_{index}")
            raw_profiles.append((name, item.get("params")))
    else:
        raise ValueError("profiles JSON must be an object or a replay-manifest list")

    profiles: Dict[str, Dict[str, float]] = {"baseline": dict(DEFAULTS)}
    for name, raw_params in raw_profiles:
        if name == "baseline":
            profiles.pop("baseline", None)
        if name in profiles:
            raise ValueError(f"duplicate profile name: {name}")
        if not isinstance(raw_params, dict):
            raise ValueError(f"profile {name} params must be an object")
        if set(raw_params) != set(DEFAULTS):
            missing = sorted(set(DEFAULTS) - set(raw_params))
            extra = sorted(set(raw_params) - set(DEFAULTS))
            raise ValueError(f"profile {name} must contain exactly seven costs; missing={missing}, extra={extra}")
        params: Dict[str, float] = {}
        for param in DEFAULTS:
            value = float(raw_params[param])
            if not math.isfinite(value):
                raise ValueError(f"profile {name} has non-finite {param}: {value}")
            params[param] = value
        profiles[name] = params

    if "baseline" not in profiles:
        raise ValueError("profiles JSON baseline must contain exactly seven costs")
    return profiles


def add_axis_sweep_profiles(
    profiles: Dict[str, Dict[str, float]],
    *,
    base_name: str,
    sweep_specs: Sequence[str],
) -> Dict[str, Dict[str, float]]:
    """Add one-parameter-at-a-time profiles around an existing seven-cost profile."""
    if not sweep_specs:
        return profiles
    if base_name not in profiles:
        raise ValueError(f"sweep base profile not found: {base_name}")
    expanded = dict(profiles)
    base = profiles[base_name]
    for spec in sweep_specs:
        param, separator, raw_values = spec.partition("=")
        param = param.strip()
        if not separator or param not in DEFAULTS:
            raise ValueError(f"invalid axis sweep spec: {spec}")
        values = [part.strip() for part in raw_values.split(",") if part.strip()]
        if not values:
            raise ValueError(f"axis sweep has no values: {spec}")
        for raw_value in values:
            value = float(raw_value)
            if not math.isfinite(value):
                raise ValueError(f"axis sweep has non-finite value: {spec}")
            label_value = format(value, ".8g").replace("-", "m").replace(".", "p")
            name = f"sweep_{param}_{label_value}"
            if name in expanded:
                if expanded[name][param] != value:
                    raise ValueError(f"duplicate axis sweep profile name: {name}")
                continue
            expanded[name] = {**base, param: value}
    return expanded


def active_bonect_processes() -> List[Dict[str, Any]]:
    proc = subprocess.run(
        ["ps", "-eo", "pid=,command="],
        check=False,
        capture_output=True,
        text=True,
    )
    active: List[Dict[str, Any]] = []
    current_pid = os.getpid()
    project_marker = str(Path(__file__).resolve().parents[1])
    for line in proc.stdout.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) != 2:
            continue
        try:
            pid = int(fields[0])
        except ValueError:
            continue
        command = fields[1]
        if pid == current_pid or project_marker not in command:
            continue
        if (
            "parameter_optimizer.py" in command
            or "run_pg_benchmark_bottleneck_matrix.py" in command
        ):
            active.append({"pid": pid, "command": command})
    return active


def strip_plan(obj: Any) -> Any:
    ignored = {
        "Startup Cost",
        "Total Cost",
        "Plan Rows",
        "Plan Width",
        "Actual Startup Time",
        "Actual Total Time",
        "Actual Rows",
        "Actual Loops",
    }
    if isinstance(obj, dict):
        return {k: strip_plan(v) for k, v in obj.items() if k not in ignored}
    if isinstance(obj, list):
        return [strip_plan(v) for v in obj]
    return obj


def walk_plan(plan: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    yield plan
    for child in plan.get("Plans", []) or []:
        if isinstance(child, dict):
            yield from walk_plan(child)


def plan_features(plan: Dict[str, Any]) -> Dict[str, Any]:
    nodes = list(walk_plan(plan))
    node_types = [str(node.get("Node Type", "?")) for node in nodes]
    relations = [
        f"{node.get('Node Type')}:{node.get('Relation Name')}"
        for node in nodes
        if node.get("Relation Name")
    ]
    joins = [
        f"{node.get('Node Type')}:{node.get('Join Type', '')}"
        for node in nodes
        if "Join" in str(node.get("Node Type", ""))
    ]
    aggs = [
        f"{node.get('Node Type')}:{node.get('Strategy', '')}"
        for node in nodes
        if "Aggregate" in str(node.get("Node Type", ""))
    ]
    workers = [
        int(node.get("Workers Planned") or 0)
        for node in nodes
        if node.get("Workers Planned") is not None
    ]
    normalized = strip_plan(plan)
    signature = hashlib.sha256(json.dumps(normalized, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    counts = Counter(node_types)
    return {
        "signature": signature,
        "root": str(plan.get("Node Type", "?")),
        "node_prefix": " -> ".join(node_types[:18]),
        "node_counts": dict(counts),
        "relations": sorted(relations),
        "joins": sorted(joins),
        "aggregates": sorted(aggs),
        "has_parallel": any(nt in {"Gather", "Gather Merge"} for nt in node_types),
        "workers_planned_max": max(workers) if workers else 0,
        "has_sort": bool(counts.get("Sort") or counts.get("Incremental Sort")),
        "has_hash": any(nt in {"Hash", "Hash Join", "HashAggregate"} for nt in node_types),
        "has_nested_loop": bool(counts.get("Nested Loop")),
        "has_index_scan": any("Index" in nt or "Bitmap" in nt for nt in node_types),
        "has_seq_scan": bool(counts.get("Seq Scan")),
        "total_cost": plan.get("Total Cost"),
        "plan_rows": plan.get("Plan Rows"),
    }


def run_explain(
    *,
    host: str,
    port: int,
    user: str,
    password: str,
    dbname: str,
    sql: str,
    profile: Dict[str, float],
    timeout_s: int,
) -> Dict[str, Any]:
    set_lines = ["SET statement_timeout = '30s';", "SET plan_cache_mode = force_custom_plan;"]
    set_lines.extend(f"SET {name} = {value:.17g};" for name, value in profile.items())
    command_sql = "\n".join(set_lines) + "\nEXPLAIN (FORMAT JSON, COSTS true) " + sql
    env = os.environ.copy()
    if password:
        env["PGPASSWORD"] = password
    proc = subprocess.run(
        [
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
            command_sql,
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout_s,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise RuntimeError(detail[:800])
    payload = proc.stdout.strip()
    data = json.loads(payload)
    return plan_features(data[0]["Plan"])


def symmetric_diff_count(a: Sequence[str], b: Sequence[str]) -> int:
    return len(set(a).symmetric_difference(set(b)))


def score_candidate(results: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    baseline = results.get("baseline")
    if not baseline:
        return {"score": 0, "reason": "missing_baseline"}
    distinct_signatures = len({r.get("signature") for r in results.values() if r.get("signature")})
    changed_profiles: List[str] = []
    max_relation_diff = 0
    max_join_diff = 0
    parallel_flip = False
    index_seq_flip = False
    hash_flip = False
    nl_flip = False
    root_flip = False
    for name, row in results.items():
        if name == "baseline" or row.get("error"):
            continue
        if row.get("signature") != baseline.get("signature"):
            changed_profiles.append(name)
        max_relation_diff = max(max_relation_diff, symmetric_diff_count(row.get("relations", []), baseline.get("relations", [])))
        max_join_diff = max(max_join_diff, symmetric_diff_count(row.get("joins", []), baseline.get("joins", [])))
        parallel_flip = parallel_flip or bool(row.get("has_parallel")) != bool(baseline.get("has_parallel"))
        index_seq_flip = index_seq_flip or (
            bool(row.get("has_index_scan")) != bool(baseline.get("has_index_scan"))
            or bool(row.get("has_seq_scan")) != bool(baseline.get("has_seq_scan"))
        )
        hash_flip = hash_flip or bool(row.get("has_hash")) != bool(baseline.get("has_hash"))
        nl_flip = nl_flip or bool(row.get("has_nested_loop")) != bool(baseline.get("has_nested_loop"))
        root_flip = root_flip or row.get("root") != baseline.get("root")
    score = (
        (distinct_signatures - 1) * 3
        + len(changed_profiles)
        + max_relation_diff
        + max_join_diff * 2
        + (4 if parallel_flip else 0)
        + (4 if index_seq_flip else 0)
        + (3 if hash_flip else 0)
        + (3 if nl_flip else 0)
        + (2 if root_flip else 0)
    )
    return {
        "score": score,
        "distinct_signatures": distinct_signatures,
        "changed_profile_count": len(changed_profiles),
        "changed_profiles": changed_profiles,
        "max_relation_diff": max_relation_diff,
        "max_join_diff": max_join_diff,
        "parallel_flip": parallel_flip,
        "index_seq_flip": index_seq_flip,
        "hash_flip": hash_flip,
        "nested_loop_flip": nl_flip,
        "root_flip": root_flip,
    }


def markdown_report(payload: Dict[str, Any], top_n: int) -> str:
    rows = sorted(payload["candidates"], key=lambda row: row["score"]["score"], reverse=True)
    input_path = payload.get("capture_file") or payload.get("stmt_file") or ""
    execution_mode = str(payload.get("execution_mode") or "execute")
    lines = [
        "# HyBenchXP  SQL ",
        "",
        f"- : `{payload['generated_at']}`",
        f"- : `{execution_mode}`",
        f"- : `{payload['db']['host']}:{payload['db']['port']}/{payload['db']['dbname']}`",
        f"- : `{input_path}`",
        f"- SQL : `{len(rows)}`",
        f"- profile : `{len(payload['profiles'])}`",
        "- :  7  PostgreSQL planner cost  EXPLAIN",
        "",
        "## Top Plan-Sensitive SQL",
        "",
        "| rank | label | source | score | signatures | changed profiles | flips | baseline prefix | example changed prefix |",
        "|---:|---|---|---:|---:|---:|---|---|---|",
    ]
    for idx, row in enumerate(rows[:top_n], start=1):
        score = row["score"]
        results = row["results"]
        baseline = results.get("baseline", {})
        example_profile = None
        for name in score.get("changed_profiles", []):
            if name in results:
                example_profile = name
                break
        example = results.get(example_profile or "baseline", {})
        flips = ",".join(
            name
            for name, flag in [
                ("parallel", score.get("parallel_flip")),
                ("index/seq", score.get("index_seq_flip")),
                ("hash", score.get("hash_flip")),
                ("nested_loop", score.get("nested_loop_flip")),
                ("root", score.get("root_flip")),
            ]
            if flag
        ) or "-"
        lines.append(
            "| {rank} | `{label}` | {source} | {score} | {sigs} | {changed} | {flips} | `{base}` | `{prof}: {ex}` |".format(
                rank=idx,
                label=row["label"],
                source=row.get("source", ""),
                score=score.get("score", 0),
                sigs=score.get("distinct_signatures", 0),
                changed=score.get("changed_profile_count", 0),
                flips=flips,
                base=str(baseline.get("node_prefix", ""))[:160],
                prof=example_profile or "baseline",
                ex=str(example.get("node_prefix", ""))[:160],
            )
        )
    lines.extend(
        [
            "",
            "## ",
            "",
            "1.  `parallel``index/seq``hash``nested_loop` flip  SQL",
            "2.  delta ",
            "3.  profile  SQL  workload",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stmt-file", default=str(DEFAULT_STMT_FILE))
    parser.add_argument(
        "--capture-file",
        default="",
        help="Use an existing VM plan-capture JSON instead of a HyBench statement TOML.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=56433)
    parser.add_argument("--user", default="postgres")
    parser.add_argument("--password", default='CHANGE_ME')
    parser.add_argument("--dbname", default="hybench_sf10x")
    parser.add_argument("--out-prefix", default=str(DEFAULT_OUT_PREFIX))
    parser.add_argument("--top-n", type=int, default=25)
    parser.add_argument("--timeout-s", type=int, default=45)
    parser.add_argument(
        "--profiles-json",
        default="",
        help="Use named exactly-seven-cost profiles from JSON; replay candidate manifests are accepted.",
    )
    parser.add_argument(
        "--sweep-base-profile",
        default="baseline",
        help="Profile used as the center for --axis-sweep profiles.",
    )
    parser.add_argument(
        "--axis-sweep",
        action="append",
        default=[],
        metavar="PARAM=V1,V2,...",
        help="Add one-parameter-at-a-time plan profiles around --sweep-base-profile.",
    )
    parser.add_argument(
        "--candidate-label-regex",
        default="",
        help="Only probe SQL candidates whose labels match this regular expression.",
    )
    parser.add_argument("--no-bounds", action="store_true")
    parser.add_argument("--include-final-audit-profiles", action="store_true")
    parser.add_argument(
        "--manifest-only",
        action="store_true",
        help="Write the query/profile manifest without connecting to PostgreSQL.",
    )
    parser.add_argument(
        "--require-no-active-bonect",
        action="store_true",
        help="Refuse EXPLAIN probing while this worktree has an active Bonect runner.",
    )
    args = parser.parse_args()

    stmt_file = Path(args.stmt_file)
    capture_file = Path(args.capture_file).resolve() if args.capture_file else None
    out_prefix = Path(args.out_prefix)
    profiles = (
        load_profiles_manifest(Path(args.profiles_json))
        if args.profiles_json
        else build_profiles(
            include_bounds=not args.no_bounds,
            include_final_audit=args.include_final_audit_profiles,
        )
    )
    profiles = add_axis_sweep_profiles(
        profiles,
        base_name=args.sweep_base_profile,
        sweep_specs=args.axis_sweep,
    )
    candidates = (
        build_capture_candidates(capture_file)
        if capture_file is not None
        else build_candidates(stmt_file)
    )
    if args.candidate_label_regex:
        label_pattern = re.compile(args.candidate_label_regex)
        candidates = [row for row in candidates if label_pattern.search(str(row.get("label") or ""))]
        if not candidates:
            raise ValueError(f"candidate label filter matched no SQL: {args.candidate_label_regex}")

    if args.require_no_active_bonect and not args.manifest_only:
        active = active_bonect_processes()
        if active:
            raise RuntimeError(
                "active Bonect processes detected; refuse VM plan probing: "
                + json.dumps(active, ensure_ascii=False)
            )

    output_rows: List[Dict[str, Any]] = []
    for index, candidate in enumerate(candidates, start=1):
        sql = candidate.get("sql", "")
        results: Dict[str, Dict[str, Any]] = {}
        if args.manifest_only:
            score = {"score": 0, "reason": "manifest_only"}
        elif not sql:
            results["baseline"] = {"error": candidate.get("error", "empty_sql")}
            score = score_candidate(results)
        else:
            for profile_name, profile in profiles.items():
                try:
                    results[profile_name] = run_explain(
                        host=args.host,
                        port=args.port,
                        user=args.user,
                        password=args.password,
                        dbname=args.dbname,
                        sql=sql,
                        profile=profile,
                        timeout_s=args.timeout_s,
                    )
                except Exception as exc:
                    results[profile_name] = {"error": str(exc)}
            score = score_candidate(results)
        row = {
            "index": index,
            "label": candidate["label"],
            "source": candidate.get("source", ""),
            "sql": sql,
            "score": score,
            "results": results,
        }
        output_rows.append(row)
        print(
            f"{index}/{len(candidates)} {row['label']} score={row['score'].get('score')} "
            f"changed={row['score'].get('changed_profile_count')}",
            flush=True,
        )

    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "execution_mode": "manifest_only" if args.manifest_only else "execute",
        "stmt_file": str(stmt_file) if capture_file is None else "",
        "capture_file": str(capture_file) if capture_file is not None else "",
        "db": {
            "host": args.host,
            "port": args.port,
            "user": args.user,
            "dbname": args.dbname,
        },
        "profiles": profiles,
        "candidates": output_rows,
    }
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    json_path = out_prefix.with_suffix(".json")
    md_path = out_prefix.with_suffix(".md")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(markdown_report(payload, args.top_n) + "\n", encoding="utf-8")
    print(f"wrote {json_path}", flush=True)
    print(f"wrote {md_path}", flush=True)


if __name__ == "__main__":
    main()

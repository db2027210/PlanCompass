#!/usr/bin/env python3
"""Fast plan-only boundary scan for Bonect Phase2 Memory reconstruction.

Captures default / v124 teacher / v21 plan teacher EXPLAIN trees for faithful
AP1/AP3/AP7/AP8/AP9/AP10 at a fixed work_mem, without running workloads.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]
SEVEN_COST_KEYS = (
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
)

DEFAULT_PARAMS = {
    "seq_page_cost": 1.0,
    "random_page_cost": 4.0,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
}
V124_TEACHER = {
    "seq_page_cost": 1.0,
    "random_page_cost": 4.5,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0075,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
}
V21_TEACHER = {
    "seq_page_cost": 75.0,
    "random_page_cost": 15.0,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
}

FAMILIES = {
    "AP1": "source/target CASE aggregate",
    "AP3": "income UNION aggregate",
    "AP7": "four-way transfer/checking UNION ALL",
    "AP8": "blocked-account UNION ALL",
    "AP9": "EXISTS/NOT EXISTS revenue",
    "AP10": "big-account inbound/outbound CTE",
}


def load_queries(path: Path) -> Dict[str, Dict[str, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if "items" in data:
        out: Dict[str, Dict[str, str]] = {}
        for item in data["items"]:
            qid = str(item["queryid"])
            out[qid] = {
                "family": str(item.get("family") or FAMILIES.get(qid, qid)),
                "query": str(item["query"]).strip().rstrip(";"),
            }
        return out
    return {
        str(k): {
            "family": str(v.get("family") or FAMILIES.get(str(k), str(k))),
            "query": str(v["query"]).strip().rstrip(";"),
        }
        for k, v in data.items()
    }


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
        "Partial Mode",
        "Operation",
        "Relation Name",
        "Alias",
        "Index Name",
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
            normalized[key] = (
                [normalize_plan_structure(child) for child in value] if isinstance(value, list) else []
            )
        else:
            normalized[key] = normalize_plan_structure(value)
    return normalized


def plan_root(plan_json: Any) -> Any:
    root = plan_json[0] if isinstance(plan_json, list) and plan_json else plan_json
    if isinstance(root, dict) and "Plan" in root:
        return root["Plan"]
    return root


def walk_nodes(node: Any) -> List[Dict[str, Any]]:
    if not isinstance(node, dict):
        return []
    out = [node]
    for child in node.get("Plans") or []:
        out.extend(walk_nodes(child))
    return out


def node_label(node: Dict[str, Any]) -> str:
    ntype = str(node.get("Node Type") or "?")
    strategy = node.get("Strategy")
    join_type = node.get("Join Type")
    rel = node.get("Relation Name")
    if ntype == "Aggregate" and strategy:
        return f"Aggregate[{strategy}]"
    if "Join" in ntype and join_type:
        return f"{ntype}/{join_type}"
    if rel:
        return f"{ntype}({rel})"
    partial = node.get("Partial Mode")
    if ntype == "Aggregate" and partial and partial != "Simple":
        return f"Aggregate[{partial}]"
    return ntype


def node_tree_lines(node: Any, depth: int = 0, max_depth: int = 16) -> List[str]:
    if not isinstance(node, dict) or depth > max_depth:
        return []
    lines = ["  " * depth + node_label(node)]
    for child in node.get("Plans") or []:
        lines.extend(node_tree_lines(child, depth + 1, max_depth=max_depth))
    return lines


def plan_metrics(root: Any) -> Dict[str, int]:
    metrics = {
        "sort_count": 0,
        "incremental_sort_count": 0,
        "hash_join_count": 0,
        "merge_join_count": 0,
        "nested_loop_count": 0,
        "hash_agg_count": 0,
        "sorted_agg_count": 0,
        "plain_agg_count": 0,
        "seq_scan_count": 0,
        "index_scan_count": 0,
        "index_only_scan_count": 0,
        "gather_count": 0,
        "parallel_seq_scan_count": 0,
        "partial_agg_count": 0,
        "finalize_agg_count": 0,
    }
    for node in walk_nodes(root):
        ntype = str(node.get("Node Type") or "")
        strategy = str(node.get("Strategy") or "")
        partial = str(node.get("Partial Mode") or "")
        if ntype == "Sort":
            metrics["sort_count"] += 1
        elif ntype == "Incremental Sort":
            metrics["incremental_sort_count"] += 1
        elif ntype == "Hash Join":
            metrics["hash_join_count"] += 1
        elif ntype == "Merge Join":
            metrics["merge_join_count"] += 1
        elif ntype == "Nested Loop":
            metrics["nested_loop_count"] += 1
        elif ntype == "Aggregate":
            if strategy == "Hashed":
                metrics["hash_agg_count"] += 1
            elif strategy == "Sorted":
                metrics["sorted_agg_count"] += 1
            else:
                metrics["plain_agg_count"] += 1
            if partial == "Partial":
                metrics["partial_agg_count"] += 1
            if partial == "Finalize":
                metrics["finalize_agg_count"] += 1
        elif ntype == "Seq Scan":
            metrics["seq_scan_count"] += 1
            if node.get("Parallel Aware"):
                metrics["parallel_seq_scan_count"] += 1
        elif ntype == "Index Scan":
            metrics["index_scan_count"] += 1
        elif ntype == "Index Only Scan":
            metrics["index_only_scan_count"] += 1
        elif ntype in {"Gather", "Gather Merge"}:
            metrics["gather_count"] += 1
    return metrics


def plan_hash(normalized: Any) -> str:
    payload = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def extract_json_payload(stdout: str) -> str:
    """psql prints 'SET' status lines before EXPLAIN JSON; keep only the JSON body."""
    text = (stdout or "").strip()
    if not text:
        return ""
    # Drop pure SET / status lines that precede the JSON array/object.
    lines = text.splitlines()
    start = 0
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped in {"SET", "BEGIN", "COMMIT", "ROLLBACK"} or stripped == "":
            start = idx + 1
            continue
        if stripped.startswith("[") or stripped.startswith("{"):
            start = idx
            break
        start = idx
        break
    payload = "\n".join(lines[start:]).strip()
    # Fallback: first JSON-looking bracket in the full text.
    if not payload or payload[0] not in "[{":
        for opener in ("[", "{"):
            pos = text.find(opener)
            if pos >= 0:
                return text[pos:].strip()
    return payload


def psql(
    host: str,
    port: int,
    user: str,
    password: str,
    dbname: str,
    sql: str,
    *,
    timeout_s: float = 120.0,
) -> str:
    env = os.environ.copy()
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
            "-v",
            "ON_ERROR_STOP=1",
            "-At",
        ],
        input=sql if sql.endswith("\n") else sql + "\n",
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout_s,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"psql failed host={host}:{port}: {detail}")
    return extract_json_payload(proc.stdout or "")


def set_gucs_sql(params: Dict[str, float], work_mem: str, shared_buffers_note: str = "") -> str:
    _ = shared_buffers_note
    lines = [
        "SET statement_timeout = '60s';",
        "SET jit = off;",
        "SET max_parallel_workers_per_gather = 4;",
        "SET plan_cache_mode = force_custom_plan;",
        f"SET work_mem = '{work_mem}';",
        "SET hash_mem_multiplier = 1.0;",
    ]
    for key in SEVEN_COST_KEYS:
        lines.append(f"SET {key} = {params[key]};")
    return "\n".join(lines)


def capture_one(
    *,
    host: str,
    port: int,
    user: str,
    password: str,
    dbname: str,
    work_mem: str,
    label: str,
    params: Dict[str, float],
    queries: Dict[str, Dict[str, str]],
) -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []
    for qid in ("AP1", "AP3", "AP7", "AP8", "AP9", "AP10"):
        meta = queries[qid]
        query = meta["query"]
        setup = set_gucs_sql(params, work_mem)
        # One session: SET + EXPLAIN
        sql = (
            setup
            + "\n"
            + f"EXPLAIN (FORMAT JSON, COSTS true, VERBOSE false) {query};"
        )
        try:
            raw = psql(host, port, user, password, dbname, sql, timeout_s=90.0)
            # psql -At may return multi-line JSON; join
            plan_json = json.loads(raw)
            root = plan_root(plan_json)
            normalized = normalize_plan_structure(root)
            tree = node_tree_lines(root)
            metrics = plan_metrics(root)
            item = {
                "queryid": qid,
                "family": meta["family"],
                "query": query,
                "plan_mode": "generic",
                "plan_hash": plan_hash(normalized),
                "plan_summary": node_label(root) if isinstance(root, dict) else None,
                "normalized_plan": normalized,
                "raw_plan_json": plan_json,
                "node_tree": tree,
                "metrics": metrics,
            }
        except Exception as exc:
            item = {
                "queryid": qid,
                "family": meta["family"],
                "query": query,
                "plan_mode": "error",
                "error": str(exc),
                "plan_hash": None,
                "node_tree": [],
                "metrics": {},
            }
        items.append(item)
    return {
        "label": label,
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "host": host,
        "port": port,
        "work_mem": work_mem,
        "params": params,
        "items": items,
    }


def boundary_notes(default_m: Dict[str, int], teacher_m: Dict[str, int]) -> str:
    parts = []
    for key in (
        "sort_count",
        "incremental_sort_count",
        "hash_agg_count",
        "sorted_agg_count",
        "hash_join_count",
        "merge_join_count",
        "nested_loop_count",
        "seq_scan_count",
        "index_only_scan_count",
        "parallel_seq_scan_count",
        "partial_agg_count",
        "finalize_agg_count",
        "gather_count",
    ):
        d = int(default_m.get(key) or 0)
        t = int(teacher_m.get(key) or 0)
        if d != t:
            short = key.replace("_count", "")
            parts.append(f"{short} {d}->{t}")
    return "; ".join(parts) if parts else "no metric delta"


def classify_boundary(qid: str, default_item: Dict[str, Any], teacher_item: Dict[str, Any]) -> Dict[str, Any]:
    changed = default_item.get("plan_hash") != teacher_item.get("plan_hash")
    d_m = default_item.get("metrics") or {}
    t_m = teacher_item.get("metrics") or {}
    notes = boundary_notes(d_m, t_m)
    boundary_ok = False
    if qid in {"AP1", "AP3", "AP7", "AP9"}:
        # Sort-removal / SortedAgg->HashAgg family
        boundary_ok = changed and (
            int(t_m.get("sort_count") or 0) < int(d_m.get("sort_count") or 0)
            or int(t_m.get("hash_agg_count") or 0) > int(d_m.get("hash_agg_count") or 0)
            or int(t_m.get("sorted_agg_count") or 0) < int(d_m.get("sorted_agg_count") or 0)
        )
    elif qid == "AP8":
        boundary_ok = changed and (
            int(t_m.get("hash_agg_count") or 0) > int(d_m.get("hash_agg_count") or 0)
            or int(t_m.get("sorted_agg_count") or 0) < int(d_m.get("sorted_agg_count") or 0)
        )
    elif qid == "AP10":
        boundary_ok = changed and (
            int(d_m.get("merge_join_count") or 0) != int(t_m.get("merge_join_count") or 0)
            or int(d_m.get("hash_join_count") or 0) != int(t_m.get("hash_join_count") or 0)
            or int(d_m.get("sort_count") or 0) != int(t_m.get("sort_count") or 0)
            or int(d_m.get("incremental_sort_count") or 0) != int(t_m.get("incremental_sort_count") or 0)
        )
    # v21-style scan/agg switch signal (secondary annotation)
    v21_like = (
        int(d_m.get("parallel_seq_scan_count") or 0) > int(t_m.get("parallel_seq_scan_count") or 0)
        and int(t_m.get("index_only_scan_count") or 0) > int(d_m.get("index_only_scan_count") or 0)
    ) or (
        int(d_m.get("partial_agg_count") or 0) > int(t_m.get("partial_agg_count") or 0)
        and int(t_m.get("sorted_agg_count") or 0) + int(t_m.get("plain_agg_count") or 0)
        != int(d_m.get("sorted_agg_count") or 0) + int(d_m.get("plain_agg_count") or 0)
    )
    return {
        "queryid": qid,
        "family": default_item.get("family"),
        "changed": bool(changed),
        "boundary_ok": bool(boundary_ok),
        "v21_like_scan_agg_switch": bool(v21_like),
        "notes": notes,
        "default_hash": default_item.get("plan_hash"),
        "teacher_hash": teacher_item.get("plan_hash"),
        "default_tree": (default_item.get("node_tree") or [])[:16],
        "teacher_tree": (teacher_item.get("node_tree") or [])[:16],
        "default_metrics": d_m,
        "teacher_metrics": t_m,
        "default_error": default_item.get("error"),
        "teacher_error": teacher_item.get("error"),
    }


def compare_pair(default_cap: Dict[str, Any], teacher_cap: Dict[str, Any], teacher_name: str) -> Dict[str, Any]:
    d_items = {it["queryid"]: it for it in default_cap.get("items") or []}
    t_items = {it["queryid"]: it for it in teacher_cap.get("items") or []}
    families: Dict[str, Any] = {}
    for qid in ("AP1", "AP3", "AP7", "AP8", "AP9", "AP10"):
        families[qid] = classify_boundary(qid, d_items.get(qid, {}), t_items.get(qid, {}))
    sort_ids = [qid for qid in ("AP1", "AP3", "AP7", "AP9") if families[qid]["boundary_ok"]]
    return {
        "teacher_name": teacher_name,
        "work_mem": default_cap.get("work_mem"),
        "host": default_cap.get("host"),
        "created": datetime.now().isoformat(timespec="seconds"),
        "default_params": default_cap.get("params"),
        "teacher_params": teacher_cap.get("params"),
        "families": families,
        "summary": {
            "changed_count": sum(1 for f in families.values() if f.get("changed")),
            "boundary_ok_count": sum(1 for f in families.values() if f.get("boundary_ok")),
            "sort_family_ok_ids": sort_ids,
            "aggregate_family_ok": bool(families["AP8"]["boundary_ok"]),
            "join_family_ok": bool(families["AP10"]["boundary_ok"]),
            "ap9_ok": bool(families["AP9"]["boundary_ok"]),
        },
    }


def run_setting(
    *,
    host: str,
    port: int,
    user: str,
    password: str,
    dbname: str,
    work_mem: str,
    queries: Dict[str, Dict[str, str]],
    out_dir: Path,
) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"[scan] host={host} work_mem={work_mem} start", flush=True)
    default_cap = capture_one(
        host=host,
        port=port,
        user=user,
        password=password,
        dbname=dbname,
        work_mem=work_mem,
        label=f"wm{work_mem}_default",
        params=DEFAULT_PARAMS,
        queries=queries,
    )
    v124_cap = capture_one(
        host=host,
        port=port,
        user=user,
        password=password,
        dbname=dbname,
        work_mem=work_mem,
        label=f"wm{work_mem}_v124",
        params=V124_TEACHER,
        queries=queries,
    )
    v21_cap = capture_one(
        host=host,
        port=port,
        user=user,
        password=password,
        dbname=dbname,
        work_mem=work_mem,
        label=f"wm{work_mem}_v21",
        params=V21_TEACHER,
        queries=queries,
    )
    tag = work_mem.replace("MB", "mb")
    paths = {
        "default": out_dir / f"wm{tag}_default.json",
        "v124": out_dir / f"wm{tag}_v124.json",
        "v21": out_dir / f"wm{tag}_v21.json",
    }
    paths["default"].write_text(json.dumps(default_cap, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths["v124"].write_text(json.dumps(v124_cap, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths["v21"].write_text(json.dumps(v21_cap, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    v124_cmp = compare_pair(default_cap, v124_cap, "v124")
    v21_cmp = compare_pair(default_cap, v21_cap, "v21")
    summary = {
        "work_mem": work_mem,
        "host": host,
        "port": port,
        "captures": {k: str(v) for k, v in paths.items()},
        "v124": v124_cmp,
        "v21": v21_cmp,
    }
    (out_dir / f"wm{tag}_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"[scan] host={host} work_mem={work_mem} done "
        f"v124_ok={v124_cmp['summary']['boundary_ok_count']} "
        f"v21_ok={v21_cmp['summary']['boundary_ok_count']}",
        flush=True,
    )
    return summary


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--queries-json",
        default=str(
            ROOT
            / "history/bonect_parallel_vm_replay/pg_bonect_p2_memory_v124ap_faithful6_plan_capture_20260713/plan_captures/faithful6_default.json"
        ),
    )
    p.add_argument(
        "--output-dir",
        required=True,
        help="Directory for plan capture JSON artifacts",
    )
    p.add_argument(
        "--workers-json",
        default="",
        help="Optional JSON list of {host,port,work_mem,user,password,dbname}",
    )
    p.add_argument("--parallel", action="store_true")
    return p.parse_args()


def default_workers() -> List[Dict[str, Any]]:
    base = {
        "user": "postgres",
        "password": "CHANGE_ME",
        "dbname": "hybench_sf10x",
        "port": 25432,
    }
    return [
        {**base, "host": "192.0.2.12", "work_mem": "4MB", "vm": "bonect-replay-v87-1"},
        {**base, "host": "192.0.2.11", "work_mem": "16MB", "vm": "bonect-replay-v87-2"},
        {**base, "host": "192.0.2.13", "work_mem": "32MB", "vm": "bonect-replay-v87-3"},
    ]


def main() -> int:
    args = parse_args()
    queries = load_queries(Path(args.queries_json))
    missing = [qid for qid in ("AP1", "AP3", "AP7", "AP8", "AP9", "AP10") if qid not in queries]
    if missing:
        raise SystemExit(f"queries missing: {missing}")
    out_dir = Path(args.output_dir)
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.workers_json:
        workers = json.loads(Path(args.workers_json).read_text(encoding="utf-8"))
    else:
        workers = default_workers()

    results: List[Dict[str, Any]] = []
    t0 = time.perf_counter()
    if args.parallel and len(workers) > 1:
        with ThreadPoolExecutor(max_workers=len(workers)) as pool:
            futs = {
                pool.submit(
                    run_setting,
                    host=str(w["host"]),
                    port=int(w.get("port") or 25432),
                    user=str(w.get("user") or "postgres"),
                    password=str(w.get("password") or "CHANGE_ME"),
                    dbname=str(w.get("dbname") or "hybench_sf10x"),
                    work_mem=str(w["work_mem"]),
                    queries=queries,
                    out_dir=out_dir,
                ): w
                for w in workers
            }
            for fut in as_completed(futs):
                results.append(fut.result())
    else:
        for w in workers:
            results.append(
                run_setting(
                    host=str(w["host"]),
                    port=int(w.get("port") or 25432),
                    user=str(w.get("user") or "postgres"),
                    password=str(w.get("password") or "CHANGE_ME"),
                    dbname=str(w.get("dbname") or "hybench_sf10x"),
                    work_mem=str(w["work_mem"]),
                    queries=queries,
                    out_dir=out_dir,
                )
            )

    # stable order by work_mem
    order = {"4MB": 0, "16MB": 1, "32MB": 2}
    results.sort(key=lambda r: order.get(str(r.get("work_mem")), 99))
    overall = {
        "created": datetime.now().isoformat(timespec="seconds"),
        "elapsed_s": round(time.perf_counter() - t0, 2),
        "settings": results,
        "family_presence": {},
    }
    for teacher in ("v124", "v21"):
        presence: Dict[str, Any] = {}
        for res in results:
            wm = res["work_mem"]
            cmp_ = res[teacher]
            presence[wm] = {
                "sort_ok_ids": cmp_["summary"]["sort_family_ok_ids"],
                "aggregate_ok": cmp_["summary"]["aggregate_family_ok"],
                "join_ok": cmp_["summary"]["join_family_ok"],
                "ap9_ok": cmp_["summary"]["ap9_ok"],
                "boundary_ok_count": cmp_["summary"]["boundary_ok_count"],
                "changed_count": cmp_["summary"]["changed_count"],
                "per_ap": {
                    qid: {
                        "changed": cmp_["families"][qid]["changed"],
                        "boundary_ok": cmp_["families"][qid]["boundary_ok"],
                        "notes": cmp_["families"][qid]["notes"],
                    }
                    for qid in ("AP1", "AP3", "AP7", "AP8", "AP9", "AP10")
                },
            }
        overall["family_presence"][teacher] = presence

    (out_dir / "overall_summary.json").write_text(
        json.dumps(overall, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(overall["family_presence"], ensure_ascii=False, indent=2), flush=True)
    print(f"wrote {out_dir / 'overall_summary.json'} elapsed={overall['elapsed_s']}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

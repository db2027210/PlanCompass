#!/usr/bin/env python3
"""HyBenchXP Memory fair v78 Phase A — offline SQL/plan-boundary probe.

Redesigns AP SQL group-state (cardinality/width/lifetime) under xap=1 protocol.
Probes seven cost params around structural thresholds. Prefers:

  default HashAgg + Gather + parallel Seq/Bitmap/heap
  → cand Group/Sorted + Gather Merge/Sort + parallel Seq/Bitmap/heap (workers retained)

Rejects IndexOnly-only, parallel collapse, node-rename-only, and ctc2-like stage collapse.
Does not start tuners or modify binary_plan_resource_tuner.py.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


REPO = Path("/home/anonymous/program/resource_adaptive")
HOST = "127.0.0.1"
PORT = 57433  # mutated only in main() via --port
USER = "postgres"
DB = "hybench_sf10x"

SEVEN = (
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
)

DEFAULT_PARAMS: Dict[str, float] = {
    "seq_page_cost": 1.0,
    "random_page_cost": 4.0,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
}

# v76 residual for residual-closure estimate (official metrics).
V76_CTC1 = {
    "base_mem": 86.15,
    "cand_mem": 83.37,
    "mem_drop_pp": 2.78,
    "base_cpu": 55.09,
    "cand_cpu": 63.75,
    "db_gain_pct": 15.13,
    "peak_mem_drop_kb": 106496.0,  # ~319505-213009 from ANALYZE
    "time_speedup_pct": 33.0,
}

SQL_CORPUS: List[str] = [
    "shell/hybenchxp_memory_ap6_transfer_v78_ref_v12.sql",
    "shell/hybenchxp_memory_ap6_transfer_v78_src_type.sql",
    "shell/hybenchxp_memory_ap6_transfer_v78_src_type_wide.sql",
    "shell/hybenchxp_memory_ap6_transfer_v78_src_wide.sql",
    "shell/hybenchxp_memory_ap6_transfer_v78_src_type_month.sql",
    "shell/hybenchxp_memory_ap6_transfer_v78_src_tgtbucket.sql",
    "shell/hybenchxp_memory_ap6_transfer_v78_src_type_hold.sql",
    "shell/hybenchxp_memory_ap6_transfer_v78_src_type_wide_filter.sql",
]


def log(msg: str) -> None:
    print(msg, flush=True)


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def strip_sql(sql_text: str) -> str:
    body = "\n".join(
        ln for ln in sql_text.splitlines() if not ln.strip().startswith("--")
    ).strip().rstrip(";")
    return body


def plan_signature(plan_node: dict) -> str:
    parts: List[str] = []

    def walk(n: dict) -> None:
        if not isinstance(n, dict):
            return
        node = n.get("Node Type", "?")
        strat = n.get("Strategy")
        rel = n.get("Relation Name")
        workers = n.get("Workers Planned")
        label = node
        if strat:
            label += f":{strat}"
        if rel:
            label += f":{rel}"
        if node in ("Gather", "Gather Merge") and workers is not None:
            label += f":W{workers}"
        parts.append(label)
        for ch in n.get("Plans") or []:
            walk(ch)

    walk(plan_node)
    return " > ".join(parts)


def collect_nodes(plan_node: dict) -> Dict[str, Any]:
    counts: Dict[str, int] = {}
    workers: List[int] = []
    has = {
        "hashagg": False,
        "hashjoin": False,
        "sort": False,
        "material": False,
        "memoize": False,
        "gather": False,
        "gather_merge": False,
        "seq": False,
        "indexonly": False,
        "index": False,
        "bitmap_heap": False,
        "bitmap_index": False,
        "nested_loop": False,
        "merge_join": False,
        "groupagg": False,
    }

    def walk(n: dict) -> None:
        if not isinstance(n, dict):
            return
        node = n.get("Node Type", "?")
        counts[node] = counts.get(node, 0) + 1
        strat = n.get("Strategy")
        if node == "Aggregate":
            if strat == "Hashed":
                has["hashagg"] = True
            elif strat == "Sorted":
                has["groupagg"] = True
        if node == "Hash Join":
            has["hashjoin"] = True
        if node == "Sort":
            has["sort"] = True
        if node == "Materialize":
            has["material"] = True
        if node == "Memoize":
            has["memoize"] = True
        if node == "Gather":
            has["gather"] = True
            if n.get("Workers Planned") is not None:
                workers.append(int(n["Workers Planned"]))
        if node == "Gather Merge":
            has["gather_merge"] = True
            if n.get("Workers Planned") is not None:
                workers.append(int(n["Workers Planned"]))
        if node == "Seq Scan":
            has["seq"] = True
        if node == "Index Only Scan":
            has["indexonly"] = True
        if node == "Index Scan":
            has["index"] = True
        if node == "Bitmap Heap Scan":
            has["bitmap_heap"] = True
        if node == "Bitmap Index Scan":
            has["bitmap_index"] = True
        if node == "Nested Loop":
            has["nested_loop"] = True
        if node == "Merge Join":
            has["merge_join"] = True
        for ch in n.get("Plans") or []:
            walk(ch)

    walk(plan_node)
    return {
        "counts": counts,
        "flags": has,
        "workers_planned": workers,
        "max_workers": max(workers) if workers else 0,
        "parallel": bool(has["gather"] or has["gather_merge"]),
    }


def explain_json(sql_body: str, params: Dict[str, float], analyze: bool = False) -> dict:
    env = {**os.environ, "PGPASSWORD": "postgres"}
    sets = [f"SET {k} = {params[k]};" for k in SEVEN]
    sets.append("SET max_parallel_workers_per_gather = 4;")
    sets.append("SET enable_partitionwise_aggregate = on;")
    mode = "ANALYZE, BUFFERS, FORMAT JSON" if analyze else "FORMAT JSON"
    body = "\n".join(sets) + f"\nEXPLAIN ({mode})\n" + sql_body + ";\n"
    r = subprocess.run(
        [
            "psql",
            "-h",
            HOST,
            "-p",
            str(PORT),
            "-U",
            USER,
            "-d",
            DB,
            "-X",
            "-q",
            "-v",
            "ON_ERROR_STOP=1",
            "-t",
            "-A",
        ],
        input=body,
        text=True,
        capture_output=True,
        env=env,
        timeout=900 if analyze else 120,
    )
    if r.returncode != 0:
        raise RuntimeError(f"psql failed: {r.stderr.strip()[:800]}")
    raw = r.stdout.strip()
    m = re.search(r"(\[.*\])", raw, re.S)
    if not m:
        raise RuntimeError(f"no JSON in explain output: {raw[:300]}")
    data = json.loads(m.group(1))
    return data[0]


def plan_meta(explain_obj: dict) -> Dict[str, Any]:
    plan = explain_obj["Plan"]
    meta = collect_nodes(plan)
    meta["sig"] = plan_signature(plan)
    meta["hash"] = hashlib.sha1(meta["sig"].encode()).hexdigest()[:12]
    meta["total_cost"] = float(plan.get("Total Cost") or 0.0)
    meta["plan_rows"] = float(plan.get("Plan Rows") or 0.0)
    meta["plan_width"] = int(plan.get("Plan Width") or 0)
    if "Actual Total Time" in plan:
        meta["actual_ms"] = float(plan["Actual Total Time"])
        meta["actual_rows"] = float(plan.get("Actual Rows") or 0.0)
        meta["workers_launched"] = int(plan.get("Workers Launched") or 0)
    shared_hit = shared_read = temp_read = temp_written = 0
    peak_mem = 0.0
    batches = 0
    sort_kb = 0.0
    hash_mem = 0.0

    def walk(n: dict) -> None:
        nonlocal shared_hit, shared_read, temp_read, temp_written, peak_mem, batches, sort_kb, hash_mem
        if not isinstance(n, dict):
            return
        shared_hit += int(n.get("Shared Hit Blocks") or 0)
        shared_read += int(n.get("Shared Read Blocks") or 0)
        temp_read += int(n.get("Temp Read Blocks") or 0)
        temp_written += int(n.get("Temp Written Blocks") or 0)
        if n.get("Peak Memory Usage") is not None:
            peak_mem = max(peak_mem, float(n["Peak Memory Usage"]))
        if n.get("Hash Batches") is not None:
            batches = max(batches, int(n["Hash Batches"]))
        if n.get("Sort Space Used") is not None:
            sort_kb = max(sort_kb, float(n["Sort Space Used"]))
            peak_mem = max(peak_mem, float(n["Sort Space Used"]))
        if n.get("Peak Memory Usage") is not None and n.get("Node Type") == "Aggregate":
            hash_mem = max(hash_mem, float(n["Peak Memory Usage"]))
        for ch in n.get("Plans") or []:
            walk(ch)

    walk(plan)
    meta["shared_hit"] = shared_hit
    meta["shared_read"] = shared_read
    meta["temp_read"] = temp_read
    meta["temp_written"] = temp_written
    meta["peak_mem_kb"] = peak_mem
    meta["sort_space_kb"] = sort_kb
    meta["hash_mem_kb"] = hash_mem
    meta["hash_batches"] = batches
    return meta


def classify_transition(def_m: Dict[str, Any], cand_m: Dict[str, Any]) -> Dict[str, Any]:
    df, cf = def_m["flags"], cand_m["flags"]
    tags: List[str] = []
    if def_m["sig"] == cand_m["sig"]:
        return {"tags": ["unchanged"], "reject": True, "score": -1.0, "reason": "identical signature"}

    drop_hashagg = df["hashagg"] and not cf["hashagg"]
    partial_hash_to_sorted = df["hashagg"] and cf["hashagg"] and (not df["groupagg"]) and cf["groupagg"]
    keep_parallel = def_m["parallel"] and cand_m["parallel"]
    drop_parallel = def_m["parallel"] and not cand_m["parallel"]
    to_indexonly = (not df["indexonly"]) and cf["indexonly"]
    lose_seq = df["seq"] and not cf["seq"]
    keep_seq = df["seq"] and cf["seq"]
    keep_heapish = keep_seq or (df["bitmap_heap"] and cf["bitmap_heap"]) or (cf["index"] and not cf["indexonly"])
    gain_groupagg = (not df["groupagg"]) and cf["groupagg"]
    gain_sort = (not df["sort"]) and cf["sort"]
    gain_gm = (not df["gather_merge"]) and cf["gather_merge"]

    if drop_hashagg:
        tags.append("drop_hashagg")
    if partial_hash_to_sorted:
        tags.append("partial_hash_to_sorted")
    if keep_parallel:
        tags.append("keep_parallel")
    if drop_parallel:
        tags.append("drop_parallel")
    if to_indexonly:
        tags.append("to_indexonly")
    if lose_seq:
        tags.append("lose_seq")
    if keep_seq:
        tags.append("keep_seq")
    if gain_groupagg:
        tags.append("gain_groupagg")
    if gain_sort:
        tags.append("gain_sort")
    if gain_gm:
        tags.append("gain_gather_merge")

    # Forbidden: IndexOnly-only / parallel collapse / ctc2-like large stage change
    ctc2_like = to_indexonly and lose_seq and keep_parallel
    par_collapse = drop_parallel
    if ctc2_like:
        tags.append("REJECT_ctc2_indexonly")
    if par_collapse:
        tags.append("REJECT_par_collapse")

    score = 0.0
    reason_bits: List[str] = []
    reject = False

    if ctc2_like:
        reject = True
        score = -100.0
        reason_bits.append("ctc2-like IndexOnly replaces Seq")
    elif par_collapse:
        reject = True
        score = -80.0
        reason_bits.append("parallel collapse")
    elif (drop_hashagg or partial_hash_to_sorted) and keep_parallel and keep_heapish:
        score += 55.0
        reason_bits.append("HashAgg→Sorted/Group path keeping parallel heap/Seq")
        if keep_seq:
            score += 15.0
            reason_bits.append("Seq retained")
        if gain_sort:
            score += 10.0
            reason_bits.append("explicit Sort (CPU)")
        if gain_gm:
            score += 5.0
        if drop_hashagg:
            score += 10.0
            reason_bits.append("full HashAgg drop (larger mem delta hope)")
        elif partial_hash_to_sorted:
            score += 5.0
            reason_bits.append("partial Hash retained (v76 ctc1-like)")
    elif keep_parallel and gain_groupagg:
        score += 20.0
        reason_bits.append("groupagg gain with parallel")
    else:
        score += 1.0
        reason_bits.append("weak / non-target structural delta")

    # Prefer workers retained
    if keep_parallel and def_m["max_workers"] and cand_m["max_workers"]:
        if cand_m["max_workers"] >= def_m["max_workers"]:
            score += 5.0
        else:
            score -= 10.0
            reason_bits.append("worker count reduced")

    # Default must be HashAgg parallel for baseline structure target
    if not (df["hashagg"] and def_m["parallel"] and (df["seq"] or df["bitmap_heap"])):
        score -= 15.0
        reason_bits.append("default not HashAgg+parallel heap")

    return {
        "tags": tags,
        "reject": reject,
        "score": score,
        "reason": "; ".join(reason_bits),
        "drop_hashagg": drop_hashagg,
        "partial_hash_to_sorted": partial_hash_to_sorted,
        "keep_parallel": keep_parallel,
        "drop_parallel": drop_parallel,
        "to_indexonly": to_indexonly,
        "keep_seq": keep_seq,
        "ctc2_like": ctc2_like,
        "v76_ctc1_like": bool(partial_hash_to_sorted and keep_parallel and keep_seq and gain_sort),
    }


def build_param_points() -> List[Tuple[str, Dict[str, float]]]:
    """Focused grid around structural thresholds; denser near ctc Hash→Sorted."""
    points: List[Tuple[str, Dict[str, float]]] = []
    seen = set()

    def add(name: str, p: Dict[str, float]) -> None:
        key = tuple(round(float(p[k]), 12) for k in SEVEN)
        if key in seen:
            return
        if all(abs(float(p[k]) - DEFAULT_PARAMS[k]) < 1e-15 for k in SEVEN):
            return
        seen.add(key)
        points.append((name, {k: float(p[k]) for k in SEVEN}))

    # Dense cpu_tuple_cost (primary Hash→Sorted lever from v76)
    for v in (
        1e-4,
        1e-3,
        0.005,
        0.02,
        0.05,
        0.1,
        0.25,
        0.5,
        0.75,
        1.0,
        1.25,
        1.5,
        1.75,
        2.0,
    ):
        p = dict(DEFAULT_PARAMS)
        p["cpu_tuple_cost"] = float(v)
        add(f"ctc={v}", p)

    for v in (1e-4, 5e-4, 0.001, 0.01, 0.05, 0.1, 0.5, 1.0):
        p = dict(DEFAULT_PARAMS)
        p["cpu_index_tuple_cost"] = float(v)
        add(f"citc={v}", p)

    for v in (1e-4, 5e-4, 0.001, 0.01, 0.05, 0.1, 0.5, 1.0):
        p = dict(DEFAULT_PARAMS)
        p["cpu_operator_cost"] = float(v)
        add(f"coc={v}", p)

    for v in (0.1, 0.5, 1.2, 1.5, 2.0, 3.0, 5.0, 10.0, 25.0, 75.0):
        p = dict(DEFAULT_PARAMS)
        p["seq_page_cost"] = float(v)
        add(f"spc={v}", p)

    for v in (0.1, 0.5, 1.0, 2.0, 6.0, 8.0, 12.0, 15.0, 20.0):
        p = dict(DEFAULT_PARAMS)
        p["random_page_cost"] = float(v)
        add(f"rpc={v}", p)

    for v in (0.0, 10.0, 100.0, 500.0, 2000.0, 5000.0, 10000.0):
        p = dict(DEFAULT_PARAMS)
        p["parallel_setup_cost"] = float(v)
        add(f"psc={v}", p)

    for v in (1e-4, 0.01, 0.05, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0):
        p = dict(DEFAULT_PARAMS)
        p["parallel_tuple_cost"] = float(v)
        add(f"ptc={v}", p)

    # Nearby multi-param that may shift threshold without IndexOnly
    for ctc in (0.5, 0.75, 1.0, 1.25, 1.5):
        for coc in (0.0025, 0.01, 0.05):
            p = dict(DEFAULT_PARAMS)
            p["cpu_tuple_cost"] = ctc
            p["cpu_operator_cost"] = coc
            add(f"ctc{ctc}_coc{coc}", p)
    for ctc in (0.75, 1.0, 1.25):
        for citc in (0.005, 0.05, 0.5):
            p = dict(DEFAULT_PARAMS)
            p["cpu_tuple_cost"] = ctc
            p["cpu_index_tuple_cost"] = citc
            add(f"ctc{ctc}_citc{citc}", p)
    for ctc in (1.0, 1.25):
        for ptc in (0.05, 0.1, 0.2):
            p = dict(DEFAULT_PARAMS)
            p["cpu_tuple_cost"] = ctc
            p["parallel_tuple_cost"] = ptc
            add(f"ctc{ctc}_ptc{ptc}", p)
    # keep parallel cheap while raising ctc
    for ctc in (1.0, 1.5):
        p = dict(DEFAULT_PARAMS)
        p["cpu_tuple_cost"] = ctc
        p["parallel_setup_cost"] = 100.0
        p["parallel_tuple_cost"] = 0.01
        add(f"ctc{ctc}_par_cheap", p)

    return points


def probe_sql(rel: str, points: Sequence[Tuple[str, Dict[str, float]]]) -> Dict[str, Any]:
    path = REPO / rel
    if not path.exists():
        return {"sql": rel, "error": "missing"}
    raw = path.read_text(encoding="utf-8")
    sql_body = strip_sql(raw)
    sql_hash = file_sha256(path)
    if not sql_body:
        return {"sql": rel, "error": "empty", "sql_sha256": sql_hash}

    log(f"[probe] default EXPLAIN {rel} sha={sql_hash[:12]}")
    try:
        def_ex = explain_json(sql_body, DEFAULT_PARAMS, analyze=False)
        def_m = plan_meta(def_ex)
    except Exception as exc:  # noqa: BLE001
        return {"sql": rel, "sql_sha256": sql_hash, "error": f"default explain failed: {exc}"}

    cands: List[Dict[str, Any]] = []
    for name, params in points:
        try:
            ex = explain_json(sql_body, params, analyze=False)
            m = plan_meta(ex)
        except Exception as exc:  # noqa: BLE001
            cands.append({"name": name, "error": str(exc)[:300], "params": params})
            continue
        cls = classify_transition(def_m, m)
        cands.append(
            {
                "name": name,
                "params": params,
                "sig": m["sig"],
                "hash": m["hash"],
                "total_cost": m["total_cost"],
                "plan_rows": m["plan_rows"],
                "plan_width": m["plan_width"],
                "max_workers": m["max_workers"],
                "flags": m["flags"],
                "changed": m["sig"] != def_m["sig"],
                "classification": cls,
            }
        )

    changed = [c for c in cands if c.get("changed")]
    promising = [
        c
        for c in changed
        if not c.get("classification", {}).get("reject")
        and float(c.get("classification", {}).get("score") or 0) >= 40.0
    ]
    promising.sort(key=lambda c: -float(c["classification"]["score"]))

    # first flip thresholds along single-param ctc axis
    ctc_axis = [
        c
        for c in cands
        if c.get("name", "").startswith("ctc=") and c.get("changed") and "error" not in c
    ]
    ctc_axis.sort(key=lambda c: float(c["params"]["cpu_tuple_cost"]))

    return {
        "sql": rel,
        "sql_sha256": sql_hash,
        "default": {
            "sig": def_m["sig"],
            "hash": def_m["hash"],
            "total_cost": def_m["total_cost"],
            "plan_rows": def_m["plan_rows"],
            "plan_width": def_m["plan_width"],
            "max_workers": def_m["max_workers"],
            "flags": def_m["flags"],
        },
        "n_points": len(points),
        "n_changed": len(changed),
        "n_promising": len(promising),
        "promising": promising[:30],
        "ctc_axis_changed": ctc_axis,
        "all_changed": changed,
    }


def analyze_pair(rel: str, params: Dict[str, float], label: str) -> Dict[str, Any]:
    path = REPO / rel
    sql_body = strip_sql(path.read_text(encoding="utf-8"))

    def one(side: str, p: Dict[str, float]) -> Dict[str, Any]:
        ex = explain_json(sql_body, p, analyze=True)
        m = plan_meta(ex)
        m["side"] = side
        return m

    ab = [one("default", DEFAULT_PARAMS), one(label, params)]
    ba = [one(label, params), one("default", DEFAULT_PARAMS)]

    def avg_field(rows: List[Dict[str, Any]], side: str, field: str) -> Optional[float]:
        vals = [float(r[field]) for r in rows if r.get("side") == side and field in r]
        if not vals:
            return None
        return sum(vals) / len(vals)

    sides_all = ab + ba
    out: Dict[str, Any] = {
        "sql": rel,
        "sql_sha256": file_sha256(path),
        "label": label,
        "params": params,
        "ab": ab,
        "ba": ba,
        "avg_default_ms": avg_field(sides_all, "default", "actual_ms"),
        "avg_cand_ms": avg_field(sides_all, label, "actual_ms"),
        "avg_default_peak_mem_kb": avg_field(sides_all, "default", "peak_mem_kb"),
        "avg_cand_peak_mem_kb": avg_field(sides_all, label, "peak_mem_kb"),
        "avg_default_shared_read": avg_field(sides_all, "default", "shared_read"),
        "avg_cand_shared_read": avg_field(sides_all, label, "shared_read"),
        "avg_default_temp_written": avg_field(sides_all, "default", "temp_written"),
        "avg_cand_temp_written": avg_field(sides_all, label, "temp_written"),
        "avg_default_sort_kb": avg_field(sides_all, "default", "sort_space_kb"),
        "avg_cand_sort_kb": avg_field(sides_all, label, "sort_space_kb"),
        "default_sig": ab[0]["sig"],
        "cand_sig": ab[1]["sig"],
        "default_workers": ab[0]["max_workers"],
        "cand_workers": ab[1]["max_workers"],
    }
    if out["avg_default_ms"] and out["avg_cand_ms"] and out["avg_default_ms"] > 0:
        out["time_delta_pct"] = (out["avg_cand_ms"] - out["avg_default_ms"]) / out["avg_default_ms"] * 100.0
    if out["avg_default_peak_mem_kb"] is not None and out["avg_cand_peak_mem_kb"] is not None:
        out["peak_mem_delta_kb"] = out["avg_cand_peak_mem_kb"] - out["avg_default_peak_mem_kb"]
    if out["avg_default_shared_read"] is not None and out["avg_cand_shared_read"] is not None:
        out["shared_read_delta"] = out["avg_cand_shared_read"] - out["avg_default_shared_read"]
    return out


def estimate_residual_closure(analyze_rec: Dict[str, Any]) -> Dict[str, Any]:
    """Map ANALYZE peak_mem/time deltas onto v76 residuals (heuristic, not a plan hash)."""
    if analyze_rec.get("error"):
        return {"credible": False, "reason": "analyze_error"}

    peak_drop = -float(analyze_rec.get("peak_mem_delta_kb") or 0.0)  # positive => cand uses less
    v76_peak_drop = float(V76_CTC1["peak_mem_drop_kb"])
    mem_scale = (peak_drop / v76_peak_drop) if v76_peak_drop > 0 else 0.0

    # Official mem drop scales roughly with peak structure delta (weak linear prior from v76).
    est_mem_drop = float(V76_CTC1["mem_drop_pp"]) * max(0.0, mem_scale)
    est_cand_mem = float(V76_CTC1["base_mem"]) - est_mem_drop

    # CPU: Sort/Group path raised v76 CPU +8.66pp to 63.75. Scale by relative time spent
    # and Sort presence. Prefer 65-80.
    time_delta = float(analyze_rec.get("time_delta_pct") or 0.0)
    # faster cand under same parallel often frees CPU for more QPS; v76 was -33% time, +15% DB
    speedup = max(0.0, -time_delta / 100.0)
    est_db_gain = float(V76_CTC1["db_gain_pct"]) * (speedup / 0.33) if speedup > 0 else float(V76_CTC1["db_gain_pct"]) * 0.5
    # CPU rise: sort-heavy paths
    cand_sort = float(analyze_rec.get("avg_cand_sort_kb") or 0.0)
    def_sort = float(analyze_rec.get("avg_default_sort_kb") or 0.0)
    sort_boost = 1.0 + max(0.0, (cand_sort - def_sort) / 100000.0)
    est_cand_cpu = float(V76_CTC1["cand_cpu"]) * sort_boost
    # if cand much slower, CPU may rise more
    if time_delta > 0:
        est_cand_cpu = min(95.0, est_cand_cpu + time_delta * 0.15)
        est_db_gain *= max(0.2, 1.0 - time_delta / 100.0)

    workers_ok = int(analyze_rec.get("default_workers") or 0) >= 4 and int(
        analyze_rec.get("cand_workers") or 0
    ) >= 4
    keep_seq = "Seq Scan" in str(analyze_rec.get("default_sig")) and "Seq Scan" in str(
        analyze_rec.get("cand_sig")
    )
    no_indexonly = "Index Only" not in str(analyze_rec.get("cand_sig"))

    clauses = {
        "est_mem_drop_5_10": 5.0 <= est_mem_drop <= 10.0,
        "est_cand_mem_75_80_prefer": 75.0 <= est_cand_mem <= 80.0,
        "est_cand_mem_lt_80": est_cand_mem < 80.0,
        "est_cpu_65_80": 65.0 <= est_cand_cpu <= 80.0,
        "est_db_gain_ge_15": est_db_gain >= 15.0,
        "workers_retained": workers_ok,
        "seq_retained": keep_seq,
        "not_indexonly": no_indexonly,
        "peak_mem_drop_gt_v76": peak_drop > v76_peak_drop * 1.3,
    }
    credible = (
        clauses["workers_retained"]
        and clauses["seq_retained"]
        and clauses["not_indexonly"]
        and peak_drop > 0
        and (
            clauses["est_mem_drop_5_10"]
            or clauses["peak_mem_drop_gt_v76"]
            or (est_mem_drop >= 4.0 and clauses["est_db_gain_ge_15"])
        )
    )
    return {
        "credible": credible,
        "est_mem_drop_pp": est_mem_drop,
        "est_cand_mem": est_cand_mem,
        "est_cand_cpu": est_cand_cpu,
        "est_db_gain_pct": est_db_gain,
        "peak_mem_drop_kb": peak_drop,
        "mem_scale_vs_v76": mem_scale,
        "clauses": clauses,
        "note": (
            "Heuristic residual map from ANALYZE peak_mem/time onto v76 official residuals; "
            "not a substitute for w1 baseline/candidate measurement."
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--out-dir",
        default=str(
            Path("/disk2/resource_adaptive/binary_plan_tuning_hybenchxp/runs")
            / "hyxp_memory_fair_v78_plan_boundary_20260714"
        ),
    )
    ap.add_argument("--sql", action="append", default=None)
    ap.add_argument("--analyze-top", type=int, default=6)
    ap.add_argument("--skip-analyze", action="store_true")
    args = ap.parse_args()

    # Prove no Memory tuner on w1 before probing (read-only EXPLAIN is fine if free)
    r = subprocess.run(["ps", "-ef"], capture_output=True, text=True)
    for ln in r.stdout.splitlines():
        if "binary_plan_resource_tuner.py" in ln and (
            "memory" in ln.lower() or "57433" in ln or "rebuild" in ln
        ):
            # external IO is disk1 not rebuild — allow only if not rebuild memory
            if "hybenchxp_io" in ln or "io_fair" in ln:
                continue
            log(f"[fatal] memory/rebuild tuner present: {ln}")
            return 12

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    points = build_param_points()
    sqls = args.sql if args.sql else SQL_CORPUS
    provenance = []
    for rel in sqls:
        p = REPO / rel
        provenance.append(
            {
                "sql": rel,
                "exists": p.exists(),
                "sha256": file_sha256(p) if p.exists() else None,
                "bytes": p.stat().st_size if p.exists() else None,
            }
        )
    (out_dir / "sql_provenance_v78.json").write_text(
        json.dumps(provenance, indent=2) + "\n", encoding="utf-8"
    )
    log(f"[v78] param points={len(points)} sqls={len(sqls)} port={PORT}")

    results: List[Dict[str, Any]] = []
    t0 = time.perf_counter()
    for rel in sqls:
        rec = probe_sql(rel, points)
        results.append(rec)
        if rec.get("error"):
            log(f"  FAIL {rel}: {rec['error']}")
        else:
            log(
                f"  ok {rel}: changed={rec['n_changed']} promising={rec['n_promising']} "
                f"defW={rec['default']['max_workers']} def={rec['default']['sig'][:90]}"
            )

    summary_path = out_dir / "plan_probe_summary_v78.json"
    summary_path.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")

    ranked: List[Dict[str, Any]] = []
    for rec in results:
        if rec.get("error"):
            continue
        for c in rec.get("promising") or []:
            ranked.append(
                {
                    "sql": rec["sql"],
                    "sql_sha256": rec.get("sql_sha256"),
                    "default_sig": rec["default"]["sig"],
                    "default_workers": rec["default"]["max_workers"],
                    "default_width": rec["default"].get("plan_width"),
                    "name": c["name"],
                    "params": c["params"],
                    "cand_sig": c["sig"],
                    "cand_workers": c["max_workers"],
                    "score": c["classification"]["score"],
                    "tags": c["classification"]["tags"],
                    "reason": c["classification"]["reason"],
                    "v76_ctc1_like": c["classification"].get("v76_ctc1_like"),
                    "drop_hashagg": c["classification"].get("drop_hashagg"),
                    "partial_hash_to_sorted": c["classification"].get("partial_hash_to_sorted"),
                    "keep_parallel": c["classification"].get("keep_parallel"),
                    "keep_seq": c["classification"].get("keep_seq"),
                    "to_indexonly": c["classification"].get("to_indexonly"),
                }
            )
    ranked.sort(key=lambda x: -float(x["score"]))
    dedup: List[Dict[str, Any]] = []
    seen_sig = set()
    for rnk in ranked:
        key = (rnk["sql"], rnk["cand_sig"], json.dumps(rnk["params"], sort_keys=True))
        # keep best params per (sql, sig)
        sig_key = (rnk["sql"], rnk["cand_sig"])
        if sig_key in seen_sig:
            continue
        seen_sig.add(sig_key)
        dedup.append(rnk)

    ranked_path = out_dir / "ranked_transitions_v78.json"
    ranked_path.write_text(json.dumps(dedup, indent=2) + "\n", encoding="utf-8")
    log(f"[v78] ranked unique transitions={len(dedup)}")

    # Prefer analyze: highest score among v76_ctc1_like or keep_seq partial/full hash transitions
    analyze_pool = [
        r
        for r in dedup
        if r.get("keep_parallel")
        and r.get("keep_seq")
        and not r.get("to_indexonly")
        and (r.get("v76_ctc1_like") or r.get("partial_hash_to_sorted") or r.get("drop_hashagg"))
    ]
    if not analyze_pool:
        analyze_pool = [r for r in dedup if r.get("keep_parallel") and not r.get("to_indexonly")]

    analyzes: List[Dict[str, Any]] = []
    if not args.skip_analyze and analyze_pool:
        top = analyze_pool[: max(0, args.analyze_top)]
        for i, rnk in enumerate(top):
            log(
                f"[analyze {i+1}/{len(top)}] {rnk['sql']} @ {rnk['name']} "
                f"score={rnk['score']} v76like={rnk.get('v76_ctc1_like')}"
            )
            try:
                rec = analyze_pair(rnk["sql"], rnk["params"], rnk["name"])
                rec["rank_meta"] = {
                    "score": rnk["score"],
                    "tags": rnk["tags"],
                    "reason": rnk["reason"],
                }
                rec["residual_estimate"] = estimate_residual_closure(rec)
                analyzes.append(rec)
                re = rec["residual_estimate"]
                log(
                    f"  ANALYZE peak_drop_kb={re.get('peak_mem_drop_kb')} "
                    f"est_mem_drop={re.get('est_mem_drop_pp')} "
                    f"est_cpu={re.get('est_cand_cpu')} est_db={re.get('est_db_gain_pct')} "
                    f"credible={re.get('credible')}"
                )
            except Exception as exc:  # noqa: BLE001
                analyzes.append(
                    {
                        "sql": rnk["sql"],
                        "name": rnk["name"],
                        "params": rnk["params"],
                        "error": str(exc)[:500],
                        "residual_estimate": {"credible": False, "reason": "analyze_error"},
                    }
                )
                log(f"  ANALYZE FAIL: {exc}")

    analyze_path = out_dir / "explain_analyze_top_v78.json"
    analyze_path.write_text(json.dumps(analyzes, indent=2) + "\n", encoding="utf-8")

    # Phase B eligibility
    eligibility: List[Dict[str, Any]] = []
    for a in analyzes:
        re = a.get("residual_estimate") or {}
        eligibility.append(
            {
                "sql": a.get("sql"),
                "sql_sha256": a.get("sql_sha256"),
                "label": a.get("label") or a.get("name"),
                "params": a.get("params"),
                "default_sig": a.get("default_sig"),
                "cand_sig": a.get("cand_sig"),
                "peak_mem_delta_kb": a.get("peak_mem_delta_kb"),
                "time_delta_pct": a.get("time_delta_pct"),
                "shared_read_delta": a.get("shared_read_delta"),
                "residual_estimate": re,
                "phase_b_eligible": bool(re.get("credible")),
            }
        )
    eligibility.sort(
        key=lambda x: (
            0 if x.get("phase_b_eligible") else 1,
            -float((x.get("residual_estimate") or {}).get("est_mem_drop_pp") or 0),
            -float((x.get("residual_estimate") or {}).get("est_db_gain_pct") or 0),
        )
    )
    elig_path = out_dir / "phase_b_eligibility_v78.json"
    elig_path.write_text(json.dumps(eligibility, indent=2) + "\n", encoding="utf-8")

    md = [
        "# Memory fair v78 Phase A — SQL/plan-boundary ranking",
        "",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Port: {PORT}",
        f"Param points: {len(points)}",
        f"SQL variants: {len(sqls)}",
        f"Elapsed_s: {time.perf_counter() - t0:.1f}",
        "",
        "## Goal",
        "",
        "Amplify v76 ctc1-like parallel-preserving HashAgg→Sorted/Group transition via",
        "group-state cardinality/width/lifetime (not xap=2, not ctid, not ctc2).",
        "",
        "## SQL provenance",
        "",
    ]
    for p in provenance:
        md.append(f"- `{p['sql']}` sha256=`{p.get('sha256')}`")
    md.extend(["", "## Ranked transitions (unique sql×sig, score≥40, not rejected)", ""])
    if not dedup:
        md.append("_No promising transitions._")
    for i, rnk in enumerate(dedup[:40], 1):
        md.extend(
            [
                f"### {i}. score={rnk['score']:.1f} `{rnk['name']}`",
                f"- sql: `{rnk['sql']}`",
                f"- sql_sha256: `{rnk.get('sql_sha256')}`",
                f"- default: `{rnk['default_sig']}` (W{rnk['default_workers']})",
                f"- cand: `{rnk['cand_sig']}` (W{rnk['cand_workers']})",
                f"- tags: {', '.join(rnk['tags'])}",
                f"- reason: {rnk['reason']}",
                f"- v76_ctc1_like={rnk.get('v76_ctc1_like')} drop_hash={rnk.get('drop_hashagg')} "
                f"partial={rnk.get('partial_hash_to_sorted')} keep_seq={rnk.get('keep_seq')}",
                f"- params: `{json.dumps(rnk['params'])}`",
                "",
            ]
        )

    md.extend(["## ANALYZE residual estimates vs v76", ""])
    if not analyzes:
        md.append("_No ANALYZE runs._")
    for a in analyzes:
        re = a.get("residual_estimate") or {}
        md.extend(
            [
                f"### `{a.get('sql')}` @ `{a.get('label') or a.get('name')}`",
                f"- default: `{a.get('default_sig')}`",
                f"- cand: `{a.get('cand_sig')}`",
                f"- time_delta_pct={a.get('time_delta_pct')} peak_mem_delta_kb={a.get('peak_mem_delta_kb')} "
                f"shared_read_delta={a.get('shared_read_delta')}",
                f"- avg_ms default/cand: {a.get('avg_default_ms')} / {a.get('avg_cand_ms')}",
                f"- peak_mem_kb default/cand: {a.get('avg_default_peak_mem_kb')} / {a.get('avg_cand_peak_mem_kb')}",
                f"- sort_kb default/cand: {a.get('avg_default_sort_kb')} / {a.get('avg_cand_sort_kb')}",
                f"- residual: est_mem_drop={re.get('est_mem_drop_pp')} est_cand_mem={re.get('est_cand_mem')} "
                f"est_cpu={re.get('est_cand_cpu')} est_db={re.get('est_db_gain_pct')} "
                f"**credible={re.get('credible')}**",
                f"- clauses: `{json.dumps(re.get('clauses'))}`",
                f"- error: {a.get('error')}",
                "",
            ]
        )

    md.extend(["## Phase B eligibility order", ""])
    for i, e in enumerate(eligibility, 1):
        md.append(
            f"{i}. eligible={e.get('phase_b_eligible')} `{e.get('sql')}` @ `{e.get('label')}` "
            f"est_mem_drop={ (e.get('residual_estimate') or {}).get('est_mem_drop_pp') }"
        )

    best = next((e for e in eligibility if e.get("phase_b_eligible")), None)
    md.extend(["", "## Decision", ""])
    if best:
        md.append(
            f"**Proceed Phase B** with `{best['sql']}` params=`{json.dumps(best.get('params'))}`."
        )
    else:
        md.append(
            "**No structurally credible variant for Phase B real candidate.** "
            "Stop without spending candidate budget; next construction must be distinct "
            "(different group-state lifetime or scan shape still within transfer realism)."
        )

    md_path = out_dir / "ranked_transitions_v78.md"
    md_path.write_text("\n".join(md) + "\n", encoding="utf-8")
    log(f"[v78] done summary={summary_path} md={md_path} analyze={analyze_path} elig={elig_path}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("[interrupt]")
        sys.exit(130)
    except Exception as exc:
        log(f"[fatal] {exc}")
        sys.exit(99)

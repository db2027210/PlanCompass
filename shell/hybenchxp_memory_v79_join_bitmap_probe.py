#!/usr/bin/env python3
"""HyBenchXP Memory fair v79 Phase A — Join/Bitmap plan-boundary probe.

Offline EXPLAIN on rebuild w1 only. Does not start tuners or edit core tuner.
Searches HashJoin→Merge/NestedLoop and Bitmap+Hash hybrids under seven costs.
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
PORT = 57433
USER = "postgres"
DB = "hybench_sf10x"
RUN_DIR = Path(
    "/disk2/resource_adaptive/binary_plan_tuning_hybenchxp/runs/"
    "hyxp_memory_fair_v79_join_bitmap_plan_boundary_20260714"
)

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

# Residual targets for 5-10pp of 6.4 GiB and DB/CPU/IO clauses.
TARGET_PEAK_DROP_KB_LO = 328 * 1024  # ~328 MiB
TARGET_PEAK_DROP_KB_HI = 655 * 1024  # ~655 MiB
V76_PEAK_CAP_KB = 106496.0

SQL_CORPUS: List[str] = [
    "shell/hybenchxp_memory_ap6_join_v79_transfer_sa_mid.sql",
    "shell/hybenchxp_memory_ap6_join_v79_transfer_sa_wide.sql",
    "shell/hybenchxp_memory_ap6_join_v79_transfer_customer.sql",
    "shell/hybenchxp_memory_ap6_join_v79_transfer_sa_agg.sql",
    "shell/hybenchxp_memory_ap6_join_v79_transfer_sa_build_dim.sql",
    "shell/hybenchxp_memory_ap6_join_v79_loanapps_customer.sql",
    "shell/hybenchxp_memory_ap6_bitmap_v79_transfer_type_range.sql",
    "shell/hybenchxp_memory_ap6_bitmap_v79_transfer_sa_join.sql",
    # control: known HashAgg family (must not re-select as primary if only this works)
    "shell/hybenchxp_memory_ap6_transfer_groupagg_switch_v12_fullscan.sql",
]

SINGLE_GRIDS: Dict[str, List[float]] = {
    "seq_page_cost": [0.1, 0.5, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0, 25.0, 75.0],
    "random_page_cost": [0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 12.0, 20.0],
    "cpu_tuple_cost": [1e-4, 1e-3, 0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0],
    "cpu_index_tuple_cost": [1e-4, 5e-4, 0.001, 0.005, 0.01, 0.05, 0.1, 0.5],
    "cpu_operator_cost": [1e-4, 5e-4, 0.001, 0.0025, 0.01, 0.05, 0.1, 0.5],
    "parallel_setup_cost": [0.0, 10.0, 100.0, 500.0, 1000.0, 2000.0, 5000.0, 10000.0],
    "parallel_tuple_cost": [1e-4, 0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0],
}

NAMED_POINTS: Dict[str, Dict[str, float]] = {
    "ctc1": {**DEFAULT_PARAMS, "cpu_tuple_cost": 1.0},
    "ctc2": {**DEFAULT_PARAMS, "cpu_tuple_cost": 2.0},
    "rpc0p05": {**DEFAULT_PARAMS, "random_page_cost": 0.05},
    "rpc0p1": {**DEFAULT_PARAMS, "random_page_cost": 0.1},
    "rpc0p5": {**DEFAULT_PARAMS, "random_page_cost": 0.5},
    "rpc1": {**DEFAULT_PARAMS, "random_page_cost": 1.0},
    "rpc20": {**DEFAULT_PARAMS, "random_page_cost": 20.0},
    "spc0p1": {**DEFAULT_PARAMS, "seq_page_cost": 0.1},
    "spc5": {**DEFAULT_PARAMS, "seq_page_cost": 5.0},
    "spc75": {**DEFAULT_PARAMS, "seq_page_cost": 75.0},
    "nl_rpc_low": {**DEFAULT_PARAMS, "random_page_cost": 0.1, "cpu_tuple_cost": 0.01},
    "nl_rpc_low_ctc_high": {
        **DEFAULT_PARAMS,
        "random_page_cost": 0.1,
        "cpu_tuple_cost": 0.5,
    },
    "nl_rpc_low_ctc1": {
        **DEFAULT_PARAMS,
        "random_page_cost": 0.1,
        "cpu_tuple_cost": 1.0,
    },
    "hashjoin_discourage": {
        **DEFAULT_PARAMS,
        "cpu_tuple_cost": 0.5,
        "cpu_operator_cost": 0.25,
        "random_page_cost": 1.0,
    },
    "hashjoin_discourage_strong": {
        **DEFAULT_PARAMS,
        "cpu_tuple_cost": 1.0,
        "cpu_operator_cost": 0.5,
        "random_page_cost": 0.5,
        "cpu_index_tuple_cost": 0.001,
    },
    "merge_encourage": {
        **DEFAULT_PARAMS,
        "cpu_tuple_cost": 0.5,
        "cpu_operator_cost": 0.1,
        "random_page_cost": 2.0,
        "seq_page_cost": 2.0,
    },
    "merge_encourage_v2": {
        **DEFAULT_PARAMS,
        "cpu_tuple_cost": 1.0,
        "cpu_operator_cost": 0.25,
        "random_page_cost": 1.0,
        "seq_page_cost": 1.5,
    },
    "bitmap_encourage_rpc_high": {
        **DEFAULT_PARAMS,
        "random_page_cost": 20.0,
        "seq_page_cost": 1.0,
        "cpu_index_tuple_cost": 0.0001,
    },
    "bitmap_encourage_spc_high": {
        **DEFAULT_PARAMS,
        "seq_page_cost": 25.0,
        "random_page_cost": 4.0,
        "cpu_index_tuple_cost": 0.0001,
    },
    "par_collapse_v75g": {
        **DEFAULT_PARAMS,
        "parallel_setup_cost": 10000.0,
        "parallel_tuple_cost": 1.0,
    },
    "keep_par_nl": {
        **DEFAULT_PARAMS,
        "random_page_cost": 0.05,
        "cpu_tuple_cost": 0.25,
        "parallel_setup_cost": 100.0,
        "parallel_tuple_cost": 0.01,
    },
    "keep_par_merge": {
        **DEFAULT_PARAMS,
        "cpu_tuple_cost": 0.75,
        "cpu_operator_cost": 0.2,
        "random_page_cost": 1.5,
        "parallel_setup_cost": 100.0,
        "parallel_tuple_cost": 0.01,
    },
    "index_cheap_hash_expensive": {
        **DEFAULT_PARAMS,
        "random_page_cost": 0.25,
        "cpu_index_tuple_cost": 0.0001,
        "cpu_tuple_cost": 1.0,
        "cpu_operator_cost": 0.5,
    },
    "seq_cheap_index_expensive": {
        **DEFAULT_PARAMS,
        "seq_page_cost": 0.1,
        "random_page_cost": 20.0,
        "cpu_index_tuple_cost": 0.5,
    },
}


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
    if ";" in body:
        body = body.split(";", 1)[0].strip()
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
        # join type extras
        if node == "Hash Join":
            label += f"[hash]"
        if node in ("Nested Loop", "Merge Join"):
            label += f"[{node.split()[0].lower()}]"
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
        "hash": False,
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
        if node == "Hash":
            has["hash"] = True
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
    sets.append("SET work_mem = '512MB';")
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
        timeout=900 if analyze else 180,
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
    shared_hit = shared_read = temp_read = temp_written = 0
    peak_mem = 0.0
    sum_peak = 0.0
    batches = 0
    heap_fetches = 0

    def walk(n: dict) -> None:
        nonlocal shared_hit, shared_read, temp_read, temp_written, peak_mem, sum_peak, batches, heap_fetches
        if not isinstance(n, dict):
            return
        shared_hit += int(n.get("Shared Hit Blocks") or 0)
        shared_read += int(n.get("Shared Read Blocks") or 0)
        temp_read += int(n.get("Temp Read Blocks") or 0)
        temp_written += int(n.get("Temp Written Blocks") or 0)
        if n.get("Peak Memory Usage") is not None:
            pm = float(n["Peak Memory Usage"])
            peak_mem = max(peak_mem, pm)
            sum_peak += pm
        if n.get("Hash Batches") is not None:
            batches = max(batches, int(n["Hash Batches"]))
        if n.get("Sort Space Used") is not None:
            sm = float(n["Sort Space Used"])
            peak_mem = max(peak_mem, sm)
            sum_peak += sm
        if n.get("Heap Fetches") is not None:
            heap_fetches += int(n["Heap Fetches"])
        for ch in n.get("Plans") or []:
            walk(ch)

    walk(plan)
    meta["shared_hit"] = shared_hit
    meta["shared_read"] = shared_read
    meta["temp_read"] = temp_read
    meta["temp_written"] = temp_written
    meta["peak_mem_kb"] = peak_mem
    meta["sum_peak_mem_kb"] = sum_peak
    meta["hash_batches"] = batches
    meta["heap_fetches"] = heap_fetches
    return meta


def classify_transition(def_m: Dict[str, Any], cand_m: Dict[str, Any]) -> Dict[str, Any]:
    df, cf = def_m["flags"], cand_m["flags"]
    tags: List[str] = []
    if def_m["sig"] == cand_m["sig"]:
        return {
            "tags": ["unchanged"],
            "reject": True,
            "score": -1.0,
            "reason": "identical signature",
            "join_class": False,
            "bitmap_class": False,
        }

    drop_hashjoin = df["hashjoin"] and not cf["hashjoin"]
    drop_hashagg = df["hashagg"] and not cf["hashagg"]
    drop_hash = df["hash"] and not cf["hash"]
    drop_material = df["material"] and not cf["material"]
    keep_parallel = def_m["parallel"] and cand_m["parallel"]
    drop_parallel = def_m["parallel"] and not cand_m["parallel"]
    to_indexonly = (not df["indexonly"]) and cf["indexonly"]
    gain_nl = (not df["nested_loop"]) and cf["nested_loop"]
    gain_merge = (not df["merge_join"]) and cf["merge_join"]
    gain_bitmap = (not df["bitmap_heap"]) and cf["bitmap_heap"]
    gain_seq = (not df["seq"]) and cf["seq"]
    lose_seq = df["seq"] and not cf["seq"]
    gain_index = (not df["index"]) and cf["index"]
    gain_sort = (not df["sort"]) and cf["sort"]
    retain_heap_work = cf["seq"] or cf["bitmap_heap"] or cf["index"]

    if drop_hashjoin:
        tags.append("drop_hashjoin")
    if drop_hashagg:
        tags.append("drop_hashagg")
    if drop_hash:
        tags.append("drop_hash")
    if keep_parallel:
        tags.append("keep_parallel")
    if drop_parallel:
        tags.append("drop_parallel")
    if gain_nl:
        tags.append("gain_nested_loop")
    if gain_merge:
        tags.append("gain_merge_join")
    if gain_bitmap:
        tags.append("gain_bitmap")
    if to_indexonly:
        tags.append("to_indexonly")
    if gain_sort:
        tags.append("gain_sort")

    join_class = bool(
        df["hashjoin"]
        and (gain_nl or gain_merge or (drop_hashjoin and (cf["nested_loop"] or cf["merge_join"])))
    )
    bitmap_class = bool(gain_bitmap or (df["bitmap_heap"] and (drop_hashjoin or drop_hashagg)))
    pure_par_collapse_indexonly = drop_parallel and to_indexonly and not keep_parallel

    score = 0.0
    reason_bits: List[str] = []
    reject = False

    if pure_par_collapse_indexonly:
        reject = True
        score = -100.0
        reason_bits.append("REJECT: IndexOnly + parallel collapse")
    elif join_class and keep_parallel and retain_heap_work:
        score += 80.0
        reason_bits.append("HashJoin→NL/Merge keep parallel + heap work")
    elif join_class and not drop_parallel and retain_heap_work:
        score += 60.0
        reason_bits.append("HashJoin→NL/Merge with heap work")
    elif join_class and drop_parallel:
        score += 15.0
        reason_bits.append("HashJoin transition but parallel collapse risk")
    elif drop_hashjoin and retain_heap_work:
        score += 40.0
        reason_bits.append("drop HashJoin retain heap")
    elif bitmap_class and (drop_hashjoin or drop_hashagg) and keep_parallel:
        score += 55.0
        reason_bits.append("Bitmap hybrid with mem-structure drop + parallel")
    elif drop_hashagg and keep_parallel:
        score += 20.0
        reason_bits.append("HashAgg family (exhausted class; lower priority)")
    else:
        score += 2.0
        reason_bits.append("plan changed")

    if gain_nl:
        score += 20.0
        reason_bits.append("Nested Loop (index/heap reads)")
    if gain_merge:
        score += 18.0
        reason_bits.append("Merge Join")
    if gain_bitmap:
        score += 15.0
        reason_bits.append("Bitmap Heap")
    if gain_index:
        score += 10.0
    if gain_seq:
        score += 8.0
    if drop_parallel:
        score -= 30.0
        reason_bits.append("parallel collapse penalty")
    if to_indexonly and lose_seq and not gain_nl:
        score -= 20.0
        reason_bits.append("IndexOnly replaces Seq (IO-down risk)")
    if not retain_heap_work:
        score -= 25.0
        reason_bits.append("no seq/bitmap/index heap work")

    # forbid pure HashAgg-only transitions as primary v79 win
    if drop_hashagg and not drop_hashjoin and not join_class and not bitmap_class:
        tags.append("hashagg_family_exhausted")
        score -= 10.0

    return {
        "tags": tags,
        "reject": reject,
        "score": score,
        "reason": "; ".join(reason_bits),
        "join_class": join_class,
        "bitmap_class": bitmap_class,
        "drop_hashjoin": drop_hashjoin,
        "drop_hashagg": drop_hashagg,
        "keep_parallel": keep_parallel,
        "drop_parallel": drop_parallel,
        "to_indexonly": to_indexonly,
        "gain_nl": gain_nl,
        "gain_merge": gain_merge,
        "gain_bitmap": gain_bitmap,
        "retain_heap_work": retain_heap_work,
    }


def build_param_points() -> List[Tuple[str, Dict[str, float]]]:
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

    for name, p in NAMED_POINTS.items():
        add(name, p)
    for param, grid in SINGLE_GRIDS.items():
        for v in grid:
            p = dict(DEFAULT_PARAMS)
            p[param] = float(v)
            add(f"{param}={v}", p)
    # join-focused 2-param interactions
    for rpc in (0.05, 0.1, 0.25, 0.5, 1.0):
        for ctc in (0.01, 0.1, 0.5, 1.0, 2.0):
            p = dict(DEFAULT_PARAMS)
            p["random_page_cost"] = rpc
            p["cpu_tuple_cost"] = ctc
            add(f"rpc{rpc}_ctc{ctc}", p)
    for rpc in (0.1, 0.5, 1.0):
        for citc in (0.0001, 0.001, 0.005):
            p = dict(DEFAULT_PARAMS)
            p["random_page_cost"] = rpc
            p["cpu_index_tuple_cost"] = citc
            add(f"rpc{rpc}_citc{citc}", p)
    for ctc in (0.5, 1.0, 2.0):
        for coc in (0.01, 0.1, 0.25, 0.5):
            p = dict(DEFAULT_PARAMS)
            p["cpu_tuple_cost"] = ctc
            p["cpu_operator_cost"] = coc
            add(f"ctc{ctc}_coc{coc}", p)
    for spc in (0.1, 1.0, 5.0, 25.0):
        for rpc in (0.1, 1.0, 4.0, 20.0):
            p = dict(DEFAULT_PARAMS)
            p["seq_page_cost"] = spc
            p["random_page_cost"] = rpc
            add(f"spc{spc}_rpc{rpc}", p)
    for rpc in (0.05, 0.1, 0.5):
        for psc in (100.0, 1000.0):
            for ptc in (0.01, 0.1):
                p = dict(DEFAULT_PARAMS)
                p["random_page_cost"] = rpc
                p["parallel_setup_cost"] = psc
                p["parallel_tuple_cost"] = ptc
                p["cpu_tuple_cost"] = 0.5
                add(f"nl_par_rpc{rpc}_psc{psc}_ptc{ptc}", p)
    return points


def probe_sql(rel: str, points: Sequence[Tuple[str, Dict[str, float]]]) -> Dict[str, Any]:
    path = REPO / rel
    if not path.exists():
        return {"sql": rel, "error": "missing"}
    sql_text = path.read_text(encoding="utf-8")
    sql_body = strip_sql(sql_text)
    sha = file_sha256(path)
    if not sql_body:
        return {"sql": rel, "error": "empty", "sha256": sha}

    log(f"[probe] default EXPLAIN {rel}")
    try:
        def_ex = explain_json(sql_body, DEFAULT_PARAMS, analyze=False)
        def_m = plan_meta(def_ex)
    except Exception as exc:  # noqa: BLE001
        return {"sql": rel, "error": f"default explain failed: {exc}", "sha256": sha}

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
        and float(c.get("classification", {}).get("score") or 0) >= 30.0
    ]
    promising.sort(key=lambda c: -float(c["classification"]["score"]))
    join_hits = [c for c in promising if c["classification"].get("join_class")]
    bitmap_hits = [c for c in promising if c["classification"].get("bitmap_class")]

    return {
        "sql": rel,
        "sha256": sha,
        "default": {
            "sig": def_m["sig"],
            "hash": def_m["hash"],
            "total_cost": def_m["total_cost"],
            "max_workers": def_m["max_workers"],
            "flags": def_m["flags"],
            "plan_rows": def_m["plan_rows"],
            "plan_width": def_m["plan_width"],
        },
        "n_points": len(cands),
        "n_changed": len(changed),
        "n_promising": len(promising),
        "n_join_hits": len(join_hits),
        "n_bitmap_hits": len(bitmap_hits),
        "top_promising": promising[:12],
        "top_join": join_hits[:8],
        "top_bitmap": bitmap_hits[:8],
        "all_changed_names": [c["name"] for c in changed[:40]],
    }


def analyze_pair(
    sql_body: str,
    def_params: Dict[str, float],
    cand_params: Dict[str, float],
    label: str,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {"label": label}
    # A then B
    try:
        a = plan_meta(explain_json(sql_body, def_params, analyze=True))
        out["A"] = a
    except Exception as exc:  # noqa: BLE001
        out["A_error"] = str(exc)[:500]
        return out
    try:
        b = plan_meta(explain_json(sql_body, cand_params, analyze=True))
        out["B"] = b
    except Exception as exc:  # noqa: BLE001
        out["B_error"] = str(exc)[:500]
        return out
    # B then A (cache-order)
    try:
        b2 = plan_meta(explain_json(sql_body, cand_params, analyze=True))
        a2 = plan_meta(explain_json(sql_body, def_params, analyze=True))
        out["B2"] = b2
        out["A2"] = a2
    except Exception as exc:  # noqa: BLE001
        out["BA_error"] = str(exc)[:500]

    def delta(x: Dict[str, Any], y: Dict[str, Any]) -> Dict[str, Any]:
        def g(d: Dict[str, Any], k: str) -> float:
            return float(d.get(k) or 0.0)

        return {
            "peak_mem_drop_kb": g(x, "peak_mem_kb") - g(y, "peak_mem_kb"),
            "sum_peak_mem_drop_kb": g(x, "sum_peak_mem_kb") - g(y, "sum_peak_mem_kb"),
            "time_delta_pct": (
                100.0 * (g(x, "actual_ms") - g(y, "actual_ms")) / g(x, "actual_ms")
                if g(x, "actual_ms") > 0
                else None
            ),
            "shared_read_delta": g(y, "shared_read") - g(x, "shared_read"),
            "temp_written_delta": g(y, "temp_written") - g(x, "temp_written"),
            "A_sig": x.get("sig"),
            "B_sig": y.get("sig"),
            "A_workers": x.get("max_workers"),
            "B_workers": y.get("max_workers"),
            "A_peak_kb": g(x, "peak_mem_kb"),
            "B_peak_kb": g(y, "peak_mem_kb"),
            "A_ms": g(x, "actual_ms"),
            "B_ms": g(y, "actual_ms"),
            "A_shared_read": g(x, "shared_read"),
            "B_shared_read": g(y, "shared_read"),
            "A_batches": x.get("hash_batches"),
            "B_batches": y.get("hash_batches"),
        }

    out["AB"] = delta(out["A"], out["B"])
    if "A2" in out and "B2" in out:
        out["BA"] = delta(out["A2"], out["B2"])
    out["classification"] = classify_transition(out["A"], out["B"])
    return out


def residual_estimate(analyze: Dict[str, Any]) -> Dict[str, Any]:
    ab = analyze.get("AB") or {}
    peak_drop = float(ab.get("peak_mem_drop_kb") or 0.0)
    sum_drop = float(ab.get("sum_peak_mem_drop_kb") or 0.0)
    best_drop = max(peak_drop, sum_drop)
    # map KB to pp of 6.4 GiB (same linear map v76 used ~106496KB → 2.78pp)
    # use 1pp ≈ 6.4GiB/100 = 67.1 MiB; but empirical v76 ratio: 2.78/106496
    pp_per_kb = 2.78 / V76_PEAK_CAP_KB if V76_PEAK_CAP_KB else 0.0
    est_mem_drop_pp = best_drop * pp_per_kb
    time_delta = ab.get("time_delta_pct")
    # negative time_delta_pct means faster candidate → positive DB gain approx
    est_db_gain = None
    if time_delta is not None:
        # rough: if elapsed drops X%, QPS rises ~ X/(100-X)*100 for single-query bound
        td = float(time_delta)
        if td < 0:
            est_db_gain = (-td) / max(1e-6, 100.0 + td) * 100.0
        else:
            est_db_gain = -td  # slower → negative gain
    shared_delta = float(ab.get("shared_read_delta") or 0.0)
    io_up = shared_delta > 0
    in_target_band = TARGET_PEAK_DROP_KB_LO <= best_drop <= TARGET_PEAK_DROP_KB_HI * 1.5
    above_v76 = best_drop > V76_PEAK_CAP_KB * 1.5
    credible = bool(
        analyze.get("classification", {}).get("join_class")
        or analyze.get("classification", {}).get("bitmap_class")
    ) and above_v76 and (est_db_gain is None or est_db_gain >= 10.0)
    # stricter residual close for Phase B
    residual_close = bool(
        best_drop >= TARGET_PEAK_DROP_KB_LO * 0.7
        and (est_db_gain is None or est_db_gain >= 12.0)
        and not analyze.get("classification", {}).get("drop_parallel")
        and (
            analyze.get("classification", {}).get("join_class")
            or analyze.get("classification", {}).get("bitmap_class")
        )
    )
    return {
        "peak_drop_kb": peak_drop,
        "sum_peak_drop_kb": sum_drop,
        "best_drop_kb": best_drop,
        "est_mem_drop_pp_via_v76_map": est_mem_drop_pp,
        "est_db_gain_pct_from_time": est_db_gain,
        "shared_read_delta": shared_delta,
        "io_shared_read_up": io_up,
        "in_5_10pp_operator_band": in_target_band,
        "above_v76_cap": above_v76,
        "credible_for_phase_b": residual_close,
        "notes": (
            f"target operator drop {TARGET_PEAK_DROP_KB_LO}-{TARGET_PEAK_DROP_KB_HI} KB; "
            f"v76 hard cap was {V76_PEAK_CAP_KB} KB"
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=57433)
    ap.add_argument("--out-dir", type=str, default=str(RUN_DIR))
    ap.add_argument("--skip-analyze", action="store_true")
    ap.add_argument("--max-analyze", type=int, default=6)
    args = ap.parse_args()
    global PORT
    PORT = int(args.port)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # provenance
    prov = {}
    for rel in SQL_CORPUS:
        p = REPO / rel
        if p.exists():
            prov[rel] = {
                "sha256": file_sha256(p),
                "bytes": p.stat().st_size,
            }
    (out_dir / "sql_provenance_v79.json").write_text(
        json.dumps(prov, indent=2), encoding="utf-8"
    )

    points = build_param_points()
    log(f"[v79] port={PORT} points={len(points)} sqls={len(SQL_CORPUS)}")
    log(f"[v79] out={out_dir}")

    results: List[Dict[str, Any]] = []
    t0 = time.perf_counter()
    for rel in SQL_CORPUS:
        r = probe_sql(rel, points)
        results.append(r)
        log(
            f"  {rel}: default_workers={r.get('default', {}).get('max_workers')} "
            f"hashjoin={r.get('default', {}).get('flags', {}).get('hashjoin')} "
            f"changed={r.get('n_changed')} promising={r.get('n_promising')} "
            f"join={r.get('n_join_hits')} bitmap={r.get('n_bitmap_hits')} "
            f"sig={str(r.get('default', {}).get('sig'))[:100]}"
        )

    (out_dir / "plan_probe_summary_v79.json").write_text(
        json.dumps(
            {
                "port": PORT,
                "n_points": len(points),
                "elapsed_s": time.perf_counter() - t0,
                "results": results,
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    # Rank global transitions
    ranked: List[Dict[str, Any]] = []
    for r in results:
        if r.get("error"):
            continue
        for c in r.get("top_promising") or []:
            ranked.append(
                {
                    "sql": r["sql"],
                    "sha256": r.get("sha256"),
                    "default_sig": r["default"]["sig"],
                    "cand_name": c["name"],
                    "cand_sig": c["sig"],
                    "score": c["classification"]["score"],
                    "tags": c["classification"]["tags"],
                    "reason": c["classification"]["reason"],
                    "join_class": c["classification"].get("join_class"),
                    "bitmap_class": c["classification"].get("bitmap_class"),
                    "params": c["params"],
                    "max_workers_def": r["default"]["max_workers"],
                    "max_workers_cand": c["max_workers"],
                }
            )
    ranked.sort(key=lambda x: -float(x["score"]))
    # dedupe by (sql, cand_sig)
    seen_pair = set()
    ranked_dedup: List[Dict[str, Any]] = []
    for item in ranked:
        key = (item["sql"], item["cand_sig"])
        if key in seen_pair:
            continue
        seen_pair.add(key)
        ranked_dedup.append(item)

    md_lines = [
        "# v79 Join/Bitmap ranked transitions",
        "",
        f"points={len(points)} sqls={len(SQL_CORPUS)}",
        "",
    ]
    for i, item in enumerate(ranked_dedup[:30], 1):
        md_lines.append(
            f"## {i}. score={item['score']:.1f} join={item['join_class']} bitmap={item['bitmap_class']}"
        )
        md_lines.append(f"- sql: `{item['sql']}`")
        md_lines.append(f"- cand: `{item['cand_name']}`")
        md_lines.append(f"- default: `{item['default_sig']}`")
        md_lines.append(f"- cand: `{item['cand_sig']}`")
        md_lines.append(f"- workers: {item['max_workers_def']} → {item['max_workers_cand']}")
        md_lines.append(f"- reason: {item['reason']}")
        md_lines.append(f"- tags: {', '.join(item['tags'])}")
        md_lines.append("")
    (out_dir / "ranked_transitions_v79.md").write_text(
        "\n".join(md_lines), encoding="utf-8"
    )
    (out_dir / "ranked_transitions_v79.json").write_text(
        json.dumps(ranked_dedup[:50], indent=2), encoding="utf-8"
    )

    # ANALYZE top join/bitmap candidates
    analyze_results: List[Dict[str, Any]] = []
    if not args.skip_analyze:
        picks: List[Dict[str, Any]] = []
        for item in ranked_dedup:
            if item.get("join_class") or item.get("bitmap_class"):
                picks.append(item)
            if len(picks) >= args.max_analyze:
                break
        # if no join/bitmap, still analyze top 2 for evidence of class absence
        if not picks:
            picks = ranked_dedup[:2]
        log(f"[v79] EXPLAIN ANALYZE pairs: {len(picks)}")
        for item in picks:
            path = REPO / item["sql"]
            body = strip_sql(path.read_text(encoding="utf-8"))
            label = f"{Path(item['sql']).stem}__{item['cand_name']}"
            log(f"[analyze] {label}")
            try:
                ar = analyze_pair(body, DEFAULT_PARAMS, item["params"], label)
                ar["sql"] = item["sql"]
                ar["sha256"] = item.get("sha256")
                ar["cand_name"] = item["cand_name"]
                ar["params"] = item["params"]
                ar["residual"] = residual_estimate(ar)
                analyze_results.append(ar)
                ab = ar.get("AB") or {}
                res = ar.get("residual") or {}
                log(
                    f"  peak_drop={ab.get('peak_mem_drop_kb')}KB "
                    f"timeΔ%={ab.get('time_delta_pct')} "
                    f"shared_readΔ={ab.get('shared_read_delta')} "
                    f"credible={res.get('credible_for_phase_b')} "
                    f"est_pp={res.get('est_mem_drop_pp_via_v76_map')}"
                )
            except Exception as exc:  # noqa: BLE001
                log(f"  ANALYZE failed: {exc}")
                analyze_results.append({"label": label, "error": str(exc)[:500]})

    (out_dir / "explain_analyze_top_v79.json").write_text(
        json.dumps(analyze_results, indent=2, default=str), encoding="utf-8"
    )

    # Phase B eligibility
    eligible = [
        a
        for a in analyze_results
        if a.get("residual", {}).get("credible_for_phase_b")
        and not a.get("error")
        and not a.get("A_error")
        and not a.get("B_error")
    ]
    decision = {
        "phase": "A",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "port": PORT,
        "n_sql": len(SQL_CORPUS),
        "n_points": len(points),
        "n_ranked": len(ranked_dedup),
        "n_join_ranked": sum(1 for x in ranked_dedup if x.get("join_class")),
        "n_bitmap_ranked": sum(1 for x in ranked_dedup if x.get("bitmap_class")),
        "n_analyze": len(analyze_results),
        "n_eligible": len(eligible),
        "phase_b_eligible": len(eligible) > 0,
        "eligible": [
            {
                "label": e.get("label"),
                "sql": e.get("sql"),
                "cand_name": e.get("cand_name"),
                "params": e.get("params"),
                "residual": e.get("residual"),
                "AB": e.get("AB"),
                "classification": e.get("classification"),
            }
            for e in eligible
        ],
        "best_analyze": None,
        "stop_reason": None,
        "next_class_if_stop": [
            "work_mem multi-batch Hash construction (if allowed to diverge setup)",
            "Materialize/Memoize lifetime class with real heap re-read",
            "parallel HashJoin batch-spill under constrained work_mem setup",
        ],
        "fallback_db_qps_best": 114.13,
    }
    if analyze_results:
        # pick best by residual best_drop among non-error
        scored = [
            a
            for a in analyze_results
            if a.get("residual") and not a.get("error") and not a.get("A_error")
        ]
        scored.sort(
            key=lambda a: (
                1 if a["residual"].get("credible_for_phase_b") else 0,
                float(a["residual"].get("best_drop_kb") or 0),
                float(a["residual"].get("est_db_gain_pct_from_time") or 0),
            ),
            reverse=True,
        )
        if scored:
            b = scored[0]
            decision["best_analyze"] = {
                "label": b.get("label"),
                "sql": b.get("sql"),
                "cand_name": b.get("cand_name"),
                "params": b.get("params"),
                "residual": b.get("residual"),
                "AB": b.get("AB"),
                "classification": b.get("classification"),
            }
    if not decision["phase_b_eligible"]:
        decision["stop_reason"] = (
            "no Join/Bitmap transition with residual operator-memory drop "
            "and DB/time evidence meeting Phase A residual-close bar"
        )

    (out_dir / "phase_a_decision_v79.json").write_text(
        json.dumps(decision, indent=2, default=str), encoding="utf-8"
    )
    (out_dir / "phase_b_eligibility_v79.json").write_text(
        json.dumps(
            {
                "phase_b_eligible": decision["phase_b_eligible"],
                "eligible": decision["eligible"],
                "best_analyze": decision["best_analyze"],
                "stop_reason": decision["stop_reason"],
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    log(
        f"[v79] DONE phase_b_eligible={decision['phase_b_eligible']} "
        f"join_ranked={decision['n_join_ranked']} "
        f"bitmap_ranked={decision['n_bitmap_ranked']} "
        f"eligible={decision['n_eligible']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

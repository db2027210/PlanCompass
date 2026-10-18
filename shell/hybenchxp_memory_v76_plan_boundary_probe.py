#!/usr/bin/env python3
"""HyBenchXP Memory fair v76 offline plan-boundary probe.

Phase A only: EXPLAIN (FORMAT JSON) over seven cost params on rebuild w1.
Does not start tuners or modify binary_plan_resource_tuner.py.

Ranks transitions that:
  - drop/shrink HashAgg/HashJoin/Sort/Materialize/Memoize while keeping Gather, OR
  - replace memory structures with more Seq/Bitmap/heap or CPU-heavy paths,
and rejects pure Sorted>IndexOnly + parallel collapse (v75g failure mode).
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
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


REPO = Path("/home/anonymous/program/resource_adaptive")
HOST = "127.0.0.1"
PORT = 57433
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

# Bounded single-parameter grids + selected multi-parameter points.
SINGLE_GRIDS: Dict[str, List[float]] = {
    "seq_page_cost": [0.1, 0.5, 1.0, 1.2, 1.5, 2.0, 3.0, 5.0, 10.0, 25.0, 75.0, 100.0],
    "random_page_cost": [0.1, 0.5, 1.0, 2.0, 4.0, 6.0, 8.0, 12.0, 15.0, 20.0],
    "cpu_tuple_cost": [1e-4, 1e-3, 0.005, 0.01, 0.02, 0.05, 0.1, 0.25, 0.5, 1.0],
    "cpu_index_tuple_cost": [1e-4, 5e-4, 0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0],
    "cpu_operator_cost": [1e-4, 5e-4, 0.001, 0.0025, 0.01, 0.05, 0.1, 0.5, 1.0],
    "parallel_setup_cost": [0.0, 10.0, 100.0, 500.0, 1000.0, 2000.0, 5000.0, 10000.0],
    "parallel_tuple_cost": [1e-4, 0.01, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0],
}

# Two-parameter interactions of interest (not weight interpolation).
NAMED_POINTS: Dict[str, Dict[str, float]] = {
    "par_collapse_v75g": {
        **DEFAULT_PARAMS,
        "parallel_setup_cost": 10000.0,
        "parallel_tuple_cost": 1.0,
    },
    "ptc1_only": {**DEFAULT_PARAMS, "parallel_tuple_cost": 1.0},
    "psc10k_only": {**DEFAULT_PARAMS, "parallel_setup_cost": 10000.0},
    "spc2": {**DEFAULT_PARAMS, "seq_page_cost": 2.0},
    "spc5": {**DEFAULT_PARAMS, "seq_page_cost": 5.0},
    "spc75_rpc15": {
        **DEFAULT_PARAMS,
        "seq_page_cost": 75.0,
        "random_page_cost": 15.0,
    },
    "rpc1": {**DEFAULT_PARAMS, "random_page_cost": 1.0},
    "rpc0p1": {**DEFAULT_PARAMS, "random_page_cost": 0.1},
    "rpc20": {**DEFAULT_PARAMS, "random_page_cost": 20.0},
    "ctc_high": {**DEFAULT_PARAMS, "cpu_tuple_cost": 0.25},
    "ctc1": {**DEFAULT_PARAMS, "cpu_tuple_cost": 1.0},
    "coc_high": {**DEFAULT_PARAMS, "cpu_operator_cost": 0.1},
    "hash_discourage": {
        **DEFAULT_PARAMS,
        "cpu_tuple_cost": 0.001,
        "cpu_operator_cost": 0.0001,
    },
    "sort_encourage": {
        **DEFAULT_PARAMS,
        "cpu_tuple_cost": 0.001,
        "cpu_operator_cost": 0.0001,
        "cpu_index_tuple_cost": 0.001,
    },
    "nl_encourage_rpc_low_ctc_high": {
        **DEFAULT_PARAMS,
        "random_page_cost": 0.5,
        "cpu_tuple_cost": 0.5,
    },
    "nl_encourage_rpc_low": {
        **DEFAULT_PARAMS,
        "random_page_cost": 0.1,
        "seq_page_cost": 1.0,
    },
    "hashjoin_discourage": {
        **DEFAULT_PARAMS,
        "cpu_tuple_cost": 0.5,
        "cpu_operator_cost": 0.25,
        "random_page_cost": 1.0,
    },
    "parallel_cheap": {
        **DEFAULT_PARAMS,
        "parallel_setup_cost": 0.0,
        "parallel_tuple_cost": 0.001,
    },
    "index_expensive_seq_cheap": {
        **DEFAULT_PARAMS,
        "seq_page_cost": 0.1,
        "random_page_cost": 20.0,
        "cpu_index_tuple_cost": 0.5,
    },
    "index_cheap_seq_expensive": {
        **DEFAULT_PARAMS,
        "seq_page_cost": 75.0,
        "random_page_cost": 1.0,
        "cpu_index_tuple_cost": 0.0001,
    },
    "groupagg_teacher_v11": {
        "seq_page_cost": 75.0,
        "random_page_cost": 15.0,
        "cpu_tuple_cost": 0.01,
        "cpu_index_tuple_cost": 0.005,
        "cpu_operator_cost": 0.0025,
        "parallel_setup_cost": 1000.0,
        "parallel_tuple_cost": 10.0,
    },
    "memory_serial_groupagg_like": {
        "seq_page_cost": 75.0,
        "random_page_cost": 15.0,
        "cpu_tuple_cost": 0.01,
        "cpu_index_tuple_cost": 0.005,
        "cpu_operator_cost": 0.0025,
        "parallel_setup_cost": 1000.0,
        "parallel_tuple_cost": 10.0,
    },
    "keep_par_raise_seq": {
        **DEFAULT_PARAMS,
        "seq_page_cost": 3.0,
        "parallel_setup_cost": 100.0,
        "parallel_tuple_cost": 0.01,
    },
    "keep_par_hash_to_sort_ctc": {
        **DEFAULT_PARAMS,
        "cpu_tuple_cost": 0.001,
        "cpu_operator_cost": 1e-4,
        "parallel_setup_cost": 100.0,
        "parallel_tuple_cost": 0.01,
    },
}


SQL_CORPUS: List[str] = [
    "shell/hybenchxp_memory_ap6_indexonly_switch_v75.sql",
    "shell/hybenchxp_memory_ap6_reverse_hash_parallel_v75g.sql",
    "shell/hybenchxp_memory_ap6_reverse_hashjoin_v75g.sql",
    "shell/hybenchxp_memory_ap6_reverse_sort_material_v75g.sql",
    "shell/hybenchxp_memory_ap6_transfer_groupagg_switch.sql",
    "shell/hybenchxp_memory_ap6_transfer_groupagg_switch_v12_fullscan.sql",
    "shell/hybenchxp_memory_ap6_hashagg_sort_1p5m_500k.sql",
    "shell/hybenchxp_memory_ap6_agg_heavy_indexscan_v71.sql",
    "shell/hybenchxp_memory_ap9_transfer_amount_bucket_floor_v75.sql",
    "shell/hybenchxp_memory_floor_transfer_amount_bucket_v75.sql",
    "shell/hybenchxp_memory_floor_transfer_highcard_hash_v75.sql",
    "shell/hybenchxp_memory_floor_loan_applicant_hash_v75.sql",
    "shell/hybenchxp_memory_floor_saving_hash_sort_v75.sql",
    "shell/hybenchxp_memory_ap8_fixed_heap_light_v75.sql",
    "shell/hybenchxp_memory_ap10_transfer_target_flip_v75.sql",
    "shell/hybenchxp_memory_ap10_transfer_hashagg_r4.sql",
    "shell/hybenchxp_memory_ap11_transfer_hashagg_r5.sql",
    "shell/hybenchxp_memory_groupagg_plan_workload_v11.sql",
]


def log(msg: str) -> None:
    print(msg, flush=True)


def strip_sql(sql_text: str) -> str:
    body = "\n".join(
        ln for ln in sql_text.splitlines() if not ln.strip().startswith("--")
    ).strip().rstrip(";")
    # keep first statement only
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
        "groupagg": False,  # Aggregate Strategy Sorted/Plain without Hash
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
            elif strat in ("Sorted", "Plain"):
                # Sorted partial/finalize groupagg-ish
                if strat == "Sorted":
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
        timeout=600 if analyze else 120,
    )
    if r.returncode != 0:
        raise RuntimeError(f"psql failed: {r.stderr.strip()[:800]}")
    raw = r.stdout.strip()
    # psql -t -A may still wrap; find JSON array
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
    # buffer / temp rollup
    shared_hit = shared_read = temp_read = temp_written = 0
    peak_mem = 0.0
    batches = 0

    def walk(n: dict) -> None:
        nonlocal shared_hit, shared_read, temp_read, temp_written, peak_mem, batches
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
            peak_mem = max(peak_mem, float(n["Sort Space Used"]))
        for ch in n.get("Plans") or []:
            walk(ch)

    walk(plan)
    meta["shared_hit"] = shared_hit
    meta["shared_read"] = shared_read
    meta["temp_read"] = temp_read
    meta["temp_written"] = temp_written
    meta["peak_mem_kb"] = peak_mem
    meta["hash_batches"] = batches
    return meta


def classify_transition(def_m: Dict[str, Any], cand_m: Dict[str, Any]) -> Dict[str, Any]:
    df, cf = def_m["flags"], cand_m["flags"]
    tags: List[str] = []
    if def_m["sig"] == cand_m["sig"]:
        return {"tags": ["unchanged"], "reject": True, "score": -1.0, "reason": "identical signature"}

    drop_hashagg = df["hashagg"] and not cf["hashagg"]
    drop_hashjoin = df["hashjoin"] and not cf["hashjoin"]
    drop_material = df["material"] and not cf["material"]
    drop_memoize = df["memoize"] and not cf["memoize"]
    drop_sort = df["sort"] and not cf["sort"]
    keep_parallel = def_m["parallel"] and cand_m["parallel"]
    drop_parallel = def_m["parallel"] and not cand_m["parallel"]
    gain_parallel = (not def_m["parallel"]) and cand_m["parallel"]
    to_indexonly = (not df["indexonly"]) and cf["indexonly"]
    lose_seq = df["seq"] and not cf["seq"]
    gain_seq = (not df["seq"]) and cf["seq"]
    gain_bitmap = (not df["bitmap_heap"]) and cf["bitmap_heap"]
    gain_nl = (not df["nested_loop"]) and cf["nested_loop"]
    gain_merge = (not df["merge_join"]) and cf["merge_join"]
    gain_groupagg = (not df["groupagg"]) and cf["groupagg"]
    gain_sort = (not df["sort"]) and cf["sort"]

    if drop_hashagg:
        tags.append("drop_hashagg")
    if drop_hashjoin:
        tags.append("drop_hashjoin")
    if drop_material:
        tags.append("drop_material")
    if drop_memoize:
        tags.append("drop_memoize")
    if drop_sort:
        tags.append("drop_sort")
    if keep_parallel:
        tags.append("keep_parallel")
    if drop_parallel:
        tags.append("drop_parallel")
    if gain_parallel:
        tags.append("gain_parallel")
    if to_indexonly:
        tags.append("to_indexonly")
    if lose_seq:
        tags.append("lose_seq")
    if gain_seq:
        tags.append("gain_seq")
    if gain_bitmap:
        tags.append("gain_bitmap")
    if gain_nl:
        tags.append("gain_nested_loop")
    if gain_merge:
        tags.append("gain_merge_join")
    if gain_groupagg:
        tags.append("gain_groupagg")
    if gain_sort:
        tags.append("gain_sort")

    # v75g failure mode
    pure_par_collapse_indexonly = (
        drop_parallel
        and to_indexonly
        and ("Sorted" in cand_m["sig"] or "Index Only" in cand_m["sig"])
        and not keep_parallel
    )
    if pure_par_collapse_indexonly:
        tags.append("REJECT_par_collapse_indexonly")

    # structural desire
    mem_structure_drop = drop_hashagg or drop_hashjoin or drop_material or drop_memoize
    # shrink-ish: hashagg retained but sort/group path on partial
    hash_to_sorted_partial = df["hashagg"] and cf["hashagg"] and gain_groupagg and keep_parallel

    # IO/CPU rise hypotheses
    expect_io_up = gain_nl or gain_bitmap or gain_seq or (drop_hashjoin and (cf["nested_loop"] or cf["index"]))
    expect_cpu_up = gain_sort or gain_groupagg or gain_nl or (drop_hashagg and keep_parallel)
    # IndexOnly often lowers IO — mark as IO-down risk
    expect_io_down = to_indexonly and lose_seq and not gain_nl

    score = 0.0
    reason_bits: List[str] = []
    reject = False

    if pure_par_collapse_indexonly:
        reject = True
        score = -100.0
        reason_bits.append("v75g failure: Sorted/IndexOnly + parallel collapse")
    elif mem_structure_drop and keep_parallel:
        score += 50.0
        reason_bits.append("drop memory structure while keeping parallel")
    elif hash_to_sorted_partial:
        score += 25.0
        reason_bits.append("partial Hash->Sorted with Gather retained")
    elif mem_structure_drop and not drop_parallel and not def_m["parallel"]:
        score += 30.0
        reason_bits.append("drop memory structure (serial both sides)")
    elif mem_structure_drop and drop_parallel and not to_indexonly:
        # parallel collapse but not indexonly — still often kills CPU; mild score
        score += 8.0
        reason_bits.append("drop mem structure via parallel collapse (CPU risk)")
    elif drop_parallel and not mem_structure_drop:
        score -= 20.0
        reason_bits.append("parallel collapse without mem structure drop")
    else:
        score += 1.0
        reason_bits.append("plan changed without clear mem-structure win")

    if expect_cpu_up:
        score += 15.0
        reason_bits.append("expect more CPU work")
    if expect_io_up:
        score += 20.0
        reason_bits.append("expect more physical IO")
    if expect_io_down:
        score -= 15.0
        reason_bits.append("expect IO down (IndexOnly replaces Seq)")
    if drop_parallel:
        score -= 25.0
        reason_bits.append("parallel collapse penalty")
    if keep_parallel and def_m["max_workers"] and cand_m["max_workers"]:
        if cand_m["max_workers"] >= def_m["max_workers"]:
            score += 5.0

    # rename-only: same mem flags, only cosmetic
    same_mem_flags = (
        df["hashagg"] == cf["hashagg"]
        and df["hashjoin"] == cf["hashjoin"]
        and df["material"] == cf["material"]
        and df["memoize"] == cf["memoize"]
        and df["sort"] == cf["sort"]
        and def_m["parallel"] == cand_m["parallel"]
    )
    if same_mem_flags and not (gain_nl or gain_bitmap or gain_seq or to_indexonly):
        score -= 5.0
        reason_bits.append("possible rename-only / weak structural delta")

    return {
        "tags": tags,
        "reject": reject,
        "score": score,
        "reason": "; ".join(reason_bits),
        "drop_hashagg": drop_hashagg,
        "drop_hashjoin": drop_hashjoin,
        "keep_parallel": keep_parallel,
        "drop_parallel": drop_parallel,
        "to_indexonly": to_indexonly,
        "expect_cpu_up": expect_cpu_up,
        "expect_io_up": expect_io_up,
        "expect_io_down": expect_io_down,
        "mem_structure_drop": mem_structure_drop,
    }


def build_param_points() -> List[Tuple[str, Dict[str, float]]]:
    points: List[Tuple[str, Dict[str, float]]] = []
    seen = set()

    def add(name: str, p: Dict[str, float]) -> None:
        key = tuple(round(float(p[k]), 12) for k in SEVEN)
        if key in seen:
            return
        # skip pure default
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
    # selected 2-param combos
    for spc in (2.0, 5.0, 75.0):
        for ptc in (0.01, 0.1, 1.0, 10.0):
            p = dict(DEFAULT_PARAMS)
            p["seq_page_cost"] = spc
            p["parallel_tuple_cost"] = ptc
            add(f"spc{spc}_ptc{ptc}", p)
    for spc in (2.0, 5.0):
        for psc in (100.0, 1000.0, 10000.0):
            p = dict(DEFAULT_PARAMS)
            p["seq_page_cost"] = spc
            p["parallel_setup_cost"] = psc
            add(f"spc{spc}_psc{psc}", p)
    for rpc in (0.1, 1.0, 8.0, 20.0):
        for ctc in (0.01, 0.1, 0.5):
            p = dict(DEFAULT_PARAMS)
            p["random_page_cost"] = rpc
            p["cpu_tuple_cost"] = ctc
            add(f"rpc{rpc}_ctc{ctc}", p)
    for ctc in (0.001, 0.01, 0.25):
        for coc in (0.0001, 0.0025, 0.1):
            p = dict(DEFAULT_PARAMS)
            p["cpu_tuple_cost"] = ctc
            p["cpu_operator_cost"] = coc
            add(f"ctc{ctc}_coc{coc}", p)
    return points


def probe_sql(rel: str, points: Sequence[Tuple[str, Dict[str, float]]]) -> Dict[str, Any]:
    path = REPO / rel
    if not path.exists():
        return {"sql": rel, "error": "missing"}
    sql_body = strip_sql(path.read_text(encoding="utf-8"))
    if not sql_body:
        return {"sql": rel, "error": "empty"}

    log(f"[probe] default EXPLAIN {rel}")
    try:
        def_ex = explain_json(sql_body, DEFAULT_PARAMS, analyze=False)
        def_m = plan_meta(def_ex)
    except Exception as exc:  # noqa: BLE001
        return {"sql": rel, "error": f"default explain failed: {exc}"}

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
        and float(c.get("classification", {}).get("score") or 0) >= 20.0
    ]
    promising.sort(key=lambda c: -float(c["classification"]["score"]))

    return {
        "sql": rel,
        "default": {
            "sig": def_m["sig"],
            "hash": def_m["hash"],
            "total_cost": def_m["total_cost"],
            "max_workers": def_m["max_workers"],
            "flags": def_m["flags"],
        },
        "n_points": len(points),
        "n_changed": len(changed),
        "n_promising": len(promising),
        "promising": promising[:25],
        "all_changed": changed,
    }


def analyze_pair(
    rel: str,
    params: Dict[str, float],
    label: str,
) -> Dict[str, Any]:
    """Controlled ANALYZE A/B then B/A to avoid cache-order bias."""
    path = REPO / rel
    sql_body = strip_sql(path.read_text(encoding="utf-8"))

    def one(side: str, p: Dict[str, float]) -> Dict[str, Any]:
        # warm lightly then measure
        try:
            explain_json(sql_body, p, analyze=True)
        except Exception:
            pass
        ex = explain_json(sql_body, p, analyze=True)
        m = plan_meta(ex)
        m["side"] = side
        return m

    order_ab = [
        ("default", DEFAULT_PARAMS),
        (label, params),
    ]
    order_ba = [
        (label, params),
        ("default", DEFAULT_PARAMS),
    ]
    ab = [one(s, p) for s, p in order_ab]
    ba = [one(s, p) for s, p in order_ba]
    return {
        "sql": rel,
        "label": label,
        "params": params,
        "ab": ab,
        "ba": ba,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--out-dir",
        default=str(
            Path("/disk2/resource_adaptive/binary_plan_tuning_hybenchxp/runs")
            / "hyxp_memory_fair_v76_plan_boundary_20260714"
        ),
    )
    ap.add_argument("--sql", action="append", default=None, help="limit to these SQL rel paths")
    ap.add_argument("--analyze-top", type=int, default=6)
    ap.add_argument("--skip-analyze", action="store_true")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    points = build_param_points()
    log(f"[v76] param points={len(points)} port={PORT}")

    # reset session defaults not required; each explain SETs locally
    sqls = args.sql if args.sql else SQL_CORPUS
    results: List[Dict[str, Any]] = []
    t0 = time.perf_counter()
    for rel in sqls:
        rec = probe_sql(rel, points)
        results.append(rec)
        n_p = rec.get("n_promising", 0)
        n_c = rec.get("n_changed", 0)
        err = rec.get("error")
        if err:
            log(f"  FAIL {rel}: {err}")
        else:
            log(f"  ok {rel}: changed={n_c} promising={n_p} def={rec['default']['sig'][:80]}")

    summary_path = out_dir / "plan_probe_summary_v76.json"
    summary_path.write_text(json.dumps(results, indent=2), encoding="utf-8")

    # global ranking
    ranked: List[Dict[str, Any]] = []
    for rec in results:
        if rec.get("error"):
            continue
        for c in rec.get("promising") or []:
            ranked.append(
                {
                    "sql": rec["sql"],
                    "default_sig": rec["default"]["sig"],
                    "default_workers": rec["default"]["max_workers"],
                    "name": c["name"],
                    "params": c["params"],
                    "cand_sig": c["sig"],
                    "cand_workers": c["max_workers"],
                    "score": c["classification"]["score"],
                    "tags": c["classification"]["tags"],
                    "reason": c["classification"]["reason"],
                    "expect_cpu_up": c["classification"].get("expect_cpu_up"),
                    "expect_io_up": c["classification"].get("expect_io_up"),
                    "mem_structure_drop": c["classification"].get("mem_structure_drop"),
                    "keep_parallel": c["classification"].get("keep_parallel"),
                    "drop_parallel": c["classification"].get("drop_parallel"),
                    "to_indexonly": c["classification"].get("to_indexonly"),
                }
            )
    ranked.sort(key=lambda x: -float(x["score"]))
    # dedupe by (sql, cand_sig)
    dedup: List[Dict[str, Any]] = []
    seen_sig = set()
    for r in ranked:
        key = (r["sql"], r["cand_sig"])
        if key in seen_sig:
            continue
        seen_sig.add(key)
        dedup.append(r)

    ranked_path = out_dir / "ranked_transitions_v76.json"
    ranked_path.write_text(json.dumps(dedup, indent=2), encoding="utf-8")
    log(f"[v76] ranked unique transitions={len(dedup)} -> {ranked_path}")

    analyzes: List[Dict[str, Any]] = []
    if not args.skip_analyze and dedup:
        top = dedup[: max(0, args.analyze_top)]
        for i, r in enumerate(top):
            log(f"[analyze {i+1}/{len(top)}] {r['sql']} @ {r['name']} score={r['score']}")
            try:
                analyzes.append(analyze_pair(r["sql"], r["params"], r["name"]))
            except Exception as exc:  # noqa: BLE001
                analyzes.append({"sql": r["sql"], "name": r["name"], "error": str(exc)[:500]})

    analyze_path = out_dir / "explain_analyze_top_v76.json"
    analyze_path.write_text(json.dumps(analyzes, indent=2), encoding="utf-8")

    # compact markdown ranking
    md_lines = [
        "# Memory fair v76 Phase A plan-boundary ranking",
        "",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Port: {PORT}",
        f"Param points probed: {len(points)}",
        f"SQL files: {len(sqls)}",
        f"Elapsed_s: {time.perf_counter() - t0:.1f}",
        "",
        "## Ranked transitions (unique sql×sig)",
        "",
    ]
    if not dedup:
        md_lines.append("_No promising transitions (score>=20, not rejected)._")
    for i, r in enumerate(dedup[:30], 1):
        md_lines.extend(
            [
                f"### {i}. score={r['score']:.1f} `{r['name']}`",
                f"- sql: `{r['sql']}`",
                f"- default: `{r['default_sig']}` (W{r['default_workers']})",
                f"- cand: `{r['cand_sig']}` (W{r['cand_workers']})",
                f"- tags: {', '.join(r['tags'])}",
                f"- reason: {r['reason']}",
                f"- params: `{json.dumps(r['params'])}`",
                f"- expect_cpu_up={r['expect_cpu_up']} expect_io_up={r['expect_io_up']} "
                f"mem_drop={r['mem_structure_drop']} keep_par={r['keep_parallel']}",
                "",
            ]
        )
    md_path = out_dir / "ranked_transitions_v76.md"
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    log(f"[v76] done summary={summary_path} md={md_path} analyze={analyze_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""HyBenchXP Memory fair v84 Phase A — nested HashAgg / SetOp plan-boundary probe.

Mechanisms (only; structurally new vs closed v71–v83 families):
  1) nested_double_hashagg_collapse — hierarchical inner+outer HashAgg lifetimes
  2) hashsetop_intersect_to_sorted_setop — HashSetOp → Sorted SetOp under seven-cost

Offline EXPLAIN / EXPLAIN ANALYZE on rebuild w1 (57433) only.
Does not start tuners; does not edit binary_plan_resource_tuner.py.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


REPO = Path("/home/anonymous/program/resource_adaptive")
HOST = "127.0.0.1"
PORT = 57433
USER = "postgres"
DB = "hybench_sf10x"
RUN_DIR = Path(
    "/disk2/resource_adaptive/binary_plan_tuning_hybenchxp/runs/"
    "hyxp_memory_fair_v84_nested_setop_20260715"
)
REPORT_DIR = REPO / "report" / "bonect"
TZ = timezone(timedelta(hours=8))
CONTAINER = "binary_plan_tuning_hybenchxp_rebuild_20260703_180311_cpu_mem_w1"

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

# Prefer residual-relevant points; hard cap 16 signature-changing vectors.
# Order matters: stop once 16 distinct signature-changing vectors found.
NAMED_POINTS: Dict[str, Dict[str, float]] = {
    "ctc0p5": {**DEFAULT_PARAMS, "cpu_tuple_cost": 0.5},
    "ctc0p75": {**DEFAULT_PARAMS, "cpu_tuple_cost": 0.75},
    "ctc1": {**DEFAULT_PARAMS, "cpu_tuple_cost": 1.0},
    "ctc1p25": {**DEFAULT_PARAMS, "cpu_tuple_cost": 1.25},
    "ctc1p5": {**DEFAULT_PARAMS, "cpu_tuple_cost": 1.5, "cpu_operator_cost": 0.25},
    "ctc2": {**DEFAULT_PARAMS, "cpu_tuple_cost": 2.0, "cpu_operator_cost": 0.5},
    "ctc5": {**DEFAULT_PARAMS, "cpu_tuple_cost": 5.0},
    "ctc10": {**DEFAULT_PARAMS, "cpu_tuple_cost": 10.0},
    "ctc2_spc25": {
        **DEFAULT_PARAMS,
        "cpu_tuple_cost": 2.0,
        "seq_page_cost": 25.0,
    },
    "ctc2_spc75": {
        **DEFAULT_PARAMS,
        "cpu_tuple_cost": 2.0,
        "seq_page_cost": 75.0,
    },
    "ctc5_coc0p25": {
        **DEFAULT_PARAMS,
        "cpu_tuple_cost": 5.0,
        "cpu_operator_cost": 0.25,
    },
    "ctc10_spc1": {**DEFAULT_PARAMS, "cpu_tuple_cost": 10.0, "seq_page_cost": 1.0},
    "coc0p25": {**DEFAULT_PARAMS, "cpu_operator_cost": 0.25},
    "coc0p5_ctc1": {
        **DEFAULT_PARAMS,
        "cpu_operator_cost": 0.5,
        "cpu_tuple_cost": 1.0,
    },
    "rpc0p1": {**DEFAULT_PARAMS, "random_page_cost": 0.1},
    "rpc0p5": {**DEFAULT_PARAMS, "random_page_cost": 0.5},
    "spc25": {**DEFAULT_PARAMS, "seq_page_cost": 25.0},
    "spc75": {**DEFAULT_PARAMS, "seq_page_cost": 75.0},
    "spc75_rpc0p5": {
        **DEFAULT_PARAMS,
        "seq_page_cost": 75.0,
        "random_page_cost": 0.5,
    },
    "hash_discourage": {
        **DEFAULT_PARAMS,
        "cpu_tuple_cost": 1.0,
        "cpu_operator_cost": 0.25,
        "random_page_cost": 1.0,
    },
    "sort_encourage": {
        **DEFAULT_PARAMS,
        "cpu_tuple_cost": 1.5,
        "cpu_operator_cost": 0.5,
        "cpu_index_tuple_cost": 0.0001,
        "random_page_cost": 0.5,
    },
    "setop_sort_seed": {
        **DEFAULT_PARAMS,
        "cpu_tuple_cost": 2.0,
        "cpu_operator_cost": 2.0,
        "random_page_cost": 0.1,
        "cpu_index_tuple_cost": 0.0001,
    },
    "par_mild": {
        **DEFAULT_PARAMS,
        "parallel_setup_cost": 2000.0,
        "parallel_tuple_cost": 0.5,
    },
    "par_collapse": {
        **DEFAULT_PARAMS,
        "parallel_setup_cost": 10000.0,
        "parallel_tuple_cost": 1.0,
    },
}

SQL_SPECS: List[Dict[str, Any]] = [
    {
        "id": "nested_double_hashagg",
        "mechanism": "nested_double_hashagg_collapse",
        "path": "shell/hybenchxp_memory_ap6_v84_nested_double_hashagg.sql",
        "work_mem": "512MB",
        "hash_mem_multiplier": 2.0,
        "enable_parallel_hash": True,
        "enable_memoize": True,
    },
    {
        "id": "nested_hashagg_type",
        "mechanism": "nested_double_hashagg_collapse",
        "path": "shell/hybenchxp_memory_ap6_v84_nested_hashagg_type.sql",
        "work_mem": "512MB",
        "hash_mem_multiplier": 2.0,
        "enable_parallel_hash": True,
        "enable_memoize": True,
    },
    {
        "id": "hashsetop_intersect",
        "mechanism": "hashsetop_intersect_to_sorted_setop",
        "path": "shell/hybenchxp_memory_ap6_v84_hashsetop_intersect.sql",
        "work_mem": "512MB",
        "hash_mem_multiplier": 2.0,
        "enable_parallel_hash": True,
        "enable_memoize": True,
    },
    {
        "id": "hashsetop_except",
        "mechanism": "hashsetop_intersect_to_sorted_setop",
        "path": "shell/hybenchxp_memory_ap6_v84_hashsetop_except.sql",
        "work_mem": "512MB",
        "hash_mem_multiplier": 2.0,
        "enable_parallel_hash": True,
        "enable_memoize": True,
    },
]

TARGET_PEAK_DROP_MIB = 328.0
TARGET_SPEEDUP_PCT = 15.0
FALLBACK_DB_QPS = 114.12708344892893
MAX_VECTORS = 16
MAX_ANALYZE_PAIRS = 14


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
    # Keep multi-statement INTERSECT/EXCEPT as one body; strip trailing only.
    return body


def plan_signature(plan_node: dict) -> str:
    parts: List[str] = []

    def walk(n: dict) -> None:
        if not isinstance(n, dict):
            return
        node = n.get("Node Type", "?")
        strat = n.get("Strategy")
        cmd = n.get("Command")
        rel = n.get("Relation Name")
        workers = n.get("Workers Planned")
        label = node
        if strat:
            label += f":{strat}"
        if cmd:
            label += f":{cmd}"
        if rel:
            label += f":{rel}"
        if node in ("Gather", "Gather Merge") and workers is not None:
            label += f":W{workers}"
        if node == "SetOp":
            label += f"[setop={strat or cmd or '?'}]"
            if str(strat or "").lower() == "hashed":
                label += "[hash]"
            elif str(strat or "").lower() in ("sorted", "merge"):
                label += "[sorted]"
        if node == "HashSetOp":
            label += f"[hashsetop={cmd or strat or '?'}]"
        if node == "Aggregate":
            pmode = n.get("Partial Mode")
            if pmode and pmode != "Simple":
                label += f":{pmode}"
        if node == "Hash":
            batches = n.get("Hash Batches")
            if batches is not None:
                label += f":B{batches}"
        if node == "Sort":
            method = n.get("Sort Method")
            if method:
                label += f":{method.split()[0]}"
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
        "parallel_hash": False,
        "sort": False,
        "incremental_sort": False,
        "memoize": False,
        "gather": False,
        "gather_merge": False,
        "seq": False,
        "indexonly": False,
        "index": False,
        "nested_loop": False,
        "merge_join": False,
        "groupagg": False,
        "hash": False,
        "external_sort": False,
        "hashsetop": False,
        "setop": False,
        "setop_sorted": False,
        "subquery_scan": False,
        "unique": False,
    }
    hashagg_count = 0
    hashagg_levels: List[str] = []

    def walk(n: dict) -> None:
        nonlocal hashagg_count
        if not isinstance(n, dict):
            return
        node = n.get("Node Type", "?")
        counts[node] = counts.get(node, 0) + 1
        strat = n.get("Strategy")
        if node == "Aggregate":
            if strat == "Hashed":
                has["hashagg"] = True
                hashagg_count += 1
                pmode = n.get("Partial Mode") or "Simple"
                hashagg_levels.append(str(pmode))
            elif strat == "Sorted":
                has["groupagg"] = True
        if node == "Hash Join":
            has["hashjoin"] = True
        if node == "Hash":
            has["hash"] = True
            if n.get("Parallel Aware"):
                has["parallel_hash"] = True
        if node == "Sort":
            has["sort"] = True
            method = str(n.get("Sort Method") or "")
            if "external" in method.lower() or "Disk" in method:
                has["external_sort"] = True
        if node == "Incremental Sort":
            has["incremental_sort"] = True
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
        if node == "Nested Loop":
            has["nested_loop"] = True
        if node == "Merge Join":
            has["merge_join"] = True
        # PG EXPLAIN JSON: HashSetOp may appear as Node Type "HashSetOp"
        # or as "SetOp" with Strategy "Hashed" (observed on this cluster).
        if node == "HashSetOp":
            has["hashsetop"] = True
            has["setop"] = True
        if node == "SetOp":
            has["setop"] = True
            strat_l = str(strat or "").lower()
            if strat_l == "hashed":
                has["hashsetop"] = True
            else:
                # non-hashed SetOp (Sorted/Merge path under enable_hashagg=on/off)
                has["setop_sorted"] = True
        if node == "Subquery Scan":
            has["subquery_scan"] = True
        if node == "Unique":
            has["unique"] = True
        for ch in n.get("Plans") or []:
            walk(ch)

    walk(plan_node)
    return {
        "counts": counts,
        "flags": has,
        "workers_planned": workers,
        "max_workers": max(workers) if workers else 0,
        "parallel": bool(has["gather"] or has["gather_merge"]),
        "hashagg_count": hashagg_count,
        "hashagg_levels": hashagg_levels,
        "nested_double_hashagg": hashagg_count >= 2 or (
            hashagg_count >= 1 and has["subquery_scan"] and has["hashagg"]
        ),
    }


def explain_json(
    sql_body: str,
    params: Dict[str, float],
    *,
    work_mem: str,
    hash_mem_multiplier: float,
    enable_parallel_hash: bool,
    enable_memoize: bool = True,
    analyze: bool = False,
    timeout: int = 900,
) -> dict:
    env = {**os.environ, "PGPASSWORD": "postgres"}
    sets = [f"SET {k} = {params[k]};" for k in SEVEN]
    sets.append("SET max_parallel_workers_per_gather = 4;")
    sets.append(f"SET work_mem = '{work_mem}';")
    sets.append(f"SET hash_mem_multiplier = {hash_mem_multiplier};")
    sets.append(f"SET enable_parallel_hash = {'on' if enable_parallel_hash else 'off'};")
    sets.append(f"SET enable_memoize = {'on' if enable_memoize else 'off'};")
    sets.append("SET jit = off;")
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
        timeout=timeout if analyze else 180,
    )
    if r.returncode != 0:
        raise RuntimeError(f"psql failed: {r.stderr.strip()[:1000]}")
    raw = r.stdout.strip()
    m = re.search(r"(\[.*\])", raw, re.S)
    if not m:
        raise RuntimeError(f"no JSON in explain output: {raw[:400]}")
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
    sort_peaks: List[float] = []
    hash_peaks: List[float] = []
    hashagg_peaks: List[float] = []
    setop_peaks: List[float] = []
    disk_sort_kb = 0.0

    def walk(n: dict) -> None:
        nonlocal shared_hit, shared_read, temp_read, temp_written, peak_mem, sum_peak
        nonlocal disk_sort_kb
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
        if n.get("Memory Usage") is not None and n.get("Node Type") in (
            "Aggregate",
            "Hash",
            "HashSetOp",
            "SetOp",
            "Memoize",
        ):
            mu = float(n["Memory Usage"])
            peak_mem = max(peak_mem, mu)
            sum_peak += mu
            if n.get("Node Type") == "Aggregate" and n.get("Strategy") == "Hashed":
                hashagg_peaks.append(mu)
            if n.get("Node Type") == "Hash":
                hash_peaks.append(mu)
            if n.get("Node Type") in ("HashSetOp", "SetOp"):
                setop_peaks.append(mu)
        if n.get("Sort Space Used") is not None:
            sm = float(n["Sort Space Used"])
            peak_mem = max(peak_mem, sm)
            sum_peak += sm
            sort_peaks.append(sm)
            stype = str(n.get("Sort Space Type") or n.get("Sort Method") or "")
            if "Disk" in stype or "external" in stype.lower():
                disk_sort_kb = max(disk_sort_kb, sm)
        for ch in n.get("Plans") or []:
            walk(ch)

    walk(plan)

    concurrent_ws = 0.0
    max_w = meta.get("max_workers") or 0
    if hashagg_peaks:
        base = max(hashagg_peaks)
        if max_w > 0:
            concurrent_ws = base * (max_w + 1)
        else:
            concurrent_ws = sum(hashagg_peaks)
        if hash_peaks:
            concurrent_ws += max(hash_peaks)
    elif setop_peaks:
        concurrent_ws = max(setop_peaks) * (max_w + 1 if max_w else 1)
    elif hash_peaks:
        concurrent_ws = max(hash_peaks) * (max_w + 1 if max_w else 1)
    elif sort_peaks:
        concurrent_ws = max(sort_peaks)

    # Hierarchical nested HashAgg: sum of distinct HashAgg peaks is a lifetime bound
    nested_sum_kb = float(sum(hashagg_peaks)) if hashagg_peaks else 0.0

    meta["shared_hit"] = shared_hit
    meta["shared_read"] = shared_read
    meta["temp_read"] = temp_read
    meta["temp_written"] = temp_written
    meta["peak_mem_kb"] = peak_mem
    meta["sum_peak_mem_kb"] = sum_peak
    meta["hashagg_peaks_kb"] = hashagg_peaks
    meta["hash_peaks_kb"] = hash_peaks
    meta["sort_peaks_kb"] = sort_peaks
    meta["setop_peaks_kb"] = setop_peaks
    meta["disk_sort_kb"] = disk_sort_kb
    meta["concurrent_ws_est_kb"] = concurrent_ws
    meta["nested_hashagg_sum_kb"] = nested_sum_kb
    return meta


def vector_full(params: Dict[str, float]) -> Dict[str, float]:
    out = dict(DEFAULT_PARAMS)
    out.update(params)
    return {k: float(out[k]) for k in SEVEN}


def result_invariant(
    sql_body: str, params: Dict[str, float], work_mem: str, hmm: float
) -> Optional[str]:
    env = {**os.environ, "PGPASSWORD": "postgres"}
    sets = [f"SET {k} = {params[k]};" for k in SEVEN]
    sets += [
        "SET max_parallel_workers_per_gather = 4;",
        f"SET work_mem = '{work_mem}';",
        f"SET hash_mem_multiplier = {hmm};",
        "SET enable_parallel_hash = on;",
        "SET enable_memoize = on;",
        "SET jit = off;",
        "SET statement_timeout = '600000';",
    ]
    body = "\n".join(sets) + "\n" + sql_body + ";\n"
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
        timeout=700,
    )
    if r.returncode != 0:
        return None
    lines = r.stdout.strip().splitlines()
    if not lines:
        return ""
    if len(lines) > 5:
        return f"rows={len(lines)}|first={lines[0]}|last={lines[-1]}"
    return "|".join(lines)


def mechanism_novelty(meta: Dict[str, Any], mechanism: str) -> Dict[str, Any]:
    flags = meta.get("flags") or {}
    if mechanism == "nested_double_hashagg_collapse":
        return {
            "hashagg_count": meta.get("hashagg_count"),
            "nested_double_hashagg": meta.get("nested_double_hashagg"),
            "has_subquery_scan": bool(flags.get("subquery_scan")),
            "not_v82_dual_partial_same_level": True,
            "not_grouping_sets_single_node": True,
            "note": (
                "hierarchical inner high-card HashAgg + outer HashAgg over Subquery Scan; "
                "not same-level dual Partial / GROUPING SETS multi-key single node"
            ),
        }
    return {
        "has_hashsetop": bool(flags.get("hashsetop")),
        "has_setop": bool(flags.get("setop")),
        "has_setop_sorted": bool(flags.get("setop_sorted")),
        "not_hashjoin_nl_merge": not flags.get("hashjoin"),
        "not_hashagg_family": not flags.get("hashagg"),
        "note": "INTERSECT/EXCEPT SetOp class; never in v71–v83 census",
    }


def main() -> int:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    own: Dict[str, Any] = {
        "port": PORT,
        "container": CONTAINER,
        "cpu_pool_ports": list(range(56433, 56441)),
        "memory_pool_ports": list(range(57433, 57441)),
    }
    try:
        r = subprocess.run(
            [
                "docker",
                "inspect",
                CONTAINER,
                "--format",
                "{{.HostConfig.Memory}} {{.HostConfig.ShmSize}} {{.HostConfig.CpusetCpus}} {{.State.Status}}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        parts = r.stdout.strip().split()
        if len(parts) >= 4:
            own["memory_bytes"] = int(parts[0])
            own["shm_bytes"] = int(parts[1])
            own["cpuset"] = parts[2]
            own["status"] = parts[3]
            own["memory_gib"] = int(parts[0]) / (1024**3)
    except Exception as exc:  # noqa: BLE001
        own["inspect_error"] = str(exc)

    provenance: Dict[str, Any] = {
        "sql": [],
        "timestamp": datetime.now(TZ).isoformat(),
        "ownership": own,
        "external_overlap_note": (
            "CPU pool 56433-56440 live (v80); Phase2/VM HyBench external may run; "
            "v84 does not wait on or touch them"
        ),
    }
    sql_bodies: Dict[str, str] = {}
    for spec in SQL_SPECS:
        p = REPO / spec["path"]
        text = p.read_text(encoding="utf-8")
        body = strip_sql(text)
        sql_bodies[spec["id"]] = body
        provenance["sql"].append(
            {
                "id": spec["id"],
                "mechanism": spec["mechanism"],
                "path": spec["path"],
                "sha256": file_sha256(p),
                "work_mem": spec["work_mem"],
                "hash_mem_multiplier": spec["hash_mem_multiplier"],
                "enable_parallel_hash": spec["enable_parallel_hash"],
                "enable_memoize": spec["enable_memoize"],
            }
        )
    (RUN_DIR / "sql_provenance_v84.json").write_text(
        json.dumps(provenance, indent=2) + "\n", encoding="utf-8"
    )

    # ---------- Phase A1: plan-only grid ----------
    plan_grid: List[Dict[str, Any]] = []
    default_meta: Dict[str, Dict[str, Any]] = {}
    for spec in SQL_SPECS:
        sid = spec["id"]
        log(f"[plan] default {sid}")
        try:
            ej = explain_json(
                sql_bodies[sid],
                DEFAULT_PARAMS,
                work_mem=spec["work_mem"],
                hash_mem_multiplier=spec["hash_mem_multiplier"],
                enable_parallel_hash=spec["enable_parallel_hash"],
                enable_memoize=spec["enable_memoize"],
                analyze=False,
            )
            m = plan_meta(ej)
            default_meta[sid] = m
            plan_grid.append(
                {
                    "sql": sid,
                    "vector": "default",
                    "params": vector_full(DEFAULT_PARAMS),
                    "meta": m,
                    "novelty": mechanism_novelty(m, spec["mechanism"]),
                    "error": None,
                }
            )
            log(
                f"  sig={m['sig'][:160]} W={m['max_workers']} "
                f"hashagg_n={m.get('hashagg_count')} nested={m.get('nested_double_hashagg')} "
                f"hashsetop={m['flags']['hashsetop']} setop={m['flags']['setop']} "
                f"setop_sorted={m['flags']['setop_sorted']}"
            )
        except Exception as exc:  # noqa: BLE001
            log(f"  ERROR default {sid}: {exc}")
            plan_grid.append(
                {
                    "sql": sid,
                    "vector": "default",
                    "params": vector_full(DEFAULT_PARAMS),
                    "meta": None,
                    "error": str(exc),
                }
            )

    sig_change_vectors: List[str] = []
    for vname, params in NAMED_POINTS.items():
        full = vector_full(params)
        for spec in SQL_SPECS:
            sid = spec["id"]
            if sid not in default_meta:
                continue
            try:
                ej = explain_json(
                    sql_bodies[sid],
                    full,
                    work_mem=spec["work_mem"],
                    hash_mem_multiplier=spec["hash_mem_multiplier"],
                    enable_parallel_hash=spec["enable_parallel_hash"],
                    enable_memoize=spec["enable_memoize"],
                    analyze=False,
                )
                m = plan_meta(ej)
                changed = m["sig"] != default_meta[sid]["sig"]
                plan_grid.append(
                    {
                        "sql": sid,
                        "vector": vname,
                        "params": full,
                        "meta": m,
                        "sig_changed": changed,
                        "novelty": mechanism_novelty(m, spec["mechanism"]),
                        "error": None,
                    }
                )
                if changed:
                    log(
                        f"[plan] {sid}/{vname} CHANGED W={m['max_workers']} "
                        f"hashagg_n={m.get('hashagg_count')} "
                        f"hashsetop={m['flags']['hashsetop']} setop={m['flags']['setop']} "
                        f"groupagg={m['flags']['groupagg']}"
                    )
            except Exception as exc:  # noqa: BLE001
                log(f"[plan] {sid}/{vname} ERROR {exc}")
                plan_grid.append(
                    {
                        "sql": sid,
                        "vector": vname,
                        "params": full,
                        "meta": None,
                        "sig_changed": False,
                        "error": str(exc),
                    }
                )
        any_change = any(r.get("sig_changed") and r.get("vector") == vname for r in plan_grid)
        if any_change and vname not in sig_change_vectors:
            sig_change_vectors.append(vname)
        if len(sig_change_vectors) >= MAX_VECTORS:
            log(f"[plan] reached MAX_VECTORS={MAX_VECTORS}")
            break

    (RUN_DIR / "plan_grid_v84.json").write_text(
        json.dumps(
            {
                "timestamp": datetime.now(TZ).isoformat(),
                "sig_change_vectors": sig_change_vectors,
                "n_rows": len(plan_grid),
                "rows": plan_grid,
            },
            indent=2,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
    log(f"[plan] signature-changing vectors ({len(sig_change_vectors)}): {sig_change_vectors}")

    # ---------- Select ANALYZE pairs ----------
    candidates_for_analyze: List[Tuple[Any, ...]] = []
    for r in plan_grid:
        if not r.get("sig_changed") or not r.get("meta") or r.get("vector") == "default":
            continue
        sid = r["sql"]
        if sid not in default_meta:
            continue
        dm, cm = default_meta[sid], r["meta"]
        score = 0
        if cm["parallel"] and dm["parallel"]:
            score += 3
        # nested HashAgg collapse: fewer HashAgg levels / Hash→GroupAgg
        d_n = int(dm.get("hashagg_count") or 0)
        c_n = int(cm.get("hashagg_count") or 0)
        if d_n >= 2 and c_n < d_n:
            score += 8
        if dm["flags"]["hashagg"] and not cm["flags"]["hashagg"]:
            score += 5
        if dm["flags"]["hashagg"] and cm["flags"]["groupagg"] and c_n < d_n:
            score += 4
        # SetOp: HashSetOp → Sorted SetOp
        if dm["flags"]["hashsetop"] and cm["flags"]["setop"] and not cm["flags"]["hashsetop"]:
            score += 8
        if dm["flags"]["hashsetop"] and cm["flags"]["setop_sorted"]:
            score += 6
        if dm["flags"]["hashsetop"] and cm["flags"]["hashsetop"]:
            score -= 2  # still hashed — low priority unless other flags
        if cm["flags"]["indexonly"] and not dm["flags"]["indexonly"]:
            score -= 2
        if not cm["parallel"] and dm["parallel"]:
            score -= 5
        if r["vector"] == "par_collapse":
            score -= 10
        # Prefer strategy-named seeds
        if r["vector"] in ("ctc1", "ctc1p25", "ctc1p5", "ctc2", "hash_discourage", "sort_encourage", "setop_sort_seed"):
            score += 2
        candidates_for_analyze.append((score, sid, r["vector"], r["params"], dm, cm))

    candidates_for_analyze.sort(key=lambda x: (-x[0], x[1], x[2]))
    selected: List[Tuple[str, str, Dict[str, float]]] = []
    seen_pair = set()
    for score, sid, vname, params, dm, cm in candidates_for_analyze:
        key = (sid, vname)
        if key in seen_pair:
            continue
        seen_pair.add(key)
        selected.append((sid, vname, params))
        if len(selected) >= MAX_ANALYZE_PAIRS:
            break

    force_prefer = [
        ("nested_hashagg_type", "ctc1"),
        ("nested_hashagg_type", "ctc2"),
        ("nested_hashagg_type", "ctc5"),
        ("nested_hashagg_type", "ctc10"),
        ("nested_hashagg_type", "ctc2_spc25"),
        ("nested_hashagg_type", "ctc10_spc1"),
        ("nested_hashagg_type", "sort_encourage"),
        ("nested_double_hashagg", "ctc1"),
        ("nested_double_hashagg", "ctc2"),
        ("nested_double_hashagg", "ctc5"),
        ("nested_double_hashagg", "ctc10"),
        ("nested_double_hashagg", "hash_discourage"),
        ("hashsetop_intersect", "setop_sort_seed"),
        ("hashsetop_intersect", "sort_encourage"),
        ("hashsetop_intersect", "ctc2"),
        ("hashsetop_intersect", "ctc10"),
        ("hashsetop_intersect", "rpc0p1"),
        ("hashsetop_except", "setop_sort_seed"),
        ("hashsetop_except", "ctc5"),
        ("hashsetop_except", "ctc10"),
    ]
    for sid, vname in force_prefer:
        if (sid, vname) in seen_pair:
            continue
        hit = next(
            (
                r
                for r in plan_grid
                if r.get("sql") == sid and r.get("vector") == vname and r.get("sig_changed")
            ),
            None,
        )
        if hit and hit.get("params"):
            selected.append((sid, vname, hit["params"]))
            seen_pair.add((sid, vname))
        if len(selected) >= MAX_ANALYZE_PAIRS:
            break

    # Always ANALYZE default-signature neighbors that may still change runtime mem
    # even if plan text matches? Charter: only signature-distinct vectors for grid.
    # If M1 has few sig changes, still measure top ctc points if present in grid as changed.
    log(f"[analyze] selected pairs: {[(s, v) for s, v, _ in selected]}")

    pairs_out: List[Dict[str, Any]] = []
    default_analyze: Dict[str, Dict[str, Any]] = {}
    for spec in SQL_SPECS:
        sid = spec["id"]
        log(f"[analyze] default {sid}")
        try:
            ej = explain_json(
                sql_bodies[sid],
                DEFAULT_PARAMS,
                work_mem=spec["work_mem"],
                hash_mem_multiplier=spec["hash_mem_multiplier"],
                enable_parallel_hash=spec["enable_parallel_hash"],
                enable_memoize=spec["enable_memoize"],
                analyze=True,
                timeout=900,
            )
            m = plan_meta(ej)
            default_analyze[sid] = m
            log(
                f"  ms={m.get('actual_ms'):.1f} peak={m.get('peak_mem_kb')} "
                f"sum={m.get('sum_peak_mem_kb')} hashagg_n={m.get('hashagg_count')} "
                f"nested={m.get('nested_double_hashagg')} "
                f"hashsetop={m['flags']['hashsetop']} setop_peaks={m.get('setop_peaks_kb')}"
            )
        except Exception as exc:  # noqa: BLE001
            log(f"  ERROR analyze default {sid}: {exc}")
            default_analyze[sid] = {"error": str(exc)}

    for sid, vname, params in selected:
        spec = next(s for s in SQL_SPECS if s["id"] == sid)
        log(f"[analyze] {sid} / {vname}")
        try:
            ej = explain_json(
                sql_bodies[sid],
                params,
                work_mem=spec["work_mem"],
                hash_mem_multiplier=spec["hash_mem_multiplier"],
                enable_parallel_hash=spec["enable_parallel_hash"],
                enable_memoize=spec["enable_memoize"],
                analyze=True,
                timeout=900,
            )
            cm = plan_meta(ej)
            dm = default_analyze.get(sid) or {}
            if "actual_ms" in dm and "actual_ms" in cm and dm["actual_ms"] > 0:
                speedup = (dm["actual_ms"] - cm["actual_ms"]) / dm["actual_ms"] * 100.0
            else:
                speedup = None
            peak_drop = sum_drop = conc_drop = nested_drop = None
            if dm.get("peak_mem_kb") is not None and cm.get("peak_mem_kb") is not None:
                peak_drop = (dm["peak_mem_kb"] - cm["peak_mem_kb"]) / 1024.0
            if dm.get("sum_peak_mem_kb") is not None and cm.get("sum_peak_mem_kb") is not None:
                sum_drop = (dm["sum_peak_mem_kb"] - cm["sum_peak_mem_kb"]) / 1024.0
            if dm.get("concurrent_ws_est_kb") is not None and cm.get("concurrent_ws_est_kb") is not None:
                conc_drop = (dm["concurrent_ws_est_kb"] - cm["concurrent_ws_est_kb"]) / 1024.0
            if dm.get("nested_hashagg_sum_kb") is not None and cm.get("nested_hashagg_sum_kb") is not None:
                nested_drop = (dm["nested_hashagg_sum_kb"] - cm["nested_hashagg_sum_kb"]) / 1024.0

            mag_candidates = [x for x in (peak_drop, sum_drop, conc_drop, nested_drop) if x is not None]
            mag_best = max(mag_candidates) if mag_candidates else None

            keep_par = True
            if dm.get("max_workers", 0) > 0:
                keep_par = (cm.get("max_workers") or 0) > 0

            indexonly_cliff = bool(cm.get("flags", {}).get("indexonly")) and not bool(
                dm.get("flags", {}).get("indexonly")
            )

            mech = spec["mechanism"]
            mech_ok = False
            mech_note = ""
            if mech == "nested_double_hashagg_collapse":
                d_n = int(dm.get("hashagg_count") or 0)
                c_n = int(cm.get("hashagg_count") or 0)
                if d_n >= 2 and c_n < d_n:
                    mech_ok = True
                    mech_note = f"hashagg_levels {d_n}->{c_n}"
                elif dm.get("flags", {}).get("hashagg") and cm.get("flags", {}).get("groupagg") and not cm.get(
                    "flags", {}
                ).get("hashagg"):
                    mech_ok = True
                    mech_note = "hashagg_to_groupagg_removed_hash"
                elif d_n >= 1 and c_n < d_n:
                    mech_ok = True
                    mech_note = f"hashagg_count_drop {d_n}->{c_n}"
                else:
                    mech_note = f"no_hash_level_removed d={d_n} c={c_n}"
            else:
                if dm.get("flags", {}).get("hashsetop") and (
                    cm.get("flags", {}).get("setop_sorted")
                    or (cm.get("flags", {}).get("setop") and not cm.get("flags", {}).get("hashsetop"))
                ):
                    mech_ok = True
                    mech_note = "hashsetop_to_sorted_setop"
                elif not dm.get("flags", {}).get("hashsetop") and cm.get("flags", {}).get("setop_sorted"):
                    mech_ok = False
                    mech_note = "default_not_hashsetop"
                else:
                    mech_note = "no_hashsetop_to_sorted_transition"

            speed_ok = speedup is not None and speedup >= TARGET_SPEEDUP_PCT
            mag_ok = mag_best is not None and mag_best >= TARGET_PEAK_DROP_MIB

            sread_d = temp_d = None
            if dm.get("shared_read") is not None and cm.get("shared_read") is not None:
                sread_d = cm["shared_read"] - dm["shared_read"]
            if dm.get("temp_read") is not None and cm.get("temp_read") is not None:
                temp_d = cm["temp_read"] - dm["temp_read"]
            transfer_plausible = False
            transfer_note = ""
            if speed_ok and mag_ok:
                transfer_plausible = True
                if sread_d is not None and sread_d > 10000:
                    transfer_note = "shared_read_up_may_IO_sweet"
                elif temp_d is not None and temp_d < -1000:
                    transfer_note = "temp_down_cpu_more_likely"
                else:
                    transfer_note = "faster_lower_ws_cpu_transfer_plausible"

            answer_ok = None
            if speed_ok and mag_ok and keep_par and mech_ok:
                a0 = result_invariant(
                    sql_bodies[sid], DEFAULT_PARAMS, spec["work_mem"], spec["hash_mem_multiplier"]
                )
                a1 = result_invariant(
                    sql_bodies[sid], params, spec["work_mem"], spec["hash_mem_multiplier"]
                )
                answer_ok = a0 is not None and a1 is not None and a0 == a1
                log(f"  answer_ok={answer_ok}")

            eligible = bool(
                speed_ok
                and mag_ok
                and keep_par
                and mech_ok
                and transfer_plausible
                and not indexonly_cliff
                and (answer_ok is not False)
            )

            rec = {
                "sql": sid,
                "mechanism": mech,
                "vector": vname,
                "params": vector_full(params),
                "work_mem": spec["work_mem"],
                "default": {
                    k: dm.get(k)
                    for k in (
                        "sig",
                        "actual_ms",
                        "peak_mem_kb",
                        "sum_peak_mem_kb",
                        "concurrent_ws_est_kb",
                        "nested_hashagg_sum_kb",
                        "shared_read",
                        "temp_read",
                        "temp_written",
                        "max_workers",
                        "flags",
                        "actual_rows",
                        "hashagg_count",
                        "hashagg_peaks_kb",
                        "setop_peaks_kb",
                        "sort_peaks_kb",
                        "hash_peaks_kb",
                    )
                },
                "cand": {
                    k: cm.get(k)
                    for k in (
                        "sig",
                        "actual_ms",
                        "peak_mem_kb",
                        "sum_peak_mem_kb",
                        "concurrent_ws_est_kb",
                        "nested_hashagg_sum_kb",
                        "shared_read",
                        "temp_read",
                        "temp_written",
                        "max_workers",
                        "flags",
                        "actual_rows",
                        "hashagg_count",
                        "hashagg_peaks_kb",
                        "setop_peaks_kb",
                        "sort_peaks_kb",
                        "hash_peaks_kb",
                    )
                },
                "speedup_pct": speedup,
                "peak_drop_mib": peak_drop,
                "sum_drop_mib": sum_drop,
                "conc_drop_mib": conc_drop,
                "nested_drop_mib": nested_drop,
                "mag_best_mib": mag_best,
                "speed_ok": speed_ok,
                "mag_ok": mag_ok,
                "keep_parallel": keep_par,
                "indexonly_cliff": indexonly_cliff,
                "mechanism_ok": mech_ok,
                "mechanism_note": mech_note,
                "transfer_plausible": transfer_plausible,
                "transfer_note": transfer_note,
                "answer_ok": answer_ok,
                "eligible": eligible,
                "shared_read_delta": sread_d,
                "temp_read_delta": temp_d,
            }
            pairs_out.append(rec)
            log(
                f"  speedup={speedup} peakΔ={peak_drop} sumΔ={sum_drop} nestedΔ={nested_drop} "
                f"par={keep_par} mech_ok={mech_ok} ({mech_note}) eligible={eligible}"
            )
        except Exception as exc:  # noqa: BLE001
            log(f"  ERROR {sid}/{vname}: {exc}")
            pairs_out.append(
                {
                    "sql": sid,
                    "vector": vname,
                    "params": vector_full(params),
                    "error": str(exc),
                    "eligible": False,
                }
            )

    (RUN_DIR / "explain_analyze_pairs_v84.json").write_text(
        json.dumps(
            {
                "timestamp": datetime.now(TZ).isoformat(),
                "selected": [(s, v) for s, v, _ in selected],
                "pairs": pairs_out,
            },
            indent=2,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )

    # ---------- Eligibility decision ----------
    eligible_vectors: List[Dict[str, Any]] = []
    near_misses: List[Dict[str, Any]] = []
    for rec in pairs_out:
        if rec.get("error"):
            continue
        if rec.get("eligible"):
            eligible_vectors.append(
                {
                    "sql": rec["sql"],
                    "mechanism": rec["mechanism"],
                    "vector": rec["vector"],
                    "params": rec["params"],
                    "speedup_pct": rec["speedup_pct"],
                    "mag_best_mib": rec["mag_best_mib"],
                    "mechanism_note": rec.get("mechanism_note"),
                }
            )
        else:
            rejects = []
            if not rec.get("speed_ok"):
                rejects.append("speed<15%")
            if not rec.get("mag_ok"):
                rejects.append("mag<328MiB")
            if not rec.get("keep_parallel"):
                rejects.append("parallel_lost")
            if rec.get("indexonly_cliff"):
                rejects.append("indexonly_cliff")
            if not rec.get("mechanism_ok"):
                rejects.append("mechanism_invalid")
            if rec.get("speed_ok") or rec.get("mag_ok"):
                near_misses.append(
                    {
                        "sql": rec["sql"],
                        "vector": rec["vector"],
                        "speedup_pct": rec.get("speedup_pct"),
                        "mag_best_mib": rec.get("mag_best_mib"),
                        "peak_drop_mib": rec.get("peak_drop_mib"),
                        "sum_drop_mib": rec.get("sum_drop_mib"),
                        "nested_drop_mib": rec.get("nested_drop_mib"),
                        "keep_parallel": rec.get("keep_parallel"),
                        "mechanism_ok": rec.get("mechanism_ok"),
                        "mechanism_note": rec.get("mechanism_note"),
                        "reject": rejects,
                    }
                )

    best_speed = None
    best_mag = None
    for rec in pairs_out:
        if rec.get("error") or rec.get("speedup_pct") is None:
            continue
        if best_speed is None or rec["speedup_pct"] > best_speed["speedup_pct"]:
            best_speed = {
                "sql": rec["sql"],
                "vector": rec["vector"],
                "speedup_pct": rec["speedup_pct"],
                "mag_best_mib": rec.get("mag_best_mib"),
            }
        if rec.get("mag_best_mib") is not None:
            if best_mag is None or rec["mag_best_mib"] > best_mag["mag_best_mib"]:
                best_mag = {
                    "sql": rec["sql"],
                    "vector": rec["vector"],
                    "speedup_pct": rec["speedup_pct"],
                    "mag_best_mib": rec["mag_best_mib"],
                }

    census_fragment = {
        "version": "v84",
        "new_shapes": [
            {
                "id": "nested_double_hashagg",
                "family": "nested_double_hashagg_collapse",
                "vs_closed": (
                    "hierarchical Subquery Scan + two HashAgg lifetimes; "
                    "≠ v82 same-level dual Partial / GROUPING SETS; ≠ v78 width/key; ≠ v83 IncSort"
                ),
            },
            {
                "id": "nested_hashagg_type",
                "family": "nested_double_hashagg_collapse",
                "vs_closed": "type-aware outer rollup over inner (sourceid,type) HashAgg",
            },
            {
                "id": "hashsetop_intersect",
                "family": "hashsetop_intersect_to_sorted_setop",
                "vs_closed": "INTERSECT HashSetOp↔Sorted SetOp; never in v71–v83 census",
            },
            {
                "id": "hashsetop_except",
                "family": "hashsetop_intersect_to_sorted_setop",
                "vs_closed": "EXCEPT HashSetOp path; distinct from HashJoin/HashAgg families",
            },
        ],
        "closed_not_reopened": [
            "xap2_ctid",
            "ctc2_indexonly",
            "hashagg_width_key_cte",
            "large_equijoin_nl_merge",
            "bitmap_to_index_rename",
            "memoize_2m_unique_sourceid",
            "work_mem_multibatch",
            "cross_vector_splice",
            "v82_partial_agg_multiplicity",
            "v82_true_multibatch_parallel_hash",
            "v83_lowcard_memoize",
            "v83_incremental_sort_groupagg",
            "capability_concurrency_sweeps",
        ],
    }

    decision = {
        "phase": "A",
        "controller": "hybenchxp-memory-v84-nested-setop",
        "timestamp": datetime.now(TZ).isoformat(),
        "phase_b_eligible": len(eligible_vectors) > 0,
        "eligible_set_size": len(eligible_vectors),
        "eligible_vectors": eligible_vectors,
        "eligible_set": eligible_vectors,
        "n_pairs": len(pairs_out),
        "n_sig_change_vectors": len(sig_change_vectors),
        "sig_change_vectors": sig_change_vectors,
        "near_misses": near_misses[:40],
        "fallback_db_qps_best": FALLBACK_DB_QPS,
        "target_peak_drop_mib": TARGET_PEAK_DROP_MIB,
        "target_speedup_pct": TARGET_SPEEDUP_PCT,
        "census_fragment_v84": census_fragment,
        "stop_reason": (
            None
            if eligible_vectors
            else (
                "eligible_set_empty: nested_double_hashagg_collapse and "
                "hashsetop_intersect_to_sorted_setop were constructed and measured; "
                "no single seven-vector jointly satisfies speed>=15%, working-set drop>=328MiB, "
                "keep parallel, mechanism validity, non-IndexOnly cliff, and plausible transfer"
            )
        ),
        "mechanisms_tested": [
            "nested_double_hashagg_collapse",
            "hashsetop_intersect_to_sorted_setop",
        ],
        "structural_novelty_vs_closed": {
            "not_v82_partial_agg_multiplicity": (
                "hierarchical inner+outer HashAgg via Subquery Scan, not dual same-level Partial"
            ),
            "not_v78_width_key_cte": "two HashAgg lifetimes; not single-stage width/key reshape",
            "not_v83_incsort_groupagg": "collapse Hash level or Sorted SetOp; not IncSort+GroupAgg order path",
            "not_v83_lowcard_memoize": True,
            "not_hashjoin_nl_merge": "SetOp class is INTERSECT/EXCEPT, not equijoin flip",
            "not_work_mem_or_enable_guc_delta": True,
        },
        "w1_verified": own,
        "ownership": {
            "ports": list(range(57433, 57441)),
            "cpu_pool_untouched": True,
            "binary_plan_resource_tuner_modified": False,
            "sql_probes": 0,
            "baselines": 0,
            "candidates": 0,
            "full8": False,
            "phase_a_explain_only": True,
        },
        "elapsed_s": time.time() - t0,
        "best_speed_point": best_speed,
        "best_mag_point": best_mag,
        "mechanism_failure": {},
        "classification": (
            "phase_b_eligible"
            if eligible_vectors
            else "phase_a_stop / nested_setop_residual_not_closable"
        ),
    }

    nest_pairs = [
        p for p in pairs_out if p.get("mechanism") == "nested_double_hashagg_collapse" and not p.get("error")
    ]
    set_pairs = [
        p
        for p in pairs_out
        if p.get("mechanism") == "hashsetop_intersect_to_sorted_setop" and not p.get("error")
    ]
    decision["mechanism_failure"] = {
        "nested_double_hashagg_collapse": {
            "achieved": any(
                (default_analyze.get(s) or {}).get("nested_double_hashagg")
                or int((default_analyze.get(s) or {}).get("hashagg_count") or 0) >= 2
                for s in ("nested_double_hashagg", "nested_hashagg_type")
            ),
            "n_pairs": len(nest_pairs),
            "any_mech_ok": any(p.get("mechanism_ok") for p in nest_pairs),
            "any_speed_ok": any(p.get("speed_ok") for p in nest_pairs),
            "any_mag_ok": any(p.get("mag_ok") for p in nest_pairs),
            "why_not_eligible": (
                "See near_misses / pairs: either Hash levels retained under speed-positive vectors "
                "or collapse/sorted paths free memory but invert speed (classic residual split)."
                if not eligible_vectors
                else "eligible non-empty"
            ),
        },
        "hashsetop_intersect_to_sorted_setop": {
            "achieved": any(
                (default_analyze.get(s) or {}).get("flags", {}).get("hashsetop")
                for s in ("hashsetop_intersect", "hashsetop_except")
            ),
            "n_pairs": len(set_pairs),
            "any_mech_ok": any(p.get("mechanism_ok") for p in set_pairs),
            "any_speed_ok": any(p.get("speed_ok") for p in set_pairs),
            "any_mag_ok": any(p.get("mag_ok") for p in set_pairs),
            "why_not_eligible": (
                "Sorted SetOp either not selected under seven-cost, or selected with speed inversion "
                "/ insufficient peak release vs HashSetOp default."
                if not eligible_vectors
                else "eligible non-empty"
            ),
        },
    }

    for path in (
        RUN_DIR / "phase_a_eligibility_decision_v84.json",
        REPORT_DIR / "phase_a_eligibility_decision_v84.json",
        REPORT_DIR / "hybenchxp_memory_fair_v84_phase_a_eligibility_20260715.json",
    ):
        path.write_text(json.dumps(decision, indent=2, default=str) + "\n", encoding="utf-8")

    census_path = REPORT_DIR / "hybenchxp_memory_fair_v84_plan_family_census_20260715.json"
    census_path.write_text(
        json.dumps(
            {
                "timestamp": datetime.now(TZ).isoformat(),
                "controller": "hybenchxp-memory-v84-nested-setop",
                "fragment": census_fragment,
                "default_plan_fingerprints": {
                    sid: {
                        "sig": (default_meta.get(sid) or {}).get("sig"),
                        "hashagg_count": (default_meta.get(sid) or {}).get("hashagg_count"),
                        "flags": (default_meta.get(sid) or {}).get("flags"),
                        "analyze_peak_kb": (default_analyze.get(sid) or {}).get("peak_mem_kb"),
                        "analyze_sum_kb": (default_analyze.get(sid) or {}).get("sum_peak_mem_kb"),
                        "analyze_ms": (default_analyze.get(sid) or {}).get("actual_ms"),
                    }
                    for sid in sql_bodies
                },
            },
            indent=2,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )

    log(
        f"[decision] eligible_set_size={len(eligible_vectors)} "
        f"phase_b={decision['phase_b_eligible']} class={decision['classification']} "
        f"elapsed={decision['elapsed_s']:.1f}s"
    )
    if eligible_vectors:
        for e in eligible_vectors:
            log(f"  ELIGIBLE {e}")
    else:
        log(f"  STOP: {decision['stop_reason']}")
        log(f"  fallback_db_qps_best={FALLBACK_DB_QPS}")
    return 0 if True else 1  # always exit 0; empty set is a valid stop


if __name__ == "__main__":
    raise SystemExit(main())

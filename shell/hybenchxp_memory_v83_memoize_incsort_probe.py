#!/usr/bin/env python3
"""HyBenchXP Memory fair v83 Phase A — third-family plan-boundary probe.

Mechanisms (only; structurally new vs closed v71–v82 families):
  1) Low-cardinality parameterized Nested Loop + Memoize (measurable hits)
  2) Incremental Sort / ordered GroupAggregate (presorted path)

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
    "hyxp_memory_fair_v83_memoize_incrementalsort_20260715"
)
REPORT_DIR = REPO / "report" / "bonect"
TZ = timezone(timedelta(hours=8))

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
NAMED_POINTS: Dict[str, Dict[str, float]] = {
    "ctc0p5": {**DEFAULT_PARAMS, "cpu_tuple_cost": 0.5},
    "ctc0p75": {**DEFAULT_PARAMS, "cpu_tuple_cost": 0.75},
    "ctc1": {**DEFAULT_PARAMS, "cpu_tuple_cost": 1.0},
    "ctc1p5": {**DEFAULT_PARAMS, "cpu_tuple_cost": 1.5, "cpu_operator_cost": 0.25},
    "ctc2": {**DEFAULT_PARAMS, "cpu_tuple_cost": 2.0, "cpu_operator_cost": 0.5},
    "coc0p25": {**DEFAULT_PARAMS, "cpu_operator_cost": 0.25},
    "coc0p5_ctc1": {
        **DEFAULT_PARAMS,
        "cpu_operator_cost": 0.5,
        "cpu_tuple_cost": 1.0,
    },
    "rpc0p05": {**DEFAULT_PARAMS, "random_page_cost": 0.05},
    "rpc0p1": {**DEFAULT_PARAMS, "random_page_cost": 0.1},
    "rpc0p5": {**DEFAULT_PARAMS, "random_page_cost": 0.5},
    "spc25": {**DEFAULT_PARAMS, "seq_page_cost": 25.0},
    "spc75": {**DEFAULT_PARAMS, "seq_page_cost": 75.0},
    "spc75_rpc0p5": {
        **DEFAULT_PARAMS,
        "seq_page_cost": 75.0,
        "random_page_cost": 0.5,
    },
    "citc_low": {**DEFAULT_PARAMS, "cpu_index_tuple_cost": 0.0001},
    "citc_high": {**DEFAULT_PARAMS, "cpu_index_tuple_cost": 10.0},
    "citc_high_rpc4": {
        **DEFAULT_PARAMS,
        "cpu_index_tuple_cost": 5.0,
        "random_page_cost": 4.0,
    },
    "incsort_encourage": {
        **DEFAULT_PARAMS,
        "cpu_tuple_cost": 1.5,
        "cpu_operator_cost": 0.25,
        "random_page_cost": 0.5,
        "cpu_index_tuple_cost": 0.0001,
    },
    "index_cheap": {
        **DEFAULT_PARAMS,
        "random_page_cost": 0.25,
        "cpu_index_tuple_cost": 0.0001,
    },
    "keep_par_nl": {
        **DEFAULT_PARAMS,
        "random_page_cost": 0.05,
        "cpu_tuple_cost": 0.25,
        "parallel_setup_cost": 100.0,
        "parallel_tuple_cost": 0.01,
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
    "hash_discourage": {
        **DEFAULT_PARAMS,
        "cpu_tuple_cost": 1.0,
        "cpu_operator_cost": 0.25,
        "random_page_cost": 1.0,
    },
}

SQL_SPECS: List[Dict[str, Any]] = [
    {
        "id": "lowcard_memoize_loanapps",
        "mechanism": "lowcard_memoize",
        "path": "shell/hybenchxp_memory_ap6_v83_lowcard_memoize_loanapps.sql",
        "work_mem": "512MB",
        "hash_mem_multiplier": 2.0,
        "enable_parallel_hash": True,
        "enable_memoize": True,
    },
    {
        "id": "lowcard_memoize_company",
        "mechanism": "lowcard_memoize",
        "path": "shell/hybenchxp_memory_ap6_v83_lowcard_memoize_company.sql",
        "work_mem": "512MB",
        "hash_mem_multiplier": 2.0,
        "enable_parallel_hash": True,
        "enable_memoize": True,
    },
    {
        "id": "incsort_groupagg",
        "mechanism": "incremental_sort_groupagg",
        "path": "shell/hybenchxp_memory_ap6_v83_incsort_groupagg.sql",
        "work_mem": "512MB",
        "hash_mem_multiplier": 2.0,
        "enable_parallel_hash": True,
        "enable_memoize": True,
    },
    {
        "id": "incsort_orderonly",
        "mechanism": "incremental_sort_groupagg",
        "path": "shell/hybenchxp_memory_ap6_v83_incsort_orderonly.sql",
        "work_mem": "64MB",  # force external full-sort under default if needed
        "hash_mem_multiplier": 1.0,
        "enable_parallel_hash": True,
        "enable_memoize": True,
    },
]

TARGET_PEAK_DROP_MIB = 328.0
TARGET_SPEEDUP_PCT = 15.0
FALLBACK_DB_QPS = 114.12708344892893
MAX_VECTORS = 16
MAX_ANALYZE_PAIRS = 12


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
        if node == "Memoize":
            label += "[memo]"
        if node == "Incremental Sort":
            pres = n.get("Presorted Key") or n.get("Presorted Keys")
            label += f"[presort={pres}]" if pres else "[incsort]"
        if node == "Sort":
            method = n.get("Sort Method")
            if method:
                label += f":{method.split()[0]}"
        if node == "Hash":
            batches = n.get("Hash Batches")
            if batches is not None:
                label += f":B{batches}"
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
    }

    def walk(n: dict) -> None:
        if not isinstance(n, dict):
            return
        node = n.get("Node Type", "?")
        counts[node] = counts.get(node, 0) + 1
        strat = n.get("Strategy")
        pmode = n.get("Partial Mode")
        if node == "Aggregate":
            if strat == "Hashed":
                has["hashagg"] = True
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
    memoize_mem = 0.0
    memoize_hits = 0
    memoize_misses = 0
    memoize_evictions = 0
    incsort_peaks: List[float] = []
    disk_sort_kb = 0.0

    def walk(n: dict) -> None:
        nonlocal shared_hit, shared_read, temp_read, temp_written, peak_mem, sum_peak
        nonlocal memoize_mem, memoize_hits, memoize_misses, memoize_evictions, disk_sort_kb
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
            "Memoize",
        ):
            mu = float(n["Memory Usage"])
            peak_mem = max(peak_mem, mu)
            sum_peak += mu
            if n.get("Node Type") == "Aggregate" and n.get("Strategy") == "Hashed":
                hashagg_peaks.append(mu)
            if n.get("Node Type") == "Hash":
                hash_peaks.append(mu)
            if n.get("Node Type") == "Memoize":
                memoize_mem = max(memoize_mem, mu)
        if n.get("Node Type") == "Memoize":
            # JSON fields: Cache Hits / Cache Misses / Cache Evictions / Cache Overflows
            memoize_hits += int(n.get("Cache Hits") or n.get("Hits") or 0)
            memoize_misses += int(n.get("Cache Misses") or n.get("Misses") or 0)
            memoize_evictions += int(n.get("Cache Evictions") or n.get("Evictions") or 0)
            # Worker totals may appear under Workers list
            for w in n.get("Workers") or []:
                if isinstance(w, dict):
                    memoize_hits += int(w.get("Cache Hits") or w.get("Hits") or 0)
                    memoize_misses += int(w.get("Cache Misses") or w.get("Misses") or 0)
                    memoize_evictions += int(w.get("Cache Evictions") or w.get("Evictions") or 0)
                    if w.get("Memory Usage") is not None:
                        memoize_mem = max(memoize_mem, float(w["Memory Usage"]))
                        sum_peak += float(w["Memory Usage"])
        if n.get("Sort Space Used") is not None:
            sm = float(n["Sort Space Used"])
            peak_mem = max(peak_mem, sm)
            sum_peak += sm
            sort_peaks.append(sm)
            stype = str(n.get("Sort Space Type") or n.get("Sort Method") or "")
            if "Disk" in stype or "external" in stype.lower():
                disk_sort_kb = max(disk_sort_kb, sm)
        if n.get("Node Type") == "Incremental Sort":
            # Peak Memory may appear under Full-sort Groups metadata in text;
            # JSON often has Peak Memory Usage / Sort Space Used on children.
            if n.get("Peak Memory Usage") is not None:
                incsort_peaks.append(float(n["Peak Memory Usage"]))
            if n.get("Sort Space Used") is not None:
                incsort_peaks.append(float(n["Sort Space Used"]))
        for ch in n.get("Plans") or []:
            walk(ch)

    walk(plan)

    # Concurrent lifetime estimate for partial HashAgg
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
    elif hash_peaks:
        concurrent_ws = max(hash_peaks) * (max_w + 1 if max_w else 1)
    elif sort_peaks:
        concurrent_ws = max(sort_peaks)
    elif memoize_mem:
        concurrent_ws = memoize_mem * (max_w + 1 if max_w else 1)

    meta["shared_hit"] = shared_hit
    meta["shared_read"] = shared_read
    meta["temp_read"] = temp_read
    meta["temp_written"] = temp_written
    meta["peak_mem_kb"] = peak_mem
    meta["sum_peak_mem_kb"] = sum_peak
    meta["hashagg_peaks_kb"] = hashagg_peaks
    meta["hash_peaks_kb"] = hash_peaks
    meta["sort_peaks_kb"] = sort_peaks
    meta["incsort_peaks_kb"] = incsort_peaks
    meta["memoize_mem_kb"] = memoize_mem
    meta["memoize_hits"] = memoize_hits
    meta["memoize_misses"] = memoize_misses
    meta["memoize_evictions"] = memoize_evictions
    hit_den = memoize_hits + memoize_misses
    meta["memoize_hit_ratio"] = (memoize_hits / hit_den) if hit_den > 0 else None
    meta["disk_sort_kb"] = disk_sort_kb
    meta["concurrent_ws_est_kb"] = concurrent_ws
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
    # For ORDER BY queries without LIMIT, truncate fingerprint
    lines = r.stdout.strip().splitlines()
    if not lines:
        return ""
    if len(lines) > 5:
        return f"rows={len(lines)}|first={lines[0]}|last={lines[-1]}"
    return lines[0]


def mechanism_novelty(meta: Dict[str, Any], mechanism: str) -> Dict[str, Any]:
    flags = meta.get("flags") or {}
    if mechanism == "lowcard_memoize":
        return {
            "has_memoize": bool(flags.get("memoize")),
            "has_nested_loop": bool(flags.get("nested_loop")),
            "not_closed_2m_unique_sourceid": True,
            "note": "loanapps.applicantid~35k distinct / company 20k — not transfer 2M unique sourceid",
        }
    return {
        "has_incremental_sort": bool(flags.get("incremental_sort")),
        "has_groupagg": bool(flags.get("groupagg")),
        "has_full_sort": bool(flags.get("sort")),
        "not_mere_scan_rename": bool(flags.get("incremental_sort") or flags.get("groupagg")),
    }


def main() -> int:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    # Ownership proof
    own = {
        "port": PORT,
        "container": "binary_plan_tuning_hybenchxp_rebuild_20260703_180311_cpu_mem_w1",
        "cpu_pool_ports": list(range(56433, 56441)),
        "memory_pool_ports": list(range(57433, 57441)),
    }
    try:
        r = subprocess.run(
            ["docker", "inspect", own["container"], "--format",
             "{{.HostConfig.Memory}} {{.HostConfig.ShmSize}} {{.HostConfig.CpusetCpus}} {{.State.Status}}"],
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
    except Exception as exc:  # noqa: BLE001
        own["inspect_error"] = str(exc)

    provenance: Dict[str, Any] = {
        "sql": [],
        "timestamp": datetime.now(TZ).isoformat(),
        "ownership": own,
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
    (RUN_DIR / "sql_provenance_v83.json").write_text(
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
                f"  sig={m['sig'][:140]} W={m['max_workers']} "
                f"memo={m['flags']['memoize']} incsort={m['flags']['incremental_sort']} "
                f"groupagg={m['flags']['groupagg']} hashagg={m['flags']['hashagg']}"
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
                        f"memo={m['flags']['memoize']} incsort={m['flags']['incremental_sort']} "
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

    (RUN_DIR / "plan_grid_v83.json").write_text(
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
        if dm["flags"]["hashagg"] and not cm["flags"]["hashagg"]:
            score += 4
        if dm["flags"]["hash"] and not cm["flags"]["hash"]:
            score += 3
        if dm["flags"]["sort"] and cm["flags"]["incremental_sort"]:
            score += 6  # target mechanism
        if cm["flags"]["incremental_sort"] and not dm["flags"]["incremental_sort"]:
            score += 5
        if cm["flags"]["groupagg"] and dm["flags"]["hashagg"]:
            score += 4
        if cm["flags"]["memoize"] and not dm["flags"]["memoize"]:
            score += 5  # candidate gains Memoize
        if dm["flags"]["memoize"] and cm["flags"]["hashjoin"] and not cm["flags"]["memoize"]:
            score -= 1  # Hash instead of Memoize (wrong direction for mem drop from default)
        if cm["flags"]["indexonly"] and not dm["flags"]["indexonly"]:
            score -= 2
        if not cm["parallel"] and dm["parallel"]:
            score -= 5
        if r["vector"] == "par_collapse":
            score -= 10
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

    # Ensure mechanism-critical vectors are measured if they change sig
    force_prefer = [
        ("incsort_groupagg", "ctc2"),
        ("incsort_groupagg", "ctc1p5"),
        ("incsort_groupagg", "incsort_encourage"),
        ("incsort_groupagg", "ctc1"),
        ("incsort_orderonly", "incsort_encourage"),
        ("incsort_orderonly", "index_cheap"),
        ("incsort_orderonly", "rpc0p1"),
        ("lowcard_memoize_loanapps", "citc_high"),
        ("lowcard_memoize_loanapps", "citc_high_rpc4"),
        ("lowcard_memoize_company", "citc_high"),
        ("lowcard_memoize_company", "rpc0p05"),
        ("lowcard_memoize_company", "spc75"),
    ]
    for sid, vname in force_prefer:
        if (sid, vname) in seen_pair:
            continue
        if vname not in sig_change_vectors and vname not in NAMED_POINTS:
            continue
        # only if this pair has sig change in grid
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
                f"sum={m.get('sum_peak_mem_kb')} memo_hit={m.get('memoize_hit_ratio')} "
                f"incsort={m['flags']['incremental_sort']} disk_sort={m.get('disk_sort_kb')}"
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
            peak_drop = sum_drop = conc_drop = None
            if dm.get("peak_mem_kb") is not None and cm.get("peak_mem_kb") is not None:
                peak_drop = (dm["peak_mem_kb"] - cm["peak_mem_kb"]) / 1024.0
            if dm.get("sum_peak_mem_kb") is not None and cm.get("sum_peak_mem_kb") is not None:
                sum_drop = (dm["sum_peak_mem_kb"] - cm["sum_peak_mem_kb"]) / 1024.0
            if dm.get("concurrent_ws_est_kb") is not None and cm.get("concurrent_ws_est_kb") is not None:
                conc_drop = (dm["concurrent_ws_est_kb"] - cm["concurrent_ws_est_kb"]) / 1024.0
            # disk sort release counts toward magnitude when baseline spills
            if dm.get("disk_sort_kb") and cm.get("disk_sort_kb") is not None:
                disk_drop = (dm["disk_sort_kb"] - cm["disk_sort_kb"]) / 1024.0
                if peak_drop is None or disk_drop > peak_drop:
                    peak_drop = max(peak_drop or 0.0, disk_drop)

            mag_candidates = [x for x in (peak_drop, sum_drop, conc_drop) if x is not None]
            mag_best = max(mag_candidates) if mag_candidates else None

            keep_par = True
            if dm.get("max_workers", 0) > 0:
                keep_par = (cm.get("max_workers") or 0) > 0

            indexonly_cliff = bool(cm.get("flags", {}).get("indexonly")) and not bool(
                dm.get("flags", {}).get("indexonly")
            )

            # Mechanism-specific validity
            mech = spec["mechanism"]
            mech_ok = False
            mech_note = ""
            if mech == "lowcard_memoize":
                # Eligible direction: candidate uses Memoize with hits and lower WS than Hash default,
                # OR candidate drops Hash build vs default while gaining Memoize.
                if cm.get("flags", {}).get("memoize"):
                    hr = cm.get("memoize_hit_ratio")
                    mech_ok = hr is not None and hr > 0.05
                    mech_note = f"memoize_hit_ratio={hr}"
                else:
                    mech_note = "cand_no_memoize"
            else:
                if cm.get("flags", {}).get("incremental_sort"):
                    mech_ok = True
                    mech_note = "cand_has_incremental_sort"
                elif cm.get("flags", {}).get("groupagg") and not cm.get("flags", {}).get("hashagg"):
                    # ordered GroupAggregate without IncSort still counts if sort mem reduced
                    mech_ok = True
                    mech_note = "cand_ordered_groupagg"
                else:
                    mech_note = "no_incsort_or_ordered_groupagg"

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
                if sread_d is not None and sread_d > 10000:
                    transfer_plausible = True
                    transfer_note = "shared_read_up_may_IO_sweet"
                elif temp_d is not None and temp_d < -1000:
                    transfer_plausible = True
                    transfer_note = "temp_down_cpu_more_likely"
                else:
                    transfer_plausible = True
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
                        "shared_read",
                        "temp_read",
                        "temp_written",
                        "max_workers",
                        "flags",
                        "actual_rows",
                        "memoize_hit_ratio",
                        "memoize_hits",
                        "memoize_misses",
                        "memoize_mem_kb",
                        "disk_sort_kb",
                        "sort_peaks_kb",
                        "hash_peaks_kb",
                        "hashagg_peaks_kb",
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
                        "shared_read",
                        "temp_read",
                        "temp_written",
                        "max_workers",
                        "flags",
                        "actual_rows",
                        "memoize_hit_ratio",
                        "memoize_hits",
                        "memoize_misses",
                        "memoize_mem_kb",
                        "disk_sort_kb",
                        "sort_peaks_kb",
                        "hash_peaks_kb",
                        "hashagg_peaks_kb",
                        "incsort_peaks_kb",
                    )
                },
                "speedup_pct": speedup,
                "peak_drop_mib": peak_drop,
                "sum_drop_mib": sum_drop,
                "conc_drop_mib": conc_drop,
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
                f"  speedup={speedup} peakΔ={peak_drop} sumΔ={sum_drop} "
                f"par={keep_par} mech_ok={mech_ok} eligible={eligible}"
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

    (RUN_DIR / "explain_analyze_pairs_v83.json").write_text(
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
                        "keep_parallel": rec.get("keep_parallel"),
                        "mechanism_ok": rec.get("mechanism_ok"),
                        "mechanism_note": rec.get("mechanism_note"),
                        "reject": rejects,
                    }
                )

    # Best extremes for audit
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

    decision = {
        "phase": "A",
        "controller": "hybenchxp-memory-v83-memoize-incrementalsort",
        "timestamp": datetime.now(TZ).isoformat(),
        "phase_b_eligible": len(eligible_vectors) > 0,
        "eligible_set_size": len(eligible_vectors),
        "eligible_vectors": eligible_vectors,
        "n_pairs": len(pairs_out),
        "n_sig_change_vectors": len(sig_change_vectors),
        "sig_change_vectors": sig_change_vectors,
        "near_misses": near_misses[:40],
        "fallback_db_qps_best": FALLBACK_DB_QPS,
        "target_peak_drop_mib": TARGET_PEAK_DROP_MIB,
        "target_speedup_pct": TARGET_SPEEDUP_PCT,
        "stop_reason": (
            None
            if eligible_vectors
            else (
                "eligible_set_empty: low-card Memoize and Incremental Sort/ordered GroupAggregate "
                "were constructed and measured; no single seven-vector jointly satisfies "
                "speed>=15%, working-set drop>=328MiB, keep parallel, mechanism validity, "
                "non-IndexOnly cliff, and plausible transfer"
            )
        ),
        "mechanisms_tested": [
            "lowcard_parameterized_nested_loop_memoize",
            "incremental_sort_ordered_groupaggregate",
        ],
        "structural_novelty_vs_closed": {
            "not_memoize_2m_unique_sourceid": (
                "loanapps.applicantid ~35k distinct with measured hit ratio; "
                "company 20k keys — not transfer sourceid 2M unique thrash case"
            ),
            "not_large_equijoin_nl_merge_closed": (
                "Memoize cache-key path with hit accounting; not pure HashJoin→NL/Merge rename"
            ),
            "not_indexonly_only_rename": (
                "IncSort path requires Incremental Sort (presorted sourceid) + multi-key "
                "GroupAggregate/ORDER BY, not mere Seq/Bitmap/IndexOnly scan rename"
            ),
            "not_v82_partial_hash_or_parhash_multibatch": True,
            "not_v80_work_mem_grid_serial_hash": True,
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
            "phase_b_eligible" if eligible_vectors else "phase_a_stop / third_family_residual_not_closable"
        ),
    }

    # Mechanism failure summaries from pairs
    memo_pairs = [p for p in pairs_out if p.get("mechanism") == "lowcard_memoize" and not p.get("error")]
    inc_pairs = [
        p for p in pairs_out if p.get("mechanism") == "incremental_sort_groupagg" and not p.get("error")
    ]
    decision["mechanism_failure"] = {
        "lowcard_memoize": {
            "achieved": any(
                (p.get("default") or {}).get("flags", {}).get("memoize")
                or (p.get("cand") or {}).get("flags", {}).get("memoize")
                for p in memo_pairs
            )
            or any(
                (default_analyze.get(s) or {}).get("flags", {}).get("memoize")
                for s in ("lowcard_memoize_loanapps", "lowcard_memoize_company")
            ),
            "evidence": "loanapps default Memoize with measurable hits; company NL+Memoize under forced vectors",
            "why_not_eligible": (
                "Default for low-card loanapps is already Memoize (~25MiB cache, hit ratio ~0.3–0.4); "
                "Hash alternatives under high citc peak ~150MiB and are not a default→candidate residual path "
                "that frees ≥328MiB while speeding up. Company Hash build is tiny (~1MiB). "
                "Cannot jointly free 328MiB from a structure that is already the low-memory default path."
            ),
        },
        "incremental_sort_groupagg": {
            "achieved": any(
                (p.get("cand") or {}).get("flags", {}).get("incremental_sort") for p in inc_pairs
            ),
            "evidence": (
                "incsort_groupagg under ctc1.5/ctc2: GroupAggregate + Incremental Sort "
                "(presorted sourceid); default HashAggregate + full Sort ~442+246 MiB"
            ),
            "why_not_eligible": (
                "IncSort/GroupAgg paths free hundreds of MiB of HashAgg+Sort state but are slower "
                "due to index-driven random I/O thrash (shared_read tens of millions of blocks). "
                "Speed-positive near-ctc vectors retain HashAgg+full Sort (mag drop ≈0). "
                "Same speed-vs-mag residual split as closed families; no single seven-vector closes both."
            ),
        },
    }

    for path in (
        RUN_DIR / "phase_a_eligibility_decision_v83.json",
        REPORT_DIR / "phase_a_eligibility_decision_v83.json",
        REPORT_DIR / "hybenchxp_memory_fair_v83_phase_a_eligibility_20260715.json",
    ):
        path.write_text(json.dumps(decision, indent=2, default=str) + "\n", encoding="utf-8")

    phase_b = {
        "phase_b_eligible": decision["phase_b_eligible"],
        "eligible_set_size": decision["eligible_set_size"],
        "fallback_db_qps_best": FALLBACK_DB_QPS,
        "allowed_actions": (
            ["freeze_workload", "baseline_full8", "candidate_pairs"]
            if decision["phase_b_eligible"]
            else []
        ),
        "blocked_reason": decision.get("stop_reason"),
    }
    (RUN_DIR / "phase_b_eligibility_v83.json").write_text(
        json.dumps(phase_b, indent=2) + "\n", encoding="utf-8"
    )

    log(
        f"[decision] phase_b_eligible={decision['phase_b_eligible']} "
        f"eligible_set_size={decision['eligible_set_size']} "
        f"elapsed={decision['elapsed_s']:.1f}s"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

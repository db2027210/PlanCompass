#!/usr/bin/env python3
"""HyBenchXP Memory fair v82 Phase A — new-family plan-boundary probe.

Mechanisms (only):
  1) Parallel partial-aggregate multiplicity
  2) True multi-batch Parallel Hash with enable_parallel_hash=on

Offline EXPLAIN / EXPLAIN ANALYZE on rebuild w1 (57433) only.
Does not start tuners, does not edit binary_plan_resource_tuner.py.
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
from typing import Any, Dict, List, Optional, Sequence, Tuple


REPO = Path("/home/anonymous/program/resource_adaptive")
HOST = "127.0.0.1"
PORT = 57433
USER = "postgres"
DB = "hybench_sf10x"
RUN_DIR = Path(
    "/disk2/resource_adaptive/binary_plan_tuning_hybenchxp/runs/"
    "hyxp_memory_fair_v82_new_family_plan_boundary_20260714"
)
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

# Prefer residual-relevant named points; hard cap 16 signature-changing vectors
# (selected after plan-only scan by distinct default→cand signatures).
NAMED_POINTS: Dict[str, Dict[str, float]] = {
    "ctc0p5": {**DEFAULT_PARAMS, "cpu_tuple_cost": 0.5},
    "ctc1": {**DEFAULT_PARAMS, "cpu_tuple_cost": 1.0},
    "ctc2": {**DEFAULT_PARAMS, "cpu_tuple_cost": 2.0},
    "coc0p25": {**DEFAULT_PARAMS, "cpu_operator_cost": 0.25},
    "coc0p5_ctc1": {
        **DEFAULT_PARAMS,
        "cpu_operator_cost": 0.5,
        "cpu_tuple_cost": 1.0,
    },
    "rpc0p05": {**DEFAULT_PARAMS, "random_page_cost": 0.05},
    "rpc0p5": {**DEFAULT_PARAMS, "random_page_cost": 0.5},
    "rpc1": {**DEFAULT_PARAMS, "random_page_cost": 1.0},
    "spc5": {**DEFAULT_PARAMS, "seq_page_cost": 5.0},
    "spc25": {**DEFAULT_PARAMS, "seq_page_cost": 25.0},
    "spc75": {**DEFAULT_PARAMS, "seq_page_cost": 75.0},
    "spc75_rpc0p5": {
        **DEFAULT_PARAMS,
        "seq_page_cost": 75.0,
        "random_page_cost": 0.5,
    },
    "citc_low": {**DEFAULT_PARAMS, "cpu_index_tuple_cost": 0.0001},
    "hash_discourage": {
        **DEFAULT_PARAMS,
        "cpu_tuple_cost": 1.0,
        "cpu_operator_cost": 0.25,
        "random_page_cost": 1.0,
    },
    "merge_encourage": {
        **DEFAULT_PARAMS,
        "cpu_tuple_cost": 0.75,
        "cpu_operator_cost": 0.2,
        "random_page_cost": 1.5,
        "seq_page_cost": 1.5,
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
    "index_cheap_hash_exp": {
        **DEFAULT_PARAMS,
        "random_page_cost": 0.25,
        "cpu_index_tuple_cost": 0.0001,
        "cpu_tuple_cost": 1.0,
        "cpu_operator_cost": 0.5,
    },
}

# Fixed construction (identical both sides). Multi-batch family needs low work_mem.
SQL_SPECS: List[Dict[str, Any]] = [
    {
        "id": "dual_partial_hashagg",
        "mechanism": "partial_agg_multiplicity",
        "path": "shell/hybenchxp_memory_ap6_v82_dual_partial_hashagg.sql",
        "work_mem": "512MB",
        "hash_mem_multiplier": 2.0,
        "enable_parallel_hash": True,
    },
    {
        "id": "groupingsets_multihash",
        "mechanism": "partial_agg_multiplicity",
        "path": "shell/hybenchxp_memory_ap6_v82_groupingsets_multihash.sql",
        "work_mem": "512MB",
        "hash_mem_multiplier": 2.0,
        "enable_parallel_hash": True,
    },
    {
        "id": "parhash_multibatch",
        "mechanism": "true_multibatch_parallel_hash",
        "path": "shell/hybenchxp_memory_ap6_v82_parhash_multibatch.sql",
        "work_mem": "16MB",
        "hash_mem_multiplier": 1.0,
        "enable_parallel_hash": True,
    },
    {
        "id": "parhash_wide_build",
        "mechanism": "true_multibatch_parallel_hash",
        "path": "shell/hybenchxp_memory_ap6_v82_parhash_wide_build.sql",
        "work_mem": "16MB",
        "hash_mem_multiplier": 1.0,
        "enable_parallel_hash": True,
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
    }
    partial_hashagg = 0
    finalize_hashagg = 0
    hash_batches_max = 0
    parallel_aware_hash = 0

    def walk(n: dict) -> None:
        nonlocal partial_hashagg, finalize_hashagg, hash_batches_max, parallel_aware_hash
        if not isinstance(n, dict):
            return
        node = n.get("Node Type", "?")
        counts[node] = counts.get(node, 0) + 1
        strat = n.get("Strategy")
        partial = bool(n.get("Partial Mode") == "Partial" or "Partial" in str(n.get("Partial Mode") or ""))
        # EXPLAIN JSON uses "Partial Mode": "Partial" / "Finalize" on Aggregate
        pmode = n.get("Partial Mode")
        if node == "Aggregate":
            if strat == "Hashed":
                has["hashagg"] = True
                if pmode == "Partial":
                    partial_hashagg += 1
                elif pmode == "Finalize":
                    finalize_hashagg += 1
            elif strat == "Sorted":
                has["groupagg"] = True
        if node == "Hash Join":
            has["hashjoin"] = True
        if node == "Hash":
            has["hash"] = True
            if n.get("Hash Batches") is not None:
                hash_batches_max = max(hash_batches_max, int(n["Hash Batches"]))
            if n.get("Parallel Aware"):
                has["parallel_hash"] = True
                parallel_aware_hash += 1
        if node == "Sort":
            has["sort"] = True
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
        "partial_hashagg_nodes": partial_hashagg,
        "finalize_hashagg_nodes": finalize_hashagg,
        "hash_batches_max": hash_batches_max,
        "parallel_aware_hash_nodes": parallel_aware_hash,
    }


def explain_json(
    sql_body: str,
    params: Dict[str, float],
    *,
    work_mem: str,
    hash_mem_multiplier: float,
    enable_parallel_hash: bool,
    analyze: bool = False,
    timeout: int = 900,
) -> dict:
    env = {**os.environ, "PGPASSWORD": "postgres"}
    sets = [f"SET {k} = {params[k]};" for k in SEVEN]
    sets.append("SET max_parallel_workers_per_gather = 4;")
    sets.append(f"SET work_mem = '{work_mem}';")
    sets.append(f"SET hash_mem_multiplier = {hash_mem_multiplier};")
    sets.append(f"SET enable_parallel_hash = {'on' if enable_parallel_hash else 'off'};")
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
    batches = 0
    disk_usage = 0.0
    concurrent_partial_peak_est = 0.0
    hashagg_peaks: List[float] = []
    hash_peaks: List[float] = []
    sort_peaks: List[float] = []

    def walk(n: dict) -> None:
        nonlocal shared_hit, shared_read, temp_read, temp_written, peak_mem, sum_peak
        nonlocal batches, disk_usage, concurrent_partial_peak_est
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
        # HashAggregate / Hash report Memory Usage
        if n.get("Memory Usage") is not None and n.get("Node Type") in ("Aggregate", "Hash"):
            mu = float(n["Memory Usage"])
            peak_mem = max(peak_mem, mu)
            sum_peak += mu
            if n.get("Node Type") == "Aggregate" and n.get("Strategy") == "Hashed":
                hashagg_peaks.append(mu)
                # concurrent worker partials: Memory Usage is per-worker in ANALYZE text;
                # JSON may not explode workers — use Workers Launched if present higher up.
            if n.get("Node Type") == "Hash":
                hash_peaks.append(mu)
        if n.get("Sort Space Used") is not None:
            sm = float(n["Sort Space Used"])
            peak_mem = max(peak_mem, sm)
            sum_peak += sm
            sort_peaks.append(sm)
        if n.get("Hash Batches") is not None:
            batches = max(batches, int(n["Hash Batches"]))
        if n.get("Disk Usage") is not None:
            disk_usage = max(disk_usage, float(n["Disk Usage"]))
        for ch in n.get("Plans") or []:
            walk(ch)

    walk(plan)

    # Lifetime estimate: for dual HashAgg, concurrent ≈ max(sum of co-live HashAgg peaks
    # in one branch) * workers approximation from max_workers+1 when partials present.
    n_partial = meta.get("partial_hashagg_nodes") or 0
    max_w = meta.get("max_workers") or 0
    if hashagg_peaks:
        # Conservative: largest single HashAgg node peak * (workers+1) if partials exist
        base = max(hashagg_peaks)
        if n_partial > 0 and max_w > 0:
            concurrent_partial_peak_est = base * (max_w + 1)
        else:
            # multi-key serial HashAgg or finalize-only
            concurrent_partial_peak_est = sum(hashagg_peaks)
        if hash_peaks:
            concurrent_partial_peak_est += max(hash_peaks)

    meta["shared_hit"] = shared_hit
    meta["shared_read"] = shared_read
    meta["temp_read"] = temp_read
    meta["temp_written"] = temp_written
    meta["peak_mem_kb"] = peak_mem
    meta["sum_peak_mem_kb"] = sum_peak
    meta["hash_batches"] = batches
    meta["disk_usage_kb"] = disk_usage
    meta["hashagg_peaks_kb"] = hashagg_peaks
    meta["hash_peaks_kb"] = hash_peaks
    meta["sort_peaks_kb"] = sort_peaks
    meta["concurrent_ws_est_kb"] = concurrent_partial_peak_est
    return meta


def vector_full(params: Dict[str, float]) -> Dict[str, float]:
    out = dict(DEFAULT_PARAMS)
    out.update(params)
    # ensure all seven present
    return {k: float(out[k]) for k in SEVEN}


def result_invariant(sql_body: str, params: Dict[str, float], work_mem: str, hmm: float) -> Optional[str]:
    """Run SELECT and return first-row fingerprint for answer invariance."""
    env = {**os.environ, "PGPASSWORD": "postgres"}
    sets = [f"SET {k} = {params[k]};" for k in SEVEN]
    sets += [
        "SET max_parallel_workers_per_gather = 4;",
        f"SET work_mem = '{work_mem}';",
        f"SET hash_mem_multiplier = {hmm};",
        "SET enable_parallel_hash = on;",
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
    return r.stdout.strip().splitlines()[0] if r.stdout.strip() else ""


def main() -> int:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    provenance: Dict[str, Any] = {"sql": [], "timestamp": datetime.now(TZ).isoformat()}
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
            }
        )
    (RUN_DIR / "sql_provenance_v82.json").write_text(
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
                    "error": None,
                }
            )
            log(
                f"  sig={m['sig'][:120]}... W={m['max_workers']} "
                f"partialHA={m['partial_hashagg_nodes']} batches={m['hash_batches_max']} "
                f"par_hash={m['flags']['parallel_hash']}"
            )
        except Exception as exc:  # noqa: BLE001
            log(f"  ERROR default {sid}: {exc}")
            plan_grid.append(
                {"sql": sid, "vector": "default", "params": vector_full(DEFAULT_PARAMS), "meta": None, "error": str(exc)}
            )

    # Collect signature-changing vectors (cap MAX_VECTORS globally by first-seen unique transitions)
    sig_change_vectors: List[str] = []
    seen_transition: set = set()
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
                        "error": None,
                    }
                )
                if changed:
                    key = (sid, m["hash"])
                    if key not in seen_transition and vname not in sig_change_vectors:
                        # count distinct vectors, not pairs
                        pass
                    if changed and vname not in sig_change_vectors:
                        # defer uniqueness: mark candidate if any SQL changes
                        pass
                if changed:
                    log(f"[plan] {sid}/{vname} CHANGED W={m['max_workers']} indexonly={m['flags']['indexonly']}")
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

        # After all SQL for this vector, decide if vector caused any change
        any_change = any(
            r.get("sig_changed") and r.get("vector") == vname for r in plan_grid
        )
        if any_change and vname not in sig_change_vectors:
            sig_change_vectors.append(vname)
        if len(sig_change_vectors) >= MAX_VECTORS:
            log(f"[plan] reached MAX_VECTORS={MAX_VECTORS}, stop adding vectors")
            # still continue to fill? Charter: at most 16 distinct seven-vectors selected
            # by actual plan-signature changes. Stop evaluating further named points.
            break

    (RUN_DIR / "plan_grid_v82.json").write_text(
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
    # Prefer: (a) keep parallel, (b) drop hashagg/hash structure, (c) not pure IndexOnly cliff if avoidable
    candidates_for_analyze: List[Tuple[str, str, Dict[str, float], Dict[str, Any], Dict[str, Any]]] = []
    for r in plan_grid:
        if not r.get("sig_changed") or not r.get("meta") or r.get("vector") == "default":
            continue
        sid = r["sql"]
        if sid not in default_meta:
            continue
        dm, cm = default_meta[sid], r["meta"]
        # skip pure no-op renames already filtered by sig
        score = 0
        if cm["parallel"] and dm["parallel"]:
            score += 3
        if dm["flags"]["hashagg"] and not cm["flags"]["hashagg"]:
            score += 4
        if dm["flags"]["hash"] and not cm["flags"]["hash"]:
            score += 3
        if cm["partial_hashagg_nodes"] < dm["partial_hashagg_nodes"]:
            score += 5
        if cm["flags"]["indexonly"] and not dm["flags"]["indexonly"]:
            score -= 2  # closed-class risk but still measure
        if not cm["parallel"] and dm["parallel"]:
            score -= 5  # parallel collapse closed
        if r["vector"] == "par_collapse":
            score -= 10
        candidates_for_analyze.append((score, sid, r["vector"], r["params"], dm, cm))

    candidates_for_analyze.sort(key=lambda x: (-x[0], x[1], x[2]))
    # unique (sql, vector)
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

    # Always include default ANALYZE for each SQL mechanism if not covered
    log(f"[analyze] selected pairs: {selected}")

    pairs_out: List[Dict[str, Any]] = []
    # default ANALYZE baselines
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
                analyze=True,
                timeout=900,
            )
            m = plan_meta(ej)
            default_analyze[sid] = m
            log(
                f"  ms={m.get('actual_ms'):.1f} peak={m.get('peak_mem_kb')} "
                f"sum={m.get('sum_peak_mem_kb')} batches={m.get('hash_batches')} "
                f"conc_est={m.get('concurrent_ws_est_kb')}"
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
                analyze=True,
                timeout=900,
            )
            cm = plan_meta(ej)
            dm = default_analyze.get(sid) or {}
            if "actual_ms" in dm and "actual_ms" in cm and dm["actual_ms"] > 0:
                speedup = (dm["actual_ms"] - cm["actual_ms"]) / dm["actual_ms"] * 100.0
            else:
                speedup = None
            peak_drop = None
            sum_drop = None
            conc_drop = None
            if dm.get("peak_mem_kb") is not None and cm.get("peak_mem_kb") is not None:
                peak_drop = (dm["peak_mem_kb"] - cm["peak_mem_kb"]) / 1024.0
            if dm.get("sum_peak_mem_kb") is not None and cm.get("sum_peak_mem_kb") is not None:
                sum_drop = (dm["sum_peak_mem_kb"] - cm["sum_peak_mem_kb"]) / 1024.0
            if dm.get("concurrent_ws_est_kb") is not None and cm.get("concurrent_ws_est_kb") is not None:
                conc_drop = (dm["concurrent_ws_est_kb"] - cm["concurrent_ws_est_kb"]) / 1024.0

            # best mag proxy among peak/sum/concurrent-est
            mag_candidates = [x for x in (peak_drop, sum_drop, conc_drop) if x is not None]
            mag_best = max(mag_candidates) if mag_candidates else None

            keep_par = bool(cm.get("parallel")) and (
                (cm.get("max_workers") or 0) >= 1 or not dm.get("parallel")
            )
            # Prefer keep useful parallel: workers not collapsed to 0 if default had workers
            if dm.get("max_workers", 0) > 0:
                keep_par = keep_par and (cm.get("max_workers") or 0) > 0

            indexonly_cliff = bool(cm.get("flags", {}).get("indexonly")) and not bool(
                dm.get("flags", {}).get("indexonly")
            )

            speed_ok = speedup is not None and speedup >= TARGET_SPEEDUP_PCT
            mag_ok = mag_best is not None and mag_best >= TARGET_PEAK_DROP_MIB

            # Plausible CPU/IO transfer: if temp/shared_read drops a lot, more CPU likely;
            # if shared_read rises moderately, IO may rise into sweet.
            sread_d = None
            temp_d = None
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
                elif temp_d is not None and temp_d < -1000 and sread_d is not None and sread_d <= 0:
                    transfer_plausible = True
                    transfer_note = "temp_down_cpu_more_likely"
                elif speed_ok:
                    # faster with mem drop often raises CPU utilization under fixed concurrency
                    transfer_plausible = True
                    transfer_note = "faster_lower_ws_cpu_transfer_plausible"
                else:
                    transfer_note = "no_clear_transfer"

            # answer invariant (expensive) — only if dual-ok candidate
            answer_ok = None
            if speed_ok and mag_ok and keep_par:
                a0 = result_invariant(
                    sql_bodies[sid], DEFAULT_PARAMS, spec["work_mem"], spec["hash_mem_multiplier"]
                )
                a1 = result_invariant(
                    sql_bodies[sid], params, spec["work_mem"], spec["hash_mem_multiplier"]
                )
                answer_ok = a0 is not None and a1 is not None and a0 == a1
                log(f"  answer_ok={answer_ok} a0={a0} a1={a1}")

            rec = {
                "sql": sid,
                "mechanism": spec["mechanism"],
                "vector": vname,
                "params": vector_full(params),
                "work_mem": spec["work_mem"],
                "hash_mem_multiplier": spec["hash_mem_multiplier"],
                "enable_parallel_hash": True,
                "default": {k: dm.get(k) for k in (
                    "sig", "actual_ms", "peak_mem_kb", "sum_peak_mem_kb", "concurrent_ws_est_kb",
                    "hash_batches", "shared_read", "temp_read", "temp_written", "max_workers",
                    "partial_hashagg_nodes", "flags", "actual_rows",
                )},
                "cand": {k: cm.get(k) for k in (
                    "sig", "actual_ms", "peak_mem_kb", "sum_peak_mem_kb", "concurrent_ws_est_kb",
                    "hash_batches", "shared_read", "temp_read", "temp_written", "max_workers",
                    "partial_hashagg_nodes", "flags", "actual_rows",
                )},
                "speedup_pct": speedup,
                "peak_drop_mib": peak_drop,
                "sum_drop_mib": sum_drop,
                "conc_drop_mib": conc_drop,
                "mag_best_mib": mag_best,
                "speed_ok": speed_ok,
                "mag_ok": mag_ok,
                "keep_parallel": keep_par,
                "indexonly_cliff": indexonly_cliff,
                "transfer_plausible": transfer_plausible,
                "transfer_note": transfer_note,
                "answer_ok": answer_ok,
                "shared_read_delta": sread_d,
                "temp_read_delta": temp_d,
            }
            pairs_out.append(rec)
            log(
                f"  speedup={speedup} peakΔ={peak_drop} sumΔ={sum_drop} concΔ={conc_drop} "
                f"par={keep_par} idxonly={indexonly_cliff} speed_ok={speed_ok} mag_ok={mag_ok}"
            )
        except Exception as exc:  # noqa: BLE001
            log(f"  ERROR {sid}/{vname}: {exc}")
            pairs_out.append(
                {
                    "sql": sid,
                    "vector": vname,
                    "params": vector_full(params),
                    "error": str(exc),
                }
            )

    (RUN_DIR / "explain_analyze_pairs_v82.json").write_text(
        json.dumps(
            {
                "timestamp": datetime.now(TZ).isoformat(),
                "default_analyze": default_analyze,
                "pairs": pairs_out,
            },
            indent=2,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )

    # ---------- Eligibility decision ----------
    eligible: List[Dict[str, Any]] = []
    near: List[Dict[str, Any]] = []
    for rec in pairs_out:
        if rec.get("error"):
            continue
        reasons = []
        if not rec.get("speed_ok"):
            reasons.append("speed<15%")
        if not rec.get("mag_ok"):
            reasons.append("mag<328MiB")
        if not rec.get("keep_parallel"):
            reasons.append("parallel_lost")
        if rec.get("indexonly_cliff"):
            reasons.append("indexonly_cliff_closed_class")
        if rec.get("speed_ok") and rec.get("mag_ok") and rec.get("keep_parallel") and not rec.get(
            "indexonly_cliff"
        ):
            if rec.get("answer_ok") is False:
                reasons.append("answer_mismatch")
            elif not rec.get("transfer_plausible"):
                reasons.append("no_transfer")
            else:
                eligible.append(rec)
                continue
        # near-miss bookkeeping
        if rec.get("speed_ok") or (rec.get("mag_best_mib") or 0) >= 100:
            near.append({**{k: rec.get(k) for k in (
                "sql", "vector", "speedup_pct", "peak_drop_mib", "sum_drop_mib",
                "conc_drop_mib", "mag_best_mib", "keep_parallel", "indexonly_cliff",
            )}, "reject": reasons or ["incomplete"]})

    # One global seven-vector shared by all selected SQL components:
    # eligible set may list multiple SQL under same vector
    by_vector: Dict[str, List[Dict[str, Any]]] = {}
    for e in eligible:
        by_vector.setdefault(e["vector"], []).append(e)

    global_eligible_vectors: List[Dict[str, Any]] = []
    for vname, items in by_vector.items():
        global_eligible_vectors.append(
            {
                "vector": vname,
                "params": items[0]["params"],
                "n_sql": len(items),
                "sql_ids": [i["sql"] for i in items],
                "items": items,
            }
        )

    phase_b_eligible = len(global_eligible_vectors) > 0
    decision = {
        "phase": "A",
        "controller": "hybenchxp-memory-v82-new-family",
        "timestamp": datetime.now(TZ).isoformat(),
        "phase_b_eligible": phase_b_eligible,
        "eligible_set_size": len(global_eligible_vectors),
        "eligible_vectors": [
            {
                "vector": g["vector"],
                "params": g["params"],
                "sql_ids": g["sql_ids"],
                "n_sql": g["n_sql"],
                "evidence": [
                    {
                        "sql": i["sql"],
                        "mechanism": i["mechanism"],
                        "speedup_pct": i["speedup_pct"],
                        "mag_best_mib": i["mag_best_mib"],
                        "peak_drop_mib": i["peak_drop_mib"],
                        "sum_drop_mib": i["sum_drop_mib"],
                        "conc_drop_mib": i["conc_drop_mib"],
                        "keep_parallel": i["keep_parallel"],
                        "transfer_note": i["transfer_note"],
                        "answer_ok": i["answer_ok"],
                        "default_sig": i["default"]["sig"],
                        "cand_sig": i["cand"]["sig"],
                    }
                    for i in g["items"]
                ],
            }
            for g in global_eligible_vectors
        ],
        "n_pairs": len(pairs_out),
        "n_sig_change_vectors": len(sig_change_vectors),
        "sig_change_vectors": sig_change_vectors,
        "near_misses": near[:30],
        "fallback_db_qps_best": FALLBACK_DB_QPS,
        "target_peak_drop_mib": TARGET_PEAK_DROP_MIB,
        "target_speedup_pct": TARGET_SPEEDUP_PCT,
        "stop_reason": None
        if phase_b_eligible
        else (
            "eligible_set_empty: no single seven-vector jointly satisfies "
            "speed>=15%, working-set drop>=328MiB, keep parallel, non-IndexOnly, "
            "plausible CPU/IO transfer under the two new mechanisms"
        ),
        "mechanisms_tested": [
            "parallel_partial_aggregate_multiplicity",
            "true_multibatch_parallel_hash",
        ],
        "w1_verified": {
            "port": PORT,
            "memory_bytes": 6871947674,
            "shm_bytes": 2147483648,
            "cpuset": "112-121",
            "enable_parallel_hash_default": True,
        },
        "ownership": {
            "ports": list(range(57433, 57441)),
            "cpu_pool_untouched": True,
            "binary_plan_resource_tuner_modified": False,
            "sql_probes": 0,
            "baselines": 0,
            "candidates": 0,
            "full8": False,
        },
        "elapsed_s": time.time() - t0,
    }
    (RUN_DIR / "phase_a_eligibility_decision_v82.json").write_text(
        json.dumps(decision, indent=2, default=str) + "\n", encoding="utf-8"
    )
    # report/bonect copy
    report_dir = REPO / "report" / "bonect"
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "phase_a_eligibility_decision_v82.json").write_text(
        json.dumps(decision, indent=2, default=str) + "\n", encoding="utf-8"
    )
    (report_dir / "hybenchxp_memory_fair_v82_phase_a_eligibility_20260714.json").write_text(
        json.dumps(decision, indent=2, default=str) + "\n", encoding="utf-8"
    )

    log(
        f"[decision] phase_b_eligible={phase_b_eligible} "
        f"eligible_vectors={len(global_eligible_vectors)} "
        f"elapsed={time.time()-t0:.1f}s"
    )
    return 0 if True else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""HyBenchXP Memory fair v85 Phase A — same-vector composition eligibility.

Legal only if ONE identical seven-vector applies to every role.
- Mag role: v84 nested_hashagg_type under ctc10/ctc12 (>=328 MiB certified)
- Speed role: existing realistic SQL under the SAME vector (>=20% elapsed)
- At most 2 predeclared compositions; predicted DB gain>=18%, mem>=328 MiB

Does not start tuners; does not edit binary_plan_resource_tuner.py.
Owns rebuild w1 port 57433 only for EXPLAIN ANALYZE.
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
    "hyxp_memory_fair_v85_same_vector_composition_20260715"
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

# At most two global vectors + signature-preserving explicit seven-key values.
VECTORS: Dict[str, Dict[str, float]] = {
    "ctc10": {**DEFAULT_PARAMS, "cpu_tuple_cost": 10.0},
    "ctc12": {**DEFAULT_PARAMS, "cpu_tuple_cost": 12.0},
}

# Mag role from v84 (certified collapse under ctc10/ctc12).
MAG_ROLE = {
    "id": "nested_hashagg_type",
    "role": "mag",
    "path": "shell/hybenchxp_memory_ap6_v84_nested_hashagg_type.sql",
    "work_mem": "512MB",
    "hash_mem_multiplier": 2.0,
    "enable_parallel_hash": True,
    "enable_memoize": True,
    "source": "v84_nested_double_hashagg_collapse",
}

# Existing realistic roles only — no new family census.
SPEED_CANDIDATES: List[Dict[str, Any]] = [
    {
        "id": "fullscan_v12",
        "role": "speed",
        "path": "shell/hybenchxp_memory_ap6_transfer_groupagg_switch_v12_fullscan.sql",
        "work_mem": "512MB",
        "hash_mem_multiplier": 2.0,
        "enable_parallel_hash": True,
        "enable_memoize": True,
        "source": "v76_live_speed_role_ctc1",
    },
    {
        "id": "v80_hashagg_fullscan",
        "role": "speed",
        "path": "shell/hybenchxp_memory_ap6_v80_hashagg_fullscan.sql",
        "work_mem": "512MB",
        "hash_mem_multiplier": 2.0,
        "enable_parallel_hash": True,
        "enable_memoize": True,
        "source": "v80_multibatch_speed_near_ctc1",
    },
    {
        "id": "dual_partial_hashagg",
        "role": "speed",
        "path": "shell/hybenchxp_memory_ap6_v82_dual_partial_hashagg.sql",
        "work_mem": "512MB",
        "hash_mem_multiplier": 2.0,
        "enable_parallel_hash": True,
        "enable_memoize": True,
        "source": "v82_speed_under_ctc1",
    },
    {
        "id": "groupingsets_multihash",
        "role": "speed",
        "path": "shell/hybenchxp_memory_ap6_v82_groupingsets_multihash.sql",
        "work_mem": "512MB",
        "hash_mem_multiplier": 2.0,
        "enable_parallel_hash": True,
        "enable_memoize": True,
        "source": "v82_speed_under_ctc1",
    },
    {
        "id": "bitmap_type_range",
        "role": "speed",
        "path": "shell/hybenchxp_memory_ap6_bitmap_v79_transfer_type_range.sql",
        "work_mem": "512MB",
        "hash_mem_multiplier": 2.0,
        "enable_parallel_hash": True,
        "enable_memoize": True,
        "source": "v79_bitmap_speed_rpc",
    },
    {
        "id": "hashsetop_except",
        "role": "speed",
        "path": "shell/hybenchxp_memory_ap6_v84_hashsetop_except.sql",
        "work_mem": "512MB",
        "hash_mem_multiplier": 2.0,
        "enable_parallel_hash": True,
        "enable_memoize": True,
        "source": "v84_setop_speed_at_setop_sort_seed_not_ctc10",
    },
    {
        "id": "incsort_groupagg",
        "role": "speed",
        "path": "shell/hybenchxp_memory_ap6_v83_incsort_groupagg.sql",
        "work_mem": "512MB",
        "hash_mem_multiplier": 2.0,
        "enable_parallel_hash": True,
        "enable_memoize": True,
        "source": "v83_near_ctc1_speed_probe",
    },
]

TARGET_MAG_MIB = 328.0
TARGET_SPEED_ROLE_PCT = 20.0
TARGET_COMPOSITION_DB_PCT = 18.0
FALLBACK_DB_QPS = 114.12708344892893

# Predeclared compositions (at most two): (speed_role_id, mag_role_id, weights)
# weights are query-mix fractions for rate-based QPS prediction.
PREDECLARED_COMPOSITIONS: List[Dict[str, Any]] = [
    {
        "id": "comp_speed_heavy",
        "description": "speed-heavy mix: speed role 0.70 + mag role 0.30 (mag still concurrent-dominant peak)",
        "mag_role": "nested_hashagg_type",
        "speed_role_slot": "auto",  # filled with best eligible speed role
        "weights": {"speed": 0.70, "mag": 0.30},
    },
    {
        "id": "comp_balanced",
        "description": "balanced mix: speed 0.55 + mag 0.45",
        "mag_role": "nested_hashagg_type",
        "speed_role_slot": "auto",
        "weights": {"speed": 0.55, "mag": 0.45},
    },
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
        if node == "Aggregate":
            pmode = n.get("Partial Mode")
            if pmode and pmode != "Simple":
                label += f":{pmode}"
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
        "gather": False,
        "gather_merge": False,
        "seq": False,
        "indexonly": False,
        "index": False,
        "nested_loop": False,
        "merge_join": False,
        "groupagg": False,
        "hashsetop": False,
        "setop_sorted": False,
        "memoize": False,
        "incremental_sort": False,
    }
    hashagg_count = 0
    peak_mem_kb = 0.0
    sum_peak_kb = 0.0

    def walk(n: dict) -> None:
        nonlocal hashagg_count, peak_mem_kb, sum_peak_kb
        if not isinstance(n, dict):
            return
        node = n.get("Node Type", "?")
        counts[node] = counts.get(node, 0) + 1
        strat = n.get("Strategy")
        if node == "Aggregate":
            if strat == "Hashed":
                has["hashagg"] = True
                hashagg_count += 1
            elif strat == "Sorted":
                has["groupagg"] = True
        if node == "Hash Join":
            has["hashjoin"] = True
        if node == "Sort":
            has["sort"] = True
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
        if node == "HashSetOp":
            has["hashsetop"] = True
        if node == "SetOp":
            strat_l = str(strat or "").lower()
            if strat_l == "hashed":
                has["hashsetop"] = True
            else:
                has["setop_sorted"] = True
        # memory
        for key in ("Peak Memory Usage", "Memory Usage"):
            if n.get(key) is not None:
                kb = float(n[key])
                # Peak Memory Usage is kB in PG; Memory Usage for Hash is kB
                peak_mem_kb = max(peak_mem_kb, kb)
                if key == "Peak Memory Usage" or n.get("Node Type") in (
                    "Aggregate",
                    "Hash",
                    "HashSetOp",
                    "SetOp",
                    "Memoize",
                    "Sort",
                ):
                    if key == "Memory Usage" or key == "Peak Memory Usage":
                        sum_peak_kb += kb if key == "Peak Memory Usage" else 0.0
        if n.get("Memory Usage") is not None and n.get("Node Type") in (
            "Aggregate",
            "Hash",
            "HashSetOp",
            "SetOp",
            "Memoize",
        ):
            sum_peak_kb += float(n["Memory Usage"])
            peak_mem_kb = max(peak_mem_kb, float(n["Memory Usage"]))
        if n.get("Sort Space Used") is not None:
            # Sort Space Used is kB
            peak_mem_kb = max(peak_mem_kb, float(n["Sort Space Used"]))
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
        "peak_mib": peak_mem_kb / 1024.0,
        "sum_peak_mib": sum_peak_kb / 1024.0,
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
    sets.append("SET enable_hashagg = on;")
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


def meta_from_explain(explain_obj: dict) -> Dict[str, Any]:
    plan = explain_obj["Plan"]
    meta = collect_nodes(plan)
    meta["sig"] = plan_signature(plan)
    meta["hash"] = hashlib.sha1(meta["sig"].encode()).hexdigest()[:12]
    meta["total_cost"] = float(plan.get("Total Cost") or 0.0)
    if "Actual Total Time" in plan:
        meta["actual_ms"] = float(plan["Actual Total Time"])
        meta["actual_rows"] = float(plan.get("Actual Rows") or 0.0)
    # buffers
    shared_hit = shared_read = temp_read = temp_written = 0

    def walk_buf(n: dict) -> None:
        nonlocal shared_hit, shared_read, temp_read, temp_written
        if not isinstance(n, dict):
            return
        shared_hit += int(n.get("Shared Hit Blocks") or 0)
        shared_read += int(n.get("Shared Read Blocks") or 0)
        temp_read += int(n.get("Temp Read Blocks") or 0)
        temp_written += int(n.get("Temp Written Blocks") or 0)
        for ch in n.get("Plans") or []:
            walk_buf(ch)

    walk_buf(plan)
    meta["shared_hit"] = shared_hit
    meta["shared_read"] = shared_read
    meta["temp_read"] = temp_read
    meta["temp_written"] = temp_written
    # CPU/IO transfer proxy from buffers: higher shared_hit/time ≈ CPU; shared_read ≈ IO
    if meta.get("actual_ms"):
        meta["hit_per_ms"] = shared_hit / max(meta["actual_ms"], 1e-6)
        meta["read_per_ms"] = shared_read / max(meta["actual_ms"], 1e-6)
    return meta


def run_pair(
    spec: Dict[str, Any],
    sql_body: str,
    vector_name: str,
    params: Dict[str, float],
    *,
    default_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Baseline=default costs; candidate=vector. ANALYZE both if default not cached."""
    common = dict(
        work_mem=spec["work_mem"],
        hash_mem_multiplier=spec["hash_mem_multiplier"],
        enable_parallel_hash=spec["enable_parallel_hash"],
        enable_memoize=spec["enable_memoize"],
        analyze=True,
    )
    if default_meta is None:
        log(f"  ANALYZE default {spec['id']}")
        d_ex = explain_json(sql_body, DEFAULT_PARAMS, **common)
        d_meta = meta_from_explain(d_ex)
    else:
        d_meta = default_meta
    log(f"  ANALYZE {vector_name} {spec['id']}")
    c_ex = explain_json(sql_body, params, **common)
    c_meta = meta_from_explain(c_ex)

    base_ms = float(d_meta.get("actual_ms") or 0.0)
    cand_ms = float(c_meta.get("actual_ms") or 0.0)
    speedup_pct = ((base_ms - cand_ms) / base_ms * 100.0) if base_ms > 0 else 0.0
    peak_drop = float(d_meta.get("peak_mib") or 0.0) - float(c_meta.get("peak_mib") or 0.0)
    sum_drop = float(d_meta.get("sum_peak_mib") or 0.0) - float(c_meta.get("sum_peak_mib") or 0.0)
    mag_best = max(peak_drop, sum_drop)
    keep_parallel = bool(d_meta.get("parallel")) and bool(c_meta.get("parallel"))
    # CPU/IO transfer proxies
    cpu_up = (c_meta.get("hit_per_ms") or 0) > (d_meta.get("hit_per_ms") or 0) * 1.05
    io_up = (c_meta.get("read_per_ms") or 0) > (d_meta.get("read_per_ms") or 0) * 1.05 or (
        (c_meta.get("shared_read") or 0) > (d_meta.get("shared_read") or 0) * 1.05
    )
    # higher workers utilization / less memory often raises CPU%
    structural_cpu_transfer = (
        (c_meta.get("flags") or {}).get("groupagg")
        or (c_meta.get("flags") or {}).get("sort")
        or (c_meta.get("hashagg_count", 0) < d_meta.get("hashagg_count", 0))
    )
    return {
        "sql": spec["id"],
        "role": spec.get("role"),
        "source": spec.get("source"),
        "vector": vector_name,
        "vector_params": {k: params[k] for k in SEVEN},
        "base_ms": base_ms,
        "cand_ms": cand_ms,
        "speedup_pct": speedup_pct,
        "peak_drop_mib": peak_drop,
        "sum_drop_mib": sum_drop,
        "mag_best_mib": mag_best,
        "keep_parallel": keep_parallel,
        "indexonly_cand": bool((c_meta.get("flags") or {}).get("indexonly")),
        "base_sig": d_meta.get("sig"),
        "cand_sig": c_meta.get("sig"),
        "base_hashagg": d_meta.get("hashagg_count"),
        "cand_hashagg": c_meta.get("hashagg_count"),
        "base_peak_mib": d_meta.get("peak_mib"),
        "cand_peak_mib": c_meta.get("peak_mib"),
        "base_sum_mib": d_meta.get("sum_peak_mib"),
        "cand_sum_mib": c_meta.get("sum_peak_mib"),
        "base_shared_read": d_meta.get("shared_read"),
        "cand_shared_read": c_meta.get("shared_read"),
        "base_hit_per_ms": d_meta.get("hit_per_ms"),
        "cand_hit_per_ms": c_meta.get("hit_per_ms"),
        "cpu_proxy_up": bool(cpu_up or structural_cpu_transfer),
        "io_proxy_up": bool(io_up),
        "default_meta": {
            k: d_meta[k]
            for k in (
                "sig",
                "actual_ms",
                "peak_mib",
                "sum_peak_mib",
                "hashagg_count",
                "parallel",
                "max_workers",
                "flags",
                "shared_read",
                "shared_hit",
                "hit_per_ms",
                "read_per_ms",
            )
            if k in d_meta
        },
        "cand_meta": {
            k: c_meta[k]
            for k in (
                "sig",
                "actual_ms",
                "peak_mib",
                "sum_peak_mib",
                "hashagg_count",
                "parallel",
                "max_workers",
                "flags",
                "shared_read",
                "shared_hit",
                "hit_per_ms",
                "read_per_ms",
            )
            if k in c_meta
        },
    }


def rate_weighted_db_gain_pct(
    roles: List[Tuple[float, float, float]],
) -> float:
    """roles: list of (weight, base_ms, cand_ms). Returns predicted DB QPS gain %."""
    base_rate = 0.0
    cand_rate = 0.0
    for w, b, c in roles:
        if b <= 0 or c <= 0 or w <= 0:
            continue
        base_rate += w / b
        cand_rate += w / c
    if base_rate <= 0:
        return 0.0
    return (cand_rate / base_rate - 1.0) * 100.0


def conservative_db_gain_pct(
    roles: List[Tuple[float, float, float]],
    haircut: float = 0.85,
) -> float:
    """Apply haircut to each role's speedup for conservative screen."""
    adj: List[Tuple[float, float, float]] = []
    for w, b, c in roles:
        if b <= 0:
            continue
        speedup = (b - c) / b
        # haircut positive speedups only; keep regressions fully
        if speedup > 0:
            speedup *= haircut
            c_adj = b * (1.0 - speedup)
        else:
            c_adj = c
        adj.append((w, b, c_adj))
    return rate_weighted_db_gain_pct(adj)


def main() -> int:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    # ownership inspect
    own: Dict[str, Any] = {
        "port": PORT,
        "container": CONTAINER,
        "cpu_pool_ports": list(range(56433, 56441)),
        "memory_pool_ports": list(range(57433, 57441)),
    }
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

    all_specs = [MAG_ROLE] + SPEED_CANDIDATES
    sql_bodies: Dict[str, str] = {}
    provenance: Dict[str, Any] = {
        "timestamp": datetime.now(TZ).isoformat(),
        "ownership": own,
        "rule": "same_global_seven_vector_for_all_roles",
        "sql": [],
    }
    for spec in all_specs:
        p = REPO / spec["path"]
        body = strip_sql(p.read_text(encoding="utf-8"))
        sql_bodies[spec["id"]] = body
        provenance["sql"].append(
            {
                "id": spec["id"],
                "role": spec.get("role"),
                "path": spec["path"],
                "sha256": file_sha256(p),
                "source": spec.get("source"),
            }
        )
    (RUN_DIR / "sql_provenance_v85.json").write_text(
        json.dumps(provenance, indent=2) + "\n", encoding="utf-8"
    )

    pairs: List[Dict[str, Any]] = []
    default_cache: Dict[str, Dict[str, Any]] = {}

    # 1) Mag role under ctc10/ctc12
    log("=== MAG ROLE nested_hashagg_type under ctc10/ctc12 ===")
    for vname, vparams in VECTORS.items():
        try:
            rec = run_pair(
                MAG_ROLE,
                sql_bodies[MAG_ROLE["id"]],
                vname,
                vparams,
                default_meta=default_cache.get(MAG_ROLE["id"]),
            )
            if MAG_ROLE["id"] not in default_cache:
                default_cache[MAG_ROLE["id"]] = rec["default_meta"]
                # re-run needs full default meta with actual_ms — store from first pair
                # Fix: store enough fields by re-extracting
            # cache full default from first successful pair
            if "actual_ms" in rec.get("default_meta", {}):
                default_cache[MAG_ROLE["id"]] = {
                    **rec["default_meta"],
                    # reconstruct meta_from_explain-compatible for reuse
                }
            # For reuse we need full meta; re-store using a side channel
            pairs.append(rec)
            log(
                f"  mag {vname}: speedup={rec['speedup_pct']:+.2f}% "
                f"mag={rec['mag_best_mib']:.1f}MiB HA {rec['base_hashagg']}->{rec['cand_hashagg']} "
                f"par={rec['keep_parallel']}"
            )
        except Exception as exc:  # noqa: BLE001
            log(f"  FAIL mag {vname}: {exc}")
            pairs.append(
                {
                    "sql": MAG_ROLE["id"],
                    "role": "mag",
                    "vector": vname,
                    "error": str(exc)[:500],
                }
            )

    # Re-run mag with proper default cache using full analyze once
    # (default_cache from default_meta may lack fields needed; ok for already-recorded pairs)

    # 2) Speed candidates under same vectors
    log("=== SPEED ROLE SEARCH under ctc10/ctc12 (existing SQL only) ===")
    for spec in SPEED_CANDIDATES:
        sid = spec["id"]
        body = sql_bodies[sid]
        # measure default once
        try:
            log(f"[default] {sid}")
            d_ex = explain_json(
                body,
                DEFAULT_PARAMS,
                work_mem=spec["work_mem"],
                hash_mem_multiplier=spec["hash_mem_multiplier"],
                enable_parallel_hash=spec["enable_parallel_hash"],
                enable_memoize=spec["enable_memoize"],
                analyze=True,
            )
            d_meta = meta_from_explain(d_ex)
            default_cache[sid] = d_meta
        except Exception as exc:  # noqa: BLE001
            log(f"  FAIL default {sid}: {exc}")
            pairs.append({"sql": sid, "role": "speed", "error": f"default:{exc}"[:500]})
            continue
        for vname, vparams in VECTORS.items():
            try:
                rec = run_pair(spec, body, vname, vparams, default_meta=d_meta)
                pairs.append(rec)
                log(
                    f"  speed {sid}/{vname}: speedup={rec['speedup_pct']:+.2f}% "
                    f"mag={rec['mag_best_mib']:.1f}MiB par={rec['keep_parallel']} "
                    f"idxonly={rec['indexonly_cand']} cpu_up={rec['cpu_proxy_up']} io_up={rec['io_proxy_up']}"
                )
            except Exception as exc:  # noqa: BLE001
                log(f"  FAIL {sid}/{vname}: {exc}")
                pairs.append(
                    {
                        "sql": sid,
                        "role": "speed",
                        "vector": vname,
                        "error": str(exc)[:500],
                    }
                )

    (RUN_DIR / "explain_analyze_pairs_v85.json").write_text(
        json.dumps({"pairs": pairs, "timestamp": datetime.now(TZ).isoformat()}, indent=2)
        + "\n",
        encoding="utf-8",
    )

    # 3) Eligibility screen
    mag_ok: Dict[str, Dict[str, Any]] = {}
    speed_ok: List[Dict[str, Any]] = []
    for rec in pairs:
        if rec.get("error"):
            continue
        if rec.get("role") == "mag" or rec.get("sql") == "nested_hashagg_type":
            if (
                rec.get("mag_best_mib", 0) >= TARGET_MAG_MIB
                and rec.get("keep_parallel")
                and rec.get("speedup_pct", -999) > -50  # not catastrophic
            ):
                # require mechanism: hashagg reduced
                if (rec.get("cand_hashagg") or 0) < (rec.get("base_hashagg") or 0):
                    mag_ok[rec["vector"]] = rec
        if rec.get("role") == "speed":
            rejects = []
            if rec.get("speedup_pct", 0) < TARGET_SPEED_ROLE_PCT:
                rejects.append(f"speed<{TARGET_SPEED_ROLE_PCT}%")
            if not rec.get("keep_parallel"):
                rejects.append("parallel_lost")
            # IndexOnly cliff closed as residual lever for mem, but speed role may use it;
            # still flag if it is the ONLY structural change with known closed-family issues.
            # Require plausible CPU/IO upward transfer.
            if not (rec.get("cpu_proxy_up") or rec.get("io_proxy_up")):
                rejects.append("no_cpu_io_transfer_proxy")
            if not rejects:
                speed_ok.append(rec)
            else:
                rec["speed_rejects"] = rejects

    # Cross-vector splice forbidden: speed and mag must share vector name
    compositions_eval: List[Dict[str, Any]] = []
    eligible: List[Dict[str, Any]] = []

    for vname in VECTORS:
        mrec = mag_ok.get(vname)
        if not mrec:
            continue
        speed_same = [s for s in speed_ok if s.get("vector") == vname]
        if not speed_same:
            continue
        # pick best speed role by speedup
        speed_same.sort(key=lambda x: -float(x.get("speedup_pct") or 0))
        best_speed = speed_same[0]
        for comp in PREDECLARED_COMPOSITIONS:
            w_s = float(comp["weights"]["speed"])
            w_m = float(comp["weights"]["mag"])
            roles = [
                (w_s, best_speed["base_ms"], best_speed["cand_ms"]),
                (w_m, mrec["base_ms"], mrec["cand_ms"]),
            ]
            pred = rate_weighted_db_gain_pct(roles)
            pred_cons = conservative_db_gain_pct(roles, haircut=0.85)
            # Concurrent memory: mag role peak drop certified; require weight >=0.25 so lifetime present
            mem_ok = mrec["mag_best_mib"] >= TARGET_MAG_MIB and w_m >= 0.25
            transfer_ok = bool(
                best_speed.get("cpu_proxy_up")
                or best_speed.get("io_proxy_up")
                or mrec.get("cpu_proxy_up")
            )
            entry = {
                "composition_id": comp["id"],
                "vector": vname,
                "vector_params": mrec["vector_params"],
                "speed_role": best_speed["sql"],
                "mag_role": mrec["sql"],
                "weights": {"speed": w_s, "mag": w_m},
                "speed_role_speedup_pct": best_speed["speedup_pct"],
                "mag_role_speedup_pct": mrec["speedup_pct"],
                "mag_release_mib": mrec["mag_best_mib"],
                "predicted_db_gain_pct": pred,
                "predicted_db_gain_conservative_pct": pred_cons,
                "mem_ok": mem_ok,
                "transfer_ok": transfer_ok,
                "speed_indexonly": best_speed.get("indexonly_cand"),
                "keep_parallel_both": best_speed.get("keep_parallel")
                and mrec.get("keep_parallel"),
                "eligible": False,
                "reject": [],
            }
            if pred_cons < TARGET_COMPOSITION_DB_PCT:
                entry["reject"].append(
                    f"conservative_db_gain<{TARGET_COMPOSITION_DB_PCT}%"
                )
            if not mem_ok:
                entry["reject"].append("mem_release_or_weight")
            if not transfer_ok:
                entry["reject"].append("no_plausible_nontarget_transfer")
            if not entry["keep_parallel_both"]:
                entry["reject"].append("parallel_lost")
            # forbid using closed IndexOnly as sole residual if speed role is IndexOnly
            # AND mag is separate — IndexOnly speed is allowed if answers preserved;
            # but ctc2 IndexOnly was closed as mem residual. Flag only.
            if best_speed.get("indexonly_cand"):
                entry["note"] = "speed_role_uses_indexonly_under_high_ctc"
            if not entry["reject"]:
                entry["eligible"] = True
                eligible.append(entry)
            compositions_eval.append(entry)

    # Also record near-miss speed roles for report
    near_miss_speed = []
    for rec in pairs:
        if rec.get("role") != "speed" or rec.get("error"):
            continue
        sp = rec.get("speedup_pct") or 0
        if sp >= 10:  # interesting near misses
            near_miss_speed.append(
                {
                    "sql": rec["sql"],
                    "vector": rec["vector"],
                    "speedup_pct": sp,
                    "mag_best_mib": rec.get("mag_best_mib"),
                    "keep_parallel": rec.get("keep_parallel"),
                    "indexonly": rec.get("indexonly_cand"),
                    "cpu_proxy_up": rec.get("cpu_proxy_up"),
                    "io_proxy_up": rec.get("io_proxy_up"),
                    "rejects": rec.get("speed_rejects") or (
                        []
                        if sp >= TARGET_SPEED_ROLE_PCT
                        else [f"speed<{TARGET_SPEED_ROLE_PCT}%"]
                    ),
                }
            )

    decision = {
        "phase": "A",
        "controller": "hybenchxp-memory-v85-same-vector-composition",
        "timestamp": datetime.now(TZ).isoformat(),
        "phase_b_eligible": bool(eligible),
        "eligible_set_size": len(eligible),
        "eligible_set": eligible,
        "eligible_vectors": sorted({e["vector"] for e in eligible}),
        "compositions_evaluated": compositions_eval,
        "mag_roles_ok": {
            k: {
                "speedup_pct": v["speedup_pct"],
                "mag_best_mib": v["mag_best_mib"],
                "base_hashagg": v["base_hashagg"],
                "cand_hashagg": v["cand_hashagg"],
                "keep_parallel": v["keep_parallel"],
            }
            for k, v in mag_ok.items()
        },
        "speed_roles_ok": [
            {
                "sql": s["sql"],
                "vector": s["vector"],
                "speedup_pct": s["speedup_pct"],
                "cpu_proxy_up": s["cpu_proxy_up"],
                "io_proxy_up": s["io_proxy_up"],
                "indexonly": s.get("indexonly_cand"),
            }
            for s in speed_ok
        ],
        "near_miss_speed_roles": near_miss_speed,
        "rules": {
            "same_global_seven_vector": True,
            "no_cross_vector_splice": True,
            "mag_role": "nested_hashagg_type (v84)",
            "speed_role_min_elapsed_pct": TARGET_SPEED_ROLE_PCT,
            "composition_min_predicted_db_pct": TARGET_COMPOSITION_DB_PCT,
            "composition_uses_conservative_haircut": 0.85,
            "mag_min_mib": TARGET_MAG_MIB,
            "max_compositions": 2,
            "max_vectors": list(VECTORS.keys()),
            "max_sql_roles": 4,
            "no_broad_new_family_census": True,
        },
        "n_pairs": len([p for p in pairs if not p.get("error")]),
        "fallback_db_qps_best": FALLBACK_DB_QPS,
        "stop_reason": None,
        "classification": None,
        "ownership": {
            "ports": list(range(57433, 57441)),
            "cpu_pool_untouched": True,
            "binary_plan_resource_tuner_modified": False,
            "w1": own,
            "sql_probes": 0,
            "baselines": 0,
            "candidates": 0,
            "full8": False,
            "phase_a_explain_only": True,
        },
        "artifacts": {
            "run_dir": str(RUN_DIR),
            "pairs": str(RUN_DIR / "explain_analyze_pairs_v85.json"),
            "provenance": str(RUN_DIR / "sql_provenance_v85.json"),
        },
        "elapsed_s": time.time() - t0,
    }

    if not eligible:
        decision["phase_b_eligible"] = False
        decision["phase_b_c_blocked"] = True
        decision["stop_reason"] = (
            "eligible_set_empty: no same-vector composition where mag role "
            f"(nested_hashagg_type@ctc10/12) releases>={TARGET_MAG_MIB}MiB AND a distinct "
            f"existing role has measured elapsed>={TARGET_SPEED_ROLE_PCT}% under the identical "
            f"seven-vector AND conservative weighted DB gain>={TARGET_COMPOSITION_DB_PCT}% "
            "with plausible CPU/IO transfer. Cross-vector splice forbidden."
        )
        decision["classification"] = (
            "phase_a_stop / same_vector_composition_not_eligible"
        )
    else:
        decision["classification"] = "phase_a_pass / composition_eligible"
        decision["phase_b_c_blocked"] = False

    out_path = RUN_DIR / "phase_a_eligibility_decision_v85.json"
    out_path.write_text(json.dumps(decision, indent=2) + "\n", encoding="utf-8")
    # also report dir copies
    (REPORT_DIR / "phase_a_eligibility_decision_v85.json").write_text(
        json.dumps(decision, indent=2) + "\n", encoding="utf-8"
    )
    (REPORT_DIR / "hybenchxp_memory_fair_v85_phase_a_eligibility_20260715.json").write_text(
        json.dumps(decision, indent=2) + "\n", encoding="utf-8"
    )

    log(f"=== DECISION eligible_set_size={len(eligible)} ===")
    log(json.dumps({"eligible": eligible, "mag_ok": list(mag_ok.keys()), "n_speed_ok": len(speed_ok)}, indent=2))
    log(f"wrote {out_path}")
    return 0 if True else 1


if __name__ == "__main__":
    raise SystemExit(main())

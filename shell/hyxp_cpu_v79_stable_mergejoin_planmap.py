#!/usr/bin/env python3
"""HyBenchXP CPU fair v79 Phase A — stable MergeJoin plan eligibility.

Goals:
  - Keep ptc3 serial MergeJoin CPU relief (distinct from closed range3m NL+Bitmap).
  - Prefer bounded-range checking/company/group variants with low physical-read
    variance under repeated default execution (prewarm-fit).
  - Primary Memory-up: AP9 exists (not AP10 transfer).
  - Map at most 8 structurally distinct seven-vectors.
  - No capability changes; probe only cpu_mem_w1:56433.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PORT = 56433
DB = "hybench_sf10x"
USER = "postgres"
ROOT = Path("/home/anonymous/program/resource_adaptive")
OUT_DIR = Path("/tmp/hyxp_cpu_planmap_v79_20260715")
PLANS = OUT_DIR / "plans"
OUT_DIR.mkdir(parents=True, exist_ok=True)
PLANS.mkdir(parents=True, exist_ok=True)

COST_KEYS = [
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
]

DEFAULTS: Dict[str, float] = {
    "seq_page_cost": 1.0,
    "random_page_cost": 4.0,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
}

CLOSED_CAND_FP = ["ser", "NL", "HA", "Idx", "Bit", "w0"]

# At most 3 AP6 variants + supporting roles (AP10 transfer is mapping-only, not primary)
SQL_FILES: Dict[str, str] = {
    "ap6_default": "shell/hybenchxp_cpu_plan_sensitive_v4_ap6_checking_company_group.sql",
    "ap6_range4m": "shell/hybenchxp_cpu_plan_sensitive_v79_ap6_checking_range4m.sql",
    "ap6_range5m": "shell/hybenchxp_cpu_plan_sensitive_v79_ap6_checking_range5m.sql",
    "ap6_amount_pos": "shell/hybenchxp_cpu_plan_sensitive_v79_ap6_checking_amount_pos.sql",
    "ap6_range3m_closed": "shell/hybenchxp_cpu_plan_sensitive_v6_ap6_source_range3m.sql",
    "ap9_exists": "shell/hybenchxp_cpu_plan_sensitive_v4_ap9_exists_loan_no_trans.sql",
    "ap8_filter": "shell/hybenchxp_cpu_plan_sensitive_v4_ap8_customer_saving_checking_filter.sql",
    "ap11_category": "shell/hybenchxp_cpu_plan_sensitive_v4_ap11_company_category_transfer.sql",
    "ap10_transfer": "shell/hybenchxp_cpu_plan_sensitive_v4_ap10_transfer_customer_company_group.sql",
}

# ≤8 structural vectors after fingerprint collapse (no dense interpolation)
RAW_POINTS: Dict[str, Dict[str, float]] = {
    "default": dict(DEFAULTS),
    "ptc3": {**DEFAULTS, "parallel_tuple_cost": 3.0},
    "ptc2p5": {**DEFAULTS, "parallel_tuple_cost": 2.5},
    "ptc4": {**DEFAULTS, "parallel_tuple_cost": 4.0},
    "citc_coc_ptc3": {
        **DEFAULTS,
        "cpu_index_tuple_cost": 0.05,
        "cpu_operator_cost": 0.025,
        "parallel_tuple_cost": 3.0,
    },
    "psc5k_ptc3": {
        **DEFAULTS,
        "parallel_setup_cost": 5000.0,
        "parallel_tuple_cost": 3.0,
    },
    "ctc02_ptc3": {
        **DEFAULTS,
        "cpu_tuple_cost": 0.02,
        "parallel_tuple_cost": 3.0,
    },
    "highrpc_ptc3": {
        **DEFAULTS,
        "random_page_cost": 16.0,
        "parallel_tuple_cost": 3.0,
    },
    # reference only (closed family; not eligibility primary)
    "teacher_lowseq_ptc2": {
        **DEFAULTS,
        "seq_page_cost": 0.2,
        "parallel_tuple_cost": 2.0,
    },
}


def load_sql_file(path: str) -> str:
    body = (ROOT / path).read_text(encoding="utf-8")
    lines = []
    for line in body.splitlines():
        s = line.strip()
        if not s or s.startswith("--"):
            continue
        lines.append(line)
    return "\n".join(lines).strip().rstrip(";")


def major_nodes(plan: str) -> List[str]:
    nodes: List[str] = []
    skip_prefixes = (
        "Workers Planned",
        "Sort Key",
        "Group Key",
        "Hash Cond",
        "Merge Cond",
        "Filter",
        "Join Filter",
        "Output",
        "Workers Launched",
        "Index Cond",
        "Recheck Cond",
        "Rows Removed",
        "Buckets",
        "Batches",
        "Memory Usage",
        "Heap Blocks",
        "Planning Time",
        "Execution Time",
        "Buffers",
        "Shared",
        "Temp",
        "I/O Timings",
        "JIT",
        "Settings",
    )
    for line in plan.splitlines():
        m = re.match(r"^\s*(?:->\s+)?([A-Za-z][A-Za-z0-9 /]+?)(?:\s+\(|$)", line)
        if not m:
            continue
        name = m.group(1).strip()
        if any(name.startswith(p) for p in skip_prefixes):
            continue
        if name not in nodes:
            nodes.append(name)
        if len(nodes) >= 18:
            break
    return nodes


def flags_from_plan(plan: str) -> Dict[str, bool]:
    return {
        "NestedLoop": "Nested Loop" in plan,
        "HashJoin": ("Hash Join" in plan) and ("Parallel Hash Join" not in plan),
        "ParHashJoin": "Parallel Hash Join" in plan,
        "MergeJoin": "Merge Join" in plan,
        "HashAgg": "HashAggregate" in plan,
        "GroupAgg": "GroupAggregate" in plan,
        "PartialHashAgg": "Partial HashAggregate" in plan,
        "Materialize": "Materialize" in plan,
        "Sort": "Sort" in plan,
        "Memoize": "Memoize" in plan,
        "Parallel": ("Gather" in plan) or ("Parallel " in plan),
        "SeqScan": "Seq Scan" in plan,
        "IdxScan": ("Index Scan" in plan) or ("Index Only Scan" in plan),
        "Bitmap": "Bitmap" in plan,
        "HashSemi": "Hash Semi Join" in plan or "Parallel Hash Semi" in plan,
        "ParHashSemi": "Parallel Hash Semi" in plan,
    }


def mem_score(flags: Dict[str, bool]) -> int:
    keys = [
        "ParHashJoin",
        "HashJoin",
        "HashAgg",
        "Materialize",
        "Sort",
        "Memoize",
        "HashSemi",
        "ParHashSemi",
        "PartialHashAgg",
    ]
    return sum(1 for k in keys if flags.get(k))


def workers_planned(plan_text: str) -> int:
    ms = re.findall(r"Workers Planned:\s*(\d+)", plan_text)
    return max((int(x) for x in ms), default=0)


def total_cost(plan_text: str) -> Optional[float]:
    m = re.search(r"cost=([0-9.]+)\.\.([0-9.]+)", plan_text)
    if not m:
        return None
    return float(m.group(2))


def fingerprint(flags: Dict[str, bool], workers: int) -> List[str]:
    return [
        "par" if flags.get("Parallel") else "ser",
        "NL" if flags.get("NestedLoop") else "",
        "MJ" if flags.get("MergeJoin") else "",
        "PHJ" if flags.get("ParHashJoin") else "",
        "HJ" if flags.get("HashJoin") else "",
        "HA" if flags.get("HashAgg") else "",
        "GA" if flags.get("GroupAgg") else "",
        "Seq" if flags.get("SeqScan") else "",
        "Idx" if flags.get("IdxScan") else "",
        "Bit" if flags.get("Bitmap") else "",
        f"w{workers}",
    ]


def is_closed_nl_bitmap(fp: List[str]) -> bool:
    # closed: serial NestedLoop + HashAgg + Idx/Bitmap
    return (
        "ser" in fp
        and "NL" in fp
        and "HA" in fp
        and ("Bit" in fp or "Idx" in fp)
        and "MJ" not in fp
    )


def explain_text(params: Dict[str, float], sql: str, analyze: bool = False) -> str:
    sets = ";\n".join(f"SET LOCAL {k} = {v}" for k, v in params.items())
    parts = [p.strip() for p in re.split(r";\s*", sql) if p.strip()]
    select_parts = [p for p in parts if re.match(r"(?is)^(with\b|select\b)", p)]
    target = select_parts[-1] if select_parts else (parts[-1] if parts else sql)
    expl = (
        "EXPLAIN (ANALYZE, BUFFERS, COSTS, TIMING OFF)"
        if analyze
        else "EXPLAIN (COSTS OFF)"
    )
    payload = (
        f"BEGIN;\n"
        f"SET LOCAL max_parallel_workers_per_gather = 4;\n"
        f"{sets};\n"
        f"{expl}\n{target};\n"
        f"ROLLBACK;\n"
    )
    env = os.environ.copy()
    env["PGPASSWORD"] = "postgres"
    cmd = [
        "psql",
        "-h",
        "127.0.0.1",
        "-p",
        str(PORT),
        "-U",
        USER,
        "-d",
        DB,
        "-v",
        "ON_ERROR_STOP=1",
        "-t",
        "-A",
        "-c",
        payload,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=360)
    if r.returncode != 0:
        raise RuntimeError(f"EXPLAIN failed: {r.stderr[:800]}")
    return r.stdout.strip()


def parse_buffers(plan_text: str) -> Dict[str, int]:
    hit = 0
    read = 0
    for m in re.finditer(
        r"Buffers:\s*shared\s+hit=(\d+)(?:\s+read=(\d+))?", plan_text, re.I
    ):
        hit = max(hit, int(m.group(1)))
        if m.group(2):
            read = max(read, int(m.group(2)))
    if hit == 0 and read == 0:
        hits = [int(x) for x in re.findall(r"shared hit=(\d+)", plan_text, re.I)]
        reads = [int(x) for x in re.findall(r"shared read=(\d+)", plan_text, re.I)]
        hit = sum(hits)
        read = sum(reads)
    return {"shared_hit": hit, "shared_read": read}


def parse_elapsed_ms(plan_text: str) -> Optional[float]:
    m = re.search(r"Execution Time:\s*([0-9.]+)\s*ms", plan_text)
    return float(m.group(1)) if m else None


def classify(
    base_flags: Dict[str, bool],
    cand_flags: Dict[str, bool],
    base_w: int,
    cand_w: int,
    base_mem: int,
    cand_mem: int,
) -> Dict[str, Any]:
    cpu_down = False
    reasons: List[str] = []
    if base_flags.get("Parallel") and not cand_flags.get("Parallel"):
        cpu_down = True
        reasons.append("lost_parallel")
    if cand_w < base_w:
        cpu_down = True
        reasons.append(f"workers_{base_w}->{cand_w}")
    if base_flags.get("ParHashJoin") and not cand_flags.get("ParHashJoin") and cand_w < base_w:
        cpu_down = True
        reasons.append("lost_phj_with_workers")
    if (
        base_flags.get("NestedLoop")
        and base_flags.get("Parallel")
        and not cand_flags.get("Parallel")
    ):
        cpu_down = True
        reasons.append("parallel_nl_collapse")

    mem_up = False
    mem_reasons: List[str] = []
    if cand_mem > base_mem:
        mem_up = True
        mem_reasons.append(f"mem_score_{base_mem}->{cand_mem}")
    for k in ["HashAgg", "HashJoin", "ParHashJoin", "Materialize", "Sort", "Memoize", "HashSemi"]:
        if cand_flags.get(k) and not base_flags.get(k):
            mem_up = True
            mem_reasons.append(f"gained_{k}")
    if base_flags.get("GroupAgg") and cand_flags.get("HashAgg") and not cand_flags.get("GroupAgg"):
        mem_up = True
        mem_reasons.append("groupagg_to_hashagg")

    return {
        "cpu_down_plan": cpu_down,
        "cpu_reasons": reasons,
        "mem_up_plan": mem_up,
        "mem_reasons": mem_reasons,
    }


def run_prewarm() -> None:
    prewarm = (ROOT / "shell/hybenchxp_cpu_prewarm_plan_relations.sql").read_text()
    # also prewarm loan tables for AP9
    extra = """
CREATE EXTENSION IF NOT EXISTS pg_prewarm;
SELECT pg_prewarm('loanapps'::regclass, 'read');
SELECT pg_prewarm('loantrans'::regclass, 'read');
SELECT pg_prewarm('loanapps_pkey'::regclass, 'read');
SELECT pg_prewarm('loantrans_pkey'::regclass, 'read');
"""
    env = os.environ.copy()
    env["PGPASSWORD"] = "postgres"
    for sql in (prewarm, extra):
        cmd = [
            "psql",
            "-h",
            "127.0.0.1",
            "-p",
            str(PORT),
            "-U",
            USER,
            "-d",
            DB,
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            sql,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=600)
        if r.returncode != 0:
            # loan indexes may differ; non-fatal for optional names
            print(f"[prewarm warning] {r.stderr[:300]}", flush=True)


def main() -> None:
    t0 = time.time()
    print(f"[v79 planmap] start port={PORT} out={OUT_DIR}", flush=True)
    sqls = {k: load_sql_file(v) for k, v in SQL_FILES.items()}
    run_prewarm()

    # base plans once
    base_plans: Dict[str, Dict[str, Any]] = {}
    for qk, sql in sqls.items():
        plan = explain_text(DEFAULTS, sql, analyze=False)
        fl = flags_from_plan(plan)
        w = workers_planned(plan)
        base_plans[qk] = {
            "plan": plan,
            "flags": fl,
            "workers": w,
            "nodes": major_nodes(plan),
            "fp": fingerprint(fl, w),
            "mem_score": mem_score(fl),
            "cost": total_cost(plan),
        }
        (PLANS / f"default__{qk}.txt").write_text(plan + "\n", encoding="utf-8")

    # map each vector
    results: Dict[str, Any] = {}
    sig_to_vecs: Dict[str, List[str]] = {}
    for vname, vec in RAW_POINTS.items():
        if vname == "default":
            continue
        per: Dict[str, Any] = {}
        for qk, sql in sqls.items():
            plan = explain_text(vec, sql, analyze=False)
            fl = flags_from_plan(plan)
            w = workers_planned(plan)
            base = base_plans[qk]
            delta = classify(
                base["flags"], fl, base["workers"], w, base["mem_score"], mem_score(fl)
            )
            fp = fingerprint(fl, w)
            entry = {
                "flags": fl,
                "workers": w,
                "nodes": major_nodes(plan),
                "fp": fp,
                "base_fp": base["fp"],
                "base_nodes": base["nodes"],
                "base_workers": base["workers"],
                "mem_score": mem_score(fl),
                "base_mem_score": base["mem_score"],
                "cost": total_cost(plan),
                "base_cost": base["cost"],
                "cost_ratio": (
                    (total_cost(plan) / base["cost"])
                    if total_cost(plan) and base["cost"]
                    else None
                ),
                "distinct_from_closed": not is_closed_nl_bitmap(fp),
                **delta,
            }
            per[qk] = entry
            (PLANS / f"{vname}__{qk}.txt").write_text(plan + "\n", encoding="utf-8")

        # structural signature for collapse: CPU-role fps + mem-role fps
        sig_parts = []
        for qk in (
            "ap6_default",
            "ap6_range4m",
            "ap6_range5m",
            "ap6_amount_pos",
            "ap9_exists",
            "ap8_filter",
        ):
            e = per[qk]
            sig_parts.append(f"{qk}:" + ",".join(x for x in e["fp"] if x))
        sig = "|".join(sig_parts)
        sig_to_vecs.setdefault(sig, []).append(vname)

        cpu_roles = [
            q
            for q, e in per.items()
            if e["cpu_down_plan"]
            and e["distinct_from_closed"]
            and q.startswith("ap6_")
            and q != "ap6_range3m_closed"
        ]
        mergejoin_cpu = [
            q
            for q in cpu_roles
            if per[q]["flags"].get("MergeJoin") and not per[q]["flags"].get("Parallel")
        ]
        mem_roles = [
            q
            for q, e in per.items()
            if e["mem_up_plan"] and q in ("ap9_exists", "ap8_filter", "ap11_category")
        ]
        # independent = mem-up without requiring the CPU role itself
        indep_mem = [q for q in mem_roles if q not in mergejoin_cpu]

        results[vname] = {
            "vector": vec,
            "cpu_roles": cpu_roles,
            "mergejoin_cpu": mergejoin_cpu,
            "mem_roles": mem_roles,
            "indep_mem": indep_mem,
            "joint_mj_mem": bool(mergejoin_cpu and indep_mem),
            "signature": sig,
            "per": per,
        }
        print(
            f"[map] {vname}: mj={mergejoin_cpu} mem={indep_mem} joint={bool(mergejoin_cpu and indep_mem)}",
            flush=True,
        )

    # collapse identical signatures; keep first + teachers
    kept: List[str] = []
    for sig, vecs in sig_to_vecs.items():
        primary = vecs[0]
        if primary not in kept:
            kept.append(primary)
        for v in vecs[1:]:
            results[v]["collapsed_into"] = primary
            results[v]["duplicate_signature"] = True
    # always keep ptc3 / citc if present
    for must in ("ptc3", "citc_coc_ptc3"):
        if must in results and must not in kept:
            kept.append(must)
    kept = kept[:8]
    print(f"[collapse] kept vectors ({len(kept)}): {kept}", flush=True)

    # ANALYZE for promising joints: mergejoin CPU roles + ap9
    analyze: Dict[str, Any] = {}
    # prewarm again before timed runs
    run_prewarm()
    candidates_for_analyze: List[Tuple[str, str]] = []
    for vname in kept:
        r = results[vname]
        for q in r["mergejoin_cpu"]:
            candidates_for_analyze.append((vname, q))
        if r["mergejoin_cpu"]:
            candidates_for_analyze.append((vname, "ap9_exists"))
            candidates_for_analyze.append((vname, "ap8_filter"))

    # uniqueness
    seen = set()
    uniq: List[Tuple[str, str]] = []
    for pair in candidates_for_analyze:
        if pair not in seen:
            seen.add(pair)
            uniq.append(pair)

    for vname, qk in uniq:
        vec = results[vname]["vector"]
        sql = sqls[qk]
        # default timed (2 warmups then measure)
        for _ in range(2):
            explain_text(DEFAULTS, sql, analyze=True)
        base_plan = explain_text(DEFAULTS, sql, analyze=True)
        # candidate
        for _ in range(1):
            explain_text(vec, sql, analyze=True)
        cand_plan = explain_text(vec, sql, analyze=True)
        base_elapsed = parse_elapsed_ms(base_plan)
        cand_elapsed = parse_elapsed_ms(cand_plan)
        base_buf = parse_buffers(base_plan)
        cand_buf = parse_buffers(cand_plan)
        ratio = (
            (cand_elapsed / base_elapsed)
            if base_elapsed and cand_elapsed and base_elapsed > 0
            else None
        )
        rec = {
            "base_elapsed": base_elapsed,
            "cand_elapsed": cand_elapsed,
            "elapsed_ratio": ratio,
            "base_read": base_buf["shared_read"],
            "cand_read": cand_buf["shared_read"],
            "base_hit": base_buf["shared_hit"],
            "cand_hit": cand_buf["shared_hit"],
            "base_w": workers_planned(base_plan),
            "cand_w": workers_planned(cand_plan),
            "cand_nodes": major_nodes(cand_plan),
            "base_nodes": major_nodes(base_plan),
            "cand_fp": fingerprint(flags_from_plan(cand_plan), workers_planned(cand_plan)),
            "base_fp": fingerprint(flags_from_plan(base_plan), workers_planned(base_plan)),
        }
        analyze.setdefault(vname, {})[qk] = rec
        (PLANS / f"analyze_{vname}__{qk}.txt").write_text(
            "=== BASE ===\n" + base_plan + "\n=== CAND ===\n" + cand_plan + "\n",
            encoding="utf-8",
        )
        print(
            f"[analyze] {vname}/{qk}: base={base_elapsed:.1f}ms cand={cand_elapsed:.1f}ms "
            f"ratio={ratio} reads={base_buf['shared_read']}->{cand_buf['shared_read']}",
            flush=True,
        )

    # Stability screen on default ANALYZE: 3 repeats for each AP6 variant under default
    stability: Dict[str, Any] = {}
    run_prewarm()
    for qk in ("ap6_default", "ap6_range4m", "ap6_range5m", "ap6_amount_pos"):
        sql = sqls[qk]
        samples = []
        for i in range(3):
            plan = explain_text(DEFAULTS, sql, analyze=True)
            samples.append(
                {
                    "elapsed": parse_elapsed_ms(plan),
                    "read": parse_buffers(plan)["shared_read"],
                    "hit": parse_buffers(plan)["shared_hit"],
                    "workers": workers_planned(plan),
                    "fp": fingerprint(flags_from_plan(plan), workers_planned(plan)),
                    "nodes": major_nodes(plan),
                }
            )
        reads = [s["read"] for s in samples if s["read"] is not None]
        elaps = [s["elapsed"] for s in samples if s["elapsed"] is not None]
        stability[qk] = {
            "samples": samples,
            "read_max": max(reads) if reads else None,
            "read_mean": (sum(reads) / len(reads)) if reads else None,
            "elapsed_cv": (
                (max(elaps) - min(elaps)) / (sum(elaps) / len(elaps))
                if elaps and sum(elaps) > 0
                else None
            ),
            "low_phys_io": (max(reads) if reads else 10**9) < 20000,
            "parallel_default": all(s["workers"] > 0 for s in samples),
        }
        print(
            f"[stability] {qk}: reads={reads} elapsed={elaps} low_io={stability[qk]['low_phys_io']}",
            flush=True,
        )

    # Eligibility selection
    eligible = False
    chosen: Optional[Dict[str, Any]] = None
    ranked: List[Dict[str, Any]] = []
    for vname in kept:
        r = results[vname]
        if vname.startswith("teacher"):
            continue
        for q in r["mergejoin_cpu"]:
            an = analyze.get(vname, {}).get(q, {})
            ratio = an.get("elapsed_ratio")
            if ratio is None or ratio > 0.87:  # need ~>=15% elapsed improvement
                continue
            if is_closed_nl_bitmap(an.get("cand_fp") or r["per"][q]["fp"]):
                continue
            # prefer MergeJoin cand
            if "Merge Join" not in (an.get("cand_nodes") or r["per"][q]["nodes"]):
                continue
            # stability of role
            stab = stability.get(q, {})
            # Memory role: prefer ap9_exists if mem_up or Hash/Group transition
            mem_ok = []
            for mq in ("ap9_exists", "ap8_filter", "ap11_category"):
                e = r["per"][mq]
                if e["mem_up_plan"] or (
                    e["flags"].get("HashAgg") or e["flags"].get("ParHashJoin") or e["flags"].get("HashJoin")
                ):
                    # reject if this is transfer-dominant path
                    if mq == "ap10_transfer":
                        continue
                    mem_ok.append(mq)
            # require ap9 or ap8 as memory path (low-IO roles)
            primary_mem = None
            for pref in ("ap9_exists", "ap8_filter", "ap11_category"):
                if pref in mem_ok:
                    primary_mem = pref
                    break
            if primary_mem is None:
                continue
            score = 0.0
            score += max(0.0, 1.0 - float(ratio)) * 100.0
            if stab.get("low_phys_io"):
                score += 20
            if q != "ap6_default":
                score += 10  # prefer bounded variants for stability intent
            if primary_mem == "ap9_exists":
                score += 15
            if an.get("base_read", 10**9) < 25000:
                score += 10
            if an.get("cand_read", 10**9) < 5000:
                score += 10
            ranked.append(
                {
                    "vector_name": vname,
                    "cpu_role": q,
                    "memory_role": primary_mem,
                    "elapsed_ratio": ratio,
                    "score": score,
                    "stability": stab,
                    "analyze": an,
                    "mem_roles": mem_ok,
                }
            )

    ranked.sort(key=lambda x: -x["score"])
    if ranked:
        best = ranked[0]
        # require either low_phys_io or clearly reduced base_read vs default full ap6
        ap6_def_read = stability.get("ap6_default", {}).get("read_mean") or 10**9
        role_read = stability.get(best["cpu_role"], {}).get("read_mean")
        stable_enough = bool(
            stability.get(best["cpu_role"], {}).get("low_phys_io")
            or (role_read is not None and role_read <= ap6_def_read * 1.05)
        )
        # also require parallel default
        if not stability.get(best["cpu_role"], {}).get("parallel_default"):
            stable_enough = False
        # memory same-vector present
        if best["memory_role"] and stable_enough and best["elapsed_ratio"] <= 0.87:
            eligible = True
            chosen = best

    decision = {
        "eligible": eligible,
        "controller": "v79_stable_mergejoin",
        "when": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "worker_probe": f"cpu_mem_w1:{PORT}",
        "closed_composition_excluded": {
            "ap6": "shell/hybenchxp_cpu_plan_sensitive_v6_ap6_source_range3m.sql",
            "ap8": "shell/hybenchxp_none_ap6_hashagg_range_10k.sql",
            "vector_family": "lowseq + parallel_tuple_cost 2.0/2.5",
            "closed_cpu_fingerprint": CLOSED_CAND_FP,
        },
        "capability_fixed": {"cpu_cores": 11.1, "memory_gib": 8.8},
        "vectors_mapped": list(results.keys()),
        "vectors_kept_after_collapse": kept,
        "signature_collapse": {k: v for k, v in sig_to_vecs.items()},
        "ranked_candidates": ranked[:10],
        "chosen": chosen,
        "stability": stability,
        "analyze": analyze,
        "fallback_absolute_db_qps": 183.02089028620412,
        "fallback_source": "v76h_w2_lowseq_ptc2p5",
        "reason": (
            "stable low-IO MergeJoin CPU role with same-vector Memory transfer via AP9/AP8"
            if eligible
            else "no stable low-IO MergeJoin variant with same-vector Memory transfer; preserve fallback"
        ),
        "elapsed_s": round(time.time() - t0, 2),
    }

    if eligible and chosen is not None:
        vname = chosen["vector_name"]
        decision["chosen_primary_vector_name"] = vname
        decision["chosen_vector"] = results[vname]["vector"]
        decision["alternate_vector_name"] = (
            "citc_coc_ptc3" if "citc_coc_ptc3" in results else None
        )
        decision["alternate_vector"] = (
            results.get("citc_coc_ptc3", {}).get("vector")
            if "citc_coc_ptc3" in results
            else None
        )
        decision["structural_cpu_relief"] = {
            "query_key": chosen["cpu_role"],
            "sql_file": SQL_FILES[chosen["cpu_role"]],
            "baseline_plan": " / ".join(
                (chosen["analyze"].get("base_nodes") or [])[:6]
            ),
            "candidate_plan": " / ".join(
                (chosen["analyze"].get("cand_nodes") or [])[:6]
            ),
            "fingerprint_cand": chosen["analyze"].get("cand_fp"),
            "distinct_from_closed_nl_serial": True,
            "analyze_elapsed_ms": {
                "base": chosen["analyze"].get("base_elapsed"),
                "cand": chosen["analyze"].get("cand_elapsed"),
                "ratio": chosen["analyze"].get("elapsed_ratio"),
            },
        }
        decision["memory_transfer"] = {
            "primary": chosen["memory_role"],
            "sql_file": SQL_FILES[chosen["memory_role"]],
            "avoid_dominant": "ap10_transfer",
            "secondary_roles": [x for x in chosen["mem_roles"] if x != chosen["memory_role"]],
        }
        # Predeclared composition: AP9 primary memory; no AP10 dominance
        # weights: AP6 heavy for CPU, AP9 memory, AP8 filter secondary, light AP11
        weights = {
            "ap_query_ids": "6,8,9,10,11",
            "ap_query_weights": "50,22,16,8,4",
            "statement_sql_files": {
                "AP-6": SQL_FILES[chosen["cpu_role"]],
                "AP-8": SQL_FILES[chosen["memory_role"]],  # AP9 exists primary memory
                "AP-9": SQL_FILES["ap8_filter"],
                "AP-10": SQL_FILES["ap11_category"],
                "AP-11": SQL_FILES["ap8_filter"],  # light secondary; avoid transfer
            },
            "roles": {
                "AP-6": f"CPU relief: {chosen['cpu_role']} parallel → serial MergeJoin under ptc3",
                "AP-8": f"Memory primary: {chosen['memory_role']} low-IO Hash/Group family",
                "AP-9": "Secondary filter join Hash/PHJ",
                "AP-10": "Light category retain",
                "AP-11": "Secondary filter retain (no AP10 transfer)",
            },
            "note": "AP10 transfer deliberately not dominant; AP11 slot reuses filter/category low-IO SQL",
        }
        # build fixed sequence of length 100 matching weights 50/22/16/8/4
        seq = _build_sequence({6: 50, 8: 22, 9: 16, 10: 8, 11: 4})
        weights["ap_query_sequence"] = ",".join(str(x) for x in seq)
        weights["counts"] = {"6": 50, "8": 22, "9": 16, "10": 8, "11": 4}
        decision["workload_predeclaration"] = weights

    # write artifacts
    (OUT_DIR / "planmap_v79_results.json").write_text(
        json.dumps(
            {
                "results": {
                    k: {
                        kk: vv
                        for kk, vv in v.items()
                        if kk != "per"
                    }
                    | {
                        "per_compact": {
                            q: {
                                "cpu_down": e["cpu_down_plan"],
                                "mem_up": e["mem_up_plan"],
                                "fp": e["fp"],
                                "base_fp": e["base_fp"],
                                "nodes": e["nodes"][:8],
                                "base_nodes": e["base_nodes"][:8],
                                "workers": [e["base_workers"], e["workers"]],
                                "distinct_from_closed": e["distinct_from_closed"],
                                "cost_ratio": e["cost_ratio"],
                            }
                            for q, e in v.get("per", {}).items()
                        }
                    }
                    for k, v in results.items()
                },
                "analyze": analyze,
                "stability": stability,
                "kept": kept,
            },
            indent=2,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
    (OUT_DIR / "planmap_v79_decision.json").write_text(
        json.dumps(decision, indent=2, default=str) + "\n", encoding="utf-8"
    )
    md = _decision_md(decision)
    (OUT_DIR / "planmap_v79_eligibility_decision.md").write_text(md, encoding="utf-8")
    print(json.dumps({"eligible": eligible, "chosen": chosen}, indent=2, default=str), flush=True)
    print(f"[v79 planmap] done eligible={eligible} elapsed={time.time()-t0:.1f}s", flush=True)


def _build_sequence(counts: Dict[int, int]) -> List[int]:
    """Interleave roles; keep AP-6 dominant but not clumped as a single block."""
    remaining = dict(counts)
    order = [6, 8, 9, 6, 8, 6, 9, 6, 10, 6, 8, 6, 9, 6, 11, 6, 8, 6, 9, 6]
    seq: List[int] = []
    # fill to sum(counts)
    total = sum(counts.values())
    i = 0
    while len(seq) < total:
        q = order[i % len(order)]
        i += 1
        if remaining.get(q, 0) > 0:
            seq.append(q)
            remaining[q] -= 1
            continue
        # pick any remaining by priority
        for p in (6, 8, 9, 10, 11):
            if remaining.get(p, 0) > 0:
                seq.append(p)
                remaining[p] -= 1
                break
        else:
            break
    return seq


def _decision_md(d: Dict[str, Any]) -> str:
    lines = [
        "# v79 Phase A plan eligibility decision",
        "",
        f"**When:** {d.get('when')}",
        f"**Worker:** {d.get('worker_probe')}",
        f"**Artifacts:** `{OUT_DIR}/`",
        "",
        f"## Decision: **{'ELIGIBLE' if d.get('eligible') else 'NOT ELIGIBLE'}**",
        "",
        d.get("reason", ""),
        "",
    ]
    if d.get("eligible") and d.get("chosen"):
        c = d["chosen"]
        lines += [
            f"- Primary vector: `{d.get('chosen_primary_vector_name')}`",
            f"- CPU role: `{c.get('cpu_role')}` → `{d['structural_cpu_relief']['sql_file']}`",
            f"- ANALYZE ratio: {c.get('elapsed_ratio')}",
            f"- Memory primary: `{c.get('memory_role')}`",
            f"- Alternate vector: `{d.get('alternate_vector_name')}`",
            "",
            "### Predeclared weights",
            "```json",
            json.dumps(d.get("workload_predeclaration"), indent=2),
            "```",
            "",
        ]
    else:
        lines += [
            "Stop before runtime. Preserve absolute DB-QPS fallback:",
            f"`{d.get('fallback_absolute_db_qps')}` from `{d.get('fallback_source')}`.",
            "",
        ]
    lines += [
        "### Closed composition excluded",
        "- AP-6 range3m + none_hashagg + lowseq/ptc∈{2.0,2.5}",
        "- Closed CPU fingerprint: serial NestedLoop + HashAgg + Idx/Bitmap",
        "",
        "### Capability (fixed)",
        "- CPU 11.1 cores / Memory 8.8 GiB — **no re-normalization**",
        "",
    ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()

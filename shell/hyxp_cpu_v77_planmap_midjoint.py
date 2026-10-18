#!/usr/bin/env python3
"""v77 midpoint-joint plan map on cpu_mem_w1 (port 56433).

Bounded seven-vector map around lowseq_ptc2..lowseq_ptc2p5 for the v76h/v77
workload families: AP6 range3m (CPU serialize lever) + none_hashagg AP-8
(HashAgg/Seq IO lever), plus AP9/10/11 retain signatures.

Axis: parallel_tuple_cost with explicit full seven-keys. Only spend EXPLAIN on
discrete fingerprint changes (at most 12 vectors including default).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Tuple

PORT = 56433
DB = "hybench_sf10x"
USER = "postgres"
ROOT = Path("/home/anonymous/program/resource_adaptive")
OUT_DIR = Path("/tmp/hyxp_cpu_planmap_v77_20260714")
OUT_DIR.mkdir(parents=True, exist_ok=True)
PLANS_DIR = OUT_DIR / "plans"
PLANS_DIR.mkdir(parents=True, exist_ok=True)

DEFAULTS: Dict[str, float] = {
    "seq_page_cost": 1.0,
    "random_page_cost": 4.0,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
}


def vec(seq: float | None = None, ptc: float | None = None, **kw: float) -> Dict[str, float]:
    p = dict(DEFAULTS)
    if seq is not None:
        p["seq_page_cost"] = float(seq)
    if ptc is not None:
        p["parallel_tuple_cost"] = float(ptc)
    p.update(kw)
    return p


# Predeclared ≤12 seven-vectors. Intermediate ptc only if EXPLAIN shows distinct AP6/AP8 phase.
POINTS: Dict[str, Dict[str, float]] = {
    "default": vec(),
    "lowseq_only": vec(seq=0.2, ptc=0.1),
    "lowseq_ptc2": vec(seq=0.2, ptc=2.0),
    "lowseq_ptc2p1": vec(seq=0.2, ptc=2.1),
    "lowseq_ptc2p2": vec(seq=0.2, ptc=2.2),
    "lowseq_ptc2p3": vec(seq=0.2, ptc=2.3),
    "lowseq_ptc2p4": vec(seq=0.2, ptc=2.4),
    "lowseq_ptc2p5": vec(seq=0.2, ptc=2.5),
    "lowseq_highrpc_ptc2": vec(seq=0.2, ptc=2.0, random_page_cost=16.0),
    "lowseq_psc3k_ptc2": vec(seq=0.2, ptc=2.0, parallel_setup_cost=3000.0),
    "lowseq_ptc2_citc": vec(
        seq=0.2, ptc=2.0, cpu_index_tuple_cost=0.05, cpu_operator_cost=0.025
    ),
    "lowseq_ptc2p5_citc": vec(
        seq=0.2, ptc=2.5, cpu_index_tuple_cost=0.05, cpu_operator_cost=0.025
    ),
}

SQL_FILES: Dict[str, str] = {
    "AP6_range3m": "shell/hybenchxp_cpu_plan_sensitive_v6_ap6_source_range3m.sql",
    "AP8_none_hashagg_10k": "shell/hybenchxp_none_ap6_hashagg_range_10k.sql",
    "AP9_exists": "shell/hybenchxp_cpu_plan_sensitive_v4_ap9_exists_loan_no_trans.sql",
    "AP10_transfer": "shell/hybenchxp_cpu_plan_sensitive_v4_ap10_transfer_customer_company_group.sql",
    "AP11_category": "shell/hybenchxp_cpu_plan_sensitive_v4_ap11_company_category_transfer.sql",
}


def load_sql(path: str) -> str:
    body = (ROOT / path).read_text(encoding="utf-8")
    lines = []
    for line in body.splitlines():
        s = line.strip()
        if not s or s.startswith("--"):
            continue
        lines.append(line)
    return "\n".join(lines).strip().rstrip(";")


def flags_from_plan(plan: str) -> Dict[str, bool]:
    return {
        "NestedLoop": "Nested Loop" in plan,
        "HashJoin": ("Hash Join" in plan) and ("Parallel Hash Join" not in plan),
        "ParHashJoin": "Parallel Hash Join" in plan,
        "HashAgg": "HashAggregate" in plan,
        "GroupAgg": "GroupAggregate" in plan,
        "Parallel": ("Gather" in plan) or ("Parallel " in plan),
        "Gather": "Gather" in plan and "Gather Merge" not in plan,
        "GatherMerge": "Gather Merge" in plan,
        "SeqScan": "Seq Scan" in plan,
        "IdxScan": ("Index Scan" in plan) or ("Index Only Scan" in plan),
        "Bitmap": "Bitmap" in plan,
        "Sort": "Sort" in plan,
        "Materialize": "Materialize" in plan,
    }


def major_nodes(plan: str) -> List[str]:
    nodes: List[str] = []
    for line in plan.splitlines():
        m = re.match(r"^\s*(?:->\s+)?([A-Za-z][A-Za-z0-9 ]+?)(?:\s+\(|$)", line)
        if not m:
            continue
        name = m.group(1).strip()
        if name.startswith("Workers") or name in {
            "Sort Key",
            "Group Key",
            "Hash Cond",
            "Filter",
            "Join Filter",
            "Output",
            "Index Cond",
            "Recheck Cond",
            "Rows Removed by Filter",
            "Bucket",
            "Batches",
            "Memory Usage",
            "Heap Blocks",
        }:
            continue
        if name not in nodes:
            nodes.append(name)
        if len(nodes) >= 16:
            break
    return nodes


def fingerprint(flags: Dict[str, bool], nodes: List[str], workers: int) -> str:
    key_flags = [
        k
        for k, v in flags.items()
        if v
        and k
        in {
            "NestedLoop",
            "HashJoin",
            "ParHashJoin",
            "HashAgg",
            "GroupAgg",
            "Parallel",
            "SeqScan",
            "IdxScan",
            "Bitmap",
            "Sort",
        }
    ]
    return f"W{workers}|{'+'.join(key_flags)}|{'/'.join(nodes[:8])}"


def workers_planned(plan_text: str) -> int:
    ms = re.findall(r"Workers Planned:\s*(\d+)", plan_text)
    return max((int(x) for x in ms), default=0)


def explain_text(params: Dict[str, float], sql: str) -> str:
    sets = ";\n".join(f"SET LOCAL {k} = {v}" for k, v in params.items())
    parts = [p.strip() for p in re.split(r";\s*", sql) if p.strip()]
    select_parts = [p for p in parts if re.match(r"(?is)^(with\b|select\b)", p)]
    target = select_parts[-1] if select_parts else (parts[-1] if parts else sql)
    payload = (
        f"BEGIN;\n"
        f"SET LOCAL max_parallel_workers_per_gather = 4;\n"
        f"{sets};\n"
        f"EXPLAIN (COSTS OFF)\n{target};\n"
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
    r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=180)
    if r.returncode != 0:
        raise RuntimeError(f"EXPLAIN failed: {r.stderr[:500]}")
    return r.stdout.strip()


def classify_ap6(base: Dict[str, Any], cand: Dict[str, Any]) -> Dict[str, Any]:
    """CPU relief: lose Parallel / NestedLoop heavy path."""
    bf, cf = base["flags"], cand["flags"]
    reasons: List[str] = []
    cpu_relief = False
    if bf.get("Parallel") and not cf.get("Parallel"):
        cpu_relief = True
        reasons.append("serialize_lose_parallel")
    if base["workers"] > 0 and cand["workers"] == 0:
        cpu_relief = True
        reasons.append("workers_0")
    if bf.get("NestedLoop") and cf.get("ParHashJoin"):
        # PHJ may still be CPU-heavy; note but not primary relief
        reasons.append("nl_to_phj")
    if bf.get("NestedLoop") and cf.get("HashJoin") and not cf.get("Parallel"):
        cpu_relief = True
        reasons.append("nl_to_serial_hash")
    return {"cpu_relief": cpu_relief, "reasons": reasons}


def classify_ap8(base: Dict[str, Any], cand: Dict[str, Any]) -> Dict[str, Any]:
    """IO joint: Group->HashAgg and/or Bitmap->SeqScan, preferably keep Parallel."""
    bf, cf = base["flags"], cand["flags"]
    reasons: List[str] = []
    io_joint = False
    if bf.get("GroupAgg") and cf.get("HashAgg") and not bf.get("HashAgg"):
        io_joint = True
        reasons.append("group_to_hashagg")
    if bf.get("Bitmap") and cf.get("SeqScan") and not cf.get("Bitmap"):
        io_joint = True
        reasons.append("bitmap_to_seq")
    if (not bf.get("SeqScan")) and cf.get("SeqScan"):
        io_joint = True
        reasons.append("gain_seqscan")
    keep_parallel = bool(cf.get("Parallel"))
    return {
        "io_joint": io_joint,
        "keep_parallel": keep_parallel,
        "hashagg": bool(cf.get("HashAgg")),
        "seqscan": bool(cf.get("SeqScan")),
        "reasons": reasons,
    }


def main() -> int:
    sqls = {k: load_sql(v) for k, v in SQL_FILES.items()}
    results: Dict[str, Any] = {
        "port": PORT,
        "db": DB,
        "defaults": DEFAULTS,
        "points": POINTS,
        "queries": list(SQL_FILES.keys()),
        "sql_files": SQL_FILES,
        "matrix": {},
        "signatures": {},
        "phases": {},
    }

    # EXPLAIN matrix
    for pname, params in POINTS.items():
        # ensure all 7 keys explicit
        assert set(params.keys()) == set(DEFAULTS.keys()), pname
        row: Dict[str, Any] = {"params": params, "queries": {}}
        sig_parts = []
        for qname, sql in sqls.items():
            plan = explain_text(params, sql)
            fl = flags_from_plan(plan)
            nodes = major_nodes(plan)
            wp = workers_planned(plan)
            fp = fingerprint(fl, nodes, wp)
            cell = {
                "flags": fl,
                "nodes": nodes,
                "workers": wp,
                "fingerprint": fp,
            }
            row["queries"][qname] = cell
            sig_parts.append(f"{qname}={fp}")
            (PLANS_DIR / f"{pname}__{qname}.txt").write_text(plan + "\n", encoding="utf-8")
        row["joint_signature"] = " || ".join(sig_parts)
        results["matrix"][pname] = row
        results["signatures"][pname] = {
            q: row["queries"][q]["fingerprint"] for q in sqls
        }
        print(f"[ok] {pname}", flush=True)

    base = results["matrix"]["default"]
    # Phase clustering on AP6+AP8 fingerprints only (structural common-vector)
    phase_key: Dict[str, str] = {}
    for pname, row in results["matrix"].items():
        if pname == "default":
            continue
        k = (
            row["queries"]["AP6_range3m"]["fingerprint"]
            + " :: "
            + row["queries"]["AP8_none_hashagg_10k"]["fingerprint"]
        )
        phase_key[pname] = k

    # unique phases in ptc axis order
    ptc_axis = [
        "lowseq_only",
        "lowseq_ptc2",
        "lowseq_ptc2p1",
        "lowseq_ptc2p2",
        "lowseq_ptc2p3",
        "lowseq_ptc2p4",
        "lowseq_ptc2p5",
    ]
    unique_phases: List[Dict[str, Any]] = []
    seen: Dict[str, str] = {}
    for pname in ptc_axis:
        k = phase_key[pname]
        if k not in seen:
            seen[k] = pname
            unique_phases.append({"phase_id": len(unique_phases), "rep": pname, "key": k})

    joint_eval: Dict[str, Any] = {}
    for pname, row in results["matrix"].items():
        if pname == "default":
            continue
        ap6 = classify_ap6(base["queries"]["AP6_range3m"], row["queries"]["AP6_range3m"])
        ap8 = classify_ap8(
            base["queries"]["AP8_none_hashagg_10k"],
            row["queries"]["AP8_none_hashagg_10k"],
        )
        # secondary retain
        ap9_fp_change = (
            row["queries"]["AP9_exists"]["fingerprint"]
            != base["queries"]["AP9_exists"]["fingerprint"]
        )
        ap10_fp_change = (
            row["queries"]["AP10_transfer"]["fingerprint"]
            != base["queries"]["AP10_transfer"]["fingerprint"]
        )
        common = bool(ap6["cpu_relief"] and ap8["io_joint"])
        joint_eval[pname] = {
            "ap6": ap6,
            "ap8": ap8,
            "ap9_fp_change": ap9_fp_change,
            "ap10_fp_change": ap10_fp_change,
            "common_vector_joint": common,
            "ap6_fp": row["queries"]["AP6_range3m"]["fingerprint"],
            "ap8_fp": row["queries"]["AP8_none_hashagg_10k"]["fingerprint"],
            "same_as_phase_rep": seen.get(phase_key[pname]) == pname
            if pname in phase_key
            else None,
            "phase_rep": seen.get(phase_key.get(pname, ""), None),
        }

    # Eligibility: strongest common structural phase among unique ptc phases
    eligible = [
        (pname, joint_eval[pname])
        for pname in ptc_axis
        if joint_eval[pname]["common_vector_joint"]
        and joint_eval[pname].get("phase_rep") == pname
    ]
    # Prefer lowseq_ptc2 family (known CPU relief) then ptc2p5 (IO) as adjacent
    # Rank: ap8 keep_parallel + hashagg + seqscan first, then lower ptc for stronger serialize
    def rank_key(item: Tuple[str, Dict[str, Any]]) -> Tuple[int, int, float]:
        pname, ev = item
        ap8 = ev["ap8"]
        score = 0
        if ap8.get("hashagg"):
            score += 2
        if ap8.get("seqscan"):
            score += 2
        if ap8.get("keep_parallel"):
            score += 1
        ptc = POINTS[pname]["parallel_tuple_cost"]
        return (score, 1 if "ptc2" in pname else 0, -ptc)

    eligible_sorted = sorted(eligible, key=rank_key, reverse=True)
    cand1 = eligible_sorted[0][0] if eligible_sorted else None

    # Distinct adjacent phases for possible cand2 residual
    distinct_joint_phases = [
        u["rep"]
        for u in unique_phases
        if joint_eval[u["rep"]]["common_vector_joint"]
    ]

    decision = {
        "eligible": bool(cand1),
        "candidate1_seed": cand1,
        "candidate1_params": POINTS.get(cand1) if cand1 else None,
        "distinct_ptc_phases": unique_phases,
        "distinct_joint_phases": distinct_joint_phases,
        "joint_eval": joint_eval,
        "rationale": (
            "Common-vector joint requires AP6 CPU relief (serialize) AND AP8 IO joint "
            "(HashAgg and/or SeqScan change) under one explicit 7-cost vector. "
            "Intermediate ptc values that share AP6+AP8 fingerprints with a neighbor "
            "are not separate candidates."
        ),
        "reject_list": [
            pname
            for pname, ev in joint_eval.items()
            if not ev["common_vector_joint"]
            or (pname in phase_key and seen.get(phase_key[pname]) != pname)
        ],
    }

    results["decision"] = decision
    out_json = OUT_DIR / "planmap_v77_midjoint_latest.json"
    decision_json = OUT_DIR / "planmap_v77_decision.json"
    out_json.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    decision_json.write_text(json.dumps(decision, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({
        "eligible": decision["eligible"],
        "candidate1_seed": decision["candidate1_seed"],
        "distinct_ptc_phases": [
            {"rep": u["rep"], "key": u["key"][:80]} for u in unique_phases
        ],
        "joint": {
            k: {
                "common": v["common_vector_joint"],
                "ap6": v["ap6"]["reasons"],
                "ap8": v["ap8"]["reasons"],
                "phase_rep": v.get("phase_rep"),
            }
            for k, v in joint_eval.items()
            if k.startswith("lowseq")
        },
    }, indent=2), flush=True)
    return 0 if decision["eligible"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

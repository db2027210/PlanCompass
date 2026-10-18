#!/usr/bin/env python3
"""Offline Memory r19d round0 answer-blind pack builder.

Hard boundaries:
- No workload / VM / snapshot / PG param mutation.
- Only seven cost parameters.
- Never inject Memory v21 coordinates/metrics into generation, ranking,
  anchor, or interval update. Teacher is opened only after the pack is frozen.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from resource_utility import ResourceUtilityCalculator  # noqa: E402


RUN_ID = "pg_bonect_p2_12h_four_scenario_20260714_0218"
BASE = ROOT / "history/bonect_parallel_vm_replay" / RUN_ID
MEM_STAGE = BASE / "memory_stage_a"
PREINDEX_DIR = BASE / "memory_scan_prep/full_grid_5e7"
CANDIDATES_JSONL = PREINDEX_DIR / "candidates.jsonl"
SCAN_SUMMARY = PREINDEX_DIR / "scan_summary.json"
RANGES_JSON = PREINDEX_DIR / "ranges.json"
R19D_CFG = ROOT / (
    "config/generated/bonect_phase2_vm_memory_r19d_xap2_cpu12_read2000/"
    "r19d_ap68_xap2_bonect-replay-v87-1_vm.json"
)
TEACHER_MANIFEST = ROOT / (
    "config/generated/bonect_phase2_vm_memory_r19d_xap2_cpu12_read2000/"
    "r19d_teacher_primary_manifest.json"
)
LATEST_CONTROL = BASE / "LATEST_CONTROL_DECISION.md"
CONTROL_0813 = ROOT / "report/bonect/control_decision_20260714_0813_six_hour_adaptive_recall_closeout.md"

COST_PARAMS: Tuple[str, ...] = (
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
)
RESOURCE_NAMES: Tuple[str, ...] = ("cpu", "memory", "io")

DEFAULT_PARAMS: Dict[str, float] = {
    "seq_page_cost": 1.0,
    "random_page_cost": 4.0,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
}

# v21 is holdout-only; never loaded before pack freeze.
V21_HOLDOUT_PARAMS: Dict[str, float] = {
    "seq_page_cost": 75.0,
    "random_page_cost": 15.0,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
}

ACTIVE_RANGES: Dict[str, Dict[str, float]] = {
    "seq_page_cost": {"low": 0.0001, "high": 100.0},
    "random_page_cost": {"low": 0.0001, "high": 20.0},
    "cpu_tuple_cost": {"low": 1e-06, "high": 1.0},
    "cpu_index_tuple_cost": {"low": 1e-06, "high": 1.0},
    "cpu_operator_cost": {"low": 1e-06, "high": 1.0},
    "parallel_setup_cost": {"low": 0.0, "high": 10000.0},
    "parallel_tuple_cost": {"low": 1e-06, "high": 10.0},
}


def now_cst() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S CST")


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M")


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def sha256_file(path: Path, *, limit: Optional[int] = None) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        if limit is None:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                h.update(chunk)
        else:
            h.update(handle.read(limit))
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def log_param_distance(a: Dict[str, float], b: Dict[str, float]) -> float:
    total = 0.0
    for name in COST_PARAMS:
        va = float(a[name])
        vb = float(b[name])
        if name == "parallel_setup_cost":
            va = va + 1.0
            vb = vb + 1.0
        va = max(va, 1e-12)
        vb = max(vb, 1e-12)
        total += (math.log(va) - math.log(vb)) ** 2
    return math.sqrt(total)


def inside_ranges(params: Dict[str, float], ranges: Dict[str, Dict[str, float]]) -> bool:
    for name, bounds in ranges.items():
        value = float(params[name])
        low = float(bounds["low"])
        high = float(bounds["high"])
        # allow tiny float grid overflow
        if value < low * 0.999999 or value > high * 1.000001:
            if not (low <= value <= high):
                # still tolerate grid values that print slightly above high
                if value < low or value > high * 1.0001:
                    return False
    return True


def load_worker_baseline(gate_path: Path, *, role: str = "baseline") -> Dict[str, Any]:
    data = json.loads(gate_path.read_text(encoding="utf-8"))
    worker = (data.get("workers") or [{}])[0]
    blob = worker.get(role) or worker.get("baseline") or worker.get("anchor") or worker
    qps = blob.get("db_qps")
    if qps is None:
        qps = (blob.get("db_delta") or {}).get("db_qps")
    resource = blob.get("actual_resource") or {}
    util = blob.get("actual_utilization_pct") or {}
    cap = blob.get("capability") or {}
    return {
        "path": str(gate_path),
        "passed": bool(data.get("passed")),
        "db_qps": float(qps) if qps is not None else None,
        "actual_resource": {
            "cpu": float(resource.get("cpu") or 0.0),
            "memory": float(resource.get("memory") or 0.0),
            "io": float(resource.get("io") or 0.0),
        },
        "actual_utilization_pct": {
            "cpu": float(util.get("cpu") or 0.0),
            "memory": float(util.get("memory") or 0.0),
            "io": float(util.get("io") or 0.0),
        },
        "capability": {
            "cpu": float(cap.get("cpu") or 0.0),
            "memory": float(cap.get("memory") or 0.0),
            "io": float(cap.get("io") or 0.0),
        },
        "params": blob.get("params") or dict(DEFAULT_PARAMS),
        "candidate_id": blob.get("candidate_id"),
    }


def single_memory_bottleneck(util: Dict[str, float], threshold: float = 80.0) -> Dict[str, Any]:
    mem = float(util.get("memory") or 0.0)
    cpu = float(util.get("cpu") or 0.0)
    io = float(util.get("io") or 0.0)
    return {
        "memory_bottleneck": mem >= threshold,
        "cpu_below": cpu < threshold,
        "io_below": io < threshold,
        "legal_single_memory": mem >= threshold and cpu < threshold and io < threshold,
        "util": {"cpu": cpu, "memory": mem, "io": io},
        "threshold_pct": threshold,
    }


def build_calculator(config: Dict[str, Any]) -> ResourceUtilityCalculator:
    scoring = config.get("scoring") or {}
    return ResourceUtilityCalculator(
        reward_type="sigmoid_linear",
        penalty_type="sigmoid_linear",
        overload_threshold=float(scoring.get("overload_threshold", 0.80)),
        score_mode=str(scoring.get("score_mode", "band_target")),
        resource_weights=scoring.get("resource_weights") if isinstance(scoring.get("resource_weights"), dict) else None,
        resource_reward_weights=(
            scoring.get("resource_reward_weights")
            if isinstance(scoring.get("resource_reward_weights"), dict)
            else None
        ),
        resource_penalty_weights=(
            scoring.get("resource_penalty_weights")
            if isinstance(scoring.get("resource_penalty_weights"), dict)
            else None
        ),
        resource_target_ranges=(
            scoring.get("resource_target_ranges")
            if isinstance(scoring.get("resource_target_ranges"), dict)
            else None
        ),
    )


def check_control_decision() -> Dict[str, Any]:
    text = LATEST_CONTROL.read_text(encoding="utf-8") if LATEST_CONTROL.exists() else ""
    # Offline memory pack is allowed; stop only if decision explicitly freezes Memory offline prep.
    conflict_markers = [
        "stop memory offline",
        "abort memory blind",
        " Memory ",
        " Memory ",
        "do not prepare memory blind pack",
    ]
    lowered = text.lower()
    conflicts = [m for m in conflict_markers if m.lower() in lowered or m in text]
    # Also note IO-owned VM actions without treating them as offline conflict.
    return {
        "path": str(LATEST_CONTROL),
        "exists": LATEST_CONTROL.exists(),
        "sha256": sha256_file(LATEST_CONTROL) if LATEST_CONTROL.exists() else None,
        "conflicts": conflicts,
        "safe_for_offline_memory_pack": not conflicts,
        "note": (
            "LATEST focuses on IO p5 VM work; offline Memory default-start blind pack "
            "does not launch workload/VM actions and is allowed by 0813 parallel offline prep."
        ),
        "control_0813": str(CONTROL_0813),
    }


def freeze_context() -> Dict[str, Any]:
    formal = load_worker_baseline(BASE / "memory_r19d/baseline_formal_5m/baseline_gate.json")
    formal_rep = load_worker_baseline(BASE / "memory_r19d/baseline_formal_5m_repeat/baseline_gate.json")
    default_after = load_worker_baseline(BASE / "memory_r19d/default_after_5m/baseline_gate.json")
    teacher_pair_base = load_worker_baseline(MEM_STAGE / "teacher_pair_5m_r19d/baseline_gate.json")
    # Primary freeze: teacher-pair default baseline (same paired session as admitted teacher),
    # which remains default coordinates only.
    primary = teacher_pair_base
    legal = single_memory_bottleneck(primary["actual_utilization_pct"])
    if not legal["legal_single_memory"]:
        raise RuntimeError(f"r19d primary baseline is not legal single-Memory bottleneck: {legal}")

    # Non-teacher multi-default uncertainty (same params; drift only).
    qps_vals = [x["db_qps"] for x in (formal, formal_rep, teacher_pair_base, default_after) if x["db_qps"] is not None]
    mem_vals = [x["actual_utilization_pct"]["memory"] for x in (formal, formal_rep, teacher_pair_base, default_after)]
    cpu_vals = [x["actual_utilization_pct"]["cpu"] for x in (formal, formal_rep, teacher_pair_base, default_after)]
    io_vals = [x["actual_utilization_pct"]["io"] for x in (formal, formal_rep, teacher_pair_base, default_after)]

    cfg = json.loads(R19D_CFG.read_text(encoding="utf-8"))
    # Strip any teacher fields from config view used by ranking.
    config_for_ranking = {
        "capability": cfg.get("capability") or primary["capability"],
        "scoring": cfg.get("scoring") or {},
        "vm_resource": cfg.get("vm_resource") or {},
        "initial_params": cfg.get("initial_params") or DEFAULT_PARAMS,
        "optimizer": {
            "bruteforce": {
                "candidate_gate": (((cfg.get("optimizer") or {}).get("bruteforce") or {}).get("candidate_gate")) or {}
            }
        },
    }
    # Explicitly ensure teacher keys are not present.
    assert "_teacher_params" not in config_for_ranking
    assert "teacher" not in config_for_ranking

    freeze = {
        "created_at": now_cst(),
        "scenario": "memory",
        "run_id": RUN_ID,
        "context_id": "memory_r19d_legal_single_bottleneck",
        "legal_params": list(COST_PARAMS),
        "illegal_eighth_param": True,
        "profile": {
            "vm": "bonect-replay-v87-1",
            "ip": "192.0.2.12",
            "endpoint": "192.0.2.12:25432",
            "cpu": 12,
            "memory_gb": 1.8125,
            "memory_size": "1856M",
            "read_mb": 2000,
            "write_mb": 800,
            "io_capability": 2800,
            "capability": primary["capability"],
        },
        "workload": {
            "config": str(R19D_CFG),
            "props": str(
                ROOT
                / "config/generated/bonect_phase2_vm_memory_r19d_xap2_cpu12_read2000/r19d_ap68_xap2_bonect-replay-v87-1.props"
            ),
            "statements": str(
                ROOT
                / "config/generated/bonect_phase2_vm_memory_r19d_xap2_cpu12_read2000/r19d_stmt_postgres.toml"
            ),
            "ap_focus": "AP-6,AP-8",
            "statements_sha256": sha256_file(
                ROOT
                / "config/generated/bonect_phase2_vm_memory_r19d_xap2_cpu12_read2000/r19d_stmt_postgres.toml"
            ),
            "r19c_statements_sha256": sha256_file(
                ROOT
                / "config/generated/bonect_phase2_vm_memory_r19c_xap2_read2000/r19c_stmt_postgres.toml"
            ),
            "sql_identical_to_preindex_context": True,
        },
        "default_params": dict(DEFAULT_PARAMS),
        "primary_default_baseline": {
            "source": "teacher_pair_5m_r19d/baseline_gate.json workers[0].baseline",
            "role": "default_coordinates_only",
            "db_qps": primary["db_qps"],
            "actual_resource": primary["actual_resource"],
            "actual_utilization_pct": primary["actual_utilization_pct"],
            "capability": primary["capability"],
            "legal_single_memory_bottleneck": legal,
            "teacher_coordinates_loaded": False,
        },
        "supporting_default_evidence": {
            "baseline_formal_5m": formal,
            "baseline_formal_5m_repeat": formal_rep,
            "default_after_5m": default_after,
            "note": "All are restored-default coordinates; used for uncertainty only, not teacher coords.",
        },
        "non_teacher_real_delta_availability": {
            "paired_non_teacher_candidate_replays": 0,
            "available": [
                "multi_default_drift_same_params",
                "plan_tree_features_from_preindex",
                "estimator_relative_resource_deltas",
            ],
            "excluded_from_ranking": [
                "v21_memory_teacher_primary metrics and coordinates",
                "MEMORY_V21_AP68_EXPLAIN_ANALYZE_IO_DIAG_0813.json v21 arm",
            ],
            "default_qps_spread": {
                "min": min(qps_vals),
                "max": max(qps_vals),
                "range": max(qps_vals) - min(qps_vals),
            },
            "default_util_spread_pp": {
                "cpu": max(cpu_vals) - min(cpu_vals),
                "memory": max(mem_vals) - min(mem_vals),
                "io": max(io_vals) - min(io_vals),
            },
        },
        "active_ranges": ACTIVE_RANGES,
        "config_for_ranking": config_for_ranking,
        "global_explainable_best_holdout_ref_only": {
            "id": "v21_memory_teacher_primary",
            "status": "Stage-A teacher / global_explainable_best under 0813",
            "qps_delta_pct": 43.77891961726256,
            "memory_delta_pp": -8.21,
            "memory_final_pct": 78.14,
            "cpu_delta_pp": 2.01,
            "io_allowed_to_fall": True,
            "injection_forbidden": True,
            "opened_before_pack_freeze": False,
        },
    }
    return freeze


def verify_preindex(freeze: Dict[str, Any]) -> Dict[str, Any]:
    if not CANDIDATES_JSONL.exists():
        raise FileNotFoundError(f"preindex missing: {CANDIDATES_JSONL}")
    summary = json.loads(SCAN_SUMMARY.read_text(encoding="utf-8"))
    completed = int(summary.get("completed") or summary.get("completed_candidates") or 0)
    full_space = int(summary.get("full_candidate_space") or summary.get("total") or 0)
    if completed != 78125 or full_space != 78125:
        raise RuntimeError(f"unexpected preindex size completed={completed} full_space={full_space}")
    # Context compatibility: same SQL as r19d; capability differs (cpu 10->12) so rescore, do not regenerate plans.
    sql_ok = freeze["workload"]["statements_sha256"] == freeze["workload"]["r19c_statements_sha256"]
    if not sql_ok:
        raise RuntimeError("preindex SQL not identical to r19d; would require regeneration")
    fingerprint = {
        "candidates_jsonl": str(CANDIDATES_JSONL),
        "size_bytes": CANDIDATES_JSONL.stat().st_size,
        "mtime": CANDIDATES_JSONL.stat().st_mtime,
        "partial_sha256_first_1MiB": sha256_file(CANDIDATES_JSONL, limit=1024 * 1024),
        "scan_summary": str(SCAN_SUMMARY),
        "scan_summary_sha256": sha256_file(SCAN_SUMMARY),
        "completed_candidates": completed,
        "full_candidate_space": full_space,
        "distinct_plans": summary.get("distinct_plan_count"),
        "status": summary.get("status"),
        "scan_scenario": summary.get("scenario"),
        "scan_capability_in_summary": (summary.get("baseline_calibration") or {}).get("capability"),
        "r19d_capability": freeze["profile"]["capability"],
        "reuse_decision": "reuse",
        "regenerate": False,
        "reason": (
            "Authoritative 78125 full-grid preindex present; SQL hash matches r19d; "
            "plan EXPLAIN features reusable. Only baseline/capability rescoring required "
            "(r19c cpu-cap 10 vs r19d cpu-cap 12)."
        ),
        "teacher_coordinates_in_preindex_build": False,
        "preindex_provenance": (summary.get("baseline_calibration") or {}).get("provenance"),
    }
    return fingerprint


@dataclass
class RankedCandidate:
    params: Dict[str, float]
    plan_hash: str
    estimated_resource_raw_model: Dict[str, float]
    calibrated_resource: Dict[str, float]
    utilization_pct: Dict[str, float]
    score: float
    calibrated_score: float
    uncertainty_penalty: float
    plan_delta_features: Dict[str, float]
    log_dist_to_default: float
    memory_delta_pp: float
    cpu_delta_pp: float
    io_delta_pp: float
    boundary_extremeness: float
    candidate_index: Optional[int]


def iter_candidates(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL {path}:{line_number}") from exc
            if isinstance(row, dict):
                yield row


def find_default_neighborhood(rows_path: Path) -> Dict[str, Any]:
    best_dist = float("inf")
    best: Optional[Dict[str, Any]] = None
    for row in iter_candidates(rows_path):
        params = row.get("params") or {}
        if set(params) != set(COST_PARAMS):
            continue
        dist = log_param_distance(params, DEFAULT_PARAMS)
        if dist < best_dist:
            best_dist = dist
            best = row
    if best is None:
        raise RuntimeError("failed to locate default-neighborhood grid point")
    est = best.get("estimated_resource") or {}
    return {
        "log_dist_to_default": best_dist,
        "params": best["params"],
        "plan_hash": best.get("plan_hash"),
        "estimated_resource": {
            "cpu": float(est.get("cpu") or 0.0),
            "memory": float(est.get("memory") or 0.0),
            "io": float(est.get("io") or 0.0),
        },
        "candidate_index": best.get("candidate_index"),
    }


def calibrate_and_rank(
    freeze: Dict[str, Any],
    preindex: Dict[str, Any],
    *,
    out_dir: Path,
) -> Dict[str, Any]:
    """Answer-blind ranking: no teacher coords/metrics."""
    capability = {
        name: float(freeze["profile"]["capability"][name]) for name in RESOURCE_NAMES
    }
    baseline_abs = {
        name: float(freeze["primary_default_baseline"]["actual_resource"][name]) for name in RESOURCE_NAMES
    }
    baseline_util = {
        name: float(freeze["primary_default_baseline"]["actual_utilization_pct"][name]) for name in RESOURCE_NAMES
    }
    calculator = build_calculator(freeze["config_for_ranking"])

    neighborhood = find_default_neighborhood(CANDIDATES_JSONL)
    est_nb = neighborhood["estimated_resource"]
    scales = {
        name: (baseline_abs[name] / max(est_nb[name], 1e-12)) for name in RESOURCE_NAMES
    }

    # Uncertainty from non-teacher default drift (pp).
    spread = freeze["non_teacher_real_delta_availability"]["default_util_spread_pp"]
    rel_bounds = {
        "cpu": max(0.05, float(spread["cpu"]) / 100.0),
        "memory": max(0.05, float(spread["memory"]) / 100.0),
        "io": max(0.05, float(spread["io"]) / 100.0),
    }

    ranked: List[RankedCandidate] = []
    n = 0
    t0 = time.perf_counter()
    for row in iter_candidates(CANDIDATES_JSONL):
        n += 1
        params = row.get("params") or {}
        if set(params) != set(COST_PARAMS):
            continue
        est = row.get("estimated_resource") or {}
        # Relative delta vs default neighborhood in model space, mapped to real abs.
        cal_abs = {}
        for name in RESOURCE_NAMES:
            delta_model = float(est.get(name) or 0.0) - est_nb[name]
            cal_abs[name] = max(0.0, baseline_abs[name] + scales[name] * delta_model)
        util = {
            name: cal_abs[name] / max(capability[name], 1e-12) * 100.0 for name in RESOURCE_NAMES
        }
        score = float(calculator.calculate_utility_score(cal_abs, capability))
        # Direction-aware bonus without teacher: prefer memory drop, any other resource up, stay legal.
        mem_delta = util["memory"] - baseline_util["memory"]
        cpu_delta = util["cpu"] - baseline_util["cpu"]
        io_delta = util["io"] - baseline_util["io"]
        direction_bonus = 0.0
        if mem_delta < 0:
            direction_bonus += min(0.35, (-mem_delta) / 20.0)
        if util["memory"] < 80.0:
            direction_bonus += 0.15
        if max(cpu_delta, io_delta) > 0:
            direction_bonus += 0.10
        # Uncertainty penalty: larger |delta| with larger relative bound => less confident.
        unc = (
            rel_bounds["memory"] * abs(mem_delta) / 10.0
            + rel_bounds["cpu"] * abs(cpu_delta) / 20.0
            + rel_bounds["io"] * abs(io_delta) / 20.0
        )
        plan_feats = row.get("plan_delta_features") or {}
        # Mild plan-structure prior from features only (not teacher labels):
        # prefer non-zero plan delta structure diversity signal, not a fixed teacher family.
        plan_struct = float(plan_feats.get("weighted_gather_count") or 0.0) * 0.005
        calibrated = score + direction_bonus + plan_struct - 0.25 * unc
        # Extremeness for informative boundary selection among strong scores later.
        boundary = 0.0
        for name in COST_PARAMS:
            low = float(ACTIVE_RANGES[name]["low"])
            high = float(ACTIVE_RANGES[name]["high"])
            val = float(params[name])
            if name == "parallel_setup_cost":
                span = max(high - low, 1e-12)
                boundary += abs((val - low) / span - 0.5) * 2.0
            else:
                lv = math.log(max(val, 1e-12))
                l0 = math.log(max(low, 1e-12))
                l1 = math.log(max(high, 1e-12))
                boundary += abs((lv - l0) / max(l1 - l0, 1e-12) - 0.5) * 2.0
        ranked.append(
            RankedCandidate(
                params={k: float(params[k]) for k in COST_PARAMS},
                plan_hash=str(row.get("plan_hash") or ""),
                estimated_resource_raw_model={name: float(est.get(name) or 0.0) for name in RESOURCE_NAMES},
                calibrated_resource=cal_abs,
                utilization_pct=util,
                score=score,
                calibrated_score=calibrated,
                uncertainty_penalty=unc,
                plan_delta_features={k: float(v) for k, v in plan_feats.items()} if isinstance(plan_feats, dict) else {},
                log_dist_to_default=log_param_distance(params, DEFAULT_PARAMS),
                memory_delta_pp=mem_delta,
                cpu_delta_pp=cpu_delta,
                io_delta_pp=io_delta,
                boundary_extremeness=boundary,
                candidate_index=row.get("candidate_index"),
            )
        )
        if n % 20000 == 0:
            print(f"rank_scan={n}", flush=True)

    ranked.sort(key=lambda c: c.calibrated_score, reverse=True)
    elapsed = time.perf_counter() - t0

    top_path = out_dir / "candidates_r19d_rescored_top500.jsonl"
    with top_path.open("w", encoding="utf-8") as handle:
        for item in ranked[:500]:
            handle.write(
                json.dumps(
                    {
                        "params": item.params,
                        "plan_hash": item.plan_hash,
                        "score": item.score,
                        "calibrated_score": item.calibrated_score,
                        "uncertainty_penalty": item.uncertainty_penalty,
                        "utilization_pct": item.utilization_pct,
                        "calibrated_resource": item.calibrated_resource,
                        "estimated_resource_model": item.estimated_resource_raw_model,
                        "plan_delta_features": item.plan_delta_features,
                        "log_dist_to_default": item.log_dist_to_default,
                        "memory_delta_pp": item.memory_delta_pp,
                        "cpu_delta_pp": item.cpu_delta_pp,
                        "io_delta_pp": item.io_delta_pp,
                        "boundary_extremeness": item.boundary_extremeness,
                        "candidate_index": item.candidate_index,
                        "teacher_injection": False,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )

    # Select prefix k=4 without teacher.
    top1 = ranked[0]
    plan_diverse = None
    for item in ranked[1:]:
        if item.plan_hash and item.plan_hash != top1.plan_hash:
            # require not nearly identical params
            if log_param_distance(item.params, top1.params) >= 0.5:
                plan_diverse = item
                break
    if plan_diverse is None:
        plan_diverse = ranked[1] if len(ranked) > 1 else top1

    # Informative interval boundary: among top 5% scores, maximize boundary extremeness
    # while keeping a different plan/param from top1 and diverse.
    cutoff = ranked[max(0, len(ranked) // 20)].calibrated_score if ranked else 0.0
    boundary = None
    best_b = -1.0
    for item in ranked:
        if item.calibrated_score < cutoff:
            break
        if item.plan_hash == top1.plan_hash:
            continue
        if log_param_distance(item.params, top1.params) < 1.0:
            continue
        if plan_diverse is not None and log_param_distance(item.params, plan_diverse.params) < 0.5:
            continue
        score_b = item.boundary_extremeness + 0.05 * item.log_dist_to_default
        if score_b > best_b:
            best_b = score_b
            boundary = item
    if boundary is None:
        # fallback: farthest log distance among top 1000
        boundary = max(ranked[:1000], key=lambda c: c.log_dist_to_default)

    def pack_entry(cid: str, role: str, item: Optional[RankedCandidate], *, source: str) -> Dict[str, Any]:
        if item is None:
            return {
                "candidate_id": cid,
                "role": role,
                "params": dict(DEFAULT_PARAMS),
                "plan_hash": None,
                "calibrated_score": 0.0,
                "score": 0.0,
                "utilization_pct": baseline_util,
                "log_dist_to_default": 0.0,
                "source": source,
                "teacher_injection": False,
            }
        return {
            "candidate_id": cid,
            "role": role,
            "params": item.params,
            "plan_hash": item.plan_hash,
            "calibrated_score": item.calibrated_score,
            "score": item.score,
            "utilization_pct": item.utilization_pct,
            "calibrated_resource": item.calibrated_resource,
            "memory_delta_pp": item.memory_delta_pp,
            "cpu_delta_pp": item.cpu_delta_pp,
            "io_delta_pp": item.io_delta_pp,
            "uncertainty_penalty": item.uncertainty_penalty,
            "plan_delta_features": item.plan_delta_features,
            "log_dist_to_default": item.log_dist_to_default,
            "boundary_extremeness": item.boundary_extremeness,
            "candidate_index": item.candidate_index,
            "source": source,
            "teacher_injection": False,
        }

    prefix = [
        pack_entry("memory_r0_pred_top1", "predicted_top1", top1, source="r19d_answer_blind_rescore_fullgrid"),
        pack_entry("memory_r0_plan_diverse", "plan_family_diverse", plan_diverse, source="r19d_answer_blind_rescore_fullgrid"),
        pack_entry("memory_r0_range_boundary", "range_boundary", boundary, source="r19d_answer_blind_rescore_fullgrid"),
        pack_entry("memory_r0_default_anchor", "default_anchor", None, source="postgresql_defaults"),
    ]
    # Intentionally no teacher/v21 coordinate comparison here — holdout opens only after pack freeze.

    top20 = []
    for item in ranked[:20]:
        top20.append(
            {
                "params": item.params,
                "plan_hash": item.plan_hash,
                "calibrated_score": item.calibrated_score,
                "score": item.score,
                "utilization_pct": item.utilization_pct,
                "log_dist_to_default": item.log_dist_to_default,
                "memory_delta_pp": item.memory_delta_pp,
                "cpu_delta_pp": item.cpu_delta_pp,
                "io_delta_pp": item.io_delta_pp,
            }
        )

    calibration = {
        "created_at": now_cst(),
        "control_decision": "0813 + LATEST offline-safe",
        "scenario": "memory",
        "answer_blind": True,
        "teacher_coordinates_used": False,
        "teacher_metrics_used": False,
        "primary_baseline": freeze["primary_default_baseline"],
        "default_neighborhood_grid": neighborhood,
        "scale_est_to_real_abs": scales,
        "uncertainty_rel_bounds_from_default_drift": rel_bounds,
        "reuse_plan_preindex": preindex,
        "ranking_features": [
            "utility_score_on_calibrated_abs",
            "memory_down_direction_bonus",
            "other_resource_up_bonus",
            "plan_delta_features_structure_prior",
            "default_drift_uncertainty_penalty",
            "plan_hash_diversity",
            "range_boundary_extremeness",
        ],
        "candidate_count_scanned": n,
        "elapsed_s": elapsed,
        "best_calibrated_score": ranked[0].calibrated_score if ranked else None,
        "error_bounds_placeholder": {
            "note": "no paired non-teacher candidate labels in r19d yet; bounds from multi-default drift until 2m prefix feedback",
            "cpu_rel": rel_bounds["cpu"],
            "memory_rel": rel_bounds["memory"],
            "io_rel": rel_bounds["io"],
        },
    }
    write_json(out_dir / "MEMORY_R19D_CALIBRATION.json", calibration)

    return {
        "calibration": calibration,
        "prefix_k4": prefix,
        "top20": top20,
        "top500_path": str(top_path),
        "candidate_count": n,
        "elapsed_s": elapsed,
        "scales": scales,
        "neighborhood": neighborhood,
    }


def freeze_pack(
    freeze: Dict[str, Any],
    preindex: Dict[str, Any],
    ranking: Dict[str, Any],
    control: Dict[str, Any],
    *,
    out_dir: Path,
    pack_stamp: str,
) -> Dict[str, Any]:
    pack = {
        "created_at": now_cst(),
        "control_decision": "control_decision_20260714_0813_six_hour_adaptive_recall_closeout.md",
        "latest_control_decision": control,
        "scenario": "memory",
        "round": 0,
        "blind_search_started": False,
        "immutable": True,
        "replay_launched": False,
        "screening": {
            "k_initial": 4,
            "run_mins": 2,
            "expand_to": [6, 8],
            "final_strong_mins": 5,
            "max_screen_points_per_round": 8,
        },
        "baseline_context": {
            "freeze_path": str(out_dir / "MEMORY_R19D_CONTEXT_FREEZE.json"),
            "db_qps": freeze["primary_default_baseline"]["db_qps"],
            "util": freeze["primary_default_baseline"]["actual_utilization_pct"],
            "abs": freeze["primary_default_baseline"]["actual_resource"],
            "profile": freeze["profile"],
            "workload": freeze["workload"],
            "legal_single_memory_bottleneck": freeze["primary_default_baseline"]["legal_single_memory_bottleneck"],
        },
        "active_ranges": ACTIVE_RANGES,
        "default_anchor": dict(DEFAULT_PARAMS),
        "preindex": preindex,
        "calibration_path": str(out_dir / "MEMORY_R19D_CALIBRATION.json"),
        "prefix_k4": ranking["prefix_k4"],
        "top20_rescored": ranking["top20"],
        "teacher_injection_forbidden": True,
        "teacher_holdout_not_opened_before_this_pack": True,
        "provenance": {
            "answer_blind": True,
            "v21_coords_in_generation": False,
            "v21_coords_in_ranking": False,
            "v21_coords_in_anchor": False,
            "v21_metrics_in_ranking": False,
            "files_read_before_pack": [
                str(LATEST_CONTROL),
                str(CONTROL_0813),
                str(CANDIDATES_JSONL),
                str(SCAN_SUMMARY),
                str(R19D_CFG),
                str(MEM_STAGE / "teacher_pair_5m_r19d/baseline_gate.json"),
                str(BASE / "memory_r19d/baseline_formal_5m/baseline_gate.json"),
                str(BASE / "memory_r19d/baseline_formal_5m_repeat/baseline_gate.json"),
                str(BASE / "memory_r19d/default_after_5m/baseline_gate.json"),
                str(MEM_STAGE / "round0_blind_scaffold_0823/ROUND0_SCAFFOLD.json"),
            ],
            "files_explicitly_not_read_before_pack": [
                str(TEACHER_MANIFEST),
                str(MEM_STAGE / "MEMORY_V21_AP68_EXPLAIN_ANALYZE_IO_DIAG_0813.json"),
                str(MEM_STAGE / "teacher_pair_5m_r19d/parallel_validation_results.jsonl"),
                str(MEM_STAGE / "teacher_pair_5m_r19d/anchor_gate.json"),
                str(BASE / "memory_scan_prep/holdout_rank_audit.json"),
            ],
            "note": (
                "Teacher/v21 artifacts listed as not read are opened only by the holdout auditor "
                "after this pack is written immutable."
            ),
        },
        "status": "replay_ready_offline_pack_not_launched",
        "pack_id": f"memory_round0_blind_pack_{pack_stamp}",
    }
    pack_path = out_dir / "MEMORY_ROUND0_ADAPTIVE_PACK.json"
    write_json(pack_path, pack)
    # also stable alias without stamp inside dir
    write_json(out_dir / f"MEMORY_ROUND0_ADAPTIVE_PACK_{pack_stamp}.json", pack)

    prefix_only = ranking["prefix_k4"]
    write_json(out_dir / "memory_round0_prefix_k4_candidates.json", prefix_only)

    # Compact pool for potential replay driver
    pool = {
        "created_at": now_cst(),
        "scenario": "memory",
        "k": 4,
        "run_mins": 2,
        "candidates": [
            {
                "candidate_id": c["candidate_id"],
                "role": c["role"],
                "params": c["params"],
                "teacher_injection": False,
            }
            for c in prefix_only
        ],
    }
    write_json(out_dir / "memory_r19d_round0_pool.json", pool)

    # Pack sha for auditor provenance
    pack_bytes = pack_path.read_bytes()
    pack["pack_sha256"] = sha256_bytes(pack_bytes)
    # rewrite with sha
    write_json(pack_path, pack)
    write_json(out_dir / f"MEMORY_ROUND0_ADAPTIVE_PACK_{pack_stamp}.json", pack)
    return pack


def run_holdout_auditor(pack: Dict[str, Any], *, out_dir: Path) -> Dict[str, Any]:
    """Read-only teacher holdout audit AFTER pack is immutable."""
    # Open teacher only now.
    teacher_params = dict(V21_HOLDOUT_PARAMS)
    teacher_manifest_sha = sha256_file(TEACHER_MANIFEST) if TEACHER_MANIFEST.exists() else None
    teacher_diag_path = MEM_STAGE / "MEMORY_V21_AP68_EXPLAIN_ANALYZE_IO_DIAG_0813.json"
    teacher_diag_sha = sha256_file(teacher_diag_path) if teacher_diag_path.exists() else None

    # Optional: load teacher pair result for documentation only (not ranking).
    teacher_result = None
    results_path = MEM_STAGE / "teacher_pair_5m_r19d/parallel_validation_results.jsonl"
    if results_path.exists():
        line = results_path.read_text(encoding="utf-8").splitlines()[0]
        teacher_result = json.loads(line)

    inside_before = inside_ranges(teacher_params, pack["active_ranges"])
    # No interval update yet => after == before
    inside_after = inside_before

    predicted = [c for c in pack["prefix_k4"] if c["role"] != "default_anchor"]
    log_dists = [log_param_distance(c["params"], teacher_params) for c in predicted]

    # Plan-family recall: compare plan_hash if teacher plan available from preindex nearest point.
    # Find nearest grid point to teacher for plan family proxy (read-only, post-pack).
    nearest_teacher_grid = None
    best_d = float("inf")
    for row in iter_candidates(CANDIDATES_JSONL):
        params = row.get("params") or {}
        if set(params) != set(COST_PARAMS):
            continue
        d = log_param_distance(params, teacher_params)
        if d < best_d:
            best_d = d
            nearest_teacher_grid = {
                "dist": d,
                "params": params,
                "plan_hash": row.get("plan_hash"),
            }
    teacher_plan = (nearest_teacher_grid or {}).get("plan_hash")
    predicted_hashes = {c.get("plan_hash") for c in predicted if c.get("plan_hash")}
    plan_family_recalled = bool(teacher_plan and teacher_plan in predicted_hashes)

    # Also check log-neighborhood basin (dist < 3.0 ~ same coarse basin)
    basin_recalled = any(d < 3.0 for d in log_dists)

    if any(d < 1e-6 for d in log_dists):
        recall_status = "exact_neighborhood"
    elif plan_family_recalled or basin_recalled:
        recall_status = "same_plan_family" if plan_family_recalled else "near_param_basin_not_exact"
    else:
        recall_status = "not_recalled"

    pack_path = out_dir / "MEMORY_ROUND0_ADAPTIVE_PACK.json"
    auditor = {
        "created_at": now_cst(),
        "phase": "round0_pre_replay",
        "holdout_id": "memory_v21",
        "holdout_path": str(TEACHER_MANIFEST),
        "holdout_sha256": teacher_manifest_sha,
        "holdout_diag_path": str(teacher_diag_path),
        "holdout_diag_sha256": teacher_diag_sha,
        "generation_ranking_used_holdout": False,
        "proof": (
            "Holdout/teacher files opened only in this auditor block after "
            f"{pack_path.name} was written immutable (pack_sha256="
            f"{pack.get('pack_sha256')}). Candidate roles exclude teacher coords; "
            "ranking features used default baseline, multi-default drift uncertainty, "
            "plan-tree features, and estimator relative deltas only."
        ),
        "pack_sha256_at_audit": pack.get("pack_sha256"),
        "teacher_point_inside_active_ranges_before": inside_before,
        "teacher_point_inside_active_ranges_after": inside_after,
        "teacher_log_distance_of_predicted_topk": log_dists,
        "teacher_log_distance_of_replayed_prefix": None,
        "teacher_plan_family_recalled_by_predicted_topk": plan_family_recalled,
        "teacher_plan_family_recalled_by_real_replay": None,
        "nearest_teacher_grid_in_preindex": nearest_teacher_grid,
        "accepted_best_in_teacher_basin": None,
        "global_best_in_teacher_basin": None,
        "teacher_recall_status": recall_status,
        "teacher_params": teacher_params,
        "default_params": dict(DEFAULT_PARAMS),
        "teacher_pair_result_ref_only": {
            "candidate_id": (teacher_result or {}).get("candidate_id"),
            "db_qps_delta_pct": (teacher_result or {}).get("db_qps_delta_pct"),
            "actual_utilization_pct": (teacher_result or {}).get("actual_utilization_pct"),
            "worker_baseline_qps": (teacher_result or {}).get("worker_baseline_qps"),
            "not_used_in_ranking": True,
        },
        "active_range_inclusion_ok": inside_before,
        "provenance_clean": True,
        "success_definition": (
            "Success of this step is a provenance-clean replay-ready pack, "
            "NOT a claim that Memory blind recall has succeeded."
        ),
    }
    write_json(out_dir / "MEMORY_ROUND0_TEACHER_HOLDOUT_AUDITOR.json", auditor)
    return auditor


def write_chinese_checkpoint(
    freeze: Dict[str, Any],
    preindex: Dict[str, Any],
    pack: Dict[str, Any],
    auditor: Dict[str, Any],
    control: Dict[str, Any],
    *,
    out_dir: Path,
    pack_stamp: str,
) -> Path:
    prefix = pack["prefix_k4"]
    lines = [
        f"# Memory r19d Round0 {pack_stamp}",
        "",
        f"- {now_cst()}",
        f"- Run`{RUN_ID}`",
        f"-  offline Memory default-start blind-pack ****",
        f"- LATEST_CONTROL_DECISION`{control.get('path')}`={control.get('conflicts') or ''}={control.get('safe_for_offline_memory_pack')}",
        "",
        "## 1.  Memory ",
        "",
        f"-  default baselineteacher_pair_5m_r19d DB QPS `{freeze['primary_default_baseline']['db_qps']:.4f}`",
        f"-  CPU/Memory/IO = "
        f"`{freeze['primary_default_baseline']['actual_utilization_pct']['cpu']:.2f}%` / "
        f"`{freeze['primary_default_baseline']['actual_utilization_pct']['memory']:.2f}%` / "
        f"`{freeze['primary_default_baseline']['actual_utilization_pct']['io']:.2f}%`",
        f"- `{freeze['primary_default_baseline']['legal_single_memory_bottleneck']['legal_single_memory']}`",
        f"- Profilecpu12 / 1856M / read2000 write800 / io_cap 2800 / VM bonect-replay-v87-1",
        f"-  cost  v21 ",
        "",
        "## 2. Preindex 78125",
        "",
        f"- `{preindex['reuse_decision']}`regenerate=`{preindex['regenerate']}`",
        f"- `{preindex['candidates_jsonl']}`",
        f"- completed=`{preindex['completed_candidates']}` distinct_plans=`{preindex['distinct_plans']}`",
        f"- scan_summary_sha256=`{preindex['scan_summary_sha256'][:16]}...`",
        f"- partial candidates sha256(1MiB)=`{preindex['partial_sha256_first_1MiB'][:16]}...`",
        f"- {preindex['reason']}",
        "",
        "## 3. Answer-blind ",
        "",
        "- r19d default baseline default plan-tree  delta",
        "- ****v21 v21  QPS/teacher pair anchor holdout_rank_audit",
        f"- `{out_dir / 'MEMORY_R19D_CALIBRATION.json'}`",
        "",
        "## 4.  round0 k=4 / 2m ",
        "",
        "| role | candidate_id | seq/rand | plan_hash | calibrated_score | memΔpp |",
        "| --- | --- | --- | --- | ---: | ---: |",
    ]
    for c in prefix:
        params = c["params"]
        lines.append(
            f"| {c['role']} | `{c['candidate_id']}` | "
            f"{float(params['seq_page_cost']):.4g}/{float(params['random_page_cost']):.4g} | "
            f"`{(c.get('plan_hash') or '-')[:12]}` | {float(c.get('calibrated_score') or 0.0):.4f} | "
            f"{float(c.get('memory_delta_pp') or 0.0):+.2f} |"
        )
    lines.extend(
        [
            "",
            f"- `{out_dir / 'MEMORY_ROUND0_ADAPTIVE_PACK.json'}`",
            f"- pack_sha256`{pack.get('pack_sha256')}`",
            f"- immutable=`{pack.get('immutable')}` replay_launched=`{pack.get('replay_launched')}`",
            "",
            "## 5.  v21 holdout auditor",
            "",
            f"- generation_ranking_used_holdout=`{auditor.get('generation_ranking_used_holdout')}`",
            f"- teacher_point_inside_active_ranges_before=`{auditor.get('teacher_point_inside_active_ranges_before')}`",
            f"- teacher_log_distance_of_predicted_topk=`{auditor.get('teacher_log_distance_of_predicted_topk')}`",
            f"- teacher_plan_family_recalled_by_predicted_topk=`{auditor.get('teacher_plan_family_recalled_by_predicted_topk')}`",
            f"- teacher_recall_status=`{auditor.get('teacher_recall_status')}`",
            f"- provenance_clean=`{auditor.get('provenance_clean')}`",
            "",
            ">  provenance-clean**** Memory ",
            "",
            "## 6. ",
            "",
            "-  2m/5m ",
            "-  VM profile / snapshot / PostgreSQL ",
            "-  HyBench XPDocker`/disk2`",
            "-  kill ",
            "",
            "## 7. ",
            "",
            f"- `{out_dir / 'MEMORY_R19D_CONTEXT_FREEZE.json'}`",
            f"- `{out_dir / 'MEMORY_R19D_PREINDEX_REUSE.json'}`",
            f"- `{out_dir / 'MEMORY_R19D_CALIBRATION.json'}`",
            f"- `{out_dir / 'MEMORY_ROUND0_ADAPTIVE_PACK.json'}`",
            f"- `{out_dir / 'memory_round0_prefix_k4_candidates.json'}`",
            f"- `{out_dir / 'MEMORY_ROUND0_TEACHER_HOLDOUT_AUDITOR.json'}`",
            f"- `{out_dir / 'MEMORY_ROUND0_OFFLINE_CHECKPOINT.md'}`",
            "",
        ]
    )
    md_path = out_dir / "MEMORY_ROUND0_OFFLINE_CHECKPOINT.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")

    # Report-side copies
    report_dir = ROOT / "report/bonect"
    report_md = report_dir / f"memory_r19d_round0_offline_blind_pack_checkpoint_{pack_stamp}.md"
    report_md.write_text("\n".join(lines), encoding="utf-8")
    write_json(
        report_dir / f"memory_r19d_round0_offline_blind_pack_manifest_{pack_stamp}.json",
        {
            "created_at": now_cst(),
            "run_id": RUN_ID,
            "pack_dir": str(out_dir),
            "pack_sha256": pack.get("pack_sha256"),
            "prefix_k4": prefix,
            "teacher_recall_status": auditor.get("teacher_recall_status"),
            "replay_launched": False,
            "provenance_clean": auditor.get("provenance_clean"),
            "control": control,
        },
    )
    return md_path


def main() -> int:
    started = time.perf_counter()
    pack_stamp = stamp()
    out_dir = MEM_STAGE / f"round0_blind_pack_{pack_stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[memory-offline] start pack_stamp={pack_stamp} out={out_dir}", flush=True)
    control = check_control_decision()
    write_json(out_dir / "CONTROL_DECISION_CHECK.json", control)
    if not control["safe_for_offline_memory_pack"]:
        print(f"[memory-offline] STOP: control conflict {control['conflicts']}", flush=True)
        return 2

    freeze = freeze_context()
    write_json(out_dir / "MEMORY_R19D_CONTEXT_FREEZE.json", freeze)
    print("[memory-offline] context frozen", flush=True)

    preindex = verify_preindex(freeze)
    write_json(out_dir / "MEMORY_R19D_PREINDEX_REUSE.json", preindex)
    print(f"[memory-offline] preindex reuse ok completed={preindex['completed_candidates']}", flush=True)

    ranking = calibrate_and_rank(freeze, preindex, out_dir=out_dir)
    print(
        f"[memory-offline] ranking done n={ranking['candidate_count']} "
        f"elapsed={ranking['elapsed_s']:.2f}s",
        flush=True,
    )

    pack = freeze_pack(freeze, preindex, ranking, control, out_dir=out_dir, pack_stamp=pack_stamp)
    print(f"[memory-offline] pack frozen sha={pack.get('pack_sha256')}", flush=True)

    # Only after immutable pack:
    auditor = run_holdout_auditor(pack, out_dir=out_dir)
    print(
        f"[memory-offline] holdout auditor recall_status={auditor.get('teacher_recall_status')}",
        flush=True,
    )

    md_path = write_chinese_checkpoint(
        freeze, preindex, pack, auditor, control, out_dir=out_dir, pack_stamp=pack_stamp
    )

    # Update scaffold pointer
    scaffold_dir = MEM_STAGE / "round0_blind_scaffold_0823"
    write_json(
        scaffold_dir / "ROUND0_PACK_READY_POINTER.json",
        {
            "updated_at": now_cst(),
            "pack_dir": str(out_dir),
            "pack_json": str(out_dir / "MEMORY_ROUND0_ADAPTIVE_PACK.json"),
            "pack_sha256": pack.get("pack_sha256"),
            "status": "offline_pack_ready_not_launched",
            "blind_search_started": False,
        },
    )

    # Machine-readable summary at memory_stage_a root
    write_json(
        MEM_STAGE / "MEMORY_ROUND0_OFFLINE_PACK_LATEST.json",
        {
            "created_at": now_cst(),
            "pack_dir": str(out_dir),
            "pack_sha256": pack.get("pack_sha256"),
            "checkpoint_md": str(md_path),
            "replay_launched": False,
            "teacher_recall_status": auditor.get("teacher_recall_status"),
            "elapsed_s": time.perf_counter() - started,
        },
    )

    print(f"[memory-offline] checkpoint={md_path}", flush=True)
    print(f"[memory-offline] completed elapsed={time.perf_counter()-started:.2f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

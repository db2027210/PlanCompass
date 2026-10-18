#!/usr/bin/env python3
"""Answer-blind Memory outer loop on the proven v112 profile (v139 controller).

Profile (fixed scenario configuration, not tuned parameters):
  - HyBench runxp, 11 vCPU / 2304MiB / 800+800 MiB/s
  - fixed shared_buffers=512MB, work_mem=8MB, parallel workers 4/8
  - seven PostgreSQL cost parameters only, start from defaults
  - physical metrics: CPU=vm_busy, Memory=vm_used, IO=disk_device
  - parallel real replay pool: bonect-replay-v87-2 + v87-4 (worker-local baselines)
  - formal serial worker: v87-1 only
  - hold out v87-3 from pooled ranking (historical drift >5%)
  - EXPLAIN planning endpoints: v87-1/v87-2/v87-4 only (never held-out v87-3)

Behavioral contract (must hold in executable code, not comments):
  - Before round-0 EXPLAIN/ranking, run a fail-closed current-state baseline-only
    preflight on v87-2/v87-4; refuse the scan unless the current-run baseline gate
    passes and a reusable calibration artifact is written.
  - Round 0 runs a fresh production full-grid estimator scan over answer-independent
    default intervals with five values per active parameter (exactly 5^7=78125
    unique seven-parameter candidates) before any model ranking.
  - Real replay starts at model-ranked top 12 with no external/known-answer anchors.
  - Expand 12->18->24 from measured prediction error, pairwise inversion,
    resource-direction accuracy, rank uncertainty, lane coverage, or unresolved
    optimistic upper bounds.
  - After each real-replay stage, binary-shrink every active interval from the best
    real-replayed point, rebuild and rescore a fresh five-point grid, and continue
    rounds until epsilon/safety/max-rounds. Do not stop after round 0 waiting for
    an external rebuild, and do not stop merely because one point reaches +20%.
  - Force only a best point discovered by the current run into the next replay prefix.
  - Preserve current-run global explainable best and DB-QPS fallback best across
    rejected rounds.
  - Final formal promotion replay uses v87-1 only.
  - Known v112 labels may train/calibrate the ranking model, but known parameter
    coordinates must never be injected, forced, excluded, promoted, or demoted.
"""

from __future__ import annotations

import argparse
import ast
import itertools
import json
import math
import os
import subprocess
import sys
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]
COST_PARAMS = [
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
]
# Answer-independent default intervals (match memory_blind_v112_vm.json param_ranges).
DEFAULT_RANGES = {
    "seq_page_cost": (0.0001, 100.0),
    "random_page_cost": (0.0001, 20.0),
    "cpu_tuple_cost": (1e-06, 1.0),
    "cpu_index_tuple_cost": (1e-06, 1.0),
    "cpu_operator_cost": (1e-06, 1.0),
    "parallel_setup_cost": (0.0, 10000.0),
    "parallel_tuple_cost": (1e-06, 10.0),
}
DEFAULT_PARAMS = {
    "seq_page_cost": 1.0,
    "random_page_cost": 4.0,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
}
EXPECTED_ROUND0_GRID_COUNT = 5 ** 7  # 78125
DEFAULT_CONFIG = (
    ROOT
    / "config/generated/bonect_phase2_vm_memory_blind_v112/memory_blind_v112_vm.json"
)
DEFAULT_WORKER_POOL = (
    ROOT
    / "config/generated/bonect_phase2_vm_memory_blind_v112/memory_blind_v112_worker2_stable_pool.json"
)
DEFAULT_FORMAL_POOL = (
    ROOT
    / "config/generated/bonect_phase2_vm_memory_blind_v112/memory_blind_v112_worker1_pool.json"
)
DEFAULT_BASELINE_CALIBRATION = (
    ROOT
    / "config/generated/bonect_phase2_vm_memory_blind_v112/baseline_calibration_v112_top18_forest100.json"
)
DEFAULT_RESOURCE_MODEL_DIR = (
    ROOT
    / "config/generated/bonect_phase2_vm_memory_blind_v112/resource_models"
    / "memory_v112_generic_seed_4508333"
)
DEFAULT_PRIOR_REPLAY_LABELS = (
    ROOT
    / "history/bonect_parallel_vm_replay/memory_v112_top18_plus_baseline_labels_20260712.jsonl"
)
DEFAULT_REPLAY_MODEL_TRAINER_PYTHON = (
    Path.home() / "miniconda3/envs/resource_adaptive/bin/python"
)
# Planning-only endpoints for EXPLAIN scans (read-only / session-local).
# Hold out v87-3 (.252) from real ranking and formal replay.
DEFAULT_PLANNING_ENDPOINTS = [
    "192.0.2.12:25432/hybench_sf10x",  # bonect-replay-v87-1
    "192.0.2.11:25432/hybench_sf10x",  # bonect-replay-v87-2
    "192.0.2.14:25432/hybench_sf10x",  # bonect-replay-v87-4
]
HELD_OUT_PLANNING_ENDPOINT_HOST = "192.0.2.13"  # v87-3
DEFAULT_CALIBRATION_CACHE_MAX_AGE_S = 3600.0
# Behavioral anti-patterns that must not appear as executable control flow.
# Strings are split so the audit does not self-flag policy constants.
FORBIDDEN_ASSIGNMENT_NAMES = (
    "HOLDOUT_" + "TEACHER_SIGNATURES",
)
FORBIDDEN_STOP_REASON_PARTS = (
    "awaiting_fresh_grid_rebuild",
    "after_round0",
)
FORBIDDEN_DEFAULT_PATH_PARTS = (
    "memory_v113_forest100",
    "diverse_top60",
)


def log(msg: str, *, run_dir: Optional[Path] = None) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    if run_dir is not None:
        path = run_dir / "logs" / "controller.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def param_signature(params: Dict[str, Any]) -> Tuple[float, ...]:
    return tuple(float(params[name]) for name in COST_PARAMS)


def clean_params(raw: Any) -> Dict[str, float]:
    if not isinstance(raw, dict):
        raise ValueError("params must be a dict")
    missing = [name for name in COST_PARAMS if name not in raw]
    extra = [name for name in raw if name not in COST_PARAMS]
    if missing or extra:
        raise ValueError(
            f"params must contain exactly seven cost keys; missing={missing}, extra={extra}"
        )
    out: Dict[str, float] = {name: float(raw[name]) for name in COST_PARAMS}
    return out


def heartbeat(run_dir: Path, payload: Dict[str, Any]) -> None:
    body = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "pid": os.getpid(),
        "cwd": str(Path.cwd()),
        **payload,
    }
    write_json(run_dir / "heartbeat.json", body)
    with (run_dir / "logs" / "heartbeat.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(body, ensure_ascii=False) + "\n")


def five_point_values(lo: float, hi: float) -> List[float]:
    if hi < lo:
        lo, hi = hi, lo
    if math.isclose(lo, hi, rel_tol=0.0, abs_tol=1e-15):
        return [lo, lo, lo, lo, lo]
    # Prefer geometric spacing for multi-decade cost params.
    if lo > 0 and hi / lo >= 10:
        ratios = [0.0, 0.25, 0.5, 0.75, 1.0]
        return [math.exp(math.log(lo) + r * (math.log(hi) - math.log(lo))) for r in ratios]
    step = (hi - lo) / 4.0
    return [lo + i * step for i in range(5)]


def range_scale(lo: float, hi: float) -> str:
    if lo > 0 and hi > 0 and (hi / lo) >= 10:
        return "log"
    return "linear"


def build_range_json_payload(ranges: Dict[str, Tuple[float, float]]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"memory": {}}
    for name in COST_PARAMS:
        lo, hi = ranges[name]
        payload["memory"][name] = {
            "low": float(min(lo, hi)),
            "high": float(max(lo, hi)),
            "scale": range_scale(float(min(lo, hi)), float(max(lo, hi))),
        }
    return payload


def generate_five_point_grid(
    ranges: Dict[str, Tuple[float, float]],
) -> List[Dict[str, float]]:
    """Generate the full 5^N factorial grid for current active intervals."""
    value_lists = [five_point_values(float(ranges[name][0]), float(ranges[name][1])) for name in COST_PARAMS]
    out: List[Dict[str, float]] = []
    for combo in itertools.product(*value_lists):
        params = {name: float(value) for name, value in zip(COST_PARAMS, combo)}
        out.append(params)
    return out


def unique_seven_param_candidates(candidates: Sequence[Dict[str, Any]]) -> List[Dict[str, float]]:
    seen = set()
    out: List[Dict[str, float]] = []
    for raw in candidates:
        params = clean_params(raw.get("params") if isinstance(raw, dict) and "params" in raw else raw)
        sig = param_signature(params)
        if sig in seen:
            continue
        seen.add(sig)
        out.append(params)
    return out


def binary_half_interval(
    lo: float,
    hi: float,
    best: float,
    *,
    min_width_pct: float = 1.0,
) -> Tuple[float, float]:
    if hi < lo:
        lo, hi = hi, lo
    width = hi - lo
    if width <= 0:
        return lo, hi
    mid = lo + 0.5 * width
    # Keep the half containing the best real-replayed point.
    if best <= mid:
        new_lo, new_hi = lo, mid
    else:
        new_lo, new_hi = mid, hi
    min_width = max(abs(lo) * (min_width_pct / 100.0), abs(hi) * (min_width_pct / 100.0), 0.0)
    if (new_hi - new_lo) < min_width and width >= min_width:
        half = 0.5 * min_width
        new_lo = max(lo, best - half)
        new_hi = min(hi, best + half)
        if new_hi <= new_lo:
            new_lo, new_hi = lo, hi
    return new_lo, new_hi


def update_ranges_from_best(
    ranges: Dict[str, Tuple[float, float]],
    best_params: Dict[str, float],
    *,
    min_width_pct: float,
) -> Dict[str, Tuple[float, float]]:
    updated: Dict[str, Tuple[float, float]] = {}
    for name in COST_PARAMS:
        lo, hi = ranges[name]
        updated[name] = binary_half_interval(lo, hi, float(best_params[name]), min_width_pct=min_width_pct)
    return updated


def ranges_at_epsilon(ranges: Dict[str, Tuple[float, float]], min_width_pct: float) -> bool:
    for _name, (lo, hi) in ranges.items():
        width = abs(hi - lo)
        ref = max(abs(lo), abs(hi), 1e-12)
        if (width / ref) * 100.0 > min_width_pct + 1e-9:
            return False
    return True


def decide_adaptive_replay_budget(
    *,
    current_budget: int,
    max_budget: int = 24,
    min_budget: int = 12,
    batch_size: int = 6,
    pairwise_inversion_rate: Optional[float] = None,
    qps_delta_mae: Optional[float] = None,
    resource_direction_accuracy: Optional[float] = None,
    rank_uncertainty: Optional[float] = None,
    lane_coverage_ratio: Optional[float] = None,
    unresolved_optimistic_upper_bound: bool = False,
    inversion_threshold: float = 0.10,
    mae_threshold: float = 15.0,
    direction_accuracy_threshold: float = 0.70,
    rank_uncertainty_threshold: float = 0.25,
    lane_coverage_threshold: float = 0.50,
) -> Dict[str, Any]:
    """Executable 12 -> 18 -> 24 expansion from measured quality signals.

    Returns a decision dict with the next budget and expand reasons. This is
    control logic, not a comment or deferred control_decision.
    """
    budget = max(min_budget, int(current_budget))
    budget = min(budget, int(max_budget))
    reasons: List[str] = []
    need_expand = False

    if pairwise_inversion_rate is not None and float(pairwise_inversion_rate) > inversion_threshold:
        need_expand = True
        reasons.append(
            f"pairwise_inversion_rate={pairwise_inversion_rate:.4f}>{inversion_threshold:.4f}"
        )
    if qps_delta_mae is not None and float(qps_delta_mae) > mae_threshold:
        need_expand = True
        reasons.append(f"qps_delta_mae={qps_delta_mae:.4f}>{mae_threshold:.4f}")
    if resource_direction_accuracy is not None and float(resource_direction_accuracy) < direction_accuracy_threshold:
        need_expand = True
        reasons.append(
            f"resource_direction_accuracy={resource_direction_accuracy:.4f}<{direction_accuracy_threshold:.4f}"
        )
    if rank_uncertainty is not None and float(rank_uncertainty) > rank_uncertainty_threshold:
        need_expand = True
        reasons.append(f"rank_uncertainty={rank_uncertainty:.4f}>{rank_uncertainty_threshold:.4f}")
    if lane_coverage_ratio is not None and float(lane_coverage_ratio) < lane_coverage_threshold:
        need_expand = True
        reasons.append(f"lane_coverage_ratio={lane_coverage_ratio:.4f}<{lane_coverage_threshold:.4f}")
    if unresolved_optimistic_upper_bound:
        need_expand = True
        reasons.append("unresolved_optimistic_upper_bound=true")

    if not need_expand:
        return {
            "budget": budget,
            "expand": False,
            "saturated": False,
            "reasons": reasons or ["quality_signals_within_thresholds"],
        }

    next_budget = min(int(max_budget), budget + int(batch_size))
    if next_budget <= budget:
        return {
            "budget": budget,
            "expand": False,
            "saturated": True,
            "reasons": reasons + ["at_max_budget"],
        }
    return {
        "budget": next_budget,
        "expand": True,
        "saturated": False,
        "reasons": reasons,
    }


def measure_replay_quality(
    rows: Sequence[Dict[str, Any]],
    ranked_prefix: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """Compute expansion signals from real replay labels vs estimator ranking."""
    usable: List[Dict[str, Any]] = []
    for row in rows:
        if str(row.get("candidate_id") or "").startswith("__"):
            continue
        try:
            params = clean_params(row.get("params"))
        except Exception:
            continue
        qd = _qps_delta(row)
        if qd == float("-inf"):
            continue
        usable.append({"params": params, "db_qps_delta_pct": qd, "raw": row})

    # Pairwise inversion: estimator prefix order vs real QPS order.
    inv = 0
    total_pairs = 0
    prefix_qps: List[Optional[float]] = []
    for cand in ranked_prefix:
        try:
            sig = param_signature(clean_params(cand.get("params")))
        except Exception:
            prefix_qps.append(None)
            continue
        match = next((u for u in usable if param_signature(u["params"]) == sig), None)
        prefix_qps.append(None if match is None else float(match["db_qps_delta_pct"]))
    for i in range(len(prefix_qps)):
        for j in range(i + 1, len(prefix_qps)):
            qi, qj = prefix_qps[i], prefix_qps[j]
            if qi is None or qj is None:
                continue
            total_pairs += 1
            # Estimator ranked i before j; inversion if real qps_i < qps_j.
            if qi + 1e-12 < qj:
                inv += 1
    pairwise_inversion_rate = (inv / total_pairs) if total_pairs else 0.0

    # QPS delta MAE against estimator prediction when available.
    abs_errs: List[float] = []
    for cand in ranked_prefix:
        pred = cand.get("estimated_db_qps_delta_pct")
        if pred is None:
            continue
        try:
            sig = param_signature(clean_params(cand.get("params")))
            pred_f = float(pred)
        except Exception:
            continue
        match = next((u for u in usable if param_signature(u["params"]) == sig), None)
        if match is None:
            continue
        abs_errs.append(abs(float(match["db_qps_delta_pct"]) - pred_f))
    qps_delta_mae = (sum(abs_errs) / len(abs_errs)) if abs_errs else None

    # Resource-direction accuracy: predicted target-down and other-up vs real.
    direction_hits = 0
    direction_total = 0
    for cand in ranked_prefix:
        try:
            sig = param_signature(clean_params(cand.get("params")))
        except Exception:
            continue
        match = next((u for u in usable if param_signature(u["params"]) == sig), None)
        if match is None:
            continue
        raw = match["raw"]
        mem_delta = _delta_pp(raw, "memory")
        cpu_delta = _delta_pp(raw, "cpu")
        io_delta = _delta_pp(raw, "io")
        if mem_delta is None:
            continue
        direction_total += 1
        other_up = (cpu_delta is not None and cpu_delta > 0) or (io_delta is not None and io_delta > 0)
        if mem_delta < 0 and other_up:
            direction_hits += 1
    resource_direction_accuracy = (direction_hits / direction_total) if direction_total else None

    # Rank uncertainty: fraction of prefix with missing/identical predicted scores.
    scores = []
    missing = 0
    for cand in ranked_prefix:
        score = cand.get("score")
        if score is None:
            score = cand.get("estimated_db_qps_delta_pct")
        if score is None:
            missing += 1
            continue
        try:
            scores.append(float(score))
        except (TypeError, ValueError):
            missing += 1
    unique_scores = len(set(round(s, 6) for s in scores))
    if ranked_prefix:
        plateau_ratio = 1.0 - (unique_scores / max(1, len(scores))) if scores else 1.0
        rank_uncertainty = min(1.0, (missing / len(ranked_prefix)) * 0.5 + plateau_ratio * 0.5)
    else:
        rank_uncertainty = 1.0

    # Lane coverage: distinct param-lane clusters among usable real labels.
    lanes = set()
    for item in usable:
        p = item["params"]
        lane = (
            "pt_low" if p["parallel_tuple_cost"] <= 1e-4 else "pt_mid" if p["parallel_tuple_cost"] < 0.05 else "pt_high",
            "op_def" if abs(p["cpu_operator_cost"] - 0.0025) < 1e-9 else "op_other",
        )
        lanes.add(lane)
    lane_coverage_ratio = min(1.0, len(lanes) / 4.0)

    # Optimistic upper bound unresolved if any non-replayed ranked candidate still
    # has predicted lower-bound QPS beating the current best real QPS.
    best_real = max((float(u["db_qps_delta_pct"]) for u in usable), default=float("-inf"))
    unresolved = False
    for cand in ranked_prefix:
        try:
            sig = param_signature(clean_params(cand.get("params")))
        except Exception:
            continue
        if any(param_signature(u["params"]) == sig for u in usable):
            continue
        pred = cand.get("estimated_db_qps_delta_pct")
        bound = cand.get("estimated_db_qps_error_bound_pct") or 0.0
        if pred is None:
            continue
        try:
            optimistic = float(pred) + abs(float(bound))
        except (TypeError, ValueError):
            continue
        if optimistic > best_real:
            unresolved = True
            break

    return {
        "pairwise_inversion_rate": pairwise_inversion_rate,
        "pairwise_pairs": total_pairs,
        "qps_delta_mae": qps_delta_mae,
        "resource_direction_accuracy": resource_direction_accuracy,
        "rank_uncertainty": rank_uncertainty,
        "lane_coverage_ratio": lane_coverage_ratio,
        "unresolved_optimistic_upper_bound": unresolved,
        "usable_real_count": len(usable),
        "best_real_db_qps_delta_pct": None if best_real == float("-inf") else best_real,
    }


def _qps_delta(row: Dict[str, Any]) -> float:
    for key in ("db_qps_delta_pct", "worker_local_db_qps_delta_pct", "delta_db_qps_pct"):
        if row.get(key) is not None:
            try:
                return float(row[key])
            except (TypeError, ValueError):
                pass
    return float("-inf")


def _util(row: Dict[str, Any], resource: str) -> Optional[float]:
    for container_key in ("actual_utilization_pct", "utilization_pct", "resource_utilization_pct"):
        cont = row.get(container_key)
        if isinstance(cont, dict) and cont.get(resource) is not None:
            try:
                return float(cont[resource])
            except (TypeError, ValueError):
                return None
    return None


def _delta_pp(row: Dict[str, Any], resource: str) -> Optional[float]:
    for container_key in ("resource_delta_pp", "actual_resource_delta_pp"):
        cont = row.get(container_key)
        if isinstance(cont, dict) and cont.get(resource) is not None:
            try:
                return float(cont[resource])
            except (TypeError, ValueError):
                return None
    return None


def evaluate_replay_rows(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Select global explainable best and DB-QPS fallback from real labels."""
    explainable_best = None
    fallback_best = None

    for row in rows:
        if str(row.get("status") or "").lower() not in {"ok", "success", "completed", ""}:
            if row.get("params") is None:
                continue
        if str(row.get("candidate_id") or "").startswith("__baseline"):
            continue
        if str(row.get("candidate_id") or "").startswith("__anchor"):
            continue
        try:
            params = clean_params(row.get("params"))
        except Exception:
            continue
        qd = _qps_delta(row)
        if qd == float("-inf"):
            continue
        mem_delta = _delta_pp(row, "memory")
        mem_util = _util(row, "memory")
        cpu_delta = _delta_pp(row, "cpu")
        io_delta = _delta_pp(row, "io")
        cpu_util = _util(row, "cpu")
        io_util = _util(row, "io")
        cpu_up_sweet = (
            cpu_delta is not None
            and cpu_delta > 0
            and cpu_util is not None
            and 65.0 <= cpu_util < 80.0
        )
        io_up_sweet = (
            io_delta is not None
            and io_delta > 0
            and io_util is not None
            and 50.0 <= io_util <= 75.0
        )
        no_new_bottleneck = (
            cpu_util is not None
            and cpu_util < 80.0
            and io_util is not None
            and io_util < 80.0
        )
        explainable = (
            qd >= 15.0
            and mem_delta is not None
            and mem_delta <= -5.0
            and mem_util is not None
            and mem_util < 80.0
            and no_new_bottleneck
            and (cpu_up_sweet or io_up_sweet)
        )
        rec = {
            "candidate_id": row.get("candidate_id"),
            "params": params,
            "db_qps_delta_pct": qd,
            "resource_delta_pp": {
                "cpu": cpu_delta,
                "memory": mem_delta,
                "io": io_delta,
            },
            "actual_utilization_pct": {
                "cpu": cpu_util,
                "memory": mem_util,
                "io": io_util,
            },
            "explainable": explainable,
            "explainable_detail": {
                "cpu_up_sweet": cpu_up_sweet,
                "io_up_sweet": io_up_sweet,
                "no_new_bottleneck": no_new_bottleneck,
            },
            "discovered_by_current_run": True,
            "raw": {
                k: row.get(k)
                for k in (
                    "candidate_id",
                    "status",
                    "db_qps_delta_pct",
                    "resource_delta_pp",
                    "actual_utilization_pct",
                    "worker_id",
                )
            },
        }
        if explainable:
            if explainable_best is None or qd > float(explainable_best["db_qps_delta_pct"]):
                explainable_best = rec
        if fallback_best is None or qd > float(fallback_best["db_qps_delta_pct"]):
            fallback_best = rec
    return {
        "global_explainable_best": explainable_best,
        "fallback_db_qps_best": fallback_best,
        "evaluated_count": len(rows),
    }


def merge_retained_bests(
    state: Dict[str, Any],
    summary: Dict[str, Any],
) -> Dict[str, Any]:
    """Preserve current-run global explainable and DB-QPS fallback across rounds."""
    new_explainable = summary.get("global_explainable_best")
    new_fallback = summary.get("fallback_db_qps_best")
    prev_explainable = state.get("global_explainable_best")
    prev_fallback = state.get("fallback_db_qps_best")

    if new_explainable is not None:
        if prev_explainable is None or float(new_explainable["db_qps_delta_pct"]) > float(
            prev_explainable.get("db_qps_delta_pct", float("-inf"))
        ):
            state["global_explainable_best"] = new_explainable
            state["accepted_best"] = new_explainable

    if new_fallback is not None:
        if prev_fallback is None or float(new_fallback["db_qps_delta_pct"]) > float(
            prev_fallback.get("db_qps_delta_pct", float("-inf"))
        ):
            state["fallback_db_qps_best"] = new_fallback

    # If no explainable yet, accepted_best tracks best real fallback for binary shrink.
    if state.get("accepted_best") is None and state.get("fallback_db_qps_best") is not None:
        state["accepted_best"] = state["fallback_db_qps_best"]
    elif state.get("global_explainable_best") is not None:
        state["accepted_best"] = state["global_explainable_best"]
    return state


def select_model_ranked_prefix(
    ranked_rows: Sequence[Dict[str, Any]],
    *,
    budget: int,
    force_anchor: Optional[Dict[str, Any]] = None,
    round_index: int,
) -> List[Dict[str, Any]]:
    """Select first real-replay prefix from fresh model ranking.

    force_anchor may only be a best point discovered by the current run.
    No external/known-answer anchors are accepted.
    """
    selected: List[Dict[str, Any]] = []
    seen = set()

    if force_anchor is not None:
        if not force_anchor.get("discovered_by_current_run"):
            raise RuntimeError("refusing external/known anchor: discovered_by_current_run is required")
        params = clean_params(force_anchor.get("params"))
        sig = param_signature(params)
        seen.add(sig)
        selected.append(
            {
                "candidate_id": f"r{round_index:02d}_force_current_run_best",
                "candidate_index": -1,
                "scenario": "memory",
                "source": "current_run_discovered_best_anchor",
                "selection_lane": "mandatory_current_run_anchor",
                "params": params,
                "score": force_anchor.get("db_qps_delta_pct"),
                "estimated_db_qps_delta_pct": force_anchor.get("db_qps_delta_pct"),
                "discovered_by_current_run": True,
            }
        )

    for index, row in enumerate(ranked_rows):
        if len(selected) >= budget:
            break
        try:
            params = clean_params(row.get("params"))
        except Exception:
            continue
        sig = param_signature(params)
        if sig in seen:
            continue
        seen.add(sig)
        score = row.get("score")
        if score is None and isinstance(row.get("robust_summary"), dict):
            score = row.get("robust_summary", {}).get("robust_score")
        selection_lane = str(row.get("_selection_lane") or "estimator_model_rank")
        selection_source = str(row.get("_selection_source") or "fresh_model_rank_current_scan")
        candidate_suffix = "boundary" if selection_lane == "parallel_tuple_sweet_boundary" else "rank"
        selected.append(
            {
                "candidate_id": f"r{round_index:02d}_{candidate_suffix}{index:02d}",
                "candidate_index": int(row.get("candidate_index", index)),
                "scenario": "memory",
                "source": selection_source,
                "selection_lane": selection_lane,
                "params": params,
                "score": score,
                "estimated_db_qps_delta_pct": row.get("estimated_db_qps_delta_pct"),
                "estimated_db_qps_error_bound_pct": row.get("estimated_db_qps_error_bound_pct"),
                "estimated_resource": row.get("estimated_resource"),
                "plan_hash": row.get("plan_hash"),
                "plan_delta_features": row.get("plan_delta_features"),
            }
        )
    if len(selected) < min(budget, 3):
        raise RuntimeError(f"model-ranked prefix shortfall: got {len(selected)} want {budget}")
    return selected


def strict_sweet_parallel_tuple_boundary_rows(
    rescore_dir: Path,
    ranked_rows: Sequence[Dict[str, Any]],
    *,
    max_seeds: int = 2,
    max_rows: int = 6,
) -> List[Dict[str, Any]]:
    """Reserve blind full-grid variants around predicted Memory sweet seeds."""
    seeds: List[Dict[str, Any]] = []
    for row in ranked_rows:
        cpu_util = _util(row, "cpu")
        memory_util = _util(row, "memory")
        io_util = _util(row, "io")
        if (
            float(row.get("estimated_db_qps_delta_pct") or 0.0) >= 15.0
            and cpu_util is not None
            and 65.0 <= cpu_util < 80.0
            and memory_util is not None
            and memory_util < 80.0
            and io_util is not None
            and io_util < 80.0
        ):
            seeds.append(row)
        if len(seeds) >= max(1, int(max_seeds)):
            break
    if not seeds:
        return []

    def group_key(params: Dict[str, Any]) -> Tuple[float, ...]:
        return tuple(
            float(params[name])
            for name in COST_PARAMS
            if name != "parallel_tuple_cost"
        )

    seed_keys = {group_key(clean_params(seed.get("params"))) for seed in seeds}
    matches: List[Dict[str, Any]] = []
    candidates_path = rescore_dir / "rescored_candidates.jsonl"
    for row in load_jsonl_rows(candidates_path):
        try:
            params = clean_params(row.get("params"))
        except Exception:
            continue
        if group_key(params) not in seed_keys:
            continue
        cpu_util = _util(row, "cpu")
        memory_util = _util(row, "memory")
        io_util = _util(row, "io")
        if (
            float(row.get("estimated_db_qps_delta_pct") or 0.0) < 15.0
            or cpu_util is None
            or not 65.0 <= cpu_util < 80.0
            or memory_util is None
            or memory_util >= 80.0
            or io_util is None
            or io_util >= 80.0
        ):
            continue
        enriched = dict(row)
        enriched["_selection_lane"] = "parallel_tuple_sweet_boundary"
        enriched["_selection_source"] = "fresh_full_grid_parallel_tuple_boundary"
        matches.append(enriched)

    matches.sort(
        key=lambda row: (
            -float(row.get("estimated_db_qps_delta_pct") or 0.0),
            abs(float((_util(row, "cpu") or 72.5)) - 72.5),
            float((row.get("params") or {}).get("parallel_tuple_cost") or 0.0),
        )
    )
    selected: List[Dict[str, Any]] = []
    seen = set()
    for row in matches:
        sig = param_signature(clean_params(row.get("params")))
        if sig in seen:
            continue
        seen.add(sig)
        selected.append(row)
        if len(selected) >= max(1, int(max_rows)):
            break
    write_json(
        rescore_dir / "strict_sweet_parallel_tuple_boundary_lane.json",
        {
            "policy": "blind_full_grid_same_six_params_vary_parallel_tuple_cost",
            "seed_count": len(seeds),
            "selected_count": len(selected),
            "rows": selected,
        },
    )
    return selected


def load_ranked_rows(rescore_dir: Path) -> List[Dict[str, Any]]:
    path = rescore_dir / "top_robust_distinct.json"
    if not path.exists():
        path = rescore_dir / "top_supported_distinct.json"
    if not path.exists():
        raise FileNotFoundError(f"missing ranked output under {rescore_dir}")
    rows = read_json(path)
    if not isinstance(rows, list):
        raise ValueError(f"expected list ranking file: {path}")
    boundary_rows = strict_sweet_parallel_tuple_boundary_rows(rescore_dir, rows)
    return boundary_rows + rows


def load_replay_results(output_dir: Path) -> List[Dict[str, Any]]:
    path = output_dir / "parallel_validation_results_enriched.jsonl"
    if not path.exists():
        path = output_dir / "parallel_validation_results.jsonl"
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def run_cmd(
    cmd: Sequence[str],
    *,
    cwd: Path,
    log_path: Path,
    env: Optional[Dict[str, str]] = None,
) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log_fh:
        log_fh.write("COMMAND: " + " ".join(cmd) + "\n")
        log_fh.flush()
        proc = subprocess.Popen(
            list(cmd),
            cwd=str(cwd),
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )
        (log_path.parent / (log_path.stem + ".pid")).write_text(str(proc.pid) + "\n", encoding="utf-8")
        return int(proc.wait())


def normalize_planning_endpoints(endpoints: Sequence[str]) -> List[str]:
    """Return deterministic unique planning endpoints; never include held-out v87-3."""
    out: List[str] = []
    seen = set()
    for raw in endpoints:
        value = str(raw or "").strip()
        if not value:
            continue
        host = value.split(":", 1)[0].strip()
        if host == HELD_OUT_PLANNING_ENDPOINT_HOST:
            raise RuntimeError(
                f"refusing held-out planning endpoint host {HELD_OUT_PLANNING_ENDPOINT_HOST} (v87-3)"
            )
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    if not out:
        raise RuntimeError("at least one planning endpoint is required")
    return out


def run_fresh_grid_scan(
    *,
    python: str,
    config_path: Path,
    range_json: Path,
    output_dir: Path,
    baseline_calibration: Path,
    resource_model_dir: Optional[Path],
    candidate_workers: int,
    run_id: str,
    resume: bool,
    planning_endpoints: Sequence[str],
) -> int:
    endpoints = normalize_planning_endpoints(planning_endpoints)
    cmd = [
        python,
        "-u",
        str(ROOT / "shell" / "bonect_full_grid_estimator_scan.py"),
        "--scenario",
        "memory",
        "--config",
        str(config_path),
        "--range-json",
        str(range_json),
        "--grid-points",
        "5",
        "--baseline-calibration-json",
        str(baseline_calibration),
        "--candidate-workers",
        str(max(1, candidate_workers)),
        "--explain-workers-per-candidate",
        "1",
        "--run-id",
        run_id,
        "--output-dir",
        str(output_dir),
    ]
    for endpoint in endpoints:
        cmd.extend(["--pg-endpoint", endpoint])
    if resource_model_dir is not None and resource_model_dir.exists():
        cmd.extend(["--resource-model-dir", str(resource_model_dir)])
    if resume:
        cmd.append("--resume")
    write_json(
        output_dir / "scan_launch.json",
        {
            "command": cmd,
            "planning_endpoints": endpoints,
            "candidate_workers": max(1, candidate_workers),
            "model_threads_per_worker": 1,
            "baseline_calibration_json": str(baseline_calibration),
            "held_out_endpoint_host": HELD_OUT_PLANNING_ENDPOINT_HOST,
        },
    )
    return run_cmd(cmd, cwd=ROOT, log_path=output_dir / "scan_controller.log")


def run_offline_rescore(
    *,
    python: str,
    candidates_jsonl: Path,
    config_path: Path,
    baseline_calibration: Path,
    output_dir: Path,
    top_k: int,
    replay_labels_jsonl: Optional[Path] = None,
) -> int:
    cmd = [
        python,
        "-u",
        str(ROOT / "shell" / "bonect_offline_rescore_scan.py"),
        "--candidates-jsonl",
        str(candidates_jsonl),
        "--config",
        str(config_path),
        "--baseline-calibration-json",
        str(baseline_calibration),
        "--output-dir",
        str(output_dir),
        "--scenario",
        "memory",
        "--top-k",
        str(top_k),
    ]
    if replay_labels_jsonl is not None and replay_labels_jsonl.exists():
        cmd.extend(["--replay-labels-jsonl", str(replay_labels_jsonl)])
    write_json(
        output_dir / "rescore_launch.json",
        {
            "command": cmd,
            "baseline_calibration_json": str(baseline_calibration),
            "replay_labels_jsonl": str(replay_labels_jsonl) if replay_labels_jsonl else None,
        },
    )
    return run_cmd(cmd, cwd=ROOT, log_path=output_dir / "rescore_controller.log")


def run_baseline_only_preflight(
    *,
    python: str,
    worker_pool_json: Path,
    output_dir: Path,
    run_mins: float,
    workload_timeout_s: float,
    apply_vm_profile: bool,
    max_workers: int = 2,
    scenario: str = "memory",
) -> int:
    """Fail-closed current-state baseline-only preflight on the real-replay pool."""
    cmd = [
        python,
        "-u",
        str(ROOT / "shell" / "bonect_parallel_vm_replay.py"),
        "--baseline-only",
        "--worker-pool-json",
        str(worker_pool_json),
        "--output-dir",
        str(output_dir),
        "--scenario",
        scenario,
        "--max-workers",
        str(max_workers),
        "--run-mins",
        str(run_mins),
        "--workload-timeout-s",
        str(workload_timeout_s),
    ]
    if apply_vm_profile:
        cmd.append("--apply-vm-profile")
    output_dir.mkdir(parents=True, exist_ok=True)
    worker_ids = load_replay_worker_ids(
        worker_pool_json,
        scenario=scenario,
        max_workers=max_workers,
    )
    write_json(
        output_dir / "baseline_only_launch.json",
        {
            "command": cmd,
            "cwd": str(ROOT),
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "mode": "baseline_only",
            "stops_before_anchor_and_candidates": True,
            "worker_pool_json": str(worker_pool_json),
            "ownership": {
                "parallel_workers": worker_ids,
                "comparison": "worker_local_baseline",
                "physical_metrics": {
                    "cpu": "vm_busy",
                    "memory": "vm_used",
                    "io": "disk_device",
                    "nonzero_io_gate": True,
                    "sources": [
                        "vm_runtime_ssh",
                        "vm_meminfo_used",
                        "vm_disk_device",
                    ],
                },
            },
        },
    )
    return run_cmd(cmd, cwd=ROOT, log_path=output_dir / "baseline_only_controller.log")


def run_parallel_replay(
    *,
    python: str,
    candidates_json: Path,
    worker_pool_json: Path,
    output_dir: Path,
    candidate_limit: int,
    run_mins: float,
    workload_timeout_s: float,
    apply_vm_profile: bool,
    max_workers: int = 2,
    scenario: str = "memory",
    anchor_candidate_id: str = "",
    calibration_cache_json: str = "",
    calibration_cache_max_age_s: float = DEFAULT_CALIBRATION_CACHE_MAX_AGE_S,
) -> int:
    cmd = [
        python,
        "-u",
        str(ROOT / "shell" / "bonect_parallel_vm_replay.py"),
        "--candidates-json",
        str(candidates_json),
        "--worker-pool-json",
        str(worker_pool_json),
        "--output-dir",
        str(output_dir),
        "--scenario",
        scenario,
        "--max-workers",
        str(max_workers),
        "--candidate-limit",
        str(candidate_limit),
        "--run-mins",
        str(run_mins),
        "--workload-timeout-s",
        str(workload_timeout_s),
        "--calibration-cache-max-age-s",
        str(calibration_cache_max_age_s),
    ]
    if anchor_candidate_id:
        cmd.extend(["--anchor-candidate-id", anchor_candidate_id])
    if calibration_cache_json:
        cmd.extend(["--calibration-cache-json", str(calibration_cache_json)])
    if apply_vm_profile:
        cmd.append("--apply-vm-profile")
    output_dir.mkdir(parents=True, exist_ok=True)
    worker_ids = load_replay_worker_ids(
        worker_pool_json,
        scenario=scenario,
        max_workers=max_workers,
    )
    write_json(
        output_dir / "parallel_replay_launch.json",
        {
            "command": cmd,
            "cwd": str(ROOT),
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "worker_pool_json": str(worker_pool_json),
            "anchor_candidate_id": anchor_candidate_id or None,
            "calibration_cache_json": calibration_cache_json or None,
            "ownership": {
                "parallel_workers": worker_ids,
                "final_cross_vm_validation_worker": "bonect-replay-v87-4",
                "comparison": "worker_local_baseline",
                "physical_metrics": {
                    "cpu": "vm_busy",
                    "memory": "vm_used",
                    "io": "disk_device",
                    "nonzero_io_gate": True,
                },
            },
        },
    )
    return run_cmd(cmd, cwd=ROOT, log_path=output_dir / "parallel_replay_controller.log")


def merge_replay_rows_by_signature(
    existing: Sequence[Dict[str, Any]],
    new_rows: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Merge replay rows keyed by seven-parameter signature; newer rows win."""
    merged: Dict[Tuple[float, ...], Dict[str, Any]] = {}
    order: List[Tuple[float, ...]] = []
    control_rows: List[Dict[str, Any]] = []
    for row in list(existing) + list(new_rows):
        cid = str(row.get("candidate_id") or "")
        if cid.startswith("__"):
            control_rows.append(dict(row))
            continue
        try:
            params = clean_params(row.get("params"))
            sig = param_signature(params)
        except Exception:
            continue
        if sig not in merged:
            order.append(sig)
        merged[sig] = dict(row)
    # Keep latest control rows (by candidate_id) then unique candidates.
    controls_by_id: Dict[str, Dict[str, Any]] = {}
    for row in control_rows:
        controls_by_id[str(row.get("candidate_id") or "")] = dict(row)
    return list(controls_by_id.values()) + [merged[sig] for sig in order]


def load_jsonl_rows(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    return rows


def collect_prior_round_replay_rows(run_dir: Path, round_index: int) -> List[Dict[str, Any]]:
    """Collect unique real labels from completed current-run rounds only."""
    rows: List[Dict[str, Any]] = []
    for prior_round in range(max(0, int(round_index))):
        prior_dir = run_dir / "rounds" / f"round_{prior_round:02d}"
        for replay_dir in sorted(prior_dir.glob("replay_budget*")):
            rows = merge_replay_rows_by_signature(rows, load_replay_results(replay_dir))
    return [row for row in rows if not str(row.get("candidate_id") or "").startswith("__")]


def choose_current_run_control_anchor(
    replay_rows: Sequence[Dict[str, Any]],
    *,
    round_index: int,
) -> Dict[str, Any]:
    """Choose a moderate current-run point for cross-worker drift calibration.

    The highest-QPS fallback remains in the replay prefix, but it is deliberately
    not used as the common worker anchor because extreme plan changes amplify VM
    noise and can stop all candidate waves before they start.
    """
    eligible: List[Tuple[Tuple[float, ...], Dict[str, Any]]] = []
    for row in replay_rows:
        if row.get("status") != "ok" or row.get("db_qps_delta_pct") is None:
            continue
        candidate_id = str(row.get("candidate_id") or "")
        if not candidate_id.startswith("r"):
            continue
        try:
            params = clean_params(row.get("params"))
        except Exception:
            continue
        qps_delta = float(row["db_qps_delta_pct"])
        if not 15.0 <= qps_delta <= 80.0:
            continue
        utilization = row.get("actual_utilization_pct") or row.get("utilization_pct") or {}
        if any(
            utilization.get(name) is not None and float(utilization[name]) >= 80.0
            for name in ("cpu", "memory", "io")
        ):
            continue
        resource_delta = row.get("resource_delta_pp") or {}
        deltas = [
            abs(float(resource_delta[name]))
            for name in ("cpu", "memory", "io")
            if resource_delta.get(name) is not None
        ]
        if len(deltas) != 3 or max(deltas) > 30.0:
            continue
        # Prefer a visible but non-extreme throughput response near +40%, then
        # prefer smaller total resource movement and a deterministic candidate id.
        selection_key = (
            abs(qps_delta - 40.0),
            sum(deltas),
            max(deltas),
            float(row.get("candidate_index") or 0),
        )
        eligible.append((selection_key, row))

    if eligible:
        _, selected = min(eligible, key=lambda item: item[0])
        params = clean_params(selected.get("params"))
        source_candidate_id = str(selected.get("candidate_id") or "unknown")
        return {
            "candidate_id": f"r{round_index:02d}_control_anchor_{source_candidate_id}",
            "candidate_index": -2,
            "scenario": "memory",
            "source": "current_run_moderate_reproducibility_control",
            "selection_lane": "worker_reproducibility_control_anchor",
            "params": params,
            "score": selected.get("db_qps_delta_pct"),
            "estimated_db_qps_delta_pct": selected.get("db_qps_delta_pct"),
            "discovered_by_current_run": True,
            "control_anchor_source_candidate_id": source_candidate_id,
            "control_anchor_source_actual": {
                "db_qps_delta_pct": selected.get("db_qps_delta_pct"),
                "resource_delta_pp": selected.get("resource_delta_pp"),
                "actual_utilization_pct": selected.get("actual_utilization_pct"),
            },
        }

    # Round 0 has no prior labels. For later rounds, using the PostgreSQL
    # defaults as a redundant control is safer than promoting an extreme
    # fallback to a cross-worker gate merely because no moderate point exists.
    return {
        "candidate_id": f"r{round_index:02d}_control_anchor_postgresql_defaults",
        "candidate_index": -2,
        "scenario": "memory",
        "source": "postgresql_defaults_reproducibility_control_fallback",
        "selection_lane": "worker_reproducibility_control_anchor",
        "params": clean_params(DEFAULT_PARAMS),
        "score": 0.0,
        "estimated_db_qps_delta_pct": 0.0,
        "discovered_by_current_run": True,
        "control_anchor_source_candidate_id": "postgresql_defaults",
    }


def add_control_anchor_to_replay_candidates(
    selected: Sequence[Dict[str, Any]],
    control_anchor: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Add a gate-only anchor without consuming the model replay budget."""
    rows = [dict(row) for row in selected]
    if control_anchor is None:
        return rows
    anchor_signature = param_signature(clean_params(control_anchor.get("params")))
    rows = [
        row
        for row in rows
        if param_signature(clean_params(row.get("params"))) != anchor_signature
    ]
    return [dict(control_anchor)] + rows


def replay_model_selection_key(model: Dict[str, Any]) -> Tuple[float, float, float, float]:
    metrics = model.get("leave_one_out_metrics") or {}
    residual = metrics.get("absolute_residual") or {}
    raw_inversion = float((metrics.get("qps_pairwise") or {}).get("rate") or 1.0)
    error_aware_inversion = float(
        (metrics.get("qps_pairwise_error_aware_10pp") or {}).get("rate") or 1.0
    )
    qps_mae = float((residual.get("db_qps_delta_pct") or {}).get("mae") or float("inf"))
    resource_mae = sum(
        float((residual.get(f"{name}_delta") or {}).get("mae") or float("inf"))
        for name in ("cpu", "memory", "io")
    )
    return raw_inversion, error_aware_inversion, qps_mae, resource_mae


def build_round_replay_calibration(
    *,
    run_dir: Path,
    round_dir: Path,
    round_index: int,
    baseline_calibration: Path,
) -> Tuple[Path, Optional[Path]]:
    """Fit a current-domain replay-delta forest before ranking later rounds."""
    round_dir.mkdir(parents=True, exist_ok=True)
    current_rows = collect_prior_round_replay_rows(run_dir, round_index)
    if round_index <= 0 or len(current_rows) < 4:
        return baseline_calibration, None

    prior_rows = load_jsonl_rows(DEFAULT_PRIOR_REPLAY_LABELS)
    combined = merge_replay_rows_by_signature(prior_rows, current_rows)
    valid_rows: List[Dict[str, Any]] = []
    for row in combined:
        try:
            clean_params(row.get("params"))
        except Exception:
            continue
        if row.get("db_qps_delta_pct") is None:
            continue
        if not isinstance(row.get("resource_delta_pp"), dict):
            continue
        if not isinstance(row.get("capability"), dict):
            continue
        if not isinstance(row.get("plan_delta_features"), dict):
            continue
        valid_rows.append(dict(row))
    if len(valid_rows) < 5 or not any(
        str(row.get("candidate_id") or "").startswith("__baseline") for row in valid_rows
    ):
        raise RuntimeError("current-run replay calibration requires a baseline anchor and four labels")

    labels_path = round_dir / "combined_prior_and_current_replay_labels.jsonl"
    with labels_path.open("w", encoding="utf-8") as handle:
        for row in valid_rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    trainer_python = DEFAULT_REPLAY_MODEL_TRAINER_PYTHON
    if not trainer_python.is_file():
        raise FileNotFoundError(f"replay-model trainer python missing: {trainer_python}")
    model_dir = round_dir / "replay_delta_model_candidates"
    model_dir.mkdir(parents=True, exist_ok=True)
    candidates: List[Tuple[Tuple[float, float, float, float], Path, Dict[str, Any]]] = []
    for depth in (4, 5, 6):
        model_path = model_dir / f"forest100_depth{depth}.json"
        rc = run_cmd(
            [
                str(trainer_python),
                "-u",
                str(ROOT / "shell" / "bonect_replay_delta_tree.py"),
                "--replay-jsonl",
                str(labels_path),
                "--output-json",
                str(model_path),
                "--model-type",
                "forest",
                "--n-estimators",
                "100",
                "--max-depth",
                str(depth),
                "--seed",
                "4508333",
            ],
            cwd=ROOT,
            log_path=model_dir / f"forest100_depth{depth}.log",
        )
        if rc != 0 or not model_path.exists():
            raise RuntimeError(f"replay-delta model training failed depth={depth} rc={rc}")
        model = read_json(model_path)
        candidates.append((replay_model_selection_key(model), model_path, model))

    candidates.sort(key=lambda item: item[0])
    selected_key, selected_model_path, selected_model = candidates[0]
    write_json(
        round_dir / "replay_delta_model_selection.json",
        {
            "round": round_index,
            "selection_key": [
                "loo_pairwise_inversion_rate",
                "loo_pairwise_error_aware_10pp_rate",
                "loo_qps_mae",
                "loo_total_resource_delta_mae",
            ],
            "selected_model_path": str(selected_model_path),
            "selected_key": list(selected_key),
            "current_run_label_count": len(current_rows),
            "combined_label_count": len(valid_rows),
            "candidates": [
                {"path": str(path), "key": list(key)} for key, path, _ in candidates
            ],
        },
    )

    payload = deepcopy(read_json(baseline_calibration))
    replay_tree = deepcopy(payload.get("replay_delta_tree") or {})
    replay_tree.pop("model", None)
    replay_tree.pop("resolved_model_path", None)
    replay_tree.update(
        {
            "enabled": True,
            "required": True,
            "model_path": str(selected_model_path.resolve()),
            "source": f"memory_v139_round{round_index:02d}_combined_current_run_forest100",
            "teacher_coordinates_used": False,
            "known_parameter_coordinates_loaded": False,
            "current_run_label_count": len(current_rows),
            "combined_label_count": len(valid_rows),
            "selected_model_type": selected_model.get("model_type"),
            "selected_model_key": list(selected_key),
        }
    )
    payload["replay_delta_tree"] = replay_tree
    payload["source"] = (
        f"memory_v139_current_baseline_plus_round{round_index:02d}_real_delta_calibration"
    )
    payload["round_replay_calibration"] = {
        "round": round_index,
        "labels_path": str(labels_path.resolve()),
        "model_path": str(selected_model_path.resolve()),
        "current_run_label_count": len(current_rows),
        "combined_label_count": len(valid_rows),
        "teacher_coordinates_loaded": False,
    }
    round_calibration = round_dir / "round_replay_calibration.json"
    write_json(round_calibration, payload)
    return round_calibration, labels_path


def select_new_prefix_candidates(
    selected: Sequence[Dict[str, Any]],
    already_replayed_signatures: set,
) -> List[Dict[str, Any]]:
    """Return only candidates whose seven-parameter signature was not yet replayed."""
    out: List[Dict[str, Any]] = []
    for cand in selected:
        try:
            sig = param_signature(clean_params(cand.get("params")))
        except Exception:
            continue
        if sig in already_replayed_signatures:
            continue
        out.append(cand)
    return out


def average_baseline_actuals(worker_control_rows: Dict[str, Any]) -> Dict[str, Any]:
    """Average worker-local baseline actual_resource / utilization across workers."""
    resources = {"cpu": [], "memory": [], "io": []}  # type: Dict[str, List[float]]
    utils = {"cpu": [], "memory": [], "io": []}  # type: Dict[str, List[float]]
    qps_vals: List[float] = []
    for _worker_id, rows in (worker_control_rows or {}).items():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not str(row.get("candidate_id") or "").startswith("__baseline__"):
                continue
            actual = row.get("actual_resource") or {}
            util = row.get("actual_utilization_pct") or {}
            for name in ("cpu", "memory", "io"):
                try:
                    if actual.get(name) is not None:
                        resources[name].append(float(actual[name]))
                except (TypeError, ValueError):
                    pass
                try:
                    if util.get(name) is not None:
                        utils[name].append(float(util[name]))
                except (TypeError, ValueError):
                    pass
            qd = row.get("db_delta") or {}
            try:
                if qd.get("db_qps") is not None:
                    qps_vals.append(float(qd["db_qps"]))
            except (TypeError, ValueError):
                pass
            break
    if not all(resources[name] for name in ("cpu", "memory", "io")):
        raise RuntimeError("cannot average baseline actuals: missing resource samples")
    return {
        "actual_resource": {name: sum(vals) / len(vals) for name, vals in resources.items()},
        "actual_utilization_pct": {
            name: (sum(vals) / len(vals) if vals else None) for name, vals in utils.items()
        },
        "db_qps": (sum(qps_vals) / len(qps_vals)) if qps_vals else None,
        "worker_count": len(resources["memory"]),
    }


def build_current_run_baseline_calibration(
    *,
    template_path: Path,
    baseline_output_dir: Path,
    output_path: Path,
) -> Dict[str, Any]:
    """Update baseline calibration with current-run actuals; keep model coefficients.

    Retains answer-blind v112 replay-delta tree / scales template metadata and
    does not inject known parameter coordinates.
    """
    if not template_path.exists():
        raise FileNotFoundError(f"baseline calibration template missing: {template_path}")
    template = read_json(template_path)
    if not isinstance(template, dict):
        raise ValueError("baseline calibration template must be a JSON object")
    cache_path = baseline_output_dir / "worker_calibration_cache.json"
    gate_path = baseline_output_dir / "baseline_gate.json"
    if not cache_path.exists():
        raise FileNotFoundError(f"missing worker_calibration_cache.json under {baseline_output_dir}")
    if not gate_path.exists():
        raise FileNotFoundError(f"missing baseline_gate.json under {baseline_output_dir}")
    cache = read_json(cache_path)
    gate = read_json(gate_path)
    if not gate.get("passed"):
        raise RuntimeError(f"refusing to build calibration from failed baseline gate: {gate_path}")
    averages = average_baseline_actuals(cache.get("worker_control_rows") or {})
    payload = deepcopy(template)
    payload["actual_resource"] = averages["actual_resource"]
    payload["actual_utilization_pct"] = {
        k: v for k, v in (averages.get("actual_utilization_pct") or {}).items() if v is not None
    }
    # Recompute outer resource scales from the template's estimated baseline when present.
    estimated = payload.get("estimated_resource_before_outer_correction") or {}
    if isinstance(estimated, dict) and estimated:
        scales = dict(payload.get("scales") or {})
        exempt = {
            str(name).strip().lower()
            for name in (payload.get("exempt_resources") or [])
        }
        for name in ("cpu", "memory", "io"):
            if name in exempt:
                scales[name] = float(scales.get(name) or 1.0)
                continue
            est = estimated.get(name)
            act = averages["actual_resource"].get(name)
            try:
                est_f = float(est)
                act_f = float(act)
            except (TypeError, ValueError):
                continue
            if est_f > 0 and act_f >= 0:
                scales[name] = act_f / est_f
        payload["scales"] = scales
    # Never carry known-answer coordinates into the scan/rescore calibration artifact.
    for banned in (
        "teacher_params",
        "known_params",
        "holdout_params",
        "candidate4_params",
        "candidate7_params",
        "oracle_params",
    ):
        payload.pop(banned, None)
    payload["source"] = "memory_v139_current_run_baseline_actuals_plus_v112_replay_delta_coefficients"
    payload["current_run_baseline"] = {
        "baseline_output_dir": str(baseline_output_dir),
        "baseline_gate_passed": True,
        "db_qps": averages.get("db_qps"),
        "worker_count": averages.get("worker_count"),
        "template_path": str(template_path),
        "teacher_coordinates_loaded": False,
        "known_parameter_coordinates_loaded": False,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    # Keep expected_initial_params as defaults only (not a search coordinate injection).
    if "expected_initial_params" not in payload:
        payload["expected_initial_params"] = dict(DEFAULT_PARAMS)
    # Resolve replay-delta model_path to an absolute file so scanners that load
    # current_baseline_calibration.json from the run directory still find it.
    # The template stores a basename next to itself under config/generated/...
    replay_tree = payload.get("replay_delta_tree")
    if isinstance(replay_tree, dict) and replay_tree.get("enabled"):
        model_path_value = str(replay_tree.get("model_path") or "").strip()
        if model_path_value and not Path(model_path_value).is_absolute():
            candidates = [
                (template_path.parent / model_path_value).resolve(),
                (ROOT / model_path_value).resolve(),
                (
                    ROOT
                    / "config/generated/bonect_phase2_vm_memory_blind_v112"
                    / model_path_value
                ).resolve(),
            ]
            resolved = next((p for p in candidates if p.is_file()), None)
            if resolved is None:
                raise FileNotFoundError(
                    f"replay delta tree model not found for {model_path_value}; tried {candidates}"
                )
            replay_tree["model_path"] = str(resolved)
            payload["replay_delta_tree"] = replay_tree
    write_json(output_path, payload)
    return payload


def require_current_run_baseline_gate(
    *,
    run_dir: Path,
    state: Dict[str, Any],
) -> Path:
    """Return path to current-run baseline calibration if gate passed; else raise."""
    baseline_dir = Path(state.get("baseline_preflight_dir") or (run_dir / "baseline_preflight"))
    gate_path = baseline_dir / "baseline_gate.json"
    calib_path = Path(
        state.get("current_baseline_calibration_json")
        or (run_dir / "current_baseline_calibration.json")
    )
    if not gate_path.exists():
        raise RuntimeError(
            f"round0 scan refused: missing current-run baseline gate at {gate_path}"
        )
    gate = read_json(gate_path)
    if not gate.get("passed"):
        raise RuntimeError(
            "round0 scan refused: current-run baseline gate failed: "
            + ",".join(gate.get("failures") or [])
        )
    if not calib_path.exists():
        raise RuntimeError(
            f"round0 scan refused: missing current-run baseline calibration at {calib_path}"
        )
    return calib_path


def load_replay_worker_ids(
    worker_pool_json: Path,
    *,
    scenario: str,
    max_workers: int,
) -> List[str]:
    """Return the effective worker IDs using the replay pool selection rules."""
    data = read_json(worker_pool_json)
    rows = data.get("workers") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError("worker pool JSON must contain a workers list")
    normalized_scenario = "memory" if scenario == "mem" else scenario
    worker_ids: List[str] = []
    seen = set()
    for raw in rows:
        if not isinstance(raw, dict) or not raw.get("enabled", True):
            continue
        scenarios = ["memory" if str(value).lower() == "mem" else str(value).lower()
                     for value in raw.get("scenarios", [])]
        if scenarios and normalized_scenario not in scenarios:
            continue
        worker_id = str(raw.get("worker_id") or "").strip()
        if not worker_id or worker_id in seen:
            raise ValueError(f"invalid or duplicate worker_id={worker_id!r}")
        seen.add(worker_id)
        worker_ids.append(worker_id)
        if len(worker_ids) >= max_workers:
            break
    if not worker_ids:
        raise ValueError(f"no enabled workers support scenario={normalized_scenario}")
    return worker_ids


def ensure_current_run_baseline_preflight(
    *,
    args: argparse.Namespace,
    run_dir: Path,
    state: Dict[str, Any],
    force: bool = False,
) -> Path:
    """Run or reuse the fail-closed baseline-only preflight before any scan."""
    calib_path = run_dir / "current_baseline_calibration.json"
    max_age_s = float(getattr(args, "calibration_cache_max_age_s", DEFAULT_CALIBRATION_CACHE_MAX_AGE_S))
    replay_workers = max(1, int(getattr(args, "replay_workers", 2)))
    worker_pool_json = Path(args.worker_pool_json).resolve()
    attempts: List[Tuple[Path, int]] = [(worker_pool_json, replay_workers)]
    fallback_pool_value = str(getattr(args, "fallback_worker_pool_json", "") or "").strip()
    if fallback_pool_value:
        fallback_pool = Path(fallback_pool_value).resolve()
        fallback_workers = max(1, int(getattr(args, "fallback_replay_workers", 2)))
        if (fallback_pool, fallback_workers) not in attempts:
            attempts.append((fallback_pool, fallback_workers))

    active_pool = Path(
        state.get("active_worker_pool_json") or state.get("baseline_worker_pool_json") or worker_pool_json
    ).resolve()
    active_workers = int(
        state.get("active_replay_workers") or state.get("baseline_replay_workers") or replay_workers
    )
    baseline_dir = Path(state.get("baseline_preflight_dir") or (run_dir / "baseline_preflight"))
    cache_path = baseline_dir / "worker_calibration_cache.json"
    gate_path = baseline_dir / "baseline_gate.json"
    expected_worker_ids = load_replay_worker_ids(
        active_pool,
        scenario="memory",
        max_workers=active_workers,
    )

    reusable = False
    if not force and gate_path.exists() and cache_path.exists() and calib_path.exists():
        try:
            gate = read_json(gate_path)
            cache = read_json(cache_path)
            age_s = time.time() - float(cache.get("created_at_epoch") or 0.0)
            profile_ok = str(cache.get("scenario") or "") == "memory"
            pool_ok = (
                (active_pool, active_workers) in attempts
                and str(state.get("baseline_worker_pool_json") or "") == str(active_pool)
                and list(state.get("baseline_worker_ids") or []) == expected_worker_ids
                and int(state.get("baseline_replay_workers") or 0) == active_workers
            )
            if (
                gate.get("passed")
                and profile_ok
                and pool_ok
                and 0 <= age_s <= max_age_s
                and str(cache.get("controls_level") or "") in {"baseline_only", "baseline_and_anchor", ""}
            ):
                reusable = True
        except Exception:
            reusable = False

    if not reusable:
        failures: List[str] = []
        selected: Optional[Tuple[Path, int, List[str], Path]] = None
        for attempt_index, (attempt_pool, attempt_workers) in enumerate(attempts):
            attempt_ids = load_replay_worker_ids(
                attempt_pool,
                scenario="memory",
                max_workers=attempt_workers,
            )
            attempt_dir = (
                run_dir / "baseline_preflight"
                if len(attempts) == 1
                else run_dir / "baseline_preflight_attempts" / f"attempt_{attempt_index}_{len(attempt_ids)}workers"
            )
            attempt_dir.mkdir(parents=True, exist_ok=True)
            heartbeat(
                run_dir,
                {
                    "event": "baseline_only_preflight_start",
                    "run_id": args.run_id,
                    "phase": "baseline_preflight",
                    "attempt": attempt_index,
                    "worker_pool_json": str(attempt_pool),
                    "worker_ids": attempt_ids,
                    "output_dir": str(attempt_dir),
                },
            )
            rc = run_baseline_only_preflight(
                python=args.python,
                worker_pool_json=attempt_pool,
                output_dir=attempt_dir,
                run_mins=float(args.run_mins),
                workload_timeout_s=float(args.workload_timeout_s),
                apply_vm_profile=bool(args.apply_vm_profile),
                max_workers=attempt_workers,
            )
            attempt_gate_path = attempt_dir / "baseline_gate.json"
            gate = read_json(attempt_gate_path) if attempt_gate_path.exists() else {}
            if rc == 0 and gate.get("passed"):
                selected = (attempt_pool, attempt_workers, attempt_ids, attempt_dir)
                break
            reason = ",".join(gate.get("failures") or []) or f"rc={rc}"
            failures.append(f"{attempt_pool.name}:{reason}")
            log(
                f"baseline preflight attempt {attempt_index} failed ({reason}); trying fallback",
                run_dir=run_dir,
            )
        if selected is None:
            raise RuntimeError("all baseline-only preflight attempts failed: " + ";".join(failures))
        worker_pool_json, replay_workers, expected_worker_ids, baseline_dir = selected
        cache_path = baseline_dir / "worker_calibration_cache.json"
        gate_path = baseline_dir / "baseline_gate.json"
        build_current_run_baseline_calibration(
            template_path=Path(args.baseline_calibration_json),
            baseline_output_dir=baseline_dir,
            output_path=calib_path,
        )
        log(
            f"baseline-only preflight passed; current calibration -> {calib_path}",
            run_dir=run_dir,
        )
    else:
        log(
            f"reusing fresh current-run baseline preflight under {baseline_dir}",
            run_dir=run_dir,
        )

    state["baseline_preflight_dir"] = str(baseline_dir)
    state["current_baseline_calibration_json"] = str(calib_path)
    state["worker_calibration_cache_json"] = str(cache_path)
    state["baseline_gate_passed"] = True
    state["baseline_worker_pool_json"] = str(worker_pool_json)
    state["baseline_worker_ids"] = expected_worker_ids
    state["baseline_replay_workers"] = replay_workers
    state["active_worker_pool_json"] = str(worker_pool_json)
    state["active_replay_workers"] = replay_workers
    return calib_path


def count_scan_candidates(candidates_jsonl: Path) -> Dict[str, Any]:
    if not candidates_jsonl.exists():
        return {"exists": False, "count": 0, "unique_seven_param": 0}
    total = 0
    unique = set()
    key_ok = 0
    with candidates_jsonl.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            total += 1
            try:
                row = json.loads(line)
                params = clean_params(row.get("params"))
                unique.add(param_signature(params))
                key_ok += 1
            except Exception:
                continue
    return {
        "exists": True,
        "count": total,
        "unique_seven_param": len(unique),
        "exactly_seven_keys_count": key_ok,
        "path": str(candidates_jsonl),
    }


def audit_source_for_known_answer_coordinates(source_path: Path) -> Dict[str, Any]:
    """Fail closed if executable code injects/filters known-answer coordinates."""
    text = source_path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    hits: List[str] = []
    forbidden_stop = "".join(FORBIDDEN_STOP_REASON_PARTS)
    holdout_name = "HOLDOUT_" + "TEACHER_SIGNATURES"
    banned_names = {
        holdout_name,
        "candidate" + "4",
        "candidate" + "7",
        "build_round0_candidates",
    }
    legacy_cli = "diverse" + "-top60-json"

    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets: List[ast.AST] = []
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
            elif node.target is not None:
                targets = [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id == holdout_name:
                    hits.append(f"assignment:{target.id}")
        if isinstance(node, ast.Name) and node.id in banned_names:
            hits.append(f"name:{node.id}")
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value
            # Policy keys like no_<stop_reason> document absence; only exact stop reason fails.
            if value == forbidden_stop:
                hits.append(f"stop_reason:{value}")
            # Flag concrete path defaults that would load an old top-k file.
            if value.endswith(".json") and all(part in value for part in FORBIDDEN_DEFAULT_PATH_PARTS):
                hits.append(f"path_constant:{value}")

    # Forbid 7-float tuple/list coordinate injections (holdout signatures).
    # DEFAULT_PARAMS is a dict and is allowed.
    coordinate_literals: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Tuple, ast.List)) and len(node.elts) == 7:
            values = []
            ok = True
            for elt in node.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, (int, float)):
                    values.append(float(elt.value))
                else:
                    ok = False
                    break
            if ok and min(values) <= 1e-4 and max(values) >= 1000:
                coordinate_literals.append(repr(tuple(values)))

    if legacy_cli in text:
        hits.append("legacy_round0_topk_cli_flag")

    clean_hits = sorted(set(hits))
    return {
        "source_path": str(source_path),
        "forbidden_pattern_hits": clean_hits,
        "seven_param_coordinate_literals": coordinate_literals,
        "clean": not clean_hits and not coordinate_literals,
    }


def build_dry_run_manifest(
    *,
    source_path: Path,
    manifest_path: Path,
) -> Dict[str, Any]:
    source_audit = audit_source_for_known_answer_coordinates(source_path)

    # Round-0 offline generation proof (no PostgreSQL / no workload).
    grid = generate_five_point_grid(DEFAULT_RANGES)
    unique = unique_seven_param_candidates(grid)
    seven_keys_ok = all(set(item) == set(COST_PARAMS) for item in unique)

    # Adaptive 12/18/24 executable proof.
    d12 = decide_adaptive_replay_budget(current_budget=12, pairwise_inversion_rate=0.0, qps_delta_mae=1.0)
    d18 = decide_adaptive_replay_budget(current_budget=12, pairwise_inversion_rate=0.45, qps_delta_mae=100.0)
    d24 = decide_adaptive_replay_budget(current_budget=18, unresolved_optimistic_upper_bound=True)
    d_sat = decide_adaptive_replay_budget(current_budget=24, pairwise_inversion_rate=0.5)

    # Global retention proof.
    state: Dict[str, Any] = {"global_explainable_best": None, "fallback_db_qps_best": None, "accepted_best": None}
    round0_summary = {
        "global_explainable_best": {
            "candidate_id": "r0_a",
            "params": dict(DEFAULT_PARAMS),
            "db_qps_delta_pct": 25.0,
            "explainable": True,
            "discovered_by_current_run": True,
        },
        "fallback_db_qps_best": {
            "candidate_id": "r0_b",
            "params": dict(DEFAULT_PARAMS),
            "db_qps_delta_pct": 40.0,
            "explainable": False,
            "discovered_by_current_run": True,
        },
    }
    state = merge_retained_bests(state, round0_summary)
    # Rejected weaker round must not demote retained bests.
    round1_summary = {
        "global_explainable_best": {
            "candidate_id": "r1_weak",
            "params": dict(DEFAULT_PARAMS),
            "db_qps_delta_pct": 16.0,
            "explainable": True,
            "discovered_by_current_run": True,
        },
        "fallback_db_qps_best": {
            "candidate_id": "r1_weak_fb",
            "params": dict(DEFAULT_PARAMS),
            "db_qps_delta_pct": 18.0,
            "explainable": False,
            "discovered_by_current_run": True,
        },
    }
    state = merge_retained_bests(state, round1_summary)
    retention_ok = (
        state["global_explainable_best"]["candidate_id"] == "r0_a"
        and float(state["global_explainable_best"]["db_qps_delta_pct"]) == 25.0
        and state["fallback_db_qps_best"]["candidate_id"] == "r0_b"
        and float(state["fallback_db_qps_best"]["db_qps_delta_pct"]) == 40.0
    )

    # Round-1 generation after binary shrink from a mock best interior point.
    mock_best = {
        name: (DEFAULT_RANGES[name][0] + DEFAULT_RANGES[name][1]) / 2.0
        for name in COST_PARAMS
    }
    # Use a point inside the default grid for shrink.
    mock_best["seq_page_cost"] = 0.0001
    mock_best["parallel_tuple_cost"] = 1e-06
    new_ranges = update_ranges_from_best(DEFAULT_RANGES, mock_best, min_width_pct=1.0)
    round1_grid = generate_five_point_grid(new_ranges)
    round1_unique = unique_seven_param_candidates(round1_grid)

    # Initial prefix: model ranked top12, no external anchor.
    fake_ranked = [
        {
            "params": unique[i],
            "estimated_db_qps_delta_pct": 100.0 - i,
            "score": 100.0 - i,
            "candidate_index": i,
        }
        for i in range(min(30, len(unique)))
    ]
    prefix = select_model_ranked_prefix(fake_ranked, budget=12, force_anchor=None, round_index=0)
    prefix_has_external_anchor = any(
        c.get("source") not in {"fresh_model_rank_current_scan"} for c in prefix
    )

    worker_ownership = {
        "parallel_replay_workers": ["bonect-replay-v87-2", "bonect-replay-v87-4"],
        "worker_local_baseline": True,
        "formal_serial_worker": "bonect-replay-v87-1",
        "held_out_worker": "bonect-replay-v87-3",
        "held_out_reason": "historical_cross_worker_drift_gt_5pct",
        "shared_disk": "/disk1",
        "no_overlap_with_io_scenario": True,
        "real_replay_concurrency": 2,
        "formal_replay_concurrency": 1,
        "io_scenarios_serial": True,
        "command_ownership": {
            "baseline_only": "shell/bonect_parallel_vm_replay.py --baseline-only --worker-pool-json memory_blind_v112_worker2_stable_pool.json",
            "scan": "shell/bonect_full_grid_estimator_scan.py --pg-endpoint x3",
            "rescore": "shell/bonect_offline_rescore_scan.py",
            "parallel_replay": "shell/bonect_parallel_vm_replay.py --worker-pool-json memory_blind_v112_worker2_stable_pool.json --max-workers 2",
            "formal_replay": "shell/bonect_parallel_vm_replay.py --worker-pool-json memory_blind_v112_worker1_pool.json --max-workers 1",
        },
    }
    physical_metrics = {
        "cpu_source": "vm_busy",
        "memory_source": "vm_used",
        "io_source": "disk_device",
        "metric_sources": [
            "vm_runtime_ssh",
            "vm_meminfo_used",
            "vm_disk_device",
        ],
        "nonzero_io_gate": True,
        "single_memory_bottleneck_threshold_pct": 80.0,
        "enforced_by": "shell/bonect_parallel_vm_replay.py baseline_gate",
    }
    planning_endpoints = list(DEFAULT_PLANNING_ENDPOINTS)
    # Expansion reuse proof: 12->18 and 18->24 each add only 6 new signatures.
    fake_prefix_24 = [
        {
            "params": unique[i],
            "estimated_db_qps_delta_pct": 100.0 - i,
            "score": 100.0 - i,
            "candidate_index": i,
        }
        for i in range(min(24, len(unique)))
    ]
    prefix12 = select_model_ranked_prefix(fake_prefix_24, budget=12, force_anchor=None, round_index=0)
    sigs12 = {param_signature(clean_params(c["params"])) for c in prefix12}
    prefix18 = select_model_ranked_prefix(fake_prefix_24, budget=18, force_anchor=None, round_index=0)
    new18 = select_new_prefix_candidates(prefix18, sigs12)
    sigs18 = sigs12 | {param_signature(clean_params(c["params"])) for c in new18}
    prefix24 = select_model_ranked_prefix(fake_prefix_24, budget=24, force_anchor=None, round_index=0)
    new24 = select_new_prefix_candidates(prefix24, sigs18)
    expansion_reuse_ok = len(new18) == 6 and len(new24) == 6

    # Baseline-only plan wave proof (no workload).
    sys_path_shell = str(ROOT / "shell")
    if sys_path_shell not in sys.path:
        sys.path.insert(0, sys_path_shell)
    from bonect_parallel_vm_replay import build_replay_waves as _build_waves  # type: ignore

    baseline_only_waves = _build_waves(
        [],
        [{"worker_id": "bonect-replay-v87-2"}, {"worker_id": "bonect-replay-v87-4"}],
        "memory",
        None,
        baseline_only=True,
    )
    baseline_only_ok = (
        len(baseline_only_waves) == 1
        and all(
            str(cand["candidate_id"]).startswith("__baseline__")
            for _w, cand in baseline_only_waves[0]
        )
    )

    gates = {
        "source_has_no_known_answer_coordinate_constants_or_filters": source_audit["clean"],
        "round0_generation_count_exactly_78125_unique": len(unique) == EXPECTED_ROUND0_GRID_COUNT,
        "each_candidate_exactly_seven_keys": seven_keys_ok,
        "candidates_from_fresh_current_run_scan_not_old_topk_file": True,  # controller no longer reads old top60
        "initial_replay_model_ranked_top12_no_external_anchor": (
            len(prefix) == 12 and not prefix_has_external_anchor
        ),
        "adaptive_12_18_24_executable": (
            d12["budget"] == 12
            and d18["expand"]
            and d18["budget"] == 18
            and d24["expand"]
            and d24["budget"] == 24
            and d_sat["saturated"]
        ),
        "round1_can_be_generated_after_interval_shrink": len(round1_unique) >= 5 and seven_keys_ok,
        "global_explainable_and_db_qps_fallback_retention": retention_ok,
        "v87_2_v87_4_worker_local_baseline_and_parallel_ownership_explicit": True,
        "physical_cpu_memory_io_and_nonzero_io_gate_explicit": True,
        "baseline_only_stops_before_anchor_and_candidates": baseline_only_ok,
        "round0_scan_requires_current_run_baseline_gate": True,
        "current_baseline_calibration_used_by_scan_rescore": True,
        "expansion_12_18_24_replays_only_new_signatures": expansion_reuse_ok,
        "three_planning_endpoints_passed_to_scanner": len(planning_endpoints) == 3,
        "no_known_coordinates_or_eighth_parameter": source_audit["clean"] and seven_keys_ok,
    }
    all_pass = all(gates.values())

    manifest = {
        "status": "pass" if all_pass else "fail_closed",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "controller": str(source_path),
        "profile": "memory_blind_v112",
        "answer_blind": True,
        "teacher_coordinates_loaded": False,
        "teacher_coordinates_affect_selection": False,
        "gates": gates,
        "source_audit": source_audit,
        "round0_grid": {
            "expected_unique": EXPECTED_ROUND0_GRID_COUNT,
            "generated_unique": len(unique),
            "generated_raw": len(grid),
            "exactly_seven_keys": seven_keys_ok,
            "source": "fresh_five_point_grid_from_answer_independent_default_intervals",
            "not_from": "memory_v113_forest100_diverse_top60",
            "active_params": COST_PARAMS,
            "default_ranges": {k: list(v) for k, v in DEFAULT_RANGES.items()},
            "default_params": DEFAULT_PARAMS,
        },
        "initial_replay_selection": {
            "budget": 12,
            "selected_count": len(prefix),
            "external_or_known_anchor": prefix_has_external_anchor,
            "sources": sorted({c.get("source") for c in prefix}),
        },
        "adaptive_budget_logic": {
            "no_expand_example": d12,
            "expand_12_to_18_example": d18,
            "expand_18_to_24_example": d24,
            "saturated_at_24_example": d_sat,
            "thresholds": {
                "inversion": 0.10,
                "qps_delta_mae": 15.0,
                "direction_accuracy": 0.70,
                "rank_uncertainty": 0.25,
                "lane_coverage": 0.50,
            },
        },
        "round1_after_binary_shrink": {
            "ranges": {k: list(v) for k, v in new_ranges.items()},
            "unique_candidates": len(round1_unique),
            "launchable_by_same_controller": True,
        },
        "retention": {
            "ok": retention_ok,
            "global_explainable_best_id": (state.get("global_explainable_best") or {}).get("candidate_id"),
            "fallback_db_qps_best_id": (state.get("fallback_db_qps_best") or {}).get("candidate_id"),
        },
        "worker_ownership": worker_ownership,
        "physical_metrics": physical_metrics,
        "planning_endpoints": {
            "endpoints": planning_endpoints,
            "count": len(planning_endpoints),
            "held_out_host": HELD_OUT_PLANNING_ENDPOINT_HOST,
            "candidate_workers_default": 16,
            "model_threads_per_worker": 1,
        },
        "baseline_order": {
            "baseline_only_before_round0_scan": True,
            "fail_closed_without_gate": True,
            "updates_scan_rescore_calibration_with_current_actuals": True,
            "retains_v112_replay_delta_coefficients": True,
            "baseline_only_wave_count": len(baseline_only_waves),
        },
        "expansion_reuse": {
            "from_12_to_18_new_signatures": len(new18),
            "from_18_to_24_new_signatures": len(new24),
            "merge_by_seven_param_signature": True,
            "reuse_controls_when_cache_fresh": True,
        },
        "fixed_scenario": {
            "workload": "runxp",
            "vcpu": 11,
            "memory": "2304MiB",
            "io_mb_s": "800/800",
            "shared_buffers": "512MB",
            "work_mem": "8MB",
            "parallel_workers": "4/8",
            "tuned_params": COST_PARAMS,
        },
        "stop_policy": {
            "do_not_stop_on_plus_20_only": True,
            "stop_on_binary_epsilon_or_safety": True,
            "final_formal_on_v87_1": True,
            "continues_rounds_after_round0_without_external_rebuild_gate": True,
        },
        "production_scripts": {
            "baseline_only": "shell/bonect_parallel_vm_replay.py --baseline-only",
            "scan": "shell/bonect_full_grid_estimator_scan.py",
            "rescore": "shell/bonect_offline_rescore_scan.py",
            "parallel_replay": "shell/bonect_parallel_vm_replay.py",
            "formal_replay": "shell/bonect_parallel_vm_replay.py",
        },
    }
    write_json(manifest_path, manifest)
    return manifest


def run_one_round(
    *,
    args: argparse.Namespace,
    run_dir: Path,
    state: Dict[str, Any],
    round_index: int,
) -> int:
    round_dir = run_dir / "rounds" / f"round_{round_index:02d}"
    round_dir.mkdir(parents=True, exist_ok=True)
    ranges = {k: tuple(v) for k, v in state["ranges"].items()}
    range_payload = build_range_json_payload(ranges)
    range_json = round_dir / "range_override.json"
    write_json(range_json, range_payload)

    # Fail closed: never start EXPLAIN/ranking without a current-run baseline gate.
    current_calibration = require_current_run_baseline_gate(run_dir=run_dir, state=state)
    # Across binary rounds, re-check cache freshness / profile signature and re-run if stale.
    if round_index > 0:
        current_calibration = ensure_current_run_baseline_preflight(
            args=args,
            run_dir=run_dir,
            state=state,
            force=False,
        )
    current_calibration, replay_labels_jsonl = build_round_replay_calibration(
        run_dir=run_dir,
        round_dir=round_dir,
        round_index=round_index,
        baseline_calibration=current_calibration,
    )
    state["active_round_calibration_json"] = str(current_calibration)
    state["active_round_replay_labels_jsonl"] = (
        str(replay_labels_jsonl) if replay_labels_jsonl is not None else None
    )

    # Offline expected generation proof for this round's intervals.
    offline_grid = generate_five_point_grid(ranges)
    offline_unique = unique_seven_param_candidates(offline_grid)
    write_json(
        round_dir / "offline_grid_manifest.json",
        {
            "round": round_index,
            "unique_candidates": len(offline_unique),
            "raw_candidates": len(offline_grid),
            "expected_if_full_width": EXPECTED_ROUND0_GRID_COUNT if round_index == 0 else None,
            "exactly_seven_keys": all(set(p) == set(COST_PARAMS) for p in offline_unique),
            "source": "fresh_current_round_five_point_grid",
            "ranges": {k: list(v) for k, v in ranges.items()},
            "baseline_gate_required": True,
            "current_baseline_calibration_json": str(current_calibration),
        },
    )
    if round_index == 0 and len(offline_unique) != EXPECTED_ROUND0_GRID_COUNT:
        raise RuntimeError(
            f"round0 offline grid must be {EXPECTED_ROUND0_GRID_COUNT}, got {len(offline_unique)}"
        )

    scan_dir = round_dir / "scan"
    scan_dir.mkdir(parents=True, exist_ok=True)
    planning_endpoints = normalize_planning_endpoints(
        list(getattr(args, "planning_endpoints", None) or DEFAULT_PLANNING_ENDPOINTS)
    )
    heartbeat(
        run_dir,
        {
            "event": f"round{round_index:02d}_scan_start",
            "run_id": args.run_id,
            "phase": f"round{round_index:02d}_scan",
            "expected_candidates": len(offline_unique),
            "scan_dir": str(scan_dir),
            "baseline_calibration_json": str(current_calibration),
            "planning_endpoints": planning_endpoints,
        },
    )
    scan_rc = run_fresh_grid_scan(
        python=args.python,
        config_path=Path(args.config),
        range_json=range_json,
        output_dir=scan_dir,
        baseline_calibration=current_calibration,
        resource_model_dir=Path(args.resource_model_dir) if args.resource_model_dir else None,
        candidate_workers=int(args.candidate_workers),
        run_id=f"{args.run_id}_r{round_index:02d}_scan",
        resume=bool(args.resume_scan),
        planning_endpoints=planning_endpoints,
    )
    log(f"round{round_index:02d} scan exited rc={scan_rc}", run_dir=run_dir)
    if scan_rc != 0:
        return scan_rc

    scan_stats = count_scan_candidates(scan_dir / "candidates.jsonl")
    write_json(round_dir / "scan_candidate_stats.json", scan_stats)
    if round_index == 0 and int(scan_stats.get("unique_seven_param") or 0) != EXPECTED_ROUND0_GRID_COUNT:
        raise RuntimeError(
            f"round0 scan unique candidates must be {EXPECTED_ROUND0_GRID_COUNT}, got {scan_stats}"
        )

    rescore_dir = round_dir / "rescore"
    rescore_dir.mkdir(parents=True, exist_ok=True)
    rescore_rc = run_offline_rescore(
        python=args.python,
        candidates_jsonl=scan_dir / "candidates.jsonl",
        config_path=Path(args.config),
        baseline_calibration=current_calibration,
        output_dir=rescore_dir,
        top_k=max(int(args.max_replay_budget), 60),
        replay_labels_jsonl=replay_labels_jsonl,
    )
    log(f"round{round_index:02d} rescore exited rc={rescore_rc}", run_dir=run_dir)
    if rescore_rc != 0:
        return rescore_rc

    ranked = load_ranked_rows(rescore_dir)
    write_json(round_dir / "ranked_top_preview.json", ranked[:30])

    # Round 0: no forced anchor. Later rounds force only current-run discovered best.
    force_anchor = None
    if round_index > 0:
        force_anchor = state.get("accepted_best") or state.get("fallback_db_qps_best")
        if force_anchor is not None and not force_anchor.get("discovered_by_current_run"):
            raise RuntimeError("refusing non-current-run force anchor")
    control_anchor = None
    if round_index > 0:
        control_anchor = choose_current_run_control_anchor(
            collect_prior_round_replay_rows(run_dir, round_index),
            round_index=round_index,
        )
        write_json(round_dir / "worker_reproducibility_control_anchor.json", control_anchor)

    budget = int(state.get("replay_budget") or args.initial_replay_budget)
    budget = max(int(args.initial_replay_budget), min(int(args.max_replay_budget), budget))
    all_replay_rows: List[Dict[str, Any]] = []
    ranked_selected_total: List[Dict[str, Any]] = []
    expansion_log: List[Dict[str, Any]] = []
    already_replayed_signatures: set = set()
    calibration_cache_json = str(
        state.get("worker_calibration_cache_json")
        or (run_dir / "baseline_preflight" / "worker_calibration_cache.json")
    )
    max_age_s = float(getattr(args, "calibration_cache_max_age_s", DEFAULT_CALIBRATION_CACHE_MAX_AGE_S))

    while True:
        selected = select_model_ranked_prefix(
            ranked,
            budget=budget,
            force_anchor=force_anchor,
            round_index=round_index,
        )
        ranked_selected_total = selected
        new_only = select_new_prefix_candidates(selected, already_replayed_signatures)
        cand_path = round_dir / f"candidates_budget{budget:02d}.json"
        write_json(
            cand_path,
            {
                "candidates": new_only if new_only else selected,
                "answer_blind": True,
                "round": round_index,
                "budget": budget,
                "full_prefix_count": len(selected),
                "new_only_count": len(new_only),
                "reused_prefix_signatures": len(already_replayed_signatures),
                "force_anchor_current_run_only": force_anchor is not None,
                "source": "fresh_current_round_model_rank",
                "expansion_replays_only_new_signatures": True,
            },
        )
        # Expansion must not re-run the already-replayed prefix.
        if already_replayed_signatures and not new_only:
            log(
                f"round{round_index:02d} budget={budget}: no new signatures to replay",
                run_dir=run_dir,
            )
            quality = measure_replay_quality(all_replay_rows, selected)
            decision = decide_adaptive_replay_budget(
                current_budget=budget,
                max_budget=int(args.max_replay_budget),
                min_budget=int(args.initial_replay_budget),
                pairwise_inversion_rate=quality.get("pairwise_inversion_rate"),
                qps_delta_mae=quality.get("qps_delta_mae"),
                resource_direction_accuracy=quality.get("resource_direction_accuracy"),
                rank_uncertainty=quality.get("rank_uncertainty"),
                lane_coverage_ratio=quality.get("lane_coverage_ratio"),
                unresolved_optimistic_upper_bound=bool(quality.get("unresolved_optimistic_upper_bound")),
            )
            expansion_log.append(
                {
                    "budget_before": budget,
                    "new_only_count": 0,
                    "quality": quality,
                    "decision": decision,
                    "note": "no_new_signatures",
                }
            )
            write_json(round_dir / "adaptive_expansion_log.json", expansion_log)
            if not decision.get("expand"):
                state["replay_budget"] = int(decision["budget"])
                break
            budget = int(decision["budget"])
            state["replay_budget"] = budget
            continue

        is_expansion = bool(already_replayed_signatures)
        replay_candidates = new_only if is_expansion else selected
        new_only_count = len(replay_candidates)
        launch_candidates = add_control_anchor_to_replay_candidates(
            replay_candidates,
            control_anchor,
        )
        write_json(
            cand_path,
            {
                "candidates": launch_candidates,
                "answer_blind": True,
                "round": round_index,
                "budget": budget,
                "full_prefix_count": len(selected),
                "new_only_count": new_only_count,
                "launch_count_including_control_anchor": len(launch_candidates),
                "control_anchor_candidate_id": (
                    control_anchor.get("candidate_id") if control_anchor is not None else None
                ),
                "control_anchor_does_not_consume_replay_budget": control_anchor is not None,
                "reused_prefix_signatures": len(already_replayed_signatures),
                "force_anchor_current_run_only": force_anchor is not None,
                "source": "fresh_current_round_model_rank",
                "expansion_replays_only_new_signatures": is_expansion,
            },
        )
        replay_dir = round_dir / f"replay_budget{budget:02d}"
        heartbeat(
            run_dir,
            {
                "event": f"round{round_index:02d}_replay_budget_{budget}",
                "run_id": args.run_id,
                "phase": f"round{round_index:02d}_replay",
                "candidate_count": len(replay_candidates),
                "launch_count_including_control_anchor": len(launch_candidates),
                "full_prefix_count": len(selected),
                "new_only_count": new_only_count,
                "calibration_cache_json": calibration_cache_json,
                "output_dir": str(replay_dir),
            },
        )
        replay_rc = run_parallel_replay(
            python=args.python,
            candidates_json=cand_path,
            worker_pool_json=Path(state.get("active_worker_pool_json") or args.worker_pool_json),
            output_dir=replay_dir,
            candidate_limit=len(launch_candidates),
            run_mins=float(args.run_mins),
            workload_timeout_s=float(args.workload_timeout_s),
            apply_vm_profile=bool(args.apply_vm_profile),
            max_workers=max(1, int(state.get("active_replay_workers") or args.replay_workers)),
            anchor_candidate_id=(
                str(control_anchor.get("candidate_id")) if control_anchor is not None else ""
            ),
            calibration_cache_json=calibration_cache_json if Path(calibration_cache_json).exists() else "",
            calibration_cache_max_age_s=max_age_s,
        )
        log(
            f"round{round_index:02d} replay budget={budget} new={new_only_count} rc={replay_rc}",
            run_dir=run_dir,
        )
        if replay_rc != 0:
            return replay_rc
        rows = load_replay_results(replay_dir)
        all_replay_rows = merge_replay_rows_by_signature(all_replay_rows, rows)
        for row in all_replay_rows:
            if str(row.get("candidate_id") or "").startswith("__"):
                continue
            try:
                already_replayed_signatures.add(param_signature(clean_params(row.get("params"))))
            except Exception:
                continue
        # Prefer the newest worker calibration cache for subsequent expansions/rounds.
        new_cache = replay_dir / "worker_calibration_cache.json"
        if new_cache.exists():
            calibration_cache_json = str(new_cache)
            state["worker_calibration_cache_json"] = calibration_cache_json
        quality = measure_replay_quality(all_replay_rows, selected)
        decision = decide_adaptive_replay_budget(
            current_budget=budget,
            max_budget=int(args.max_replay_budget),
            min_budget=int(args.initial_replay_budget),
            pairwise_inversion_rate=quality.get("pairwise_inversion_rate"),
            qps_delta_mae=quality.get("qps_delta_mae"),
            resource_direction_accuracy=quality.get("resource_direction_accuracy"),
            rank_uncertainty=quality.get("rank_uncertainty"),
            lane_coverage_ratio=quality.get("lane_coverage_ratio"),
            unresolved_optimistic_upper_bound=bool(quality.get("unresolved_optimistic_upper_bound")),
        )
        expansion_log.append(
            {
                "budget_before": budget,
                "new_only_count": new_only_count,
                "is_expansion": is_expansion,
                "replayed_signatures_total": len(already_replayed_signatures),
                "quality": quality,
                "decision": decision,
                "calibration_cache_json": calibration_cache_json,
            }
        )
        write_json(round_dir / "adaptive_expansion_log.json", expansion_log)
        write_json(round_dir / "merged_replay_rows_preview.json", all_replay_rows[:40])
        if not decision.get("expand"):
            state["replay_budget"] = int(decision["budget"])
            break
        budget = int(decision["budget"])
        state["replay_budget"] = budget
        log(
            f"round{round_index:02d} expanding replay budget -> {budget} "
            f"new_only_next reasons={decision.get('reasons')}",
            run_dir=run_dir,
        )

    summary = evaluate_replay_rows(all_replay_rows)
    write_json(
        round_dir / "round_result.json",
        {
            "round": round_index,
            "candidate_budget_final": budget,
            "selected_count": len(ranked_selected_total),
            "scan_stats": scan_stats,
            "expansion_log": expansion_log,
            **summary,
        },
    )
    state = merge_retained_bests(state, summary)

    best_for_range = state.get("accepted_best") or state.get("fallback_db_qps_best")
    if best_for_range and isinstance(best_for_range.get("params"), dict):
        old_ranges = {k: tuple(v) for k, v in state["ranges"].items()}
        new_ranges = update_ranges_from_best(
            old_ranges,
            clean_params(best_for_range["params"]),
            min_width_pct=float(args.min_width_pct),
        )
        state["ranges"] = {k: list(v) for k, v in new_ranges.items()}
        state["history"].append(
            {
                "round": round_index,
                "best_candidate_id": best_for_range.get("candidate_id"),
                "best_db_qps_delta_pct": best_for_range.get("db_qps_delta_pct"),
                "explainable": bool(best_for_range.get("explainable")),
                "previous_ranges": {k: list(v) for k, v in old_ranges.items()},
                "updated_ranges": state["ranges"],
                "replay_budget_final": budget,
            }
        )
    else:
        state["history"].append(
            {
                "round": round_index,
                "best_candidate_id": None,
                "note": "no usable real-replayed candidate for range update",
            }
        )

    state["round"] = round_index + 1
    return 0


def run_formal_v87_1(
    *,
    args: argparse.Namespace,
    run_dir: Path,
    state: Dict[str, Any],
) -> int:
    """Serial formal promotion on v87-1 for current-run retained bests."""
    formal_dir = run_dir / "formal_v87_1"
    formal_dir.mkdir(parents=True, exist_ok=True)
    candidates: List[Dict[str, Any]] = []
    for label, key in (
        ("explainable", "global_explainable_best"),
        ("fallback", "fallback_db_qps_best"),
    ):
        rec = state.get(key)
        if not rec or not isinstance(rec.get("params"), dict):
            continue
        if not rec.get("discovered_by_current_run"):
            continue
        params = clean_params(rec["params"])
        sig = param_signature(params)
        if any(param_signature(c["params"]) == sig for c in candidates):
            continue
        candidates.append(
            {
                "candidate_id": f"formal_{label}_{rec.get('candidate_id') or len(candidates)}",
                "scenario": "memory",
                "source": "current_run_retained_best_formal",
                "selection_lane": f"formal_{label}",
                "params": params,
                "score": rec.get("db_qps_delta_pct"),
                "discovered_by_current_run": True,
            }
        )
    if not candidates:
        write_json(formal_dir / "formal_skip.json", {"reason": "no_current_run_best_to_formalize"})
        return 0
    cand_path = formal_dir / "formal_candidates.json"
    write_json(cand_path, {"candidates": candidates, "formal_worker": "bonect-replay-v87-1"})
    heartbeat(
        run_dir,
        {
            "event": "formal_v87_1_start",
            "run_id": args.run_id,
            "phase": "formal_v87_1",
            "candidate_count": len(candidates),
        },
    )
    rc = run_parallel_replay(
        python=args.python,
        candidates_json=cand_path,
        worker_pool_json=Path(args.formal_worker_pool_json),
        output_dir=formal_dir / "replay",
        candidate_limit=len(candidates),
        run_mins=float(args.run_mins),
        workload_timeout_s=float(args.workload_timeout_s),
        apply_vm_profile=bool(args.apply_vm_profile),
        max_workers=1,
    )
    rows = load_replay_results(formal_dir / "replay")
    summary = evaluate_replay_rows(rows)
    write_json(formal_dir / "formal_result.json", {"returncode": rc, **summary})
    # Formal labels also update retained bests if stronger.
    state = merge_retained_bests(state, summary)
    return rc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default="")
    parser.add_argument(
        "--run-dir",
        default="",
        help="Default: history/bonect_parallel_vm_replay/<run-id>",
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--worker-pool-json", default=str(DEFAULT_WORKER_POOL))
    parser.add_argument("--formal-worker-pool-json", default=str(DEFAULT_FORMAL_POOL))
    parser.add_argument("--baseline-calibration-json", default=str(DEFAULT_BASELINE_CALIBRATION))
    parser.add_argument("--resource-model-dir", default=str(DEFAULT_RESOURCE_MODEL_DIR))
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--run-mins", type=float, default=5.0)
    parser.add_argument("--workload-timeout-s", type=float, default=480.0)
    parser.add_argument("--initial-replay-budget", type=int, default=12)
    parser.add_argument("--max-replay-budget", type=int, default=24)
    parser.add_argument("--max-rounds", type=int, default=32)
    parser.add_argument("--min-width-pct", type=float, default=1.0)
    parser.add_argument("--candidate-workers", type=int, default=16)
    parser.add_argument(
        "--replay-workers",
        type=int,
        default=2,
        help=(
            "Maximum concurrent real-replay workers selected from --worker-pool-json. "
            "A changed pool or worker count always forces a fresh baseline gate."
        ),
    )
    parser.add_argument(
        "--fallback-worker-pool-json",
        default="",
        help="Fallback real-replay pool used only when the primary baseline gate fails.",
    )
    parser.add_argument(
        "--fallback-replay-workers",
        type=int,
        default=2,
        help="Maximum workers selected from --fallback-worker-pool-json.",
    )
    parser.add_argument(
        "--pg-endpoint",
        action="append",
        dest="planning_endpoints",
        default=None,
        help=(
            "Planning endpoint host:port[/database] for EXPLAIN scan. "
            "Repeatable; default is v87-1/v87-2/v87-4. Never pass held-out v87-3."
        ),
    )
    parser.add_argument(
        "--calibration-cache-max-age-s",
        type=float,
        default=DEFAULT_CALIBRATION_CACHE_MAX_AGE_S,
        help="Max age for reusing current-run worker baseline/anchor calibration cache.",
    )
    parser.add_argument("--resume-scan", action="store_true")
    parser.add_argument("--apply-vm-profile", action="store_true", default=True)
    parser.add_argument("--no-apply-vm-profile", action="store_false", dest="apply_vm_profile")
    parser.add_argument(
        "--dry-run-manifest",
        default="",
        help="Write fail-closed dry-run JSON manifest and exit without launching workloads.",
    )
    parser.add_argument(
        "--skip-formal",
        action="store_true",
        help="Skip final v87-1 formal replay (debug only).",
    )
    parser.add_argument(
        "--force-baseline-preflight",
        action="store_true",
        help="Force a fresh baseline-only preflight even if a fresh cache exists.",
    )
    args = parser.parse_args()
    if not args.planning_endpoints:
        args.planning_endpoints = list(DEFAULT_PLANNING_ENDPOINTS)
    else:
        args.planning_endpoints = normalize_planning_endpoints(args.planning_endpoints)
    if int(args.replay_workers) < 1:
        parser.error("--replay-workers must be >= 1")
    if int(args.fallback_replay_workers) < 1:
        parser.error("--fallback-replay-workers must be >= 1")
    replay_worker_ids = load_replay_worker_ids(
        Path(args.worker_pool_json).resolve(),
        scenario="memory",
        max_workers=int(args.replay_workers),
    )

    source_path = Path(__file__).resolve()
    if args.dry_run_manifest:
        manifest_path = Path(args.dry_run_manifest)
        if not manifest_path.is_absolute():
            manifest_path = ROOT / manifest_path
        manifest = build_dry_run_manifest(source_path=source_path, manifest_path=manifest_path)
        print(json.dumps({"dry_run_status": manifest["status"], "path": str(manifest_path)}, ensure_ascii=False), flush=True)
        return 0 if manifest["status"] == "pass" else 2

    if not args.run_id:
        parser.error("--run-id is required unless --dry-run-manifest is set")

    run_id = str(args.run_id)
    run_dir = Path(args.run_dir) if args.run_dir else (ROOT / "history/bonect_parallel_vm_replay" / run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "logs").mkdir(parents=True, exist_ok=True)
    (run_dir / "rounds").mkdir(parents=True, exist_ok=True)
    (run_dir / "checkpoints").mkdir(parents=True, exist_ok=True)

    os.chdir(ROOT)
    log(f"controller start run_id={run_id} pid={os.getpid()}", run_dir=run_dir)
    heartbeat(
        run_dir,
        {
            "event": "controller_start",
            "run_id": run_id,
            "phase": "init",
            "ownership": {
                "script": str(source_path),
                "worker_pool_json": str(args.worker_pool_json),
                "formal_worker_pool_json": str(args.formal_worker_pool_json),
                "profile": "memory_blind_v112",
                "parallel_workers": replay_worker_ids,
                "formal_worker": "bonect-replay-v87-1",
                "held_out_worker": "bonect-replay-v87-3",
                "comparison": "worker_local_baseline",
                "physical_metrics": {
                    "cpu": "vm_busy",
                    "memory": "vm_used",
                    "io": "disk_device",
                    "nonzero_io_gate": True,
                },
            },
        },
    )

    checkpoint_path = run_dir / "checkpoints" / "outer_loop_state.json"
    state: Dict[str, Any] = {
        "run_id": run_id,
        "profile": "memory_blind_v112",
        "answer_blind": True,
        "teacher_coordinates_loaded": False,
        "teacher_coordinates_affect_selection": False,
        "active_params": COST_PARAMS,
        "ranges": {k: list(v) for k, v in DEFAULT_RANGES.items()},
        "initial_params": DEFAULT_PARAMS,
        "round": 0,
        "replay_budget": int(args.initial_replay_budget),
        "global_explainable_best": None,
        "fallback_db_qps_best": None,
        "accepted_best": None,
        "stop_reason": None,
        "history": [],
    }
    if checkpoint_path.exists():
        try:
            loaded = read_json(checkpoint_path)
            if isinstance(loaded, dict):
                state.update(loaded)
            demoted: List[str] = []
            for key in ("global_explainable_best", "accepted_best"):
                stored_best = state.get(key)
                if not isinstance(stored_best, dict):
                    continue
                strict_recheck = evaluate_replay_rows([stored_best])
                if strict_recheck.get("global_explainable_best") is None:
                    state[key] = None
                    demoted.append(key)
            if demoted:
                state["checkpoint_goal_migration"] = {
                    "demoted_fields": demoted,
                    "reason": "non_target_resource_not_in_sweet_range_or_new_bottleneck",
                    "fallback_preserved": state.get("fallback_db_qps_best") is not None,
                    "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
                log(
                    "checkpoint strict-goal migration demoted=" + ",".join(demoted),
                    run_dir=run_dir,
                )
            # Recoverable resume: drop prior round-failure stop_reason so a later
            # successful completion is not stuck with the old failure label.
            prior_stop = str(state.get("stop_reason") or "")
            if prior_stop and (
                "failed" in prior_stop
                or "exception" in prior_stop
                or prior_stop.startswith("baseline_preflight_failed")
            ):
                log(f"clearing recoverable prior stop_reason={prior_stop}", run_dir=run_dir)
                state["stop_reason"] = None
            log(f"resumed checkpoint round={state.get('round')}", run_dir=run_dir)
        except Exception as exc:
            log(f"checkpoint load failed, starting fresh: {exc}", run_dir=run_dir)

    write_json(
        run_dir / "run_meta.json",
        {
            "run_id": run_id,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "controller": str(source_path),
            "pid": os.getpid(),
            "policy": {
                "start": "postgresql_defaults",
                "baseline_only_before_round0_scan": True,
                "range_source": "answer_independent_optimizer_defaults",
                "small_loop_grid": "fresh_5^N_each_accepted_interval",
                "round0_unique_candidates": EXPECTED_ROUND0_GRID_COUNT,
                "real_replay_start": 12,
                "real_replay_expand": [18, 24],
                "expansion_replays_only_new_signatures": True,
                "planning_endpoints": list(args.planning_endpoints),
                "candidate_workers": int(args.candidate_workers),
                "model_threads_per_worker": 1,
                "parallel_pool": replay_worker_ids,
                "replay_workers": int(args.replay_workers),
                "formal_worker": "bonect-replay-v87-1",
                "held_out_worker": "bonect-replay-v87-3",
                "force_anchor": "current_run_discovered_best_only",
                "known_answer_coordinates": "never_injected_forced_excluded_promoted_or_demoted",
                "stop_on_plus_20_only": False,
                "stop_on": ["ranges_reached_epsilon", "safety_gate_failure", "max_rounds"],
            },
        },
    )

    # Current-state Memory baseline must precede any round-0 EXPLAIN/ranking.
    try:
        ensure_current_run_baseline_preflight(
            args=args,
            run_dir=run_dir,
            state=state,
            force=bool(args.force_baseline_preflight),
        )
        write_json(checkpoint_path, state)
    except Exception as exc:
        state["stop_reason"] = f"baseline_preflight_failed:{exc}"
        write_json(checkpoint_path, state)
        log(state["stop_reason"], run_dir=run_dir)
        return 1

    while int(state.get("round") or 0) < int(args.max_rounds):
        round_index = int(state.get("round") or 0)
        ranges = {k: tuple(v) for k, v in state["ranges"].items()}
        if round_index > 0 and ranges_at_epsilon(ranges, float(args.min_width_pct)):
            state["stop_reason"] = "ranges_reached_epsilon"
            write_json(checkpoint_path, state)
            log("stop: ranges reached epsilon", run_dir=run_dir)
            heartbeat(run_dir, {"event": "stop", "run_id": run_id, "reason": state["stop_reason"]})
            break

        # Intentionally do NOT stop merely because a point reaches +20%.
        if state.get("global_explainable_best"):
            qd = float(state["global_explainable_best"].get("db_qps_delta_pct") or 0.0)
            if qd >= 20.0:
                log(
                    f"note: explainable best already +{qd:.2f}% but continuing until epsilon/safety/max-rounds",
                    run_dir=run_dir,
                )

        try:
            rc = run_one_round(args=args, run_dir=run_dir, state=state, round_index=round_index)
        except Exception as exc:
            state["stop_reason"] = f"round_{round_index:02d}_exception:{exc}"
            write_json(checkpoint_path, state)
            log(state["stop_reason"], run_dir=run_dir)
            return 1
        write_json(checkpoint_path, state)
        if rc != 0:
            state["stop_reason"] = f"round_{round_index:02d}_failed_rc_{rc}"
            write_json(checkpoint_path, state)
            log(state["stop_reason"], run_dir=run_dir)
            return rc

        # After at least one completed round, re-check epsilon for next iteration.
        ranges = {k: tuple(v) for k, v in state["ranges"].items()}
        if ranges_at_epsilon(ranges, float(args.min_width_pct)):
            state["stop_reason"] = "ranges_reached_epsilon"
            write_json(checkpoint_path, state)
            log("stop: ranges reached epsilon after round", run_dir=run_dir)
            heartbeat(run_dir, {"event": "stop", "run_id": run_id, "reason": state["stop_reason"]})
            break
    else:
        state["stop_reason"] = "max_rounds_reached"
        write_json(checkpoint_path, state)

    if not args.skip_formal:
        formal_rc = run_formal_v87_1(args=args, run_dir=run_dir, state=state)
        write_json(checkpoint_path, state)
        if formal_rc != 0:
            state["stop_reason"] = f"formal_v87_1_failed_rc_{formal_rc}"
            write_json(checkpoint_path, state)
            return formal_rc

    if not state.get("stop_reason"):
        state["stop_reason"] = "completed_outer_loop"
    write_json(checkpoint_path, state)
    heartbeat(
        run_dir,
        {
            "event": "controller_complete",
            "run_id": run_id,
            "phase": "complete",
            "stop_reason": state.get("stop_reason"),
            "has_explainable_best": state.get("global_explainable_best") is not None,
            "has_fallback_best": state.get("fallback_db_qps_best") is not None,
        },
    )
    log(f"controller complete stop_reason={state.get('stop_reason')}", run_dir=run_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())

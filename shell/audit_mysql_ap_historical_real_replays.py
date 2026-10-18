#!/usr/bin/env python3
"""Audit AP historical MySQL replays and emit a fail-closed T candidate."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history"
V5 = ROOT / "config/formal/frozen_mysql_workloads_and_parameters_20260723_v5_estimator_t"
REQUEST = V5 / "ap.collection_request.json"
V4_ENVELOPE = ROOT / "config/formal/frozen_mysql_deployment_schedule_20260723_v4_mysql_only/evidence/hybench_ap_memory.mysql_only_envelope_readback.json"
CUTOFF = datetime.fromisoformat("2026-07-20T16:00:00+00:00")
PARAMS = [
    "io_block_read_cost", "memory_block_read_cost", "row_evaluate_cost",
    "key_compare_cost", "disk_temptable_create_cost", "disk_temptable_row_cost",
    "memory_temptable_create_cost", "memory_temptable_row_cost",
]
RESOURCES = ("cpu", "memory", "io")


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict): raise ValueError(path)
    return value


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def after_cutoff(value: dict[str, Any]) -> bool:
    raw = value.get("completed_at_utc") or value.get("started_at_utc")
    if not isinstance(raw, str): return False
    try: return datetime.fromisoformat(raw.replace("Z", "+00:00")) >= CUTOFF
    except ValueError: return False


def resource_stable(value: dict[str, Any]) -> tuple[bool, str]:
    stability = value.get("stability") or {}
    windows = ((stability.get("resource_and_throughput") or {}).get("resource_windows") or [])
    if windows:
        last = windows[-6:]
        passed = len(last) == 6 and all(
            all((row.get(resource) or {}).get("passed") is True for resource in RESOURCES)
            for row in last
        )
        return passed, "last_six_30s_resource_windows"
    windows = stability.get("windows") or []
    for end in range(6, len(windows) + 1):
        recent = windows[end - 6:end]
        if all(
            all(((row.get("metrics") or {}).get(resource) or {}).get("stable") is True for resource in RESOURCES)
            for row in recent
        ):
            return True, f"rolling_six_resource_windows_ending_{end}"
    return False, "no_six_consecutive_resource_stable_windows"


def envelope(value: dict[str, Any]) -> dict[str, Any]:
    row = value.get("resource_readback") or {}
    host = row.get("docker_host_config") or {}
    return {
        "cpu_quota_cores": row.get("cpu_quota_cores"),
        "memory_bytes": row.get("memory_limit_bytes"),
        "memory_swap_bytes": row.get("memory_swap_limit_bytes"),
        "cpuset_mems": str(row.get("cpuset_mems")),
        "io_read_bps": next((x.get("Rate") for x in host.get("BlkioDeviceReadBps", []) if isinstance(x, dict)), None),
        "io_write_bps": next((x.get("Rate") for x in host.get("BlkioDeviceWriteBps", []) if isinstance(x, dict)), None),
        "pids_max": host.get("PidsLimit"),
    }


def dbqps_from_mysql_status(directory: Path) -> dict[str, Any] | None:
    path = directory / "database_samples.jsonl"
    if not path.is_file(): return None
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try: row = json.loads(line)
        except json.JSONDecodeError: continue
        if row.get("phase") == "measurement" and isinstance((row.get("status_counters") or {}).get("Com_select"), (int, float)):
            rows.append(row)
    if len(rows) < 2: return None
    seconds = float(rows[-1]["elapsed_s"]) - float(rows[0]["elapsed_s"])
    delta = float(rows[-1]["status_counters"]["Com_select"]) - float(rows[0]["status_counters"]["Com_select"])
    if seconds <= 0 or delta < 0: return None
    return {"value": delta / seconds, "counter": "Com_select", "source": "SHOW GLOBAL STATUS", "sample_count": len(rows), "seconds": seconds, "artifact": str(path.relative_to(ROOT)), "sha256": digest(path)}


def exact_envelope(observed: dict[str, Any], target: dict[str, Any]) -> bool:
    return all(observed.get(key) == target.get(key) for key in (
        "cpu_quota_cores", "memory_bytes", "memory_swap_bytes",
        "io_read_bps", "io_write_bps", "pids_max",
    )) and observed.get("cpuset_mems") == "1"


def atomic_json(path: Path, value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle: handle.write(payload); handle.flush(); os.fsync(handle.fileno())
        os.replace(raw, path)
    finally:
        if os.path.exists(raw): os.unlink(raw)
    result = hashlib.sha256(payload).hexdigest()
    sidecar = path.with_name(path.name + ".sha256")
    sidecar.write_text(f"{result}  {path.name}\n", encoding="ascii")
    return result


def main() -> int:
    request = load(REQUEST)
    ranges = request["binding"]["parameter_space"]["ranges"]
    anchor = request["anchor"]
    target = load(V4_ENVELOPE)["normalized_envelope"]
    candidates = []
    paths = list(HISTORY.rglob("point_result.json")) + list(HISTORY.rglob("measurement_summary.json")) + list(HISTORY.rglob("live_measurement.json"))
    for path in sorted(paths):
        try: value = load(path)
        except Exception: continue
        engine = str(value.get("engine") or ("mysql" if "/mysql/" in str(path) or "mysql_experiments" in str(path) else "")).lower()
        workload = str(value.get("workload") or value.get("case_id") or value.get("benchmark") or "").lower()
        scenario = str(value.get("scenario") or value.get("case_id") or "").lower()
        if engine != "mysql" or not ("hybench_ap" in workload or "/random_walk/ap/" in str(path).lower()): continue
        if not ("memory" in scenario or "hybench_ap_memory" in str(path).lower() or "/random_walk/ap/" in str(path).lower()): continue
        params = value.get("final_cost_readback") or value.get("params_applied") or (value.get("cost_apply") or {}).get("values")
        means = value.get("resource_means") or {
            key.removesuffix("_utilization"): val for key, val in (value.get("live_point") or {}).items()
            if key.endswith("_utilization")
        }
        valid = value.get("measurement_valid") is True or (
            value.get("cleanup_complete") is True and (value.get("correctness") or {}).get("status") == "passed"
        )
        stable, stable_basis = resource_stable(value)
        observed_envelope = envelope(value)
        final_equal = isinstance(params, dict) and all(name in params for name in PARAMS) and (
            value.get("final_cost_readback") is None or value.get("final_cost_readback") == value.get("params_applied")
        )
        dbqps = dbqps_from_mysql_status(path.parent)
        row = {
            "artifact": str(path.relative_to(ROOT)), "sha256": digest(path),
            "after_cutoff": after_cutoff(value), "measurement_valid": valid,
            "cost_readback_complete": final_equal, "resource_values_complete": all(name in means for name in RESOURCES),
            "dbqps": dbqps, "resource_stable": stable, "resource_stability_basis": stable_basis,
            "resource_envelope": observed_envelope, "target_envelope_exact": exact_envelope(observed_envelope, target),
            "params": params if final_equal else None,
            "resources": {name: means[name] for name in RESOURCES} if all(name in means for name in RESOURCES) else None,
        }
        row["formal_eligible"] = all((row["after_cutoff"], valid, final_equal, row["resource_values_complete"], dbqps is not None, stable, row["target_envelope_exact"]))
        candidates.append(row)

    cohort = [row for row in candidates if "brute_formal_mixed_20260722_v6_minimal_gate_retry3/mysql/brute_force/A5_r1/round_" in row["artifact"] and row["measurement_valid"] and row["cost_readback_complete"] and row["resource_values_complete"] and row["dbqps"]]
    # Keep one valid attempt for each logical round.
    by_round: dict[str, dict[str, Any]] = {}
    for row in cohort:
        logical = next(part for part in Path(row["artifact"]).parts if part.startswith("round_"))
        by_round.setdefault(logical, row)
    cohort = [by_round[key] for key in sorted(by_round)]
    X = np.array([[1.0] + [math.log(float(row["params"][name])) for name in PARAMS] for row in cohort])
    rank = int(np.linalg.matrix_rank(X)) if len(X) else 0
    holdout: tuple[int, ...] = ()
    best_condition = math.inf
    for pair in itertools.combinations(range(len(cohort)), 2):
        train = [i for i in range(len(cohort)) if i not in pair]
        if len(train) >= 9 and np.linalg.matrix_rank(X[train]) == 9:
            condition = float(np.linalg.cond(X[train]))
            if condition < best_condition: holdout, best_condition = pair, condition
    regression: dict[str, Any] = {"design_rows": len(cohort), "design_rank": rank, "parameter_count_plus_intercept": 9, "usable_for_formal_t": False}
    if holdout:
        train = [i for i in range(len(cohort)) if i not in holdout]
        regression.update({"train_indices": train, "heldout_indices": list(holdout), "train_condition_number": best_condition, "resources": {}})
        for resource in RESOURCES:
            y = np.array([float(row["resources"][resource]) for row in cohort])
            beta = np.linalg.lstsq(X[train], y[train], rcond=None)[0]
            pred = X[list(holdout)] @ beta
            residuals = [float(y[i] - pred[j]) for j, i in enumerate(holdout)]
            regression["resources"][resource] = {
                "intercept": float(beta[0]), "log_parameter_coefficients": {name: float(beta[i + 1]) for i, name in enumerate(PARAMS)},
                "effect_across_current_range": {name: float(beta[i + 1] * math.log(ranges[name]["upper"] / ranges[name]["lower"])) for i, name in enumerate(PARAMS)},
                "heldout_residuals": residuals,
                "error_lower": max([0.0] + [-value for value in residuals]),
                "error_upper": max([0.0] + residuals),
                "abs_error_max": max(abs(value) for value in residuals),
            }
    coverage = {}
    for name in PARAMS:
        values = [float(row["params"][name]) for row in cohort]
        coverage[name] = {
            "historical_directional_lower_count": sum(value < anchor[name] for value in values),
            "historical_directional_upper_count": sum(value > anchor[name] for value in values),
            "exact_current_lower_count": sum(value == ranges[name]["lower"] for value in values),
            "exact_current_upper_count": sum(value == ranges[name]["upper"] for value in values),
            "formal_exact_envelope_stable_lower_count": 0,
            "formal_exact_envelope_stable_upper_count": 0,
        }
    supplemental_sources = [item for item in cohort if "round_000" not in item["artifact"]]
    observed_bounds = {
        name: (
            min(float(row["params"][name]) for row in supplemental_sources),
            max(float(row["params"][name]) for row in supplemental_sources),
        ) for name in PARAMS
    }
    supplemental = []
    for row in supplemental_sources:
        mapped = {}
        for name in PARAMS:
            value = float(row["params"][name])
            old_lower, old_upper = observed_bounds[name]
            position = min(1.0, max(0.0, math.log(value / old_lower) / math.log(old_upper / old_lower)))
            if value == old_lower: mapped[name] = float(ranges[name]["lower"])
            elif value == old_upper: mapped[name] = float(ranges[name]["upper"])
            else: mapped[name] = math.exp(math.log(ranges[name]["lower"]) + position * math.log(ranges[name]["upper"] / ranges[name]["lower"]))
        supplemental.append({"source_design": row["artifact"], "params": mapped})
    supplemental_X = np.array([[1.0] + [math.log(row["params"][name]) for name in PARAMS] for row in supplemental])
    result = {
        "schema_version": "mysql_ap_historical_real_replay_t_audit_v1",
        "status": "INSUFFICIENT_FOR_FORMAL_T_MINIMAL_SUPPLEMENT_REQUIRED",
        "cutoff": "2026-07-21T00:00:00+08:00",
        "database": "mysql", "scenario": "hybench_ap_memory",
        "target_envelope": target,
        "source_counts": {"candidate_artifacts": len(candidates), "post_cutoff": sum(row["after_cutoff"] for row in candidates), "measurement_valid": sum(row["measurement_valid"] for row in candidates), "formal_eligible": sum(row["formal_eligible"] for row in candidates)},
        "rejection_summary": {
            "target_envelope_exact": sum(row["target_envelope_exact"] for row in candidates),
            "resource_stable": sum(row["resource_stable"] for row in candidates),
            "dbqps_from_mysql_status": sum(row["dbqps"] is not None for row in candidates),
        },
        "a5_joint_candidate": {
            "point_count": len(cohort), "all_mysql_real_replay": True,
            "dbqps_source": "SHOW GLOBAL STATUS Com_select delta only; client QPS excluded",
            "legacy_envelope": cohort[0]["resource_envelope"] if cohort else None,
            "target_envelope_mismatch": True,
            "resource_stable_point_count": sum(row["resource_stable"] for row in cohort),
            "regression": regression,
            "points": cohort,
        },
        "parameter_side_coverage": coverage,
        "strict_explain_model_fallback": {
            "invoked_for_formal_t": False,
            "reason": "A5 candidate points already contain CPU/Memory/IO values; their blockers are resource instability and target-envelope mismatch, which EXPLAIN/model inference cannot repair",
            "cluster_count_alone_used": False,
            "modeled_values_may_not_replace_missing_stability_or_envelope_readback": True,
        },
        "minimum_supplement": {
            "initial_point_count": len(supplemental),
            "design_rank": int(np.linalg.matrix_rank(supplemental_X)),
            "required_rank": 9,
            "heldout_residual_degree_of_freedom": max(0, len(supplemental) - 9),
            "points": supplemental,
            "measurement_rule": "each point first run uses rolling latest six 30s resource-only windows at 180s then +30s to 600s; if first run unstable require 3 valid independent runs",
            "maximum_valid_runs_if_every_first_run_unstable": len(supplemental) * 3,
        },
        "pruning_qualification": {"joint_coverage_guarantee": False, "fail_open_keep_real_replay": True, "qps_model_constructed": False},
        "t_created": False, "r0_started": False, "sgd_started": False,
        "source_artifacts": candidates,
        "authority": {"request": {"path": str(REQUEST.relative_to(ROOT)), "sha256": digest(REQUEST)}, "v4_envelope": {"path": str(V4_ENVELOPE.relative_to(ROOT)), "sha256": digest(V4_ENVELOPE)}},
    }
    output = HISTORY / "mysql_sgd_env_agents_20260723/ap/ap_historical_real_replay_t_audit_20260723_0805.json"
    sha = atomic_json(output, result)
    print(json.dumps({"status": result["status"], "output": str(output), "sha256": sha, "formal_eligible": result["source_counts"]["formal_eligible"], "supplement_points": len(supplemental)}, sort_keys=True))
    return 0


if __name__ == "__main__": raise SystemExit(main())

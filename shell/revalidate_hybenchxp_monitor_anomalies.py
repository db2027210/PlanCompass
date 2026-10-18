#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import math
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from binary_plan_resource_tuner import (  # noqa: E402
    append_jsonl,
    baseline_map_from_validations,
    compare_actual,
    cpuset_cpu_count,
    load_config,
    load_jsonl,
    run_real_validation_safe,
    safe_float,
    validation_worker_identity,
    validation_workers,
    worker_identity,
)


def monitor_anomaly(validation: Dict[str, Any], tolerance: float) -> Tuple[bool, List[str]]:
    reasons: List[str] = []
    stats = validation.get("resource_stats") if isinstance(validation.get("resource_stats"), dict) else {}
    worker = validation.get("worker") if isinstance(validation.get("worker"), dict) else {}
    samples = stats.get("stable_sample_count") if isinstance(stats.get("stable_sample_count"), dict) else {}
    if not samples:
        samples = validation.get("resource_samples") if isinstance(validation.get("resource_samples"), dict) else {}
    required = int(stats.get("min_stable_samples") or 0)
    counts = [int(value) for value in samples.values() if isinstance(value, (int, float))]
    minimum = min(counts) if counts else 0

    if validation.get("status") != "ok":
        reasons.append("status_not_ok")
    if stats.get("resource_monitor_ok") is not True:
        reasons.append("resource_monitor_not_ok")
    if stats.get("resource_scope") != "docker_container":
        reasons.append("resource_scope_invalid")
    if required <= 0 or minimum < required:
        reasons.append("stable_samples_insufficient")
    if not str(stats.get("memory_source") or "").startswith("docker_cgroup"):
        reasons.append("memory_source_invalid")
    if not str(stats.get("io_source") or "").startswith("docker_cgroup"):
        reasons.append("io_source_invalid")
    if str(stats.get("container_name") or "") != str(worker.get("container") or ""):
        reasons.append("worker_container_mismatch")

    cpus = cpuset_cpu_count(worker.get("cpuset_cpus"))
    cpu_stats = stats.get("cpu") if isinstance(stats.get("cpu"), dict) else {}
    cpu_avg = safe_float(cpu_stats.get("avg"), math.nan)
    cpu_max = safe_float(cpu_stats.get("max"), math.nan)
    if not math.isfinite(cpu_avg) or cpu_avg <= 0.0:
        reasons.append("cpu_invalid")
    if cpus > 0 and (cpu_avg > cpus * tolerance or cpu_max > cpus * tolerance):
        reasons.append("cpu_over_cpuset_limit")
    if int(stats.get("cpu_outlier_count") or 0) > 0:
        reasons.append("cpu_outliers_excluded")

    resources = validation.get("resources") if isinstance(validation.get("resources"), dict) else {}
    if safe_float(resources.get("memory"), 0.0) <= 0.0:
        reasons.append("memory_nonpositive")
    if safe_float(resources.get("io"), -1.0) < 0.0:
        reasons.append("io_negative")
    if safe_float(validation.get("db_qps"), 0.0) <= 0.0:
        reasons.append("db_qps_nonpositive")
    return bool(reasons), sorted(set(reasons))


def corrected_labels(path: Path) -> set:
    labels = set()
    for row in load_jsonl(path):
        if row.get("event") != "candidate_monitor_revalidation":
            continue
        if row.get("status") == "ok" and row.get("monitor_clean") is True:
            labels.add(str(row.get("original_label") or ""))
    return labels


def baseline_for_worker(
    validation: Dict[str, Any],
    primary: Dict[str, Any],
    by_worker: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    key = validation_worker_identity(validation)
    return by_worker.get(key) or primary


def worker_revalidations(
    cfg: Dict[str, Any],
    run_dir: Path,
    target: str,
    worker: Dict[str, Any],
    items: Sequence[Tuple[Dict[str, Any], List[str]]],
    primary_baseline: Dict[str, Any],
    baselines_by_worker: Dict[str, Dict[str, Any]],
    tolerance: float,
    max_attempts: int,
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for original, anomaly_reasons in items:
        original_label = str(original.get("label") or "candidate")
        final: Dict[str, Any] = {}
        for attempt in range(1, max_attempts + 1):
            label = f"{original_label}_monitor_recheck_a{attempt}"
            validation = run_real_validation_safe(
                cfg,
                copy.deepcopy(original.get("params") or {}),
                output_dir=run_dir,
                label=label,
                worker=worker,
            )
            dirty, correction_reasons = monitor_anomaly(validation, tolerance)
            baseline = baseline_for_worker(original, primary_baseline, baselines_by_worker)
            accepted, decision_reason, decision = compare_actual(
                validation,
                baseline,
                baseline,
                target=target,
                cfg=cfg,
            )
            final = copy.deepcopy(validation)
            final.update({
                "event": "candidate_monitor_revalidation",
                "target": target,
                "original_label": original_label,
                "original_round": original.get("round"),
                "original_candidate_index": original.get("candidate_index"),
                "original_accept_reject_reason": original.get("accept_reject_reason"),
                "original_monitor_anomalies": anomaly_reasons,
                "monitor_revalidation_attempt": attempt,
                "monitor_clean": not dirty,
                "monitor_anomalies": correction_reasons,
                "accepted_against_same_worker_baseline": accepted,
                "accept_reject_reason_against_same_worker_baseline": decision_reason,
                "decision_against_same_worker_baseline": decision,
                "comparison_baseline_key": validation_worker_identity(original),
                "comparison_baseline_label": baseline.get("label"),
            })
            results.append(final)
            if not dirty:
                break
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--target", required=True, choices=("cpu", "memory", "none", "io"))
    parser.add_argument("--cpu-tolerance", type=float, default=1.15)
    parser.add_argument("--max-attempts", type=int, default=2)
    args = parser.parse_args()

    cfg = load_config(args.config)
    run_dir = Path(args.run_dir).resolve()
    suffix = "hybenchxp"
    stage_log = run_dir / f"{args.target}_binary_search_{suffix}.jsonl"
    correction_log = run_dir / f"{args.target}_resource_monitor_revalidation_{suffix}.jsonl"
    rows = load_jsonl(stage_log)
    candidates = [row for row in rows if row.get("event") == "candidate_validation"]
    baselines = [row for row in rows if row.get("event") == "baseline_worker_validation"]
    primary_rows = [row for row in rows if row.get("event") == "baseline_validation"]
    if not primary_rows:
        raise RuntimeError(f"baseline_validation missing from {stage_log}")
    primary = primary_rows[-1]
    by_worker = baseline_map_from_validations(baselines)
    completed = corrected_labels(correction_log)

    anomalies: List[Tuple[Dict[str, Any], List[str]]] = []
    for candidate in candidates:
        dirty, reasons = monitor_anomaly(candidate, max(1.0, args.cpu_tolerance))
        label = str(candidate.get("label") or "")
        if dirty and label not in completed:
            anomalies.append((candidate, reasons))

    append_jsonl(correction_log, {
        "event": "monitor_revalidation_start",
        "workload": "hybenchxp",
        "target": args.target,
        "source_candidate_count": len(candidates),
        "pending_anomaly_count": len(anomalies),
        "already_corrected_count": len(completed),
        "cpu_tolerance": max(1.0, args.cpu_tolerance),
        "max_attempts": max(1, args.max_attempts),
    })
    if not anomalies:
        append_jsonl(correction_log, {
            "event": "monitor_revalidation_summary",
            "workload": "hybenchxp",
            "target": args.target,
            "source_candidate_count": len(candidates),
            "pending_anomaly_count": 0,
            "validation_count": 0,
            "clean_count": 0,
            "failed_count": 0,
        })
        print(f"[{args.target}] no pending monitor anomalies", flush=True)
        return 0

    configured_workers = validation_workers(cfg, args.target)
    workers_by_key = {worker_identity(worker): worker for worker in configured_workers}
    grouped: Dict[str, List[Tuple[Dict[str, Any], List[str]]]] = defaultdict(list)
    for item in anomalies:
        key = validation_worker_identity(item[0])
        if key not in workers_by_key:
            append_jsonl(correction_log, {
                "event": "candidate_monitor_revalidation",
                "workload": "hybenchxp",
                "target": args.target,
                "original_label": item[0].get("label"),
                "original_round": item[0].get("round"),
                "original_candidate_index": item[0].get("candidate_index"),
                "status": "failed",
                "monitor_clean": False,
                "monitor_anomalies": ["worker_not_configured"],
            })
            continue
        grouped[key].append(item)

    all_results: List[Dict[str, Any]] = []
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max(1, len(grouped))) as pool:
        futures = [
            pool.submit(
                worker_revalidations,
                cfg,
                run_dir,
                args.target,
                workers_by_key[key],
                items,
                primary,
                by_worker,
                max(1.0, args.cpu_tolerance),
                max(1, args.max_attempts),
            )
            for key, items in grouped.items()
        ]
        for future in as_completed(futures):
            for result in future.result():
                append_jsonl(correction_log, result)
                append_jsonl(stage_log, {
                    "event": "candidate_monitor_revalidation_reference",
                    "workload": "hybenchxp",
                    "target": args.target,
                    "original_label": result.get("original_label"),
                    "original_round": result.get("original_round"),
                    "original_candidate_index": result.get("original_candidate_index"),
                    "revalidation_label": result.get("label"),
                    "revalidation_log": str(correction_log),
                    "status": result.get("status"),
                    "monitor_clean": result.get("monitor_clean"),
                    "monitor_anomalies": result.get("monitor_anomalies"),
                })
                all_results.append(result)

    latest_by_original: Dict[str, Dict[str, Any]] = {}
    for result in all_results:
        latest_by_original[str(result.get("original_label") or "")] = result
    clean_count = sum(
        row.get("status") == "ok" and row.get("monitor_clean") is True
        for row in latest_by_original.values()
    )
    failed_count = len(latest_by_original) - clean_count
    append_jsonl(correction_log, {
        "event": "monitor_revalidation_summary",
        "workload": "hybenchxp",
        "target": args.target,
        "source_candidate_count": len(candidates),
        "pending_anomaly_count": len(anomalies),
        "validation_count": len(all_results),
        "unique_candidate_count": len(latest_by_original),
        "clean_count": clean_count,
        "failed_count": failed_count,
        "parallelism": len(grouped),
        "elapsed_s": time.perf_counter() - started,
    })
    print(
        f"[{args.target}] monitor revalidation candidates={len(latest_by_original)} "
        f"clean={clean_count} failed={failed_count} attempts={len(all_results)}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

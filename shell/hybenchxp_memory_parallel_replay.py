#!/usr/bin/env python3
"""Parallel per-round HyBenchXP memory replay helper."""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RUN_DIR = Path(
    "/disk2/resource_adaptive/binary_plan_tuning_hybenchxp/runs/"
    "hyxp_docker_full_bruteforce_warmup_20260704_014438"
)
DEFAULT_CONFIG = ROOT / "config" / "binary_plan_tuning_hybenchxp.json"

COST_PARAMS = [
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
]

DEFAULT_PG_COST_PARAMS: Dict[str, float] = {
    "seq_page_cost": 1.0,
    "random_page_cost": 4.0,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
}

JSONL_LOCK = threading.Lock()


def load_tuner_module():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import binary_plan_resource_tuner as tuner  # noqa: WPS433

    return tuner


def median(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(float(v) for v in values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def select_workers(cfg: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
    workers_cfg = (((cfg.get("binary_plan_tuning") or {}).get("validation") or {}).get("docker_workers") or {})
    workers = []
    for item in workers_cfg.get("workers") or []:
        if isinstance(item, dict) and str(item.get("name", "")).startswith("cpu_mem_w") and item.get("container"):
            workers.append(dict(item))
    if limit > 0:
        workers = workers[:limit]
    return workers


def result_summary(result: Dict[str, Any]) -> Dict[str, Any]:
    resource_stats = result.get("resource_stats") if isinstance(result.get("resource_stats"), dict) else {}
    resource_metric = result.get("resource_metric") if isinstance(result.get("resource_metric"), dict) else {}
    memory_metric = resource_metric.get("memory") if isinstance(resource_metric.get("memory"), dict) else {}
    memory_mode = (
        memory_metric.get("mode_effective")
        or resource_stats.get("memory_mode_effective")
        or resource_stats.get("memory_source")
    )
    worker = result.get("worker") if isinstance(result.get("worker"), dict) else {}
    resources = result.get("resources") if isinstance(result.get("resources"), dict) else {}
    return {
        "label": result.get("label"),
        "status": result.get("status"),
        "error": result.get("error"),
        "db_qps": result.get("db_qps"),
        "effective_params": result.get("effective_params"),
        "p95_ms": result.get("p95_ms"),
        "p99_ms": result.get("p99_ms"),
        "worker": worker.get("name"),
        "container": worker.get("container"),
        "elapsed_s": result.get("elapsed_s"),
        "params": result.get("params"),
        "memory_mode": memory_mode,
        "memory_mode_requested": resource_stats.get("memory_mode_requested"),
        "memory_mode_effective": resource_stats.get("memory_mode_effective"),
        "memory_source": resource_stats.get("memory_source"),
        "memory_used_gib": resources.get("memory"),
        "resource_samples": result.get("resource_samples"),
        "hybench_xp_qps": ((result.get("hybench") or {}).get("xp_qps") if isinstance(result.get("hybench"), dict) else None),
        "hybench_xp_tps": ((result.get("hybench") or {}).get("xp_tps") if isinstance(result.get("hybench"), dict) else None),
        "hybench_run_config": (((result.get("hybench") or {}).get("run_config")) if isinstance(result.get("hybench"), dict) else None),
        "stdout_path": result.get("stdout_path"),
    }


def append_jsonl(path: Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with JSONL_LOCK:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def candidate_params(candidate: Dict[str, Any]) -> Dict[str, float]:
    raw = candidate.get("params") if isinstance(candidate.get("params"), dict) else {}
    params = dict(DEFAULT_PG_COST_PARAMS)
    for key in COST_PARAMS:
        if key in raw:
            params[key] = float(raw[key])
    return params


def run_validation(
    tuner,
    cfg: Dict[str, Any],
    run_dir: Path,
    jsonl_path: Path,
    kind: str,
    label: str,
    params: Dict[str, float],
    worker: Dict[str, Any],
    extra: Dict[str, Any],
    dry_run: bool,
    append_row: bool = True,
) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "kind": kind,
        "label": label,
        "worker": worker.get("name"),
        "container": worker.get("container"),
        "params": dict(params),
        "ts": extra.get("ts"),
    }
    row.update(extra)
    if dry_run:
        row["dry_run"] = True
        if append_row:
            append_jsonl(jsonl_path, row)
        return row
    result = tuner.run_real_validation_safe(cfg, params, output_dir=run_dir, label=label, worker=worker)
    row.update(result_summary(result))
    if append_row:
        append_jsonl(jsonl_path, row)
    return row


def run_baselines(
    tuner,
    cfg: Dict[str, Any],
    run_dir: Path,
    jsonl_path: Path,
    workers: List[Dict[str, Any]],
    repeat: int,
    ts: str,
    dry_run: bool,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    base_params = dict(DEFAULT_PG_COST_PARAMS) if dry_run else tuner.initial_params(cfg)
    for rep in range(1, repeat + 1):
        print(f"[parallel-replay] baseline rep={rep}/{repeat} workers={len(workers)}", flush=True)
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(workers)) as executor:
            futures = []
            for worker in workers:
                label = f"mpr_baseline_{ts}_{worker.get('name')}_rep{rep}"
                futures.append(
                    executor.submit(
                        run_validation,
                        tuner,
                        cfg,
                        run_dir,
                        jsonl_path,
                        "baseline_worker",
                        label,
                        base_params,
                        worker,
                        {"rep": rep, "ts": ts},
                        dry_run,
                    )
                )
            for future in concurrent.futures.as_completed(futures):
                row = future.result()
                rows.append(row)
                print(
                    "[parallel-replay] baseline done "
                    f"worker={row.get('worker')} rep={row.get('rep')} "
                    f"status={row.get('status')} db_qps={row.get('db_qps')} mem={row.get('memory_used_gib')}",
                    flush=True,
                )
    return rows


def baseline_medians(rows: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Optional[float]]]:
    grouped: Dict[str, Dict[str, List[float]]] = {}
    for row in rows:
        if row.get("status") != "ok":
            continue
        worker = str(row.get("worker") or "")
        grouped.setdefault(worker, {"db_qps": [], "memory_used_gib": []})
        if row.get("db_qps") is not None:
            grouped[worker]["db_qps"].append(float(row["db_qps"]))
        if row.get("memory_used_gib") is not None:
            grouped[worker]["memory_used_gib"].append(float(row["memory_used_gib"]))
    return {
        worker: {
            "db_qps": median(values["db_qps"]),
            "memory_used_gib": median(values["memory_used_gib"]),
        }
        for worker, values in grouped.items()
    }


def has_offline_memory_drop(candidate: Dict[str, Any]) -> bool:
    value = candidate.get("working_set_delta_pct")
    if value is None:
        value = candidate.get("working_set_delta_pct_real")
    if value is None:
        return False
    try:
        return float(value) < 0.0
    except (TypeError, ValueError):
        return False


def ordered_candidates(
    plan: Dict[str, Any],
    round_start: int,
    round_end: int,
    topk_per_round: int,
    allow_positive_offline_memory: bool,
) -> Dict[int, List[Dict[str, Any]]]:
    candidates = plan.get("candidates") or plan.get("top16_replay_candidates") or []
    by_round: Dict[int, List[Dict[str, Any]]] = {}
    for candidate in candidates:
        try:
            round_no = int(candidate.get("round"))
        except (TypeError, ValueError):
            continue
        if round_start <= round_no <= round_end:
            if not allow_positive_offline_memory and not has_offline_memory_drop(candidate):
                continue
            by_round.setdefault(round_no, []).append(dict(candidate))
    for round_no, items in by_round.items():
        items.sort(key=lambda item: (int(item.get("round_rank") or 10**9), str(item.get("label") or "")))
        by_round[round_no] = items[:topk_per_round]
    return by_round


def run_candidates(
    tuner,
    cfg: Dict[str, Any],
    run_dir: Path,
    jsonl_path: Path,
    workers: List[Dict[str, Any]],
    candidates_by_round: Dict[int, List[Dict[str, Any]]],
    baselines: Dict[str, Dict[str, Optional[float]]],
    ts: str,
    dry_run: bool,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for round_no in sorted(candidates_by_round):
        candidates = candidates_by_round[round_no]
        print(f"[parallel-replay] round={round_no} candidates={len(candidates)}", flush=True)
        for batch_start in range(0, len(candidates), len(workers)):
            batch = candidates[batch_start: batch_start + len(workers)]
            batch_no = batch_start // len(workers) + 1
            print(
                f"[parallel-replay] round={round_no} batch={batch_no} size={len(batch)}",
                flush=True,
            )
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(batch)) as executor:
                futures = []
                for slot, candidate in enumerate(batch):
                    worker = workers[slot]
                    worker_name = str(worker.get("name") or "")
                    base = baselines.get(worker_name, {})
                    orig_label = str(candidate.get("label") or f"r{round_no:02d}_rank{candidate.get('round_rank')}")
                    label = f"mpr_candidate_{ts}_r{round_no:02d}_b{batch_no:02d}_s{slot + 1:02d}_{orig_label}"
                    extra = {
                        "ts": ts,
                        "round": round_no,
                        "round_rank": candidate.get("round_rank"),
                        "batch": batch_no,
                        "slot": slot + 1,
                        "orig_label": orig_label,
                        "orig_worker": candidate.get("worker_name"),
                        "assigned_worker": worker_name,
                        "offline_db_qps": candidate.get("offline_db_qps") or candidate.get("db_qps"),
                        "offline_baseline_db_qps": candidate.get("baseline_db_qps"),
                        "offline_working_set_delta_pct": candidate.get("working_set_delta_pct"),
                        "offline_acceptable": candidate.get("offline_acceptable"),
                        "baseline_db_qps_same_worker": base.get("db_qps"),
                        "baseline_memory_used_gib_same_worker": base.get("memory_used_gib"),
                    }
                    futures.append(
                        executor.submit(
                            run_validation,
                            tuner,
                            cfg,
                            run_dir,
                            jsonl_path,
                            "candidate_replay",
                            label,
                            candidate_params(candidate),
                            worker,
                            extra,
                            dry_run,
                            False,
                        )
                    )
                for future in concurrent.futures.as_completed(futures):
                    row = future.result()
                    base_qps = row.get("baseline_db_qps_same_worker")
                    base_mem = row.get("baseline_memory_used_gib_same_worker")
                    if row.get("db_qps") is not None and base_qps:
                        row["db_qps_gain_pct_real"] = (float(row["db_qps"]) / float(base_qps) - 1.0) * 100.0
                    if row.get("memory_used_gib") is not None and base_mem:
                        row["working_set_delta_pct_real"] = (
                            float(row["memory_used_gib"]) / float(base_mem) - 1.0
                        ) * 100.0
                    row["accepted_real"] = (
                        row.get("status") == "ok"
                        and row.get("working_set_delta_pct_real") is not None
                        and float(row["working_set_delta_pct_real"]) < 0.0
                        and row.get("db_qps") is not None
                        and base_qps is not None
                        and float(row["db_qps"]) >= float(base_qps)
                    )
                    append_jsonl(jsonl_path, {**row, "kind": "candidate_replay_scored"})
                    rows.append(row)
                    print(
                        "[parallel-replay] candidate done "
                        f"round={row.get('round')} rank={row.get('round_rank')} worker={row.get('assigned_worker')} "
                        f"status={row.get('status')} accepted={row.get('accepted_real')} "
                        f"db_gain={row.get('db_qps_gain_pct_real')} mem_delta={row.get('working_set_delta_pct_real')}",
                        flush=True,
                    )
    return rows


def write_summary(
    path: Path,
    ts: str,
    plan_path: Path,
    baseline_rows: Sequence[Dict[str, Any]],
    candidate_rows: Sequence[Dict[str, Any]],
    baselines: Dict[str, Dict[str, Optional[float]]],
) -> None:
    accepted = [row for row in candidate_rows if row.get("accepted_real")]
    payload = {
        "kind": "memory_per_round_parallel_replay_summary",
        "ts": ts,
        "plan_source": str(plan_path),
        "baseline_rows": len(baseline_rows),
        "candidate_rows": len(candidate_rows),
        "accepted_rows": len(accepted),
        "baseline_medians_by_worker": baselines,
        "accepted": [
            {
                "round": row.get("round"),
                "round_rank": row.get("round_rank"),
                "orig_label": row.get("orig_label"),
                "assigned_worker": row.get("assigned_worker"),
                "db_qps": row.get("db_qps"),
                "baseline_db_qps_same_worker": row.get("baseline_db_qps_same_worker"),
                "db_qps_gain_pct_real": row.get("db_qps_gain_pct_real"),
                "memory_used_gib": row.get("memory_used_gib"),
                "baseline_memory_used_gib_same_worker": row.get("baseline_memory_used_gib_same_worker"),
                "working_set_delta_pct_real": row.get("working_set_delta_pct_real"),
            }
            for row in accepted
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run per-round topK memory replay with parallel HyBenchXP workers.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--run-dir", default=str(DEFAULT_RUN_DIR))
    parser.add_argument("--plan", required=True)
    parser.add_argument("--round-start", type=int, default=1)
    parser.add_argument("--round-end", type=int, default=5)
    parser.add_argument("--topk-per-round", type=int, default=32)
    parser.add_argument("--parallel-workers", type=int, default=8)
    parser.add_argument("--repeat-baseline", type=int, default=2)
    parser.add_argument("--allow-positive-offline-memory", action="store_true",
                        help="allow replaying candidates whose offline working_set_delta_pct is non-negative")
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--no-dry-run", dest="dry_run", action="store_false")
    args = parser.parse_args()

    ts = time.strftime("%Y%m%dT%H%M%S")
    cfg = load_json(Path(args.config))
    plan_path = Path(args.plan)
    plan = load_json(plan_path)
    run_dir = Path(args.run_dir)
    workers = select_workers(cfg, args.parallel_workers)
    if not workers:
        print("[parallel-replay] FATAL: no cpu_mem_w* workers found", flush=True)
        return 2
    candidates_by_round = ordered_candidates(
        plan,
        args.round_start,
        args.round_end,
        args.topk_per_round,
        args.allow_positive_offline_memory,
    )
    missing_rounds = [round_no for round_no in range(args.round_start, args.round_end + 1) if not candidates_by_round.get(round_no)]
    if missing_rounds:
        print(f"[parallel-replay] FATAL: missing candidates for rounds {missing_rounds}", flush=True)
        return 2

    jsonl_path = run_dir / f"memory_per_round_parallel_replay_{ts}_r{args.round_start}to{args.round_end}.jsonl"
    summary_path = run_dir / f"memory_per_round_parallel_replay_{ts}_r{args.round_start}to{args.round_end}_summary.json"
    print("=" * 72, flush=True)
    print(f"[parallel-replay] ts={ts}", flush=True)
    print(f"[parallel-replay] run_dir={run_dir}", flush=True)
    print(f"[parallel-replay] plan={plan_path}", flush=True)
    print(f"[parallel-replay] dry_run={args.dry_run}", flush=True)
    print(f"[parallel-replay] rounds={args.round_start}-{args.round_end}", flush=True)
    print(f"[parallel-replay] topk_per_round={args.topk_per_round}", flush=True)
    print(f"[parallel-replay] workers={[w.get('name') for w in workers]}", flush=True)
    print(f"[parallel-replay] jsonl={jsonl_path}", flush=True)
    print("=" * 72, flush=True)

    tuner = load_tuner_module()
    baseline_rows = run_baselines(
        tuner,
        cfg,
        run_dir,
        jsonl_path,
        workers,
        max(1, args.repeat_baseline),
        ts,
        args.dry_run,
    )
    baselines = baseline_medians(baseline_rows)
    candidate_rows = run_candidates(
        tuner,
        cfg,
        run_dir,
        jsonl_path,
        workers,
        candidates_by_round,
        baselines,
        ts,
        args.dry_run,
    )
    write_summary(summary_path, ts, plan_path, baseline_rows, candidate_rows, baselines)
    print(f"[parallel-replay] summary={summary_path}", flush=True)
    print("[parallel-replay] done", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "shell" / "run_pg_benchmark_bottleneck_matrix.py"
SOURCE_CONFIG = (
    ROOT
    / "config/generated/pg_benchmark_bottleneck_matrix"
    / "pg_bonect_p2n_none_estimator_guard_v77_20260711_100940"
    / "none/hybench_xp.json"
)
CAMPAIGN_ID = "none_delta_cal_v78_20260711"
CAMPAIGN_DIR = ROOT / "report/bonect" / CAMPAIGN_ID
MANIFEST_PATH = CAMPAIGN_DIR / "manifest.json"
STATE_PATH = CAMPAIGN_DIR / "state.jsonl"

COST_PARAM_KEYS = (
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
)


def read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return data


def append_jsonl(path: Path, record: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")[:48]


def normalized_params(params: Dict[str, Any]) -> Dict[str, float]:
    keys = set(params)
    invalid = sorted(keys - set(COST_PARAM_KEYS))
    missing = sorted(set(COST_PARAM_KEYS) - keys)
    if invalid or missing:
        raise RuntimeError(
            f"calibration point must contain exactly seven cost params; "
            f"invalid={invalid}, missing={missing}"
        )
    return {name: float(params[name]) for name in COST_PARAM_KEYS}


def build_points(source_cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    default = normalized_params(source_cfg.get("initial_params") or {})
    bruteforce = ((source_cfg.get("optimizer") or {}).get("bruteforce") or {})
    seeds = bruteforce.get("force_best_replay_anchor_seed_records") or []
    points: List[Dict[str, Any]] = []
    for seed in seeds:
        if not isinstance(seed, dict):
            continue
        points.append(
            {
                "name": str(seed.get("name") or "forced_anchor"),
                "kind": "historical_anchor_revalidation",
                "params": normalized_params(seed.get("optimized_params") or {}),
                "historical_db_qps_delta_pct": seed.get("db_qps_delta_pct"),
            }
        )

    ranges = source_cfg.get("param_ranges") or {}
    # v77 preflight proved parallel_setup_cost did not change the resource
    # feature signature at either endpoint. Keep it fixed and calibrate the six
    # parameters that actually changed the current workload's plan features.
    active_params = [name for name in COST_PARAM_KEYS if name != "parallel_setup_cost"]
    for name in active_params:
        spec = ranges.get(name) or {}
        for bound in ("min_value", "max_value"):
            if bound not in spec:
                raise RuntimeError(f"missing {name}.{bound} in source config")
            params = dict(default)
            params[name] = float(spec[bound])
            points.append(
                {
                    "name": f"endpoint_{name}_{'low' if bound == 'min_value' else 'high'}",
                    "kind": "single_parameter_endpoint",
                    "changed_param": name,
                    "bound": bound,
                    "params": normalized_params(params),
                }
            )

    # Keep this point after the historical anchors and endpoints so adding the
    # updated feature-bearing baseline does not renumber an in-flight campaign.
    points.append(
        {
            "name": "postgresql_default_feature_baseline",
            "kind": "current_feature_baseline",
            "params": default,
        }
    )

    seen = set()
    unique: List[Dict[str, Any]] = []
    for point in points:
        signature = tuple((name, point["params"][name]) for name in COST_PARAM_KEYS)
        if signature in seen:
            continue
        seen.add(signature)
        point = dict(point)
        point["index"] = len(unique)
        unique.append(point)
    cand24 = next(
        (
            point
            for point in unique
            if str(point.get("name") or "").startswith(
                "phase2_none_v59_cand24_dbqps_fallback"
            )
        ),
        None,
    )
    if cand24 is not None:
        unique.append(
            {
                "name": "phase2_none_v59_cand24_fixed_snapshot_repeat",
                "kind": "current_anchor_repeat",
                "repeat_of": cand24["name"],
                "params": dict(cand24["params"]),
                "index": len(unique),
            }
        )
    return unique


def active_optimizer_processes() -> List[Dict[str, Any]]:
    proc = subprocess.run(
        ["ps", "-eo", "pid=,command="],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    active: List[Dict[str, Any]] = []
    for line in proc.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        pid_text, command = parts
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        if pid == os.getpid() or "run_bonect_phase2_none_delta_calibration.py" in command:
            continue
        if "parameter_optimizer.py" in command or "run_pg_benchmark_bottleneck_matrix.py" in command:
            active.append({"pid": pid, "command": command})
    return active


def iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                yield record


def compact_plan_features(plan_estimation: Dict[str, Any]) -> Dict[str, float]:
    """Extract stable aggregate plan features from a baseline-only estimate."""
    metadata = plan_estimation.get("estimation_metadata") or {}
    breakdown = metadata.get("resource_breakdown") or {}
    features: Dict[str, float] = {}
    for resource_name in ("cpu", "memory", "io", "io_read", "io_write"):
        item = breakdown.get(resource_name) or {}
        calibration = item.get("calibration_breakdown") or {}
        learned = (
            calibration.get("learned_model")
            or calibration.get("learned_model_fallback")
            or {}
        )
        learned_features = learned.get("features") or {}
        if not isinstance(learned_features, dict):
            continue
        for name, value in learned_features.items():
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(number):
                features.setdefault(str(name), number)
    return dict(sorted(features.items()))


def feature_signature(features: Dict[str, float]) -> str:
    payload = json.dumps(features, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def measurement_issues(result: Dict[str, Any]) -> List[str]:
    """Validate that a calibration point contains a real replay label."""
    issues: List[str] = []

    def positive(value: Any) -> bool:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return False
        return math.isfinite(number) and number > 0.0

    if not positive(result.get("db_qps")):
        issues.append("db_qps")
    if not positive(result.get("db_xact_tps")):
        issues.append("db_xact_tps")
    for field in ("resource_normalized", "utilization_pct"):
        values = result.get(field) or {}
        for resource in ("cpu", "memory", "io"):
            if not positive(values.get(resource)):
                issues.append(f"{field}.{resource}")
    sources = result.get("resource_sources") or {}
    for source in ("cpu_source", "memory_source", "io_source", "io_disk_devices"):
        if not str(sources.get(source) or "").strip():
            issues.append(f"resource_sources.{source}")
    samples = result.get("stable_sample_count") or {}
    for resource in ("cpu", "memory", "io"):
        if not positive(samples.get(resource)):
            issues.append(f"stable_sample_count.{resource}")
    if not result.get("benchmark"):
        issues.append("benchmark")
    if not positive(result.get("plan_capture_count")):
        issues.append("plan_capture_count")
    if not positive(result.get("all_sql_count")):
        issues.append("all_sql_count")
    if not positive(result.get("plan_feature_count")):
        issues.append("plan_feature_count")
    return issues


def compact_result(run_id: str, point: Dict[str, Any]) -> Dict[str, Any]:
    run_dir = ROOT / "history/pg_benchmark_bottleneck_matrix" / run_id
    histories = sorted((run_dir / "none").glob("*optimizer_history*.jsonl"))
    if not histories:
        raise RuntimeError(f"optimizer history missing for {run_id}")
    history_path = histories[-1]
    runtime: Dict[str, Any] = {}
    db_view: Dict[str, Any] = {}
    baseline_only: Dict[str, Any] = {}
    strong_gate: Dict[str, Any] = {}
    baseline_top_sql: Dict[str, Any] = {}
    plan_estimation: Dict[str, Any] = {}
    for record in iter_jsonl(history_path):
        event = record.get("event")
        if event == "baseline_runtime_resource_summary":
            runtime = record
        elif event == "db_view_monitor_summary" and str(record.get("label")) == "baseline":
            db_view = record
        elif event == "baseline_only_complete":
            baseline_only = record
        elif event == "baseline_strong_constraint_gate":
            strong_gate = record
        elif event == "baseline_top_sql":
            baseline_top_sql = record
        elif event == "baseline_only_plan_resource_estimation":
            plan_estimation = record
    if not baseline_only:
        raise RuntimeError(f"baseline_only_complete missing for {run_id}")
    stats = runtime.get("resource_stats") or {}
    plan_features = compact_plan_features(plan_estimation)
    sources = {
        name: ((stats.get(name) or {}).get("value"))
        for name in ("cpu_source", "memory_source", "io_source", "io_disk_devices")
    }
    result = {
        "event": "calibration_point_complete",
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "campaign_id": CAMPAIGN_ID,
        "run_id": run_id,
        "point_index": point["index"],
        "point_name": point["name"],
        "kind": point["kind"],
        "params": point["params"],
        "history_path": str(history_path),
        "resource_normalized": runtime.get("resource_normalized") or {},
        "utilization_pct": runtime.get("utilization_pct") or {},
        "resource_sources": sources,
        "benchmark": runtime.get("benchmark") or baseline_only.get("benchmark") or {},
        "db_qps": db_view.get("db_qps"),
        "db_xact_tps": db_view.get("db_xact_tps"),
        "strong_none_gate_passed": strong_gate.get("passed"),
        "stable_sample_count": (stats.get("stable_sample_count") or {}),
        "plan_capture_file": baseline_top_sql.get("plan_capture_file"),
        "plan_capture_count": baseline_top_sql.get("plan_capture_count"),
        "all_sql_count": baseline_top_sql.get("all_sql_count"),
        "plan_estimated_resource_normalized": (
            plan_estimation.get("estimated_resource_normalized") or {}
        ),
        "plan_features": plan_features,
        "plan_feature_count": len(plan_features),
        "plan_feature_signature": feature_signature(plan_features) if plan_features else "",
    }
    issues = measurement_issues(result)
    if issues:
        raise RuntimeError(
            f"incomplete calibration measurement for {run_id}: {', '.join(issues)}"
        )
    result["measurement_valid"] = True
    return result


def runner_command(run_id: str, params: Dict[str, float], *, execute: bool) -> List[str]:
    cmd = [
        sys.executable,
        str(RUNNER),
        "--scenario-set", "bonect",
        "--bonect-bottlenecks", "none",
        "--run-id", run_id,
        "--use-resource-estimator",
        "--apply-vm-profile",
        "--no-warmup",
        "--baseline-only",
        "--allow-noncanonical-none-profile",
        "--hybench-run-mins", "5",
        "--hybench-tp-clients", "14",
        "--hybench-ap-clients", "9",
        "--hybench-ap-query-ids", "5,6,8,11",
        "--hybench-ap-query-weights", "35,45,10,10",
        "--hybench-ap-query-sequence", "5,6,5,6,8,11,6,5,6,8",
        "--hybench-progress-windows", "10",
        "--hybench-statement-timeout-ms", "300000",
        "--measured-workload-timeout-sec", "420",
        "--vm-none-cpu", "96",
        "--vm-none-mem", "24G",
        "--vm-none-read-mb", "25",
        "--vm-none-write-mb", "15",
        "--bonect-capability", "cpu=86.4",
        "--bonect-capability", "memory=21.6",
        "--bonect-capability", "io=36",
    ]
    if execute:
        cmd.append("--run")
    for name in COST_PARAM_KEYS:
        cmd.extend(["--bonect-initial-param", f"{name}={params[name]:.17g}"])
    return cmd


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Collect fixed-snapshot VM resource-delta labels for Phase2 None."
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--start-index", type=int, default=0)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--paired-endpoint-blocks",
        action="store_true",
        help="Run endpoint blocks as low -> restored default -> high.",
    )
    args = parser.parse_args()

    source_cfg = read_json(SOURCE_CONFIG)
    points = build_points(source_cfg)
    selected = [point for point in points if point["index"] >= max(0, args.start_index)]
    if args.limit > 0:
        selected = selected[: args.limit]

    CAMPAIGN_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {
        "campaign_id": CAMPAIGN_ID,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source_config": str(SOURCE_CONFIG),
        "baseline_evidence_run_id": "pg_bonect_p2n_none_estimator_guard_v77_20260711_100940",
        "baseline_resource_normalized": {
            "cpu": 18.818978198361435,
            "memory": 4.644558369029652,
            "io": 9.431888133308407,
        },
        "baseline_db_qps": 382.6054363697833,
        "excluded_param": {
            "name": "parallel_setup_cost",
            "reason": "v77 preflight endpoint feature signature unchanged",
        },
        "points": points,
    }
    MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"manifest={MANIFEST_PATH}", flush=True)
    print(f"selected_points={[point['name'] for point in selected]}", flush=True)
    if not args.execute:
        for point in selected:
            run_id = f"pg_bonect_p2n_{CAMPAIGN_ID}_{point['index']:02d}_{slug(point['name'])}"
            print(" ".join(runner_command(run_id, point["params"], execute=False)), flush=True)
        return 0

    active = active_optimizer_processes()
    if active:
        raise RuntimeError(
            "refusing to start calibration while a runner/optimizer is active: "
            + json.dumps(active, ensure_ascii=False)
        )

    def fresh_run_id(base_run_id: str) -> str:
        history_root = ROOT / "history/pg_benchmark_bottleneck_matrix"
        config_root = ROOT / "config/generated/pg_benchmark_bottleneck_matrix"
        if not (history_root / base_run_id).exists() and not (config_root / base_run_id).exists():
            return base_run_id
        retry = 2
        while True:
            candidate = f"{base_run_id}_r{retry}"
            if not (history_root / candidate).exists() and not (config_root / candidate).exists():
                return candidate
            retry += 1

    def run_point(
        point: Dict[str, Any],
        run_id: str,
        *,
        event_prefix: str = "calibration_point",
        append_complete: bool = True,
        extra_start: Optional[Dict[str, Any]] = None,
    ) -> Tuple[int, Optional[Dict[str, Any]]]:
        started = {
            "event": f"{event_prefix}_start",
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "campaign_id": CAMPAIGN_ID,
            "run_id": run_id,
            "point_index": point["index"],
            "point_name": point["name"],
            "params": point["params"],
        }
        if extra_start:
            started.update(extra_start)
        append_jsonl(STATE_PATH, started)
        print(json.dumps(started, ensure_ascii=False), flush=True)
        proc = subprocess.run(
            runner_command(run_id, point["params"], execute=True),
            cwd=str(ROOT),
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
            check=False,
        )
        if proc.returncode != 0:
            failed = {
                **started,
                "event": f"{event_prefix}_failed",
                "finished_at": datetime.now().isoformat(timespec="seconds"),
                "returncode": proc.returncode,
            }
            append_jsonl(STATE_PATH, failed)
            print(json.dumps(failed, ensure_ascii=False), flush=True)
            return proc.returncode, None
        try:
            completed = compact_result(run_id, point)
        except Exception as exc:
            failed = {
                **started,
                "event": f"{event_prefix}_failed",
                "finished_at": datetime.now().isoformat(timespec="seconds"),
                "returncode": 1,
                "reason": "incomplete_calibration_measurement",
                "error": str(exc),
            }
            append_jsonl(STATE_PATH, failed)
            print(json.dumps(failed, ensure_ascii=False), flush=True)
            return 1, None
        completed["event"] = f"{event_prefix}_complete"
        if append_complete:
            append_jsonl(STATE_PATH, completed)
            print(json.dumps(completed, ensure_ascii=False), flush=True)
        return 0, completed

    def attach_paired_baseline(
        completed: Dict[str, Any],
        paired: Dict[str, Any],
        *,
        pair_position: str,
    ) -> Dict[str, Any]:
        result = dict(completed)
        result["event"] = "calibration_point_complete"
        result["paired_baseline_valid"] = bool(
            result.get("measurement_valid") is True
            and paired.get("measurement_valid") is True
        )
        result["paired_baseline_position"] = pair_position
        result["paired_baseline"] = {
            key: paired.get(key)
            for key in (
                "run_id",
                "history_path",
                "db_qps",
                "db_xact_tps",
                "resource_normalized",
                "utilization_pct",
                "resource_sources",
                "stable_sample_count",
                "plan_features",
                "plan_feature_count",
                "plan_feature_signature",
            )
        }
        return result

    if not args.paired_endpoint_blocks:
        for point in selected:
            run_id = fresh_run_id(
                f"pg_bonect_p2n_{CAMPAIGN_ID}_{point['index']:02d}_{slug(point['name'])}"
            )
            returncode, _completed = run_point(point, run_id)
            if returncode != 0:
                return returncode
        return 0

    invalid_kinds = sorted(
        {
            str(point.get("kind"))
            for point in selected
            if point.get("kind") != "single_parameter_endpoint"
        }
    )
    if invalid_kinds:
        raise RuntimeError(
            "paired endpoint blocks only accept single_parameter_endpoint points: "
            f"invalid_kinds={invalid_kinds}"
        )
    blocks: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for point in selected:
        changed_param = str(point.get("changed_param") or "")
        bound = "low" if point.get("bound") == "min_value" else "high"
        blocks.setdefault(changed_param, {})[bound] = point

    default_params = normalized_params(source_cfg.get("initial_params") or {})
    for changed_param, block in blocks.items():
        low_result: Optional[Dict[str, Any]] = None
        low = block.get("low")
        high = block.get("high")
        if low is not None:
            low_run_id = fresh_run_id(
                f"pg_bonect_p2n_{CAMPAIGN_ID}_{low['index']:02d}_{slug(low['name'])}"
            )
            returncode, low_result = run_point(
                low,
                low_run_id,
                append_complete=False,
            )
            if returncode != 0:
                return returncode

        pair_index = int((low or high or {}).get("index", -1))
        paired_point = {
            "index": pair_index,
            "name": f"paired_default_{changed_param}",
            "kind": "paired_default",
            "params": default_params,
        }
        paired_run_id = fresh_run_id(
            f"pg_bonect_p2n_{CAMPAIGN_ID}_pair_{slug(changed_param)}_default"
        )
        returncode, paired_result = run_point(
            paired_point,
            paired_run_id,
            event_prefix="calibration_paired_baseline",
            extra_start={
                "paired_param": changed_param,
                "paired_point_indices": [
                    point["index"] for point in (low, high) if point is not None
                ],
            },
        )
        if returncode != 0 or paired_result is None:
            if low_result is not None:
                low_result["paired_baseline_valid"] = False
                low_result["paired_baseline_failure"] = paired_run_id
                append_jsonl(STATE_PATH, low_result)
                print(json.dumps(low_result, ensure_ascii=False), flush=True)
            return returncode or 1

        if low_result is not None:
            paired_low = attach_paired_baseline(
                low_result,
                paired_result,
                pair_position="after",
            )
            append_jsonl(STATE_PATH, paired_low)
            print(json.dumps(paired_low, ensure_ascii=False), flush=True)

        if high is not None:
            high_run_id = fresh_run_id(
                f"pg_bonect_p2n_{CAMPAIGN_ID}_{high['index']:02d}_{slug(high['name'])}"
            )
            returncode, high_result = run_point(
                high,
                high_run_id,
                append_complete=False,
            )
            if returncode != 0 or high_result is None:
                return returncode or 1
            paired_high = attach_paired_baseline(
                high_result,
                paired_result,
                pair_position="before",
            )
            append_jsonl(STATE_PATH, paired_high)
            print(json.dumps(paired_high, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

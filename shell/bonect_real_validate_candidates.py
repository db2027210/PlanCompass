#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
import traceback
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import fcntl  # type: ignore
except ImportError:  # pragma: no cover - non-Unix fallback
    fcntl = None


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from double_loop_scenario import sigmoid_linear_resource_score_details
from system_resource_monitor import monitor_resources_from_vm_runtime_workload


COST_PARAMS = [
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
]

SCENARIO_ORDER = ["memory", "cpu", "io", "none"]

DEFAULT_CONFIGS = {
    "memory": ROOT / "config/generated/optimizer_config_universal_xp_16_8_memory_coarse.json",
    "cpu": ROOT / "config/generated/optimizer_config_universal_xp_16_8_cpu_coarse.json",
    "io": ROOT / "config/generated/optimizer_config_universal_xp_16_8_io_coarse.json",
    "none": ROOT / "config/generated/optimizer_config_universal_xp_16_8_cpu_coarse.json",
}

DEFAULT_TOP3 = {
    "memory": ROOT
    / "history/bonect_full_grid_scan/mem_io_orderwide_20260624_170900/memory/top20_distinct_scores.json",
    "cpu": ROOT / "history/bonect_full_grid_scan/all_grid5_20260624_152314/cpu/top20_distinct_scores.json",
    "io": ROOT / "history/bonect_full_grid_scan/mem_io_orderwide_20260624_170900/io/top20_distinct_scores.json",
    "none": ROOT / "history/bonect_full_grid_scan/all_grid5_20260624_152314/none/top20_distinct_scores.json",
}

DEFAULT_RESULT_BONECT = {
    "memory": ROOT / "result/bonect/mem.md",
    "cpu": ROOT / "result/bonect/cpu.md",
    "io": ROOT / "result/bonect/io.md",
}

DEFAULT_COST_VALUES = {
    "seq_page_cost": 1.0,
    "random_page_cost": 4.0,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
}

FIXED_PARAM_DEFAULTS = {
    "effective_cache_size": "4GB",
    "work_mem": "4MB",
    "maintenance_work_mem": "64MB",
    "temp_buffers": "8MB",
    "max_connections": 100,
    "shared_buffers": "128MB",
    "wal_buffers": "4MB",
}

NONE_VM_RESOURCE = {
    "vm_name": "artifact-ubuntu20",
    "cpu": 32,
    "memory_gb": 16,
    "read_mb": 100,
    "write_mb": 100,
    "disk_dev": "vda",
}


def now_tag() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def read_json_if_exists(path: Path) -> Any:
    try:
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def append_jsonl(path: Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        if fcntl is not None:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())
        finally:
            if fcntl is not None:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def acquire_validation_run_lock(path: Path, *, wait: bool) -> Any:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    if fcntl is None:
        handle.write(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "acquired_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
                    "warning": "fcntl is unavailable; validation run lock is advisory-only on this platform",
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n"
        )
        handle.flush()
        return handle
    flags = fcntl.LOCK_EX if wait else fcntl.LOCK_EX | fcntl.LOCK_NB
    try:
        fcntl.flock(handle.fileno(), flags)
    except BlockingIOError as exc:
        handle.close()
        raise RuntimeError(
            f"another Bonect validation run is active for this output dir: {path}. "
            "Run chunks sequentially, use the status refresh command between chunks, or pass --wait-run-lock."
        ) from exc
    handle.seek(0)
    handle.truncate()
    handle.write(
        json.dumps(
            {
                "pid": os.getpid(),
                "acquired_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
                "waited": bool(wait),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n"
    )
    handle.flush()
    os.fsync(handle.fileno())
    return handle


def release_validation_run_lock(handle: Any) -> None:
    try:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def write_jsonl(path: Path, rows: Sequence[Dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            count += 1
    return count


def update_latest_validation_plan_index(
    *,
    run_dir: Path,
    candidates_path: Path,
    plan_json_path: Optional[Path] = None,
    plan_md_path: Optional[Path] = None,
    status_json_path: Optional[Path] = None,
    status_md_path: Optional[Path] = None,
) -> Path:
    output_dir = run_dir.parent
    index_path = output_dir / "latest_robust_ranking_validation_plan_index.json"
    outputs = {
        "run_dir": str(run_dir),
        "candidates_path": str(candidates_path),
        "validation_plan_json": str(plan_json_path or run_dir / "validation_plan.json"),
        "validation_plan_md": str(plan_md_path or run_dir / "validation_plan.md"),
        "validation_status_json": str(status_json_path or run_dir / "validation_status.json"),
        "validation_status_md": str(status_md_path or run_dir / "validation_status.md"),
        "remaining_validation_queue_jsonl": str(run_dir / "remaining_validation_queue.jsonl"),
        "failed_retry_queue_jsonl": str(run_dir / "failed_retry_queue.jsonl"),
        "pending_only_queue_jsonl": str(run_dir / "pending_only_queue.jsonl"),
        "scenario_queues_dir": str(run_dir / "scenario_queues"),
        "scenario_batch_runbook_md": str(run_dir / "scenario_batches.md"),
    }
    payload = {
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "latest_validation_plan_outputs": outputs,
        "note": (
            "Stable pointer to the latest Bonect robust-ranking real-validation plan/status. "
            "The files listed here are offline validation aids only."
        ),
    }
    write_json(index_path, payload)

    calibration_index_path = output_dir / "latest_robust_ranking_calibration_index.json"
    if calibration_index_path.exists():
        try:
            calibration_index = read_json(calibration_index_path)
        except (OSError, json.JSONDecodeError):
            calibration_index = {}
        if isinstance(calibration_index, dict):
            calibration_index["latest_validation_plan_outputs"] = outputs
            calibration_index["validation_plan_index_json"] = str(index_path)
            calibration_index["validation_plan_updated_at"] = payload["updated_at"]
            write_json(calibration_index_path, calibration_index)
    return index_path


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _iter_manifest_candidates(data: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                yield item
    elif isinstance(data, dict):
        if isinstance(data.get("candidates"), list):
            yield from _iter_manifest_candidates(data.get("candidates"))
        elif isinstance(data.get("scenarios"), dict):
            for scenario, info in data.get("scenarios", {}).items():
                if not isinstance(info, dict):
                    continue
                for item in _iter_manifest_candidates(info):
                    row = dict(item)
                    row.setdefault("scenario", scenario)
                    row.setdefault("source", "robust_validation_manifest")
                    row.setdefault("source_path", str(info.get("scan_dir") or ""))
                    yield row


def normalize_candidate(row: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(row)
    scenario = str(item.get("scenario") or item.get("bottleneck") or "").strip().lower()
    if scenario == "mem":
        scenario = "memory"
    if scenario not in {"memory", "cpu", "io", "none"}:
        raise ValueError(f"candidate missing supported scenario: {item.get('candidate_id')}")
    item["scenario"] = scenario
    if not item.get("candidate_id"):
        params = clean_params(item.get("params") or {})
        item["candidate_id"] = "|".join(f"{name}={params[name]:.17g}" for name in COST_PARAMS if name in params)
    if "rank" not in item and item.get("robust_rank") is not None:
        item["rank"] = item.get("robust_rank")
    item.setdefault("source", "robust_pending_validation")
    item.setdefault("estimated_score", item.get("point_score"))
    item.setdefault("estimated_resource", item.get("estimated_resource"))
    item.setdefault("estimated_utilization_pct", item.get("estimated_utilization_pct"))
    item["params"] = clean_params(item.get("params") or {})
    return item


def extract_balanced_object(text: str, object_start: int) -> Dict[str, Any]:
    depth = 0
    in_string = False
    escaped = False
    for pos in range(object_start, len(text)):
        ch = text[pos]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[object_start : pos + 1])
    raise ValueError("unclosed JSON object")


def extract_first_optimized_params(path: Path) -> Dict[str, float]:
    text = path.read_text(encoding="utf-8")
    match = re.search(r'"optimized_params"\s*:\s*\{', text)
    if not match:
        raise ValueError(f"optimized_params not found in {path}")
    params = extract_balanced_object(text, match.end() - 1)
    return {name: float(params[name]) for name in COST_PARAMS if name in params}


def clean_params(params: Dict[str, Any]) -> Dict[str, float]:
    return {name: float(params[name]) for name in COST_PARAMS if name in params}


def make_candidate_manifest(output_path: Path) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for scenario in ["memory", "cpu", "io", "none"]:
        top_path = DEFAULT_TOP3[scenario]
        rows = read_json(top_path)
        for index, row in enumerate(rows[:3], start=1):
            candidates.append(
                {
                    "candidate_id": f"{scenario}_estimator_top{index}",
                    "scenario": scenario,
                    "source": "estimator_top3",
                    "source_path": str(top_path),
                    "rank": index,
                    "estimated_score": row.get("score"),
                    "estimated_resource": row.get("estimated_resource") or row.get("resource"),
                    "estimated_utilization_pct": row.get("utilization_pct"),
                    "params": clean_params(row["params"]),
                }
            )

    for scenario, path in DEFAULT_RESULT_BONECT.items():
        candidates.append(
            {
                "candidate_id": f"{scenario}_result_bonect_best",
                "scenario": scenario,
                "source": "result_bonect_best",
                "source_path": str(path),
                "rank": None,
                "estimated_score": None,
                "estimated_resource": None,
                "estimated_utilization_pct": None,
                "params": extract_first_optimized_params(path),
            }
        )

    candidates.append(
        {
            "candidate_id": "none_result_bonect_missing_default",
            "scenario": "none",
            "source": "result_bonect_missing_default",
            "source_path": "result/bonect/none.md missing; fallback to PostgreSQL default cost params",
            "rank": None,
            "estimated_score": None,
            "estimated_resource": None,
            "estimated_utilization_pct": None,
            "params": dict(DEFAULT_COST_VALUES),
        }
    )
    write_json(output_path, {"created_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"), "candidates": candidates})
    return candidates


def load_candidates(path: Path) -> List[Dict[str, Any]]:
    if path.suffix.lower() == ".jsonl":
        return [normalize_candidate(row) for row in load_jsonl(path)]
    data = read_json(path)
    candidates = [normalize_candidate(row) for row in _iter_manifest_candidates(data)]
    if candidates:
        return candidates
    raise ValueError(f"unsupported candidates file: {path}")


def run_command(
    args: Sequence[str],
    *,
    cwd: Path = ROOT,
    env: Optional[Dict[str, str]] = None,
    timeout: Optional[float] = None,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        list(args),
        cwd=str(cwd),
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check and proc.returncode != 0:
        detail = ((proc.stdout or "") + (proc.stderr or "")).strip()
        raise RuntimeError(f"command failed exit={proc.returncode}: {shlex.join(args)}\n{detail}")
    return proc


def psql_env(pg: Dict[str, Any]) -> Dict[str, str]:
    env = dict(os.environ)
    password = pg.get("password") or pg.get("pg_password")
    if password:
        env["PGPASSWORD"] = str(password)
    return env


def psql_args(pg: Dict[str, Any], sql: str, *, dbname: Optional[str] = None, tuples_only: bool = True) -> List[str]:
    args = [
        "psql",
        "-h",
        str(pg.get("host", "localhost")),
        "-p",
        str(pg.get("port", 5432)),
        "-U",
        str(pg.get("user", "postgres")),
        "-d",
        str(dbname or pg.get("dbname", "postgres")),
        "-X",
        "-q",
        "-v",
        "ON_ERROR_STOP=1",
    ]
    if tuples_only:
        args.append("-At")
    args.extend(["-c", sql])
    return args


def psql_query(pg: Dict[str, Any], sql: str, *, dbname: Optional[str] = None, timeout: float = 60.0) -> str:
    proc = run_command(psql_args(pg, sql, dbname=dbname), env=psql_env(pg), timeout=timeout, check=True)
    return (proc.stdout or "").strip()


def quote_pg_value(value: Any) -> str:
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return str(float(value))


def apply_pg_params(pg: Dict[str, Any], params: Dict[str, float]) -> None:
    for name in COST_PARAMS:
        psql_query(pg, f"ALTER SYSTEM SET {name} = {quote_pg_value(params[name])};", dbname="postgres", timeout=90.0)
    psql_query(pg, "SELECT pg_reload_conf();", dbname="postgres", timeout=90.0)
    time.sleep(2.0)


def run_pre_workload_sql_files(pg: Dict[str, Any], config: Dict[str, Any]) -> List[Dict[str, Any]]:
    benchmark = config.get("benchmark") or {}
    raw_paths = benchmark.get("pre_workload_sql_files") or []
    if not isinstance(raw_paths, list):
        raise ValueError("benchmark.pre_workload_sql_files must be a list")
    timeout_s = max(1.0, float(benchmark.get("pre_workload_sql_timeout_s") or 600.0))
    results: List[Dict[str, Any]] = []
    for raw_path in raw_paths:
        path = Path(str(raw_path))
        if not path.is_absolute():
            path = ROOT / path
        path = path.resolve()
        if not path.is_file():
            raise FileNotFoundError(f"pre-workload SQL file does not exist: {path}")
        args = [
            "psql",
            "-h",
            str(pg.get("host", "localhost")),
            "-p",
            str(pg.get("port", 5432)),
            "-U",
            str(pg.get("user", "postgres")),
            "-d",
            str(pg.get("dbname", "postgres")),
            "-X",
            "-q",
            "-v",
            "ON_ERROR_STOP=1",
            "-f",
            str(path),
        ]
        started = time.time()
        run_command(args, env=psql_env(pg), timeout=timeout_s, check=True)
        results.append(
            {
                "path": str(path),
                "elapsed_s": time.time() - started,
                "status": "ok",
            }
        )
    return results


def restore_default_pg_params(pg: Dict[str, Any]) -> None:
    for name, value in DEFAULT_COST_VALUES.items():
        psql_query(pg, f"ALTER SYSTEM SET {name} = {quote_pg_value(value)};", dbname="postgres", timeout=90.0)
    for name, value in FIXED_PARAM_DEFAULTS.items():
        psql_query(pg, f"ALTER SYSTEM SET {name} = {quote_pg_value(value)};", dbname="postgres", timeout=90.0)
    psql_query(pg, "ALTER SYSTEM RESET max_parallel_workers_per_gather;", dbname="postgres", timeout=90.0)
    psql_query(pg, "ALTER SYSTEM RESET max_parallel_workers;", dbname="postgres", timeout=90.0)
    psql_query(pg, "SELECT pg_reload_conf();", dbname="postgres", timeout=90.0)


def apply_fixed_params(pg: Dict[str, Any], fixed_params: Any) -> None:
    """Apply scenario fixed params at system and database scope.

    Database-level GUC overrides (e.g. work_mem set on hybench_sf10x) must not
    silently mask the v112 Memory fixed profile. System-only ALTER SYSTEM is
    insufficient for session-scoped settings that already have a DB override.
    """
    if not isinstance(fixed_params, dict):
        return
    # Settings that may legally exist as database-level overrides and would
    # otherwise hide the intended scenario fixed profile.
    db_scoped_names = {
        "work_mem",
        "maintenance_work_mem",
        "temp_buffers",
        "effective_cache_size",
        "max_parallel_workers_per_gather",
        "max_parallel_workers",
    }
    for name, value in fixed_params.items():
        psql_query(pg, f"ALTER SYSTEM SET {name} = {quote_pg_value(value)};", dbname="postgres", timeout=90.0)
    psql_query(pg, "SELECT pg_reload_conf();", dbname="postgres", timeout=90.0)
    target_db = str(pg.get("dbname") or "").strip()
    if target_db and re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", target_db):
        for name, value in fixed_params.items():
            if str(name) not in db_scoped_names:
                continue
            psql_query(
                pg,
                f"ALTER DATABASE {target_db} SET {name} = {quote_pg_value(value)};",
                dbname="postgres",
                timeout=90.0,
            )
    time.sleep(2.0)


def try_psql_query(pg: Dict[str, Any], sql: str, *, dbname: Optional[str] = None, timeout: float = 60.0) -> Tuple[Optional[str], Optional[str]]:
    try:
        return psql_query(pg, sql, dbname=dbname, timeout=timeout), None
    except Exception as exc:
        return None, str(exc)


def reset_pg_stat_statements(pg: Dict[str, Any]) -> Optional[str]:
    _, err = try_psql_query(pg, "SELECT pg_stat_statements_reset();", timeout=60.0)
    return err


def db_snapshot(pg: Dict[str, Any]) -> Dict[str, Any]:
    snapshot: Dict[str, Any] = {"ts": time.time()}
    dbname = str(pg.get("dbname", "postgres"))
    sql = (
        "SELECT COALESCE(xact_commit,0)+COALESCE(xact_rollback,0), "
        "COALESCE(tup_returned,0)+COALESCE(tup_fetched,0)+COALESCE(tup_inserted,0)+"
        "COALESCE(tup_updated,0)+COALESCE(tup_deleted,0) "
        f"FROM pg_stat_database WHERE datname = {quote_pg_value(dbname)};"
    )
    out, err = try_psql_query(pg, sql, dbname="postgres")
    if out and "|" in out:
        parts = out.split("|")
        snapshot["xacts"] = float(parts[0] or 0.0)
        snapshot["tuples"] = float(parts[1] or 0.0)
    elif err:
        snapshot["pg_stat_database_error"] = err

    out, err = try_psql_query(pg, "SELECT COALESCE(sum(calls),0) FROM pg_stat_statements;", timeout=60.0)
    if out:
        try:
            snapshot["pg_stat_statements_calls"] = float(out.splitlines()[-1])
        except ValueError:
            snapshot["pg_stat_statements_raw"] = out
    elif err:
        snapshot["pg_stat_statements_error"] = err
    return snapshot


def db_delta(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    elapsed = max(0.001, float(after.get("ts", 0.0)) - float(before.get("ts", 0.0)))
    result = {"elapsed_s": elapsed}
    for key, out_key in [
        ("xacts", "db_xact_tps"),
        ("tuples", "db_tuple_tps"),
        ("pg_stat_statements_calls", "db_qps"),
    ]:
        if key in before and key in after:
            delta = max(0.0, float(after[key]) - float(before[key]))
            result[out_key] = delta / elapsed
            result[out_key.replace("_tps", "_delta").replace("_qps", "_delta")] = delta
    return result


def normalize_runtime_resource(
    resource_avg_raw: Dict[str, float],
    resource_stats_raw: Dict[str, Any],
    capability: Dict[str, float],
) -> Tuple[Dict[str, float], Dict[str, float]]:
    io_read_mb_s = float(((resource_stats_raw.get("io_read") or {}).get("avg", 0.0) or 0.0)) / (1024 ** 2)
    io_write_mb_s = float(((resource_stats_raw.get("io_write") or {}).get("avg", 0.0) or 0.0)) / (1024 ** 2)
    io_sum_mb_s = io_read_mb_s + io_write_mb_s
    io_mb_s = (
        float(((resource_stats_raw.get("io") or {}).get("avg", 0.0) or 0.0)) / (1024 ** 2)
        if isinstance(resource_stats_raw.get("io"), dict)
        else float(resource_avg_raw.get("io", 0.0) or 0.0) / (1024 ** 2)
    )
    if io_sum_mb_s > 0.0 and (io_mb_s <= 0.0 or io_mb_s < io_sum_mb_s * 0.5):
        io_mb_s = io_sum_mb_s
    normalized = {
        "cpu": float(resource_avg_raw.get("cpu", 0.0) or 0.0),
        "memory": float(resource_avg_raw.get("memory", 0.0) or 0.0) / (1024 ** 3),
        "io": io_mb_s,
        "io_read": io_read_mb_s,
        "io_write": io_write_mb_s,
    }
    utilization = {}
    for name, cap in capability.items():
        if cap:
            utilization[name] = float(normalized.get(name, 0.0)) / float(cap) * 100.0
    return normalized, utilization


def _configured_sweet_bound(
    raw: Any,
    capability: float,
) -> Optional[float]:
    if raw is None:
        return None
    value = float(raw)
    utilization = value / 100.0 if value > 1.0 else value
    return utilization * float(capability)


def calculate_configured_resource_score(
    config: Dict[str, Any],
    scenario: str,
    resource: Dict[str, float],
    capability: Dict[str, float],
) -> Dict[str, Any]:
    """Use the exact small-loop formula for a real replay resource vector."""
    scoring = config.get("scoring") or {}
    if str(scoring.get("score_mode", "band_target")) != "band_target":
        raise ValueError("double-loop real replay requires scoring.score_mode=band_target")
    if scenario not in SCENARIO_ORDER:
        raise ValueError(f"unsupported scenario for resource score: {scenario}")
    target_ranges = scoring.get("resource_target_ranges") or {}
    target_range = target_ranges.get(scenario) if scenario != "none" else {}
    target_range = target_range if isinstance(target_range, dict) else {}
    target_capability = float(capability[scenario]) if scenario != "none" else 0.0
    target_sweet_lower = _configured_sweet_bound(
        target_range.get("target_min_pct", target_range.get("min_pct", target_range.get("min"))),
        target_capability,
    ) if scenario != "none" else None
    target_sweet_upper = _configured_sweet_bound(
        target_range.get("target_max_pct", target_range.get("max_pct", target_range.get("max"))),
        target_capability,
    ) if scenario != "none" else None
    return sigmoid_linear_resource_score_details(
        scenario=scenario,
        resource=resource,
        resource_limits=capability,
        target_sweet_lower=target_sweet_lower,
        target_sweet_upper=target_sweet_upper,
        io_reward_weight=0.25,
    )


def maybe_override_run_mins(workload_cmd: str, run_mins: Optional[float]) -> str:
    if run_mins is None:
        return workload_cmd
    value = str(int(run_mins)) if float(run_mins).is_integer() else str(float(run_mins))
    replacements = [
        (r"s/\^xpRunMins=\.\*/xpRunMins=[^/']*/", f"s/^xpRunMins=.*/xpRunMins={value}/"),
        (r"s/\^apRunMins=\.\*/apRunMins=[^/']*/", f"s/^apRunMins=.*/apRunMins={value}/"),
    ]
    updated = workload_cmd
    for pattern, replacement in replacements:
        updated = re.sub(pattern, replacement, updated)
    return updated


def wrap_workload(workload_cmd: str, timeout_s: Optional[float]) -> str:
    if timeout_s is None or timeout_s <= 0:
        return workload_cmd
    return f"timeout --preserve-status {int(timeout_s)}s bash -lc {shlex.quote(workload_cmd)}"


def vm_resource_for_scenario(scenario: str, config: Dict[str, Any]) -> Dict[str, Any]:
    if scenario == "none":
        return dict(NONE_VM_RESOURCE)
    resource = config.get("vm_resource") or {}
    if not resource:
        return dict(NONE_VM_RESOURCE)
    return dict(resource)


def apply_vm_profile(vm_resource: Dict[str, Any], *, force_destroy: bool, log_path: Path) -> None:
    cmd = [
        str(ROOT / "shell/set_vm_resource.sh"),
        str(vm_resource.get("vm_name", "artifact-ubuntu20")),
        str(vm_resource.get("cpu")),
        f"{vm_resource.get('memory_gb')}G",
        str(vm_resource.get("read_mb")),
        str(vm_resource.get("write_mb")),
        str(vm_resource.get("disk_dev", "vda")),
    ]
    env = dict(os.environ)
    if force_destroy:
        env["VM_RESOURCE_FORCE_DESTROY_ON_SHUTDOWN_TIMEOUT"] = "1"
    with log_path.open("a", encoding="utf-8") as fp:
        fp.write(f"$ {shlex.join(cmd)}\n")
        fp.flush()
        proc = subprocess.Popen(cmd, cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        assert proc.stdout is not None
        for line in proc.stdout:
            fp.write(line)
            fp.flush()
            print(line, end="", flush=True)
        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"VM profile command failed exit={proc.returncode}: {shlex.join(cmd)}")


def ssh_base(vm_runtime: Dict[str, Any]) -> List[str]:
    cmd = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        "-p",
        str(vm_runtime.get("ssh_port", 22)),
    ]
    if vm_runtime.get("ssh_identity_file"):
        cmd.extend(["-i", str(vm_runtime["ssh_identity_file"])])
    target = vm_runtime.get("ssh_target") or vm_runtime.get("host")
    cmd.append(str(target))
    return cmd


def wait_for_ssh(vm_runtime: Dict[str, Any], timeout_s: float = 300.0) -> None:
    deadline = time.time() + timeout_s
    next_log = 0.0
    while time.time() < deadline:
        proc = run_command(ssh_base(vm_runtime) + ["true"], timeout=20.0)
        if proc.returncode == 0:
            print(f"VM SSH ready: {vm_runtime.get('ssh_target') or vm_runtime.get('host')}", flush=True)
            return
        if time.time() >= next_log:
            print("Waiting for VM SSH...", flush=True)
            next_log = time.time() + 30.0
        time.sleep(5.0)
    raise RuntimeError("VM SSH did not become ready")


def start_postgres_and_wait(pg: Dict[str, Any], vm_runtime: Dict[str, Any], timeout_s: float = 300.0) -> None:
    start_cmd = (
        "sudo -n -u postgres /usr/lib/postgresql/17/bin/pg_ctl "
        "-D /home/anonymous/pg_master/pg_data status >/dev/null 2>&1 && "
        "sudo -n -u postgres /usr/lib/postgresql/17/bin/pg_ctl "
        "-D /home/anonymous/pg_master/pg_data -w -t 600 -m fast restart "
        "</dev/null >/tmp/resource_adaptive_pg_start.log 2>&1 || "
        "sudo -n -u postgres /usr/lib/postgresql/17/bin/pg_ctl "
        "-D /home/anonymous/pg_master/pg_data -w -t 600 start "
        "</dev/null >/tmp/resource_adaptive_pg_start.log 2>&1; "
        "rc=$?; cat /tmp/resource_adaptive_pg_start.log; exit $rc"
    )
    proc = run_command(ssh_base(vm_runtime) + [f"bash -lc {shlex.quote(start_cmd)}"], timeout=120.0)
    if proc.returncode != 0:
        detail = ((proc.stdout or "") + (proc.stderr or "")).strip()
        print(f"PostgreSQL restart command returned {proc.returncode}: {detail}", flush=True)
    deadline = time.time() + timeout_s
    next_log = 0.0
    while time.time() < deadline:
        proc = run_command(psql_args(pg, "SELECT 1;", dbname="postgres"), env=psql_env(pg), timeout=15.0)
        if proc.returncode == 0:
            print(f"PostgreSQL ready: {pg.get('host')}:{pg.get('port')}", flush=True)
            return
        if time.time() >= next_log:
            print(f"Waiting for PostgreSQL {pg.get('host')}:{pg.get('port')}", flush=True)
            next_log = time.time() + 30.0
        time.sleep(5.0)
    raise RuntimeError("PostgreSQL did not become ready")


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_worker_config(path: Optional[str]) -> Dict[str, Any]:
    if not path:
        return {}
    data = read_json(Path(path))
    if not isinstance(data, dict):
        raise ValueError("--worker-config-json must contain a JSON object")
    return data


def scenario_context(
    scenario: str,
    worker_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    worker_config = worker_config or {}
    scenario_paths = worker_config.get("scenario_configs") or {}
    config_path = Path(scenario_paths.get(scenario) or DEFAULT_CONFIGS[scenario])
    config = read_json(config_path)
    common_override = worker_config.get("config_override") or {}
    scenario_overrides = worker_config.get("scenario_overrides") or {}
    scenario_override = scenario_overrides.get(scenario) or {}
    if not isinstance(common_override, dict) or not isinstance(scenario_override, dict):
        raise ValueError("worker config overrides must be JSON objects")
    config = _deep_merge(config, common_override)
    config = _deep_merge(config, scenario_override)
    vm_resource = vm_resource_for_scenario(scenario, config)
    worker_vm_resource = worker_config.get("vm_resource") or {}
    if worker_vm_resource:
        if not isinstance(worker_vm_resource, dict):
            raise ValueError("worker vm_resource must be a JSON object")
        vm_resource = _deep_merge(vm_resource, worker_vm_resource)
    if worker_config.get("vm_name"):
        vm_resource["vm_name"] = str(worker_config["vm_name"])
    capability, io_capability_basis = resolve_runtime_capability(config, vm_resource)
    return {
        "scenario": scenario,
        "config_path": str(config_path),
        "config": config,
        "pg": config.get("postgres") or {},
        "capability": capability,
        "io_capability_basis": io_capability_basis,
        "vm_runtime": config.get("metrics", {}).get("vm_runtime") or {},
        "vm_resource": vm_resource,
        "worker": {
            "worker_id": str(worker_config.get("worker_id") or "default"),
            "vm_name": str(
                worker_config.get("vm_name")
                or (config.get("metrics", {}).get("vm_resource") or {}).get("vm_name")
                or ""
            ),
            "disk_isolation_id": worker_config.get("disk_isolation_id"),
            "disk_isolation_verified": bool(
                worker_config.get("disk_isolation_verified", False)
            ),
        },
    }


def resolve_runtime_capability(
    config: Dict[str, Any],
    vm_resource: Dict[str, Any],
) -> Tuple[Dict[str, float], Dict[str, Any]]:
    capability = {
        str(name): float(value)
        for name, value in (config.get("capability") or {}).items()
    }
    vm_runtime = ((config.get("metrics") or {}).get("vm_runtime") or {})
    mode = str(vm_runtime.get("io_capability_mode") or "configured_total").strip().lower()
    read_mb = float(vm_resource.get("read_mb") or capability.get("io_read") or 0.0)
    write_mb = float(vm_resource.get("write_mb") or capability.get("io_write") or 0.0)
    configured = float(capability.get("io") or 0.0)
    directional_sum = read_mb + write_mb
    directional_max = max(read_mb, write_mb)
    basis: Dict[str, Any] = {
        "mode": mode,
        "aggregate_definition": mode,
        "configured_io_mib_s": configured if configured > 0 else None,
        "read_cap_mib_s": read_mb,
        "write_cap_mib_s": write_mb,
        "read_limit_mib_s": read_mb,
        "write_limit_mib_s": write_mb,
        "directional_sum_mib_s": directional_sum,
        "directional_max_mib_s": directional_max,
        "capability_mismatch": False,
        "capability_mismatch_reason": "",
    }
    if mode == "sum_directional_limits":
        expected = directional_sum
        basis["expected_total_mib_s"] = expected
        if expected <= 0:
            raise ValueError("sum_directional_limits requires positive read_mb + write_mb")
        if not math.isclose(configured, expected, rel_tol=1e-9, abs_tol=1e-6):
            raise ValueError(
                "IO capability mismatch: disk_device metric is read+write, "
                f"configured={configured}, expected={expected}"
            )
    elif mode == "configured_total":
        # disk_device averages report read+write bytes/s. When both directional
        # limits are known, the aggregate denominator must equal their sum.
        # Using write-only (or max) as aggregate while counting read+write is a
        # fail-closed capability mismatch (historical cap=80 with read10+write80).
        if read_mb > 0.0 and write_mb > 0.0 and configured > 0.0:
            matches_sum = math.isclose(configured, directional_sum, rel_tol=1e-9, abs_tol=1e-6)
            if not matches_sum:
                reason = (
                    "configured_total IO capability does not match directional sum: "
                    f"configured={configured}, sum={directional_sum}, "
                    f"read_cap={read_mb}, write_cap={write_mb}"
                )
                basis["capability_mismatch"] = True
                basis["capability_mismatch_reason"] = reason
                raise ValueError(f"IO capability mismatch: {reason}")
        basis["expected_total_mib_s"] = configured if configured > 0 else directional_sum
    else:
        raise ValueError(f"unsupported io_capability_mode={mode!r}")
    return capability, basis


def _vm_runtime_bool(vm_runtime: Dict[str, Any], key: str, default: bool = False) -> bool:
    raw = vm_runtime.get(key, default)
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off", ""}:
        return False
    return bool(default)


def vm_runtime_monitor_options(vm_runtime: Dict[str, Any]) -> Dict[str, Any]:
    """Build monitor options without dropping configured metric modes.

    Sampler accelerations are opt-in only (0949):
    - ssh_control_master
    - light_remote_script
    - interval_correct_sleep
    Missing keys preserve legacy one-shot SSH + full-interval sleep.
    """
    return {
        "ssh_target": str(vm_runtime.get("ssh_target") or ""),
        "ssh_user": str(vm_runtime.get("ssh_user") or ""),
        "ssh_port": int(vm_runtime.get("ssh_port", 22)),
        "ssh_identity_file": str(vm_runtime.get("ssh_identity_file") or ""),
        "interval_s": float(vm_runtime.get("interval_sec", 2.0)),
        "warmup_s": float(vm_runtime.get("warmup_sec", 0.0)),
        "connect_timeout_s": float(vm_runtime.get("connect_timeout_sec", 5.0)),
        "disk_devices": (
            vm_runtime.get("disk_devices")
            if isinstance(vm_runtime.get("disk_devices"), list)
            else None
        ),
        "cpu_mode": str(vm_runtime.get("cpu_mode") or "pg_proc"),
        "io_mode": str(vm_runtime.get("io_mode") or "disk_device"),
        "memory_mode": str(vm_runtime.get("memory_mode") or "pg_proc_rss"),
        "max_runtime_s": float(vm_runtime.get("max_runtime_s") or 0.0),
        "fixed_horizon_success": bool(vm_runtime.get("fixed_horizon_success", False)),
        "ssh_control_master": _vm_runtime_bool(vm_runtime, "ssh_control_master", False),
        "light_remote_script": _vm_runtime_bool(vm_runtime, "light_remote_script", False),
        "interval_correct_sleep": _vm_runtime_bool(
            vm_runtime, "interval_correct_sleep", False
        ),
    }


def _to_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def capture_pg_show_settings(pg: Dict[str, Any], names: Sequence[str]) -> Dict[str, Any]:
    """Capture selected GUC values via SHOW for provenance logging."""
    out: Dict[str, Any] = {"captured_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"), "settings": {}}
    for name in names:
        safe = str(name).strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", safe):
            out["settings"][safe] = {"error": "unsafe_identifier"}
            continue
        try:
            value = psql_query(pg, f"SHOW {safe};", timeout=30.0)
            out["settings"][safe] = value
        except Exception as exc:
            out["settings"][safe] = {"error": repr(exc)}
    return out


def wait_for_validation_warmup_connection_drain(
    pg: Dict[str, Any],
    *,
    application_name: str,
    timeout_sec: float,
    poll_sec: float,
    max_app_connections: int,
) -> Dict[str, Any]:
    """Wait until warmup JDBC sessions drain before measured workload."""
    if timeout_sec <= 0.0 or not application_name:
        return {"passed": True, "enabled": False, "reason": "disabled"}
    deadline = time.monotonic() + timeout_sec
    started = time.monotonic()
    last_count: Optional[int] = None
    last_error = ""
    dbname = str(pg.get("dbname") or "postgres")
    print(
        f"[validate] waiting for validation warmup JDBC sessions to drain "
        f"(app={application_name!r}) ...",
        flush=True,
    )
    while True:
        try:
            raw = psql_query(
                pg,
                "SELECT count(*) FROM pg_stat_activity "
                f"WHERE datname = '{dbname.replace(chr(39), chr(39)+chr(39))}' "
                "AND pid <> pg_backend_pid() "
                f"AND application_name = '{application_name.replace(chr(39), chr(39)+chr(39))}';",
                timeout=30.0,
            ).strip()
            last_count = max(0, int(float(raw)))
            last_error = ""
            if last_count <= max_app_connections:
                result = {
                    "passed": True,
                    "enabled": True,
                    "application_name": application_name,
                    "connection_count": last_count,
                    "max_allowed": max_app_connections,
                    "elapsed_sec": time.monotonic() - started,
                }
                print(
                    f"[validate] validation warmup JDBC sessions drained: count={last_count}",
                    flush=True,
                )
                return result
        except Exception as exc:
            last_error = str(exc)
        if time.monotonic() >= deadline:
            result = {
                "passed": False,
                "enabled": True,
                "application_name": application_name,
                "connection_count": last_count,
                "max_allowed": max_app_connections,
                "elapsed_sec": time.monotonic() - started,
                "error": last_error or "connection drain timed out",
            }
            raise RuntimeError(
                "validation warmup JDBC sessions did not drain before measured "
                f"workload: {result}"
            )
        time.sleep(max(0.2, poll_sec))


def run_validation_warmup(
    *,
    tag: str,
    params: Dict[str, float],
    config: Dict[str, Any],
    pg: Dict[str, Any],
) -> Dict[str, Any]:
    """Explicitly execute benchmark.validation_warmup_cmd when enabled.

    The parallel real-validator path historically ignored optimizer validation
    warmup flags. r15 requires real start/end/duration evidence; a config flag
    alone is not proof.
    """
    benchmark = config.get("benchmark") or {}
    enabled = _to_bool(benchmark.get("validation_warmup_enabled"), False)
    if not enabled:
        return {
            "event": "validation_warmup",
            "tag": tag,
            "enabled": False,
            "executed": False,
            "success": True,
            "reason": "validation_warmup_enabled=false",
        }

    warmup_cmd = str(
        benchmark.get("validation_warmup_cmd")
        or benchmark.get("warmup_cmd")
        or ""
    ).strip()
    required = _to_bool(benchmark.get("validation_warmup_required"), True)
    timeout_sec = float(benchmark.get("validation_warmup_timeout_sec") or 180.0)
    min_duration_sec = max(
        0.0,
        float(benchmark.get("validation_warmup_min_duration_sec") or 0.0),
    )
    drain_timeout_sec = float(benchmark.get("validation_warmup_drain_timeout_sec") or 90.0)
    drain_poll_sec = float(benchmark.get("validation_warmup_drain_poll_sec") or 2.0)
    drain_max = int(benchmark.get("validation_warmup_drain_max_app_connections") or 0)
    drain_app = str(
        benchmark.get("validation_warmup_drain_application_name")
        or "resource-adaptive-matrix"
    )

    if not warmup_cmd:
        detail = "validation warmup is enabled but benchmark.validation_warmup_cmd is empty"
        row = {
            "event": "validation_warmup",
            "tag": tag,
            "enabled": True,
            "executed": False,
            "success": False,
            "error": detail,
        }
        if required:
            raise RuntimeError(detail)
        print(f"[WARN] {detail}", flush=True)
        return row

    print(f"[validate] {tag} validation warmup starts (timeout={timeout_sec}s) ...", flush=True)
    start_ts = time.time()
    start_wall = time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime(start_ts))
    proc = subprocess.Popen(
        warmup_cmd,
        shell=True,
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        text=True,
    )
    output_lines: List[str] = []
    timed_out = False
    try:
        stdout, _ = proc.communicate(timeout=timeout_sec if timeout_sec > 0 else None)
        if stdout:
            output_lines.extend(stdout.splitlines())
            print(stdout, end="" if stdout.endswith("\n") else "\n", flush=True)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(proc.pid, 15)  # SIGTERM
            stdout, _ = proc.communicate(timeout=10)
        except Exception:
            try:
                os.killpg(proc.pid, 9)  # SIGKILL
            except Exception:
                pass
            stdout, _ = proc.communicate()
        if stdout:
            output_lines.extend(stdout.splitlines())
            print(stdout, end="" if stdout.endswith("\n") else "\n", flush=True)

    end_ts = time.time()
    end_wall = time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime(end_ts))
    duration_s = end_ts - start_ts
    # Historical optimizer treats intentional timeout as success for fixed-horizon warmup.
    success = timed_out or (proc.returncode == 0)
    duration_error = ""
    if success and duration_s < min_duration_sec:
        success = False
        duration_error = (
            f"validation warmup ended too early: duration_s={duration_s:.3f} "
            f"min_duration_sec={min_duration_sec:.3f}"
        )
    row: Dict[str, Any] = {
        "event": "validation_warmup",
        "tag": tag,
        "enabled": True,
        "executed": True,
        "success": success,
        "start_ts": start_ts,
        "end_ts": end_ts,
        "start_wall": start_wall,
        "end_wall": end_wall,
        "duration_s": duration_s,
        "timeout_sec": timeout_sec,
        "min_duration_sec": min_duration_sec,
        "timed_out": timed_out,
        "returncode": proc.returncode,
        "command": warmup_cmd,
        "params": dict(params),
        "output_tail": output_lines[-20:],
    }
    if duration_error:
        row["error"] = duration_error
    print(
        f"[validate] {tag} validation warmup done success={success} "
        f"duration_s={duration_s:.3f} timed_out={timed_out}",
        flush=True,
    )
    if not success and required:
        raise RuntimeError(f"validation warmup failed: {row}")

    drain = wait_for_validation_warmup_connection_drain(
        pg,
        application_name=drain_app,
        timeout_sec=drain_timeout_sec,
        poll_sec=drain_poll_sec,
        max_app_connections=drain_max,
    )
    row["connection_drain"] = drain
    return row


SHOW_CAPTURE_NAMES = [
    "work_mem",
    "shared_buffers",
    "hash_mem_multiplier",
    "max_parallel_workers",
    "max_parallel_workers_per_gather",
    "effective_cache_size",
    "maintenance_work_mem",
    "temp_buffers",
    "max_connections",
    "wal_buffers",
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
]


def validate_candidate(
    candidate: Dict[str, Any],
    context: Dict[str, Any],
    *,
    run_mins: Optional[float],
    workload_timeout_s: Optional[float],
) -> Dict[str, Any]:
    scenario = str(candidate["scenario"])
    config = context["config"]
    pg = context["pg"]
    capability = context["capability"]
    vm_runtime = context["vm_runtime"]
    params = clean_params(candidate["params"])
    workload_cmd = str((config.get("benchmark") or {}).get("workload_cmd") or "")
    workload_cmd = maybe_override_run_mins(workload_cmd, run_mins)
    workload_cmd = wrap_workload(workload_cmd, workload_timeout_s)

    started_at = time.strftime("%Y-%m-%d %H:%M:%S %Z")
    start_ts = time.time()
    reset_err = None
    before: Dict[str, Any] = {}
    after: Dict[str, Any] = {}
    status = "ok"
    error = None
    error_traceback = None
    raw_avg: Dict[str, float] = {}
    raw_stats: Dict[str, Any] = {}
    normalized: Dict[str, float] = {}
    utilization: Dict[str, float] = {}
    actual_score: Optional[float] = None
    actual_score_detail: Dict[str, Any] = {}
    benchmark: Dict[str, Any] = {}
    benchmark_latency: Dict[str, Any] = {}
    pre_workload_setup: List[Dict[str, Any]] = []
    validation_warmup: Dict[str, Any] = {}
    pg_show_before: Dict[str, Any] = {}
    pg_show_after: Dict[str, Any] = {}

    try:
        print(f"[validate] {candidate['candidate_id']} applying params", flush=True)
        apply_fixed_params(pg, config.get("fixed_params"))
        apply_pg_params(pg, params)
        pre_workload_setup = run_pre_workload_sql_files(pg, config)
        pg_show_before = capture_pg_show_settings(pg, SHOW_CAPTURE_NAMES)
        print(
            f"[validate] {candidate['candidate_id']} SHOW before="
            f"{json.dumps(pg_show_before.get('settings') or {}, sort_keys=True)}",
            flush=True,
        )
        validation_warmup = run_validation_warmup(
            tag=str(candidate["candidate_id"]),
            params=params,
            config=config,
            pg=pg,
        )
        # Re-apply cost params after warmup in case workload sessions mutated GUCs.
        apply_pg_params(pg, params)
        reset_err = reset_pg_stat_statements(pg)
        before = db_snapshot(pg)
        print(f"[validate] {candidate['candidate_id']} measured workload start", flush=True)
        raw_avg, raw_stats = monitor_resources_from_vm_runtime_workload(
            workload_cmd,
            str(vm_runtime.get("host") or pg.get("host")),
            **vm_runtime_monitor_options(vm_runtime),
        )
        after = db_snapshot(pg)
        pg_show_after = capture_pg_show_settings(pg, SHOW_CAPTURE_NAMES)
        print(
            f"[validate] {candidate['candidate_id']} SHOW after="
            f"{json.dumps(pg_show_after.get('settings') or {}, sort_keys=True)}",
            flush=True,
        )
        normalized, utilization = normalize_runtime_resource(raw_avg, raw_stats, capability)
        actual_score_detail = calculate_configured_resource_score(
            config,
            scenario,
            normalized,
            capability,
        )
        actual_score = float(actual_score_detail["total"])
        benchmark = raw_stats.get("benchmark") or {}
        benchmark_latency = raw_stats.get("benchmark_latency") or {}
    except Exception as exc:
        status = "failed"
        error = repr(exc)
        error_traceback = traceback.format_exc()
        try:
            after = db_snapshot(pg)
        except Exception:
            after = {}
        if not pg_show_after:
            try:
                pg_show_after = capture_pg_show_settings(pg, SHOW_CAPTURE_NAMES)
            except Exception:
                pg_show_after = {}

    elapsed_s = time.time() - start_ts
    result = {
        "candidate_id": candidate["candidate_id"],
        "scenario": scenario,
        "source": candidate.get("source"),
        "source_path": candidate.get("source_path"),
        "rank": candidate.get("rank"),
        "robust_rank": candidate.get("robust_rank"),
        "candidate_index": candidate.get("candidate_index"),
        "online_selected_variant": candidate.get("online_selected_variant"),
        "status": status,
        "error": error,
        "error_traceback": error_traceback,
        "started_at": started_at,
        "elapsed_s": elapsed_s,
        "run_mins_override": run_mins,
        "params": params,
        "estimated_score": candidate.get("estimated_score"),
        "estimated_resource": candidate.get("estimated_resource"),
        "estimated_utilization_pct": candidate.get("estimated_utilization_pct"),
        "actual_score": actual_score,
        "actual_score_detail": actual_score_detail,
        "actual_resource": normalized,
        "actual_utilization_pct": utilization,
        "raw_resource_avg": raw_avg,
        "raw_resource_stats": raw_stats,
        "benchmark": benchmark,
        "benchmark_latency": benchmark_latency,
        "pre_workload_setup": pre_workload_setup,
        "validation_warmup": validation_warmup,
        "pg_show_before": pg_show_before,
        "pg_show_after": pg_show_after,
        "pg_stat_reset_error": reset_err,
        "db_before": before,
        "db_after": after,
        "db_delta": db_delta(before, after) if before and after else {},
        "config_path": context["config_path"],
        "capability": capability,
        "io_capability_basis": dict(context.get("io_capability_basis") or {}),
        "vm_resource": context["vm_resource"],
        "workload_cmd": workload_cmd,
        "worker": dict(context.get("worker") or {}),
    }
    print(f"[validate] {candidate['candidate_id']} status={status} elapsed={elapsed_s:.1f}s", flush=True)
    return result


def fmt(value: Any, digits: int = 6) -> str:
    if value is None:
        return "-"
    try:
        x = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(x) >= 100000 or (abs(x) < 0.001 and x != 0.0):
        return f"{x:.{digits}e}"
    return f"{x:.{digits}f}".rstrip("0").rstrip(".")


def parse_workload_label(cmd: str) -> str:
    items = []
    for key in ["xtpclient", "xapclient", "xpRunMins", "apclient", "apRunMins", "ap_query_sequence"]:
        matches = re.findall(rf"{re.escape(key)}=([0-9A-Za-z_,.-]+)", cmd)
        values = [value for value in matches if value not in {".", ".."} and not value.startswith(".")]
        if values:
            items.append(f"{key}={values[-1]}")
    if "runxp" in cmd:
        mode = "HyBench XP"
    elif "runap" in cmd:
        mode = "HyBench AP"
    else:
        mode = "HyBench"
    return mode + ("; " + ", ".join(items) if items else "")


def sort_results_for_report(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    order = {"memory": 0, "cpu": 1, "io": 2, "none": 3}
    return sorted(rows, key=lambda r: (order.get(str(r.get("scenario")), 99), str(r.get("source")), str(r.get("candidate_id"))))


def best_ok(rows: Iterable[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    ok = [row for row in rows if row.get("status") == "ok" and row.get("actual_score") is not None]
    if not ok:
        return None
    return max(ok, key=lambda row: float(row.get("actual_score") or -1e300))


def completed_validation_ids(rows: Sequence[Dict[str, Any]], *, retry_failed: bool) -> set:
    latest_by_id: Dict[Any, Dict[str, Any]] = {}
    for row in rows:
        cid = row.get("candidate_id")
        if not cid:
            continue
        latest_by_id[cid] = row
    if not retry_failed:
        return set(latest_by_id)
    return {
        cid
        for cid, row in latest_by_id.items()
        if row.get("status") == "ok" and row.get("actual_score") is not None
    }


def default_run_dir(candidates_path: Path) -> Path:
    name = candidates_path.name
    if "pending_validation_queue" in name or "validation_manifest" in name:
        return ROOT / "history/bonect_real_validation" / f"robust_top15_{now_tag()}"
    return ROOT / "history/bonect_real_validation" / f"top3_vs_result_bonect_{now_tag()}"


def _command_for_plan(parts: Sequence[Any]) -> str:
    return shlex.join(str(part) for part in parts if str(part))


def _validation_runtime_flags(args: argparse.Namespace) -> List[Any]:
    flags: List[Any] = []
    if args.pending_only:
        flags.append("--pending-only")
    if args.apply_vm_profile:
        flags.append("--apply-vm-profile")
    if args.restore_defaults:
        flags.append("--restore-defaults")
    if args.retry_failed:
        flags.append("--retry-failed")
    if args.force_vm_destroy:
        flags.append("--force-vm-destroy")
    if args.stop_on_failure:
        flags.append("--stop-on-failure")
    if args.wait_run_lock:
        flags.append("--wait-run-lock")
    if args.run_mins is not None:
        flags.extend(["--run-mins", args.run_mins])
    if args.workload_timeout_s is not None:
        flags.extend(["--workload-timeout-s", args.workload_timeout_s])
    if args.run_partial_evaluation_after_status:
        flags.append("--run-partial-evaluation-after-status")
    return flags


def _validation_command(
    *,
    candidates_path: Path,
    run_dir: Path,
    args: argparse.Namespace,
    scenarios: Optional[str] = None,
    status_candidates_path: Optional[Path] = None,
) -> str:
    parts: List[Any] = [
        "python3",
        "shell/bonect_real_validate_candidates.py",
        "--candidates-json",
        str(candidates_path),
        "--output-dir",
        str(run_dir),
        "--resume",
    ]
    if scenarios:
        parts.extend(["--scenarios", scenarios])
    if status_candidates_path is not None:
        parts.extend(["--status-candidates-json", str(status_candidates_path)])
    parts.extend(_validation_runtime_flags(args))
    return _command_for_plan(parts)


def _status_refresh_command(*, candidates_path: Path, run_dir: Path, args: argparse.Namespace) -> str:
    parts: List[Any] = [
        "python3",
        "shell/bonect_real_validate_candidates.py",
        "--candidates-json",
        str(candidates_path),
        "--output-dir",
        str(run_dir),
        "--pending-only",
        "--status-only",
    ]
    if args.apply_vm_profile:
        parts.append("--apply-vm-profile")
    if args.restore_defaults:
        parts.append("--restore-defaults")
    if args.retry_failed:
        parts.append("--retry-failed")
    if args.force_vm_destroy:
        parts.append("--force-vm-destroy")
    if args.stop_on_failure:
        parts.append("--stop-on-failure")
    if args.wait_run_lock:
        parts.append("--wait-run-lock")
    if args.run_mins is not None:
        parts.extend(["--run-mins", args.run_mins])
    if args.workload_timeout_s is not None:
        parts.extend(["--workload-timeout-s", args.workload_timeout_s])
    if args.scenario_batch_size is not None:
        parts.extend(["--scenario-batch-size", args.scenario_batch_size])
    if args.run_partial_evaluation_after_status:
        parts.append("--run-partial-evaluation-after-status")
    return _command_for_plan(parts)


def _evaluation_command(*, results_path: Path, strict_goal: bool, output_prefix: Optional[Path] = None) -> str:
    parts: List[Any] = [
        "python3",
        "shell/evaluate_bonect_estimator_ranking.py",
        "--extra-labels",
        str(results_path),
    ]
    if output_prefix is not None:
        parts.extend(["--output-prefix", str(output_prefix)])
    if strict_goal:
        parts.append("--strict-goal")
    return _command_for_plan(parts)


def _params_text(params: Dict[str, Any]) -> str:
    return ", ".join(f"{name}={fmt(params.get(name))}" for name in COST_PARAMS)


def write_scenario_batch_queues(
    *,
    run_dir: Path,
    candidates: Sequence[Dict[str, Any]],
    args: argparse.Namespace,
    prefix: str,
    batch_size: int,
    status_candidates_path: Path,
) -> Dict[str, Any]:
    queue_dir = run_dir / "scenario_queues"
    queue_dir.mkdir(parents=True, exist_ok=True)
    batches: Dict[str, Any] = {}
    normalized_batch_size = max(0, int(batch_size or 0))
    for scenario in SCENARIO_ORDER:
        rows = [dict(row) for row in candidates if row.get("scenario") == scenario]
        queue_path = queue_dir / f"{prefix}_{scenario}.jsonl"
        count = write_jsonl(queue_path, rows)
        chunks: List[Dict[str, Any]] = []
        if normalized_batch_size > 0:
            for start in range(0, len(rows), normalized_batch_size):
                chunk_rows = rows[start : start + normalized_batch_size]
                chunk_number = len(chunks) + 1
                chunk_path = queue_dir / f"{prefix}_{scenario}_part{chunk_number:02d}.jsonl"
                chunk_count = write_jsonl(chunk_path, chunk_rows)
                chunks.append(
                    {
                        "part": chunk_number,
                        "candidate_count": chunk_count,
                        "queue_jsonl": str(chunk_path),
                        "command": _validation_command(
                            candidates_path=chunk_path,
                            run_dir=run_dir,
                            args=args,
                            status_candidates_path=status_candidates_path,
                        ),
                    }
                )
        batches[scenario] = {
            "candidate_count": count,
            "queue_jsonl": str(queue_path),
            "command": _validation_command(
                candidates_path=queue_path,
                run_dir=run_dir,
                args=args,
                status_candidates_path=status_candidates_path,
            ),
            "chunk_size": normalized_batch_size,
            "chunks": chunks,
        }
    return {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "queue_dir": str(queue_dir),
        "prefix": prefix,
        "scenario_batch_size": normalized_batch_size,
        "batches": batches,
        "note": (
            "Scenario queues are offline validation aids. Their validation_results.jsonl output must only be "
            "passed back to the offline evaluator or a later ranking formula selection round."
        ),
    }


def write_scenario_batch_runbook(
    path: Path,
    payload: Dict[str, Any],
    *,
    refresh_status_command: str,
    post_evaluation_command: str,
    partial_evaluation_command: str = "",
) -> None:
    lines = [
        "# Bonect Scenario Validation Batches",
        "",
        f"- created_at: `{payload.get('created_at')}`",
        f"- queue_dir: `{payload.get('queue_dir')}`",
        f"- prefix: `{payload.get('prefix')}`",
        f"- scenario_batch_size: `{payload.get('scenario_batch_size')}`",
        "- These commands run offline real validation only; do not use produced labels as same-round online ranking inputs.",
        "- Run chunk commands sequentially. Real validation uses one shared VM/PostgreSQL target and enforces `validation_run.lock` per output directory.",
        "",
        "## Commands",
        "",
    ]
    for scenario, info in (payload.get("batches") or {}).items():
        lines.extend(
            [
                f"### {scenario}",
                "",
                f"- candidates: `{info.get('candidate_count')}`",
                f"- queue_jsonl: `{info.get('queue_jsonl')}`",
                "",
                "```bash",
                str(info.get("command") or ""),
                "```",
                "",
            ]
        )
        chunks = info.get("chunks") or []
        if chunks:
            lines.extend(["Chunk commands:", ""])
            for chunk in chunks:
                lines.extend(
                    [
                        f"- part `{chunk.get('part')}` candidates `{chunk.get('candidate_count')}`: `{chunk.get('queue_jsonl')}`",
                        "",
                        "```bash",
                        str(chunk.get("command") or ""),
                        "```",
                        "",
                    ]
                )
    lines.extend(
        [
            "## Refresh Status",
            "",
            "Run this after any chunk completes to rebuild remaining queues from `validation_results.jsonl`:",
            "",
            "```bash",
            refresh_status_command,
            "```",
            "",
            "## After Each Chunk",
            "",
            "Run this to recompute offline partial metrics without treating them as final goal proof:",
            "",
            "```bash",
            partial_evaluation_command or post_evaluation_command,
            "```",
            "",
            "## After Validation",
            "",
            "Run this only when status is complete; it fails until every robust top10/top15 row has a real label:",
            "",
            "```bash",
            post_evaluation_command,
            "```",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def next_validation_batch(scenario_batches: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    batches = scenario_batches.get("batches") or {}
    for scenario in SCENARIO_ORDER:
        batch = batches.get(scenario) or {}
        chunks = batch.get("chunks") or []
        for chunk in chunks:
            candidate_count = int(chunk.get("candidate_count") or 0)
            if candidate_count <= 0:
                continue
            return {
                "scenario": scenario,
                "part": chunk.get("part"),
                "candidate_count": candidate_count,
                "queue_jsonl": chunk.get("queue_jsonl"),
                "command": chunk.get("command"),
                "source": "chunk",
            }
        candidate_count = int(batch.get("candidate_count") or 0)
        if candidate_count > 0:
            return {
                "scenario": scenario,
                "part": None,
                "candidate_count": candidate_count,
                "queue_jsonl": batch.get("queue_jsonl"),
                "command": batch.get("command"),
                "source": "scenario",
            }
    return None


def build_validation_plan(
    *,
    run_dir: Path,
    candidates_path: Path,
    candidates: Sequence[Dict[str, Any]],
    contexts: Dict[str, Dict[str, Any]],
    args: argparse.Namespace,
) -> Dict[str, Any]:
    # Only plan scenarios that were actually constructed. Single-scenario workers
    # intentionally omit unused DEFAULT contexts after the context-isolation fix;
    # requiring all SCENARIO_ORDER keys here reintroduced KeyError/capability
    # pollution on --plan-only None/IO-only runs.
    plan_scenarios = [s for s in SCENARIO_ORDER if s in contexts]
    if not plan_scenarios:
        plan_scenarios = list(contexts.keys())
    per_scenario: Dict[str, Any] = {}
    for scenario in plan_scenarios:
        rows = [row for row in candidates if row.get("scenario") == scenario]
        context = contexts[scenario]
        config = context["config"]
        workload_cmd = str((config.get("benchmark") or {}).get("workload_cmd") or "")
        per_scenario[scenario] = {
            "candidate_count": len(rows),
            "config_path": context["config_path"],
            "capability": context["capability"],
            "vm_resource": context["vm_resource"],
            "workload_label": parse_workload_label(workload_cmd),
            "workload_cmd": workload_cmd,
            "command": _validation_command(
                candidates_path=candidates_path,
                run_dir=run_dir,
                args=args,
                scenarios=scenario,
            ),
            "candidates": [
                {
                    "candidate_id": row.get("candidate_id"),
                    "rank": row.get("rank"),
                    "robust_rank": row.get("robust_rank"),
                    "candidate_index": row.get("candidate_index"),
                    "source": row.get("source"),
                    "online_selected_variant": row.get("online_selected_variant"),
                    "estimated_score": row.get("estimated_score"),
                    "estimated_resource": row.get("estimated_resource"),
                    "params": row.get("params"),
                }
                for row in rows
            ],
        }

    results_path = run_dir / "validation_results.jsonl"
    partial_eval_prefix = run_dir / "partial_robust_ranking_evaluation"
    strict_eval_prefix = run_dir / "strict_robust_ranking_evaluation"
    return {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "purpose": (
            "Plan the next offline real-validation pass for robust ranking labels. "
            "The generated validation_results.jsonl is an offline evaluator input only."
        ),
        "run_dir": str(run_dir),
        "candidates_path": str(candidates_path),
        "results_jsonl": str(results_path),
        "candidate_count": len(candidates),
        "scenario_counts": {
            scenario: per_scenario[scenario]["candidate_count"] for scenario in plan_scenarios
        },
        "constructed_scenarios": plan_scenarios,
        "run_mins_override": args.run_mins,
        "workload_timeout_s": args.workload_timeout_s,
        "apply_vm_profile": bool(args.apply_vm_profile),
        "restore_defaults": bool(args.restore_defaults),
        "retry_failed": bool(args.retry_failed),
        "pending_only": bool(args.pending_only),
        "full_command": _validation_command(
            candidates_path=candidates_path,
            run_dir=run_dir,
            args=args,
        ),
        "status_refresh_command": _status_refresh_command(
            candidates_path=candidates_path,
            run_dir=run_dir,
            args=args,
        ),
        "per_scenario": per_scenario,
        "partial_evaluation_prefix": str(partial_eval_prefix),
        "strict_evaluation_prefix": str(strict_eval_prefix),
        "partial_evaluation_json": str(partial_eval_prefix.with_suffix(".json")),
        "partial_evaluation_md": str(partial_eval_prefix.with_suffix(".md")),
        "strict_evaluation_json": str(strict_eval_prefix.with_suffix(".json")),
        "strict_evaluation_md": str(strict_eval_prefix.with_suffix(".md")),
        "partial_evaluation_command": _evaluation_command(
            results_path=results_path,
            strict_goal=False,
            output_prefix=partial_eval_prefix,
        ),
        "strict_evaluation_command": _evaluation_command(
            results_path=results_path,
            strict_goal=True,
            output_prefix=strict_eval_prefix,
        ),
        "recommended_evaluation_command": _evaluation_command(
            results_path=results_path,
            strict_goal=False,
            output_prefix=partial_eval_prefix,
        ),
        "post_evaluation_command": _evaluation_command(
            results_path=results_path,
            strict_goal=True,
            output_prefix=strict_eval_prefix,
        ),
    }


def write_validation_plan_markdown(path: Path, plan: Dict[str, Any]) -> None:
    lines = [
        "# Bonect Robust Ranking Validation Plan",
        "",
        f"- created_at: `{plan.get('created_at')}`",
        f"- run_dir: `{plan.get('run_dir')}`",
        f"- candidates_path: `{plan.get('candidates_path')}`",
        f"- results_jsonl: `{plan.get('results_jsonl')}`",
        f"- candidate_count: `{plan.get('candidate_count')}`",
        f"- scenario_counts: `{plan.get('scenario_counts')}`",
        f"- apply_vm_profile: `{plan.get('apply_vm_profile')}`",
        f"- restore_defaults: `{plan.get('restore_defaults')}`",
        f"- retry_failed: `{plan.get('retry_failed')}`",
        f"- scenario_batch_runbook_md: `{plan.get('scenario_batch_runbook_md')}`",
        f"- validation_run_lock: `{Path(str(plan.get('run_dir'))) / 'validation_run.lock'}`",
        "- validation_results.jsonl is for the offline evaluator only; do not feed real labels into the same online ranking round.",
        "- Run chunk commands sequentially; the real runner rejects concurrent chunks in the same output directory unless `--wait-run-lock` is used.",
        "",
        "## Commands",
        "",
        "Full resumable run:",
        "",
        "```bash",
        str(plan.get("full_command") or ""),
        "```",
        "",
        "Scenario-sized queues:",
        "",
        f"`{plan.get('scenario_batch_runbook_md')}`",
        "",
        "Refresh status and remaining queues after any chunk:",
        "",
        "```bash",
        str(plan.get("status_refresh_command") or ""),
        "```",
        "",
        "After successful validation:",
        "",
        "```bash",
        str(plan.get("post_evaluation_command") or ""),
        "```",
        "",
        "## Per Scenario",
        "",
        "| scenario | candidates | config | workload | VM profile | command |",
        "| --- | ---: | --- | --- | --- | --- |",
    ]
    for scenario, info in (plan.get("per_scenario") or {}).items():
        vm = info.get("vm_resource") or {}
        vm_text = (
            f"cpu={vm.get('cpu')}, mem={vm.get('memory_gb')}G, "
            f"read/write={vm.get('read_mb')}/{vm.get('write_mb')} MB/s"
        )
        lines.append(
            f"| {scenario} | {info.get('candidate_count')} | `{Path(str(info.get('config_path'))).name}` | "
            f"{info.get('workload_label')} | {vm_text} | `{info.get('command')}` |"
        )
    lines.append("")
    for scenario, info in (plan.get("per_scenario") or {}).items():
        lines.append(f"## {scenario} Candidates")
        lines.append("")
        lines.append("| rank | robust_rank | candidate_index | candidate_id | params |")
        lines.append("| ---: | ---: | ---: | --- | --- |")
        for row in info.get("candidates") or []:
            lines.append(
                f"| {row.get('rank') or ''} | {row.get('robust_rank') or ''} | "
                f"{row.get('candidate_index') or ''} | `{row.get('candidate_id')}` | "
                f"`{_params_text(row.get('params') or {})}` |"
            )
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_validation_status(
    *,
    run_dir: Path,
    candidates_path: Path,
    candidates: Sequence[Dict[str, Any]],
    results: Sequence[Dict[str, Any]],
    args: Optional[argparse.Namespace] = None,
) -> Dict[str, Any]:
    latest_by_id: Dict[str, Dict[str, Any]] = {}
    for row in results:
        cid = str(row.get("candidate_id") or "")
        if cid:
            latest_by_id[cid] = dict(row)

    per_scenario: Dict[str, Any] = {}
    rows: List[Dict[str, Any]] = []
    for candidate in candidates:
        cid = str(candidate.get("candidate_id") or "")
        result = latest_by_id.get(cid)
        if result is None:
            status = "pending"
        elif result.get("status") == "ok" and result.get("actual_score") is not None:
            status = "ok"
        else:
            status = "failed"
        item = {
            "candidate_id": cid,
            "scenario": candidate.get("scenario"),
            "rank": candidate.get("rank"),
            "robust_rank": candidate.get("robust_rank"),
            "candidate_index": candidate.get("candidate_index"),
            "status": status,
            "actual_score": result.get("actual_score") if result else None,
            "error": result.get("error") if result else None,
            "params": candidate.get("params"),
        }
        rows.append(item)

    for scenario in SCENARIO_ORDER:
        group = [row for row in rows if row.get("scenario") == scenario]
        per_scenario[scenario] = {
            "total": len(group),
            "ok": sum(1 for row in group if row.get("status") == "ok"),
            "failed": sum(1 for row in group if row.get("status") == "failed"),
            "pending": sum(1 for row in group if row.get("status") == "pending"),
            "pending_candidate_ids": [row.get("candidate_id") for row in group if row.get("status") == "pending"],
            "failed_candidate_ids": [row.get("candidate_id") for row in group if row.get("status") == "failed"],
        }

    ok_count = sum(1 for row in rows if row.get("status") == "ok")
    failed_count = sum(1 for row in rows if row.get("status") == "failed")
    pending_count = sum(1 for row in rows if row.get("status") == "pending")
    complete = pending_count == 0 and failed_count == 0 and ok_count == len(candidates)
    results_path = run_dir / "validation_results.jsonl"
    partial_eval_prefix = run_dir / "partial_robust_ranking_evaluation"
    strict_eval_prefix = run_dir / "strict_robust_ranking_evaluation"
    remaining_queue_path = run_dir / "remaining_validation_queue.jsonl"
    failed_retry_queue_path = run_dir / "failed_retry_queue.jsonl"
    pending_only_queue_path = run_dir / "pending_only_queue.jsonl"
    if args is not None:
        resume_command = _validation_command(
            candidates_path=remaining_queue_path,
            run_dir=run_dir,
            args=args,
        )
    else:
        resume_command = _command_for_plan(
            [
                "python3",
                "shell/bonect_real_validate_candidates.py",
                "--candidates-json",
                str(remaining_queue_path),
                "--output-dir",
                str(run_dir),
                "--resume",
                "--pending-only",
            ]
        )

    return {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "purpose": (
            "Audit progress of an offline Bonect real-validation run. "
            "Completed real labels should be passed to the evaluator with --extra-labels only."
        ),
        "run_dir": str(run_dir),
        "candidates_path": str(candidates_path),
        "results_jsonl": str(results_path),
        "candidate_count": len(candidates),
        "result_record_count": len(results),
        "ok_count": ok_count,
        "failed_count": failed_count,
        "pending_count": pending_count,
        "remaining_count": failed_count + pending_count,
        "complete": complete,
        "retry_failed": bool(args.retry_failed) if args is not None else False,
        "remaining_validation_queue_jsonl": str(remaining_queue_path),
        "failed_retry_queue_jsonl": str(failed_retry_queue_path),
        "pending_only_queue_jsonl": str(pending_only_queue_path),
        "per_scenario": per_scenario,
        "rows": rows,
        "resume_command": resume_command,
        "status_refresh_command": _status_refresh_command(
            candidates_path=candidates_path,
            run_dir=run_dir,
            args=args,
        )
        if args is not None
        else "",
        "partial_evaluation_prefix": str(partial_eval_prefix),
        "strict_evaluation_prefix": str(strict_eval_prefix),
        "partial_evaluation_json": str(partial_eval_prefix.with_suffix(".json")),
        "partial_evaluation_md": str(partial_eval_prefix.with_suffix(".md")),
        "strict_evaluation_json": str(strict_eval_prefix.with_suffix(".json")),
        "strict_evaluation_md": str(strict_eval_prefix.with_suffix(".md")),
        "partial_evaluation_command": _evaluation_command(
            results_path=results_path,
            strict_goal=False,
            output_prefix=partial_eval_prefix,
        ),
        "strict_evaluation_command": _evaluation_command(
            results_path=results_path,
            strict_goal=True,
            output_prefix=strict_eval_prefix,
        ),
        "recommended_evaluation_command": _evaluation_command(
            results_path=results_path,
            strict_goal=complete,
            output_prefix=strict_eval_prefix if complete else partial_eval_prefix,
        ),
        "post_evaluation_command": _evaluation_command(
            results_path=results_path,
            strict_goal=True,
            output_prefix=strict_eval_prefix,
        ),
    }


def write_validation_status_queues(run_dir: Path, candidates: Sequence[Dict[str, Any]], status: Dict[str, Any]) -> Dict[str, int]:
    row_by_id = {str(row.get("candidate_id") or ""): row for row in status.get("rows") or []}
    remaining_rows: List[Dict[str, Any]] = []
    failed_rows: List[Dict[str, Any]] = []
    pending_rows: List[Dict[str, Any]] = []
    for candidate in candidates:
        cid = str(candidate.get("candidate_id") or "")
        status_row = row_by_id.get(cid) or {}
        validation_status = status_row.get("status") or "pending"
        item = dict(candidate)
        item["validation_status"] = validation_status
        item["needs_validation"] = validation_status != "ok"
        item["previous_actual_score"] = status_row.get("actual_score")
        item["previous_error"] = status_row.get("error")
        if validation_status == "ok":
            continue
        remaining_rows.append(item)
        if validation_status == "failed":
            failed_rows.append(item)
        elif validation_status == "pending":
            pending_rows.append(item)

    return {
        "remaining": write_jsonl(Path(str(status.get("remaining_validation_queue_jsonl"))), remaining_rows),
        "failed": write_jsonl(Path(str(status.get("failed_retry_queue_jsonl"))), failed_rows),
        "pending": write_jsonl(Path(str(status.get("pending_only_queue_jsonl"))), pending_rows),
    }


def evaluation_summary_from_report(path: Path) -> Dict[str, Any]:
    payload = read_json_if_exists(path)
    if not isinstance(payload, dict):
        return {}
    gate = payload.get("goal_gate") or {}
    completion = payload.get("completion_audit") or {}
    per_scenario: Dict[str, Any] = {}
    for scenario in SCENARIO_ORDER:
        info = ((gate.get("per_scenario") or {}).get(scenario) or {})
        per_scenario[scenario] = {
            "partial_pass": info.get("partial_pass"),
            "full_pass": info.get("full_pass"),
            "coverage_pass": info.get("coverage_pass"),
            "top10_labeled_items": info.get("top10_labeled_items"),
            "top15_labeled_items": info.get("top15_labeled_items"),
            "top10_pairwise_inversion_rate": info.get("top10_pairwise_inversion_rate"),
            "top15_pairwise_inversion_rate": info.get("top15_pairwise_inversion_rate"),
            "reasons": info.get("reasons") or [],
        }
    return {
        "report_json": str(path),
        "generated_at": payload.get("generated_at"),
        "online_selected_variant": payload.get("online_selected_variant"),
        "threshold": gate.get("threshold"),
        "partial_pass": gate.get("partial_pass"),
        "full_pass": gate.get("full_pass"),
        "strict_goal_pass": gate.get("strict_goal_pass"),
        "label_conflict_pass": gate.get("label_conflict_pass"),
        "overall_complete": completion.get("overall_complete"),
        "pending_validation_count": completion.get("pending_validation_count"),
        "per_scenario": per_scenario,
    }


def attach_evaluation_summaries(status: Dict[str, Any]) -> None:
    partial_path_text = str(status.get("partial_evaluation_json") or "")
    strict_path_text = str(status.get("strict_evaluation_json") or "")
    partial_summary = evaluation_summary_from_report(Path(partial_path_text)) if partial_path_text else {}
    strict_summary = evaluation_summary_from_report(Path(strict_path_text)) if strict_path_text else {}
    if partial_summary:
        status["partial_evaluation_summary"] = partial_summary
    if strict_summary:
        status["strict_evaluation_summary"] = strict_summary
    if status.get("complete") and strict_summary:
        status["active_evaluation_summary"] = strict_summary
    elif partial_summary:
        status["active_evaluation_summary"] = partial_summary


def remaining_validation_candidates(candidates: Sequence[Dict[str, Any]], status: Dict[str, Any]) -> List[Dict[str, Any]]:
    row_by_id = {str(row.get("candidate_id") or ""): row for row in status.get("rows") or []}
    remaining: List[Dict[str, Any]] = []
    for candidate in candidates:
        cid = str(candidate.get("candidate_id") or "")
        status_row = row_by_id.get(cid) or {}
        validation_status = status_row.get("status") or "pending"
        if validation_status == "ok":
            continue
        item = dict(candidate)
        item["validation_status"] = validation_status
        item["needs_validation"] = True
        item["previous_actual_score"] = status_row.get("actual_score")
        item["previous_error"] = status_row.get("error")
        remaining.append(item)
    return remaining


def write_validation_status_markdown(path: Path, status: Dict[str, Any]) -> None:
    lines = [
        "# Bonect Robust Ranking Validation Status",
        "",
        f"- created_at: `{status.get('created_at')}`",
        f"- run_dir: `{status.get('run_dir')}`",
        f"- candidates_path: `{status.get('candidates_path')}`",
        f"- results_jsonl: `{status.get('results_jsonl')}`",
        f"- candidate_count: `{status.get('candidate_count')}`",
        f"- result_record_count: `{status.get('result_record_count')}`",
        f"- ok_count: `{status.get('ok_count')}`",
        f"- failed_count: `{status.get('failed_count')}`",
        f"- pending_count: `{status.get('pending_count')}`",
        f"- complete: `{status.get('complete')}`",
        f"- retry_failed: `{status.get('retry_failed')}`",
        f"- remaining_validation_queue_jsonl: `{status.get('remaining_validation_queue_jsonl')}`",
        f"- failed_retry_queue_jsonl: `{status.get('failed_retry_queue_jsonl')}`",
        f"- pending_only_queue_jsonl: `{status.get('pending_only_queue_jsonl')}`",
        f"- status_queue_counts: `{status.get('status_queue_counts')}`",
        f"- scenario_batch_runbook_md: `{status.get('scenario_batch_runbook_md')}`",
        f"- partial_evaluation_json: `{status.get('partial_evaluation_json')}`",
        f"- partial_evaluation_md: `{status.get('partial_evaluation_md')}`",
        f"- strict_evaluation_json: `{status.get('strict_evaluation_json')}`",
        f"- strict_evaluation_md: `{status.get('strict_evaluation_md')}`",
        f"- validation_run_lock: `{Path(str(status.get('run_dir'))) / 'validation_run.lock'}`",
        "- Status rows track offline real validations only; they are not online ranking inputs.",
        "- Run chunk commands sequentially; the real runner rejects concurrent chunks in the same output directory unless `--wait-run-lock` is used.",
        "",
        "## Commands",
        "",
        "Dry-run the queue orchestrator:",
        "",
        "```bash",
        str(status.get("queue_orchestrator_dry_run_command") or ""),
        "```",
        "",
        "Execute the next queue batch:",
        "",
        "```bash",
        str(status.get("queue_orchestrator_execute_next_command") or ""),
        "```",
        "",
        "Next chunk:",
        "",
        "```bash",
        str(status.get("next_validation_command") or "# no remaining validation batch"),
        "```",
        "",
        "Resume unfinished validation from the remaining queue:",
        "",
        "```bash",
        str(status.get("resume_command") or ""),
        "```",
        "",
        "Run scenario-sized remaining queues:",
        "",
        f"`{status.get('scenario_batch_runbook_md')}`",
        "",
        "Refresh status and remaining queues after any chunk:",
        "",
        "```bash",
        str(status.get("status_refresh_command") or ""),
        "```",
        "",
        "Recompute offline partial ranking metrics after any chunk:",
        "",
        "```bash",
        str(status.get("partial_evaluation_command") or ""),
        "```",
        "",
        "Recommended evaluation command for the current status:",
        "",
        "```bash",
        str(status.get("recommended_evaluation_command") or ""),
        "```",
        "",
        "Recompute strict robust-ranking evaluation after validation completes:",
        "",
        "```bash",
        str(status.get("strict_evaluation_command") or status.get("post_evaluation_command") or ""),
        "```",
        "",
    ]
    summary = status.get("active_evaluation_summary") or status.get("partial_evaluation_summary") or {}
    if summary:
        lines.extend(
            [
                "## Current Offline Evaluation Gate",
                "",
                f"- report_json: `{summary.get('report_json')}`",
                f"- generated_at: `{summary.get('generated_at')}`",
                f"- online_selected_variant: `{summary.get('online_selected_variant')}`",
                f"- threshold: `{summary.get('threshold')}`",
                f"- partial_pass: `{summary.get('partial_pass')}`",
                f"- full_pass: `{summary.get('full_pass')}`",
                f"- strict_goal_pass: `{summary.get('strict_goal_pass')}`",
                f"- label_conflict_pass: `{summary.get('label_conflict_pass')}`",
                f"- overall_complete: `{summary.get('overall_complete')}`",
                f"- pending_validation_count: `{summary.get('pending_validation_count')}`",
                "",
                "| scenario | partial_pass | full_pass | coverage_pass | top10_labeled | top15_labeled | top10_inv | top15_inv | reasons |",
                "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- |",
            ]
        )
        for scenario in SCENARIO_ORDER:
            info = ((summary.get("per_scenario") or {}).get(scenario) or {})
            reasons = "; ".join(str(item) for item in (info.get("reasons") or []))
            lines.append(
                f"| {scenario} | {info.get('partial_pass')} | {info.get('full_pass')} | "
                f"{info.get('coverage_pass')} | {info.get('top10_labeled_items')} | "
                f"{info.get('top15_labeled_items')} | {fmt(info.get('top10_pairwise_inversion_rate'))} | "
                f"{fmt(info.get('top15_pairwise_inversion_rate'))} | `{reasons}` |"
            )
        lines.append("")
    lines.extend(
        [
            "## Last Automatic Partial Evaluation",
            "",
            f"`{status.get('post_status_partial_evaluation') or {}}`",
            "",
            "## Per Scenario",
            "",
            "| scenario | total | ok | failed | pending |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for scenario, info in (status.get("per_scenario") or {}).items():
        lines.append(
            f"| {scenario} | {info.get('total')} | {info.get('ok')} | "
            f"{info.get('failed')} | {info.get('pending')} |"
        )
    lines.extend(
        [
            "",
            "## Pending / Failed",
            "",
            "| scenario | status | robust_rank | candidate_index | candidate_id |",
            "| --- | --- | ---: | ---: | --- |",
        ]
    )
    for row in status.get("rows") or []:
        if row.get("status") == "ok":
            continue
        lines.append(
            f"| {row.get('scenario')} | {row.get('status')} | {row.get('robust_rank') or ''} | "
            f"{row.get('candidate_index') or ''} | `{row.get('candidate_id')}` |"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_validation_status_artifacts(
    *,
    run_dir: Path,
    candidates_path: Path,
    candidates: Sequence[Dict[str, Any]],
    results: Sequence[Dict[str, Any]],
    args: argparse.Namespace,
) -> Dict[str, Any]:
    status = build_validation_status(
        run_dir=run_dir,
        candidates_path=candidates_path,
        candidates=candidates,
        results=results,
        args=args,
    )
    status_json_path = run_dir / "validation_status.json"
    status_md_path = run_dir / "validation_status.md"
    queue_counts = write_validation_status_queues(run_dir, candidates, status)
    status["status_queue_counts"] = queue_counts
    scenario_batches = write_scenario_batch_queues(
        run_dir=run_dir,
        candidates=remaining_validation_candidates(candidates, status),
        args=args,
        prefix="remaining",
        batch_size=args.scenario_batch_size,
        status_candidates_path=candidates_path,
    )
    scenario_runbook_path = run_dir / "scenario_batches.md"
    write_scenario_batch_runbook(
        scenario_runbook_path,
        scenario_batches,
        refresh_status_command=str(status.get("status_refresh_command") or ""),
        post_evaluation_command=str(status.get("post_evaluation_command") or ""),
        partial_evaluation_command=str(status.get("partial_evaluation_command") or ""),
    )
    status["scenario_batches"] = scenario_batches
    status["scenario_batch_runbook_md"] = str(scenario_runbook_path)
    next_batch = next_validation_batch(scenario_batches)
    status["next_validation_batch"] = next_batch
    status["next_validation_command"] = (next_batch or {}).get("command")
    status["queue_orchestrator_dry_run_command"] = _command_for_plan(
        [
            "python3",
            "shell/bonect_validation_queue_orchestrator.py",
            "--status-json",
            str(status_json_path),
            "--output-json",
            str(run_dir / "validation_queue_orchestrator_last.json"),
        ]
    )
    status["queue_orchestrator_execute_next_command"] = _command_for_plan(
        [
            "python3",
            "shell/bonect_validation_queue_orchestrator.py",
            "--status-json",
            str(status_json_path),
            "--max-batches",
            1,
            "--execute",
            "--strict-after-complete",
            "--output-json",
            str(run_dir / "validation_queue_orchestrator_last.json"),
        ]
    )
    attach_evaluation_summaries(status)
    write_json(status_json_path, status)
    write_validation_status_markdown(status_md_path, status)
    latest_index_path = update_latest_validation_plan_index(
        run_dir=run_dir,
        candidates_path=candidates_path,
        status_json_path=status_json_path,
        status_md_path=status_md_path,
    )
    status["latest_validation_plan_index_json"] = str(latest_index_path)
    return status


def run_partial_evaluation_after_status(status: Dict[str, Any]) -> Dict[str, Any]:
    command = str(status.get("partial_evaluation_command") or "")
    started_at = time.strftime("%Y-%m-%d %H:%M:%S %Z")
    result: Dict[str, Any] = {
        "started_at": started_at,
        "command": command,
        "output_prefix": status.get("partial_evaluation_prefix"),
        "report_json": status.get("partial_evaluation_json"),
        "report_md": status.get("partial_evaluation_md"),
    }
    if not command:
        result.update({"status": "skipped", "reason": "missing partial_evaluation_command"})
        return result
    proc = run_command(shlex.split(command), check=False)
    result.update(
        {
            "finished_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
            "status": "ok" if proc.returncode == 0 else "failed",
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }
    )
    return result


def generate_report(
    path: Path,
    *,
    run_dir: Path,
    candidates_path: Path,
    results: List[Dict[str, Any]],
    contexts: Dict[str, Dict[str, Any]],
    run_mins: Optional[float],
) -> None:
    lines: List[str] = []
    lines.append("# Bonect ")
    lines.append("")
    lines.append(f"- `{time.strftime('%Y-%m-%d %H:%M:%S %Z')}`")
    lines.append(f"- `{run_dir}`")
    lines.append(f"- `{candidates_path}`")
    lines.append("-  `validation_results.jsonl`  `shell/evaluate_bonect_estimator_ranking.py --extra-labels` ")
    lines.append(f"- workload `{'config ' if run_mins is None else str(run_mins) + ' min override'}`")
    ok_count = sum(1 for row in results if row.get("status") == "ok")
    fail_count = len(results) - ok_count
    lines.append(f"- `{len(results)}` `{ok_count}` `{fail_count}`")
    lines.append("")
    lines.append("## Description")
    lines.append("")
    lines.append("-  estimator top3/result  robust top-k pending validation queue")
    lines.append("- robust pending queue  manifest ranking ")
    lines.append("-  estimator top3memory/io  `mem_io_orderwide_20260624_170900`cpu/none  `all_grid5_20260624_152314`")
    lines.append("-  result/bonect  `result/bonect/{cpu,mem,io}.md`  `optimized_params`  `result/bonect/none.md`none  PostgreSQL  cost  fallback")
    lines.append("- actual_resource CPU=used coresmemory=GBio/io_read/io_write=MB/sutilization  actual_resource / capability ")
    lines.append("- none  `result/bonect`  none  CPU config workload  no-bottleneck VM profile Bonect none ")
    lines.append("")

    lines.append("## ")
    lines.append("")
    lines.append("|  | config |  | VM profile | capability(cpu/memory/io) |")
    lines.append("| --- | --- | --- | --- | --- |")
    report_scenarios = [s for s in ["memory", "cpu", "io", "none"] if s in contexts]
    if not report_scenarios:
        report_scenarios = list(contexts.keys())
    for scenario in report_scenarios:
        ctx = contexts[scenario]
        cfg = ctx["config"]
        cmd = str((cfg.get("benchmark") or {}).get("workload_cmd") or "")
        vm = ctx["vm_resource"]
        cap = ctx["capability"]
        lines.append(
            "| {scenario} | `{config}` | {workload} | cpu={cpu}, mem={mem}G, read/write={read}/{write} MB/s | {cap_cpu}/{cap_mem}/{cap_io} |".format(
                scenario=scenario,
                config=Path(ctx["config_path"]).name,
                workload=parse_workload_label(cmd),
                cpu=vm.get("cpu"),
                mem=vm.get("memory_gb"),
                read=vm.get("read_mb"),
                write=vm.get("write_mb"),
                cap_cpu=fmt(cap.get("cpu")),
                cap_mem=fmt(cap.get("memory")),
                cap_io=fmt(cap.get("io")),
            )
        )
    lines.append("")

    for scenario in report_scenarios:
        group = [row for row in results if row.get("scenario") == scenario]
        lines.append(f"## {scenario} ")
        lines.append("")
        winner = best_ok(group)
        if winner:
            lines.append(
                f"- actual score `{winner['candidate_id']}`score `{fmt(winner.get('actual_score'))}`"
                f"CPU `{fmt((winner.get('actual_resource') or {}).get('cpu'))}`"
                f"memory `{fmt((winner.get('actual_resource') or {}).get('memory'))}`"
                f"IO `{fmt((winner.get('actual_resource') or {}).get('io'))}`"
            )
        else:
            lines.append("- ")
        lines.append("")
        lines.append(
            "| candidate | source | status | est_score | actual_score | cpu | mem_GB | io_MBps | cpu% | mem% | io% | db_qps | db_xact_tps |"
        )
        lines.append("| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
        for row in sorted(group, key=lambda r: str(r.get("candidate_id"))):
            res = row.get("actual_resource") or {}
            util = row.get("actual_utilization_pct") or {}
            delta = row.get("db_delta") or {}
            lines.append(
                "| `{cid}` | {source} | {status} | {est_score} | {actual_score} | {cpu} | {mem} | {io} | {cpu_pct} | {mem_pct} | {io_pct} | {db_qps} | {db_xact} |".format(
                    cid=row.get("candidate_id"),
                    source=row.get("source"),
                    status=row.get("status"),
                    est_score=fmt(row.get("estimated_score")),
                    actual_score=fmt(row.get("actual_score")),
                    cpu=fmt(res.get("cpu")),
                    mem=fmt(res.get("memory")),
                    io=fmt(res.get("io")),
                    cpu_pct=fmt(util.get("cpu")),
                    mem_pct=fmt(util.get("memory")),
                    io_pct=fmt(util.get("io")),
                    db_qps=fmt(delta.get("db_qps")),
                    db_xact=fmt(delta.get("db_xact_tps")),
                )
            )
        lines.append("")
        lines.append("### ")
        lines.append("")
        lines.append(
            "| candidate | seq | random | cpu_tuple | cpu_index | cpu_op | parallel_setup | parallel_tuple | cpu | mem_GB | io_MBps |"
        )
        lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
        for row in sorted(group, key=lambda r: str(r.get("candidate_id"))):
            params = row.get("params") or {}
            res = row.get("actual_resource") or {}
            lines.append(
                "| `{cid}` | {seq} | {random} | {cpu_tuple} | {cpu_index} | {cpu_op} | {setup} | {tuple_cost} | {cpu} | {mem} | {io} |".format(
                    cid=row.get("candidate_id"),
                    seq=fmt(params.get("seq_page_cost")),
                    random=fmt(params.get("random_page_cost")),
                    cpu_tuple=fmt(params.get("cpu_tuple_cost")),
                    cpu_index=fmt(params.get("cpu_index_tuple_cost")),
                    cpu_op=fmt(params.get("cpu_operator_cost")),
                    setup=fmt(params.get("parallel_setup_cost")),
                    tuple_cost=fmt(params.get("parallel_tuple_cost")),
                    cpu=fmt(res.get("cpu")),
                    mem=fmt(res.get("memory")),
                    io=fmt(res.get("io")),
                )
            )
        failures = [row for row in group if row.get("status") != "ok"]
        if failures:
            lines.append("")
            lines.append("### ")
            lines.append("")
            for row in failures:
                lines.append(f"- `{row.get('candidate_id')}`: `{row.get('error')}`")
        lines.append("")

    lines.append("## ")
    lines.append("")
    for scenario in ["memory", "cpu", "io", "none"]:
        group = [row for row in results if row.get("scenario") == scenario]
        top_best = best_ok(row for row in group if row.get("source") == "estimator_top3")
        result_best = best_ok(row for row in group if str(row.get("source", "")).startswith("result_bonect"))
        if top_best and result_best:
            delta = float(top_best.get("actual_score") or 0.0) - float(result_best.get("actual_score") or 0.0)
            lines.append(
                f"- {scenario}: estimator top3  `{top_best['candidate_id']}` actual_score `{fmt(top_best.get('actual_score'))}`"
                f"result/bonect  `{result_best['candidate_id']}` actual_score `{fmt(result_best.get('actual_score'))}` `{fmt(delta)}`"
            )
        elif top_best:
            lines.append(f"- {scenario}: estimator top3  `{top_best['candidate_id']}` actual_score `{fmt(top_best.get('actual_score'))}` result/bonect ")
        elif result_best:
            lines.append(f"- {scenario}: result/bonect  `{result_best['candidate_id']}` actual_score `{fmt(result_best.get('actual_score'))}`estimator top3 ")
        else:
            lines.append(f"- {scenario}: ")
    lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_validation(args: argparse.Namespace) -> int:
    input_candidates_path = Path(args.candidates_json) if args.candidates_json else Path("")
    run_dir = Path(args.output_dir) if args.output_dir else default_run_dir(input_candidates_path)
    run_dir.mkdir(parents=True, exist_ok=True)
    candidates_path = input_candidates_path if args.candidates_json else run_dir / "candidates.json"
    if args.generate_candidates or not candidates_path.exists():
        candidates = make_candidate_manifest(candidates_path)
    else:
        candidates = load_candidates(candidates_path)
    if args.pending_only:
        candidates = [row for row in candidates if bool(row.get("needs_validation", True))]
    if args.candidates_only:
        print(f"wrote candidates: {candidates_path} count={len(candidates)}", flush=True)
        return 0

    scenario_filter = set(args.scenarios.split(",")) if args.scenarios else None
    if scenario_filter:
        candidates = [row for row in candidates if row.get("scenario") in scenario_filter]
    scenario_order = {"memory": 0, "cpu": 1, "io": 2, "none": 3}
    source_order = {
        "parallel_worker_baseline": -2,
        "parallel_worker_anchor": -1,
        "estimator_top3": 0,
        "result_bonect_best": 1,
        "result_bonect_missing_default": 1,
    }
    candidates = sorted(
        candidates,
        key=lambda row: (
            scenario_order.get(str(row.get("scenario")), 99),
            source_order.get(str(row.get("source")), 99),
            int(row.get("rank") or 99),
            str(row.get("candidate_id")),
        ),
    )
    if args.limit:
        candidates = candidates[: int(args.limit)]
    status_candidates_path = Path(args.status_candidates_json) if args.status_candidates_json else candidates_path
    if status_candidates_path != candidates_path:
        status_candidates = load_candidates(status_candidates_path)
        if args.pending_only:
            status_candidates = [row for row in status_candidates if bool(row.get("needs_validation", True))]
        status_candidates = sorted(
            status_candidates,
            key=lambda row: (
                scenario_order.get(str(row.get("scenario")), 99),
                source_order.get(str(row.get("source")), 99),
                int(row.get("rank") or 99),
                str(row.get("candidate_id")),
            ),
        )
    else:
        status_candidates = candidates

    results_path = run_dir / "validation_results.jsonl"
    done_ids = completed_validation_ids(load_jsonl(results_path), retry_failed=bool(args.retry_failed)) if args.resume else set()
    all_results = load_jsonl(results_path) if args.resume else []

    if args.status_only:
        status = write_validation_status_artifacts(
            run_dir=run_dir,
            candidates_path=status_candidates_path,
            candidates=status_candidates,
            results=load_jsonl(results_path),
            args=args,
        )
        if args.run_partial_evaluation_after_status:
            eval_result = run_partial_evaluation_after_status(status)
            status["post_status_partial_evaluation"] = eval_result
            attach_evaluation_summaries(status)
            write_json(run_dir / "validation_status.json", status)
            write_validation_status_markdown(run_dir / "validation_status.md", status)
            print(
                f"ran partial evaluation after status: {eval_result.get('status')} "
                f"{eval_result.get('report_json')}",
                flush=True,
            )
        print(f"updated latest validation plan index: {status.get('latest_validation_plan_index_json')}", flush=True)
        print(f"wrote validation status: {run_dir / 'validation_status.json'}, {run_dir / 'validation_status.md'}", flush=True)
        return 0

    worker_config = load_worker_config(args.worker_config_json)
    # Build contexts only for scenarios that will actually run. Materialized
    # worker configs keep a single scenario_configs entry, and DEFAULT_CONFIGS
    # capability totals often disagree with directional read/write limits. The
    # post-0942 fail-closed sum check therefore cannot safely construct all four
    # DEFAULT contexts when only one owned worker scenario is active.
    needed_scenarios = set()
    if scenario_filter:
        needed_scenarios |= {str(s).strip() for s in scenario_filter if str(s).strip()}
    for row in candidates:
        scen = str(row.get("scenario") or "").strip()
        if scen:
            needed_scenarios.add(scen)
    if not needed_scenarios:
        needed_scenarios = {"memory", "cpu", "io", "none"}
    contexts = {
        scenario: scenario_context(scenario, worker_config)
        for scenario in ["memory", "cpu", "io", "none"]
        if scenario in needed_scenarios
    }
    if args.plan_only:
        plan = build_validation_plan(
            run_dir=run_dir,
            candidates_path=candidates_path,
            candidates=candidates,
            contexts=contexts,
            args=args,
        )
        plan_json_path = run_dir / "validation_plan.json"
        plan_md_path = run_dir / "validation_plan.md"
        scenario_batches = write_scenario_batch_queues(
            run_dir=run_dir,
            candidates=candidates,
            args=args,
            prefix="planned",
            batch_size=args.scenario_batch_size,
            status_candidates_path=status_candidates_path,
        )
        scenario_runbook_path = run_dir / "scenario_batches.md"
        write_scenario_batch_runbook(
            scenario_runbook_path,
            scenario_batches,
            refresh_status_command=str(plan.get("status_refresh_command") or ""),
            post_evaluation_command=str(plan.get("post_evaluation_command") or ""),
            partial_evaluation_command=str(plan.get("partial_evaluation_command") or ""),
        )
        plan["scenario_batches"] = scenario_batches
        plan["scenario_batch_runbook_md"] = str(scenario_runbook_path)
        write_json(plan_json_path, plan)
        write_validation_plan_markdown(plan_md_path, plan)
        latest_index_path = update_latest_validation_plan_index(
            run_dir=run_dir,
            candidates_path=candidates_path,
            plan_json_path=plan_json_path,
            plan_md_path=plan_md_path,
        )
        print(f"updated latest validation plan index: {latest_index_path}", flush=True)
        print(f"wrote validation plan: {plan_json_path}, {plan_md_path}", flush=True)
        return 0

    run_lock = acquire_validation_run_lock(run_dir / "validation_run.lock", wait=bool(args.wait_run_lock))
    try:
        write_json(
            run_dir / "validation_run_config.json",
            {
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
                "run_dir": str(run_dir),
                "candidates_json": str(candidates_path),
                "status_candidates_json": str(status_candidates_path),
                "run_mins_override": args.run_mins,
                "workload_timeout_s": args.workload_timeout_s,
                "apply_vm_profile": args.apply_vm_profile,
                "retry_failed": args.retry_failed,
                "wait_run_lock": args.wait_run_lock,
                "run_lock_path": str(run_dir / "validation_run.lock"),
                "scenarios": args.scenarios,
                "limit": args.limit,
                "worker_config_json": args.worker_config_json,
                "worker": {
                    "worker_id": worker_config.get("worker_id"),
                    "vm_name": worker_config.get("vm_name"),
                    "disk_isolation_id": worker_config.get("disk_isolation_id"),
                    "disk_isolation_verified": bool(
                        worker_config.get("disk_isolation_verified", False)
                    ),
                },
                "contexts": {
                    key: {
                        "config_path": value["config_path"],
                        "capability": value["capability"],
                        "vm_resource": value["vm_resource"],
                        "workload_cmd": (value["config"].get("benchmark") or {}).get("workload_cmd"),
                        "fixed_params": value["config"].get("fixed_params"),
                    }
                    for key, value in contexts.items()
                },
            },
        )

        current_scenario: Optional[str] = None
        exit_code = 0
        try:
            for candidate in candidates:
                cid = str(candidate["candidate_id"])
                scenario = str(candidate["scenario"])
                if cid in done_ids:
                    print(f"[resume] skip {cid}", flush=True)
                    continue
                context = contexts[scenario]
                if args.apply_vm_profile and scenario != current_scenario:
                    current_scenario = scenario
                    print(f"[vm] applying scenario={scenario}", flush=True)
                    apply_vm_profile(
                        context["vm_resource"],
                        force_destroy=args.force_vm_destroy,
                        log_path=run_dir / "vm_profile.log",
                    )
                    wait_for_ssh(context["vm_runtime"], timeout_s=float(args.vm_ssh_timeout_s))
                    start_postgres_and_wait(context["pg"], context["vm_runtime"], timeout_s=float(args.pg_ready_timeout_s))
                result = validate_candidate(
                    candidate,
                    context,
                    run_mins=args.run_mins,
                    workload_timeout_s=args.workload_timeout_s,
                )
                append_jsonl(results_path, result)
                all_results.append(result)
                if result.get("status") != "ok":
                    exit_code = 1
                    if args.stop_on_failure:
                        break
        finally:
            if args.restore_defaults:
                for scenario in list(contexts.keys()) or ["memory", "cpu", "io", "none"]:
                    try:
                        restore_default_pg_params(contexts[scenario]["pg"])
                        break
                    except Exception as exc:
                        print(f"[restore] failed via scenario={scenario}: {exc}", flush=True)

        all_results = load_jsonl(results_path)
        summary = {
            "run_dir": str(run_dir),
            "candidates_json": str(candidates_path),
            "total_results": len(all_results),
            "ok_count": sum(1 for row in all_results if row.get("status") == "ok"),
            "failure_count": sum(1 for row in all_results if row.get("status") != "ok"),
            "scenario_counts": {},
            "report_path": str(run_dir / "real_validation_report.md"),
        }
        for scenario in ["memory", "cpu", "io", "none"]:
            group = [row for row in all_results if row.get("scenario") == scenario]
            summary["scenario_counts"][scenario] = {
                "total": len(group),
                "ok": sum(1 for row in group if row.get("status") == "ok"),
                "failed": sum(1 for row in group if row.get("status") != "ok"),
            }
        write_json(run_dir / "validation_summary.json", summary)
        generate_report(
            run_dir / "real_validation_report.md",
            run_dir=run_dir,
            candidates_path=candidates_path,
            results=all_results,
            contexts=contexts,
            run_mins=args.run_mins,
        )
        status = write_validation_status_artifacts(
            run_dir=run_dir,
            candidates_path=status_candidates_path,
            candidates=status_candidates,
            results=all_results,
            args=args,
        )
        if args.run_partial_evaluation_after_status:
            eval_result = run_partial_evaluation_after_status(status)
            status["post_status_partial_evaluation"] = eval_result
            attach_evaluation_summaries(status)
            write_json(run_dir / "validation_status.json", status)
            write_validation_status_markdown(run_dir / "validation_status.md", status)
            print(
                f"ran partial evaluation after status: {eval_result.get('status')} "
                f"{eval_result.get('report_json')}",
                flush=True,
            )
        print(
            f"wrote validation status after run: {run_dir / 'validation_status.json'} "
            f"(remaining={status.get('remaining_count')})",
            flush=True,
        )
        return exit_code
    finally:
        release_validation_run_lock(run_lock)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Real-validate Bonect candidates. The output validation_results.jsonl can be passed "
            "to shell/evaluate_bonect_estimator_ranking.py --extra-labels."
        )
    )
    parser.add_argument(
        "--candidates-json",
        default="",
        help="Candidate JSON, validation manifest JSON, or pending validation queue JSONL.",
    )
    parser.add_argument(
        "--status-candidates-json",
        default="",
        help=(
            "Candidate JSON/JSONL used when refreshing validation_status after a chunk run. "
            "Defaults to --candidates-json."
        ),
    )
    parser.add_argument("--output-dir", default="")
    parser.add_argument(
        "--worker-config-json",
        default="",
        help=(
            "Optional isolated-worker config with scenario_configs and nested "
            "config_override/scenario_overrides for PG, SSH, workload, and VM name."
        ),
    )
    parser.add_argument("--generate-candidates", action="store_true")
    parser.add_argument("--candidates-only", action="store_true")
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Write validation_plan.json/md and exit without applying VM, PostgreSQL params, or workloads.",
    )
    parser.add_argument(
        "--status-only",
        action="store_true",
        help="Write validation_status.json/md from existing validation_results.jsonl and exit without VM/PG/workloads.",
    )
    parser.add_argument(
        "--pending-only",
        action="store_true",
        help="When a manifest contains known and pending candidates, validate only needs_validation=true rows.",
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="With --resume, retry rows whose latest result is failed instead of treating them as completed.",
    )
    parser.add_argument("--scenarios", default="", help="Comma-separated scenario filter, e.g. memory,cpu.")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--scenario-batch-size",
        type=int,
        default=3,
        help="Plan/status artifact chunk size for per-scenario validation queues; use 0 to disable chunk files.",
    )
    parser.add_argument("--run-mins", type=float, default=None, help="Optional workload run-min override for smoke tests.")
    parser.add_argument("--workload-timeout-s", type=float, default=900.0)
    parser.add_argument("--apply-vm-profile", action="store_true")
    parser.add_argument("--force-vm-destroy", action="store_true")
    parser.add_argument(
        "--wait-run-lock",
        action="store_true",
        help="Wait for another validation process in the same output dir instead of failing fast.",
    )
    parser.add_argument(
        "--run-partial-evaluation-after-status",
        action="store_true",
        help=(
            "After writing validation_status, run the non-strict offline evaluator with the current "
            "validation_results.jsonl and record the result in validation_status.json."
        ),
    )
    parser.add_argument("--vm-ssh-timeout-s", type=float, default=300.0)
    parser.add_argument("--pg-ready-timeout-s", type=float, default=300.0)
    parser.add_argument("--restore-defaults", action="store_true")
    parser.add_argument("--stop-on-failure", action="store_true")
    return run_validation(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())

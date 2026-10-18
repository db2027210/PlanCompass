#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import Counter
import concurrent.futures
import copy
import hashlib
import itertools
import json
import math
import multiprocessing
import os
from pathlib import Path
import re
import signal
import shlex
import subprocess
import sys
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
import uuid


# Candidate-level processes provide the parallelism. Prevent each model call
# from creating hundreds of BLAS/OpenMP threads and collapsing into futex
# contention on large hosts.
_MODEL_THREADS = str(max(1, int(os.getenv("BONECT_ESTIMATOR_MODEL_THREADS", "1") or "1")))
for _thread_env in (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
):
    os.environ[_thread_env] = _MODEL_THREADS


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plan_resource_estimator import COST_PARAM_SET, PlanResourceEstimator, _is_plannable_query, _safe_float, _safe_int
from resource_utility import ResourceUtilityCalculator
from bonect_robust_ranking import (
    DEFAULT_VARIANT as DEFAULT_ROBUST_VARIANT,
    ROBUST_VARIANTS,
    attach_robust_scores,
    extract_error_profile,
    robust_sort_key,
)
from shell.bonect_replay_delta_tree import predict_replay_delta_tree


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
SCENARIO_ALIASES = {
    "mem": "memory",
    "memory": "memory",
    "cpu": "cpu",
    "io": "io",
    "none": "none",
    "all": "all",
}
DEFAULT_BONECT_MD = {
    "memory": ROOT / "result" / "bonect" / "mem.md",
    "cpu": ROOT / "result" / "bonect" / "cpu.md",
    "io": ROOT / "result" / "bonect" / "io.md",
}
DEFAULT_CONFIG = {
    "memory": ROOT / "config" / "generated" / "optimizer_config_universal_xp_16_8_memory_coarse.json",
    "cpu": ROOT / "config" / "generated" / "optimizer_config_universal_xp_16_8_cpu_coarse.json",
    "io": ROOT / "config" / "generated" / "optimizer_config_universal_xp_16_8_io_coarse.json",
}
DEFAULT_PLAN_CAPTURE_GLOBS = {
    "memory": [
        ROOT
        / "history"
        / "pg_benchmark_bottleneck_matrix"
        / "pg_bonect_bf_mem_ap8_vm3g_cap143_20260624_092103"
        / "memory"
        / "hybench_ap_plan_captures"
        / "baseline_*.json",
        ROOT / "history" / "universal_xp_16_8_memory_coarse_plan_captures" / "baseline_*.json",
    ],
    "cpu": [
        ROOT / "history" / "universal_xp_16_8_cpu_coarse_plan_captures" / "baseline_*.json",
    ],
    "io": [
        ROOT / "history" / "universal_xp_16_8_io_coarse_plan_captures" / "baseline_*.json",
    ],
    "none": [
        ROOT / "history" / "universal_xp_16_8_cpu_coarse_plan_captures" / "baseline_*.json",
    ],
}
NONE_HEURISTIC_RANGES = {
    "seq_page_cost": {"low": 0.1, "high": 10.0, "scale": "log"},
    "random_page_cost": {"low": 0.0001, "high": 100.0, "scale": "log"},
    "cpu_tuple_cost": {"low": 0.000001, "high": 0.1, "scale": "log"},
    "cpu_index_tuple_cost": {"low": 0.000001, "high": 0.05, "scale": "log"},
    "cpu_operator_cost": {"low": 0.000001, "high": 0.05, "scale": "log"},
    "parallel_setup_cost": {"low": 10.0, "high": 100000.0, "scale": "log"},
    "parallel_tuple_cost": {"low": 0.0001, "high": 10.0, "scale": "log"},
}
PROCESS_PATTERN = (
    "pg_bonect|bonect_supervisor|bonect_cloud|parameter_optimizer.py|hybench|"
    "run_pg_benchmark_bottleneck_matrix|param_boundary_sweep|bonect_full_grid_estimator_scan"
)

_WORKER_STATE: Dict[str, Any] = {}


def json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    return str(value)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True, default=json_default)
        f.write("\n")


def append_jsonl(path: Path, rows: Sequence[Dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=json_default) + "\n")


def run_text_command(args: Sequence[str], *, timeout: Optional[float] = None) -> str:
    proc = subprocess.run(args, check=False, capture_output=True, text=True, timeout=timeout)
    return ((proc.stdout or "") + (proc.stderr or "")).rstrip("\n")


def shell_snapshot() -> str:
    proc = subprocess.run(
        ["ps", "-eo", "pid,ppid,stat,lstart,cmd", "--width", "240"],
        check=False,
        capture_output=True,
        text=True,
    )
    lines = []
    pattern = re.compile(PROCESS_PATTERN, re.IGNORECASE)
    for line in (proc.stdout or "").splitlines():
        if pattern.search(line):
            lines.append(line)
    return "\n".join(lines) + ("\n" if lines else "")


def parse_ps_snapshot(snapshot: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for line in snapshot.splitlines():
        parts = line.split(None, 7)
        if len(parts) < 8:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        rows.append({"pid": pid, "ppid": ppid, "cmd": parts[7], "raw": line})
    return rows


def current_ancestor_pids() -> set[int]:
    ancestors = {os.getpid(), os.getppid()}
    current = os.getppid()
    for _ in range(32):
        try:
            with open(f"/proc/{current}/stat", "r", encoding="utf-8") as f:
                fields = f.read().split()
            parent = int(fields[3])
        except Exception:
            break
        if parent <= 1 or parent in ancestors:
            break
        ancestors.add(parent)
        current = parent
    return ancestors


def should_stop_process(row: Dict[str, Any], protected_pids: set[int]) -> bool:
    pid = int(row.get("pid", -1))
    cmd = str(row.get("cmd") or "")
    if pid in protected_pids:
        return False
    if " rg -i " in cmd or cmd.startswith("rg -i "):
        return False
    if "ps -eo pid,ppid" in cmd:
        return False
    allowed = [
        "pg_bonect",
        "bonect_supervisor.py",
        "bonect_cloud_git_sync.py",
        "bonect_cloud_bridge.py",
        "parameter_optimizer.py",
        "HyBench",
        "bash hybench",
        "run_pg_benchmark_bottleneck_matrix.py",
        "param_boundary_sweep.py",
        "bonect_full_grid_estimator_scan.py",
    ]
    return any(token.lower() in cmd.lower() for token in allowed)


def run_exclusive_preflight(run_dir: Path) -> Dict[str, Any]:
    run_dir.mkdir(parents=True, exist_ok=True)
    before = shell_snapshot()
    (run_dir / "preflight_processes_before.txt").write_text(before, encoding="utf-8")

    protected = current_ancestor_pids()
    actions: Dict[str, Any] = {
        "protected_pids": sorted(protected),
        "terminated_pids": [],
        "sigkilled_pids": [],
        "skipped_processes": [],
        "killed_tmux_sessions": [],
        "kept_tmux_sessions": [],
    }

    rows = parse_ps_snapshot(before)
    target_pids = []
    for row in rows:
        if should_stop_process(row, protected):
            target_pids.append(int(row["pid"]))
        else:
            actions["skipped_processes"].append(row)

    tmux_ls = run_text_command(["tmux", "ls"], timeout=5.0) if shutil_exists("tmux") else ""
    actions["tmux_ls_before"] = tmux_ls
    for line in tmux_ls.splitlines():
        name = line.split(":", 1)[0].strip()
        if re.search(r"(pg_bonect|bonect|hybench|benchmark)", name, flags=re.IGNORECASE):
            proc = subprocess.run(["tmux", "kill-session", "-t", name], check=False, capture_output=True, text=True)
            actions["killed_tmux_sessions"].append(
                {"name": name, "returncode": proc.returncode, "stderr": (proc.stderr or "").strip()}
            )
        else:
            actions["kept_tmux_sessions"].append(name)

    for pid in sorted(set(target_pids)):
        try:
            os.kill(pid, signal.SIGTERM)
            actions["terminated_pids"].append(pid)
        except ProcessLookupError:
            actions["terminated_pids"].append(pid)
        except PermissionError as exc:
            actions.setdefault("term_errors", []).append({"pid": pid, "error": str(exc)})

    time.sleep(10.0 if target_pids or actions["killed_tmux_sessions"] else 0.1)
    for pid in sorted(set(target_pids)):
        if not Path(f"/proc/{pid}").exists():
            continue
        try:
            os.kill(pid, signal.SIGKILL)
            actions["sigkilled_pids"].append(pid)
        except ProcessLookupError:
            pass
        except PermissionError as exc:
            actions.setdefault("kill_errors", []).append({"pid": pid, "error": str(exc)})

    after = shell_snapshot()
    (run_dir / "preflight_processes_after.txt").write_text(after, encoding="utf-8")
    write_json(run_dir / "preflight_actions.json", actions)
    return actions


def shutil_exists(binary: str) -> bool:
    for item in os.environ.get("PATH", "").split(os.pathsep):
        if item and os.access(os.path.join(item, binary), os.X_OK):
            return True
    return False


def extract_json_object_after_key(text: str, key: str) -> Dict[str, Any]:
    match = re.search(rf'"{re.escape(key)}"\s*:\s*\{{', text)
    if not match:
        raise ValueError(f"cannot find {key} in markdown")
    start = text.find("{", match.start())
    depth = 0
    in_string = False
    escape = False
    for idx in range(start, len(text)):
        ch = text[idx]
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : idx + 1])
    raise ValueError(f"unterminated object for {key}")


def load_ranges_from_markdown(path: Path) -> Dict[str, Dict[str, float]]:
    text = path.read_text(encoding="utf-8")
    initial = extract_json_object_after_key(text, "initial_params")
    optimized = extract_json_object_after_key(text, "optimized_params")
    ranges: Dict[str, Dict[str, float]] = {}
    for name in COST_PARAMS:
        if name not in initial or name not in optimized:
            raise ValueError(f"{path} missing {name} in initial_params or optimized_params")
        a = float(initial[name])
        b = float(optimized[name])
        ranges[name] = {"low": min(a, b), "high": max(a, b), "scale": "linear", "initial": a, "optimized": b}
    return ranges


def normalize_scenario(raw: str) -> str:
    scenario = SCENARIO_ALIASES.get(str(raw or "").strip().lower())
    if not scenario:
        raise ValueError(f"unsupported scenario: {raw}")
    return scenario


def select_range_override_payload(overrides: Dict[str, Any], scenario: str) -> Dict[str, Any]:
    if scenario in overrides and isinstance(overrides[scenario], dict):
        return dict(overrides[scenario])
    aliases = {
        "memory": ["mem", "memory"],
        "cpu": ["cpu"],
        "io": ["io"],
        "none": ["none", "no_bottleneck", "nobottleneck", "no-bottleneck"],
    }.get(scenario, [scenario])
    for alias in aliases:
        if alias in overrides and isinstance(overrides[alias], dict):
            return dict(overrides[alias])
    return overrides


def apply_range_override(
    ranges: Dict[str, Dict[str, Any]],
    range_json: Optional[Path],
    *,
    scenario: str,
) -> Dict[str, Dict[str, Any]]:
    if range_json is None:
        return ranges
    with range_json.open("r", encoding="utf-8") as f:
        overrides = json.load(f)
    if not isinstance(overrides, dict):
        raise ValueError("--range-json must contain a JSON object")
    overrides = select_range_override_payload(overrides, scenario)
    merged = copy.deepcopy(ranges)
    for name, raw in overrides.items():
        if name not in COST_PARAMS:
            continue
        if isinstance(raw, list) and len(raw) >= 2:
            low, high = float(raw[0]), float(raw[1])
            scale = str(raw[2]) if len(raw) >= 3 else str(merged[name].get("scale", "linear"))
        elif isinstance(raw, dict):
            low = float(raw.get("low", raw.get("min", raw.get("min_value"))))
            high = float(raw.get("high", raw.get("max", raw.get("max_value"))))
            scale = str(raw.get("scale", merged[name].get("scale", "linear")))
        else:
            raise ValueError(f"unsupported override for {name}: {raw!r}")
        merged[name]["low"] = min(low, high)
        merged[name]["high"] = max(low, high)
        merged[name]["scale"] = scale.lower()
    return merged


def grid_values(low: float, high: float, points: int, scale: str = "linear") -> List[float]:
    if points <= 1:
        return [float(low)]
    if math.isclose(low, high):
        return [float(low) for _ in range(points)]
    if str(scale or "linear").lower() == "log":
        if low <= 0.0 or high <= 0.0:
            raise ValueError(f"log scale requires positive low/high, got low={low}, high={high}")
        log_low = math.log(low)
        log_high = math.log(high)
        return [float(math.exp(log_low + (log_high - log_low) * idx / (points - 1))) for idx in range(points)]
    return [float(low + (high - low) * idx / (points - 1)) for idx in range(points)]


def parameter_signature(params: Dict[str, float]) -> str:
    return "|".join(f"{name}={float(params[name]):.17g}" for name in COST_PARAMS)


def candidate_iter(ranges: Dict[str, Dict[str, Any]], grid_points: int) -> Iterable[Dict[str, float]]:
    values = [
        grid_values(ranges[name]["low"], ranges[name]["high"], grid_points, str(ranges[name].get("scale", "linear")))
        for name in COST_PARAMS
    ]
    for combo in itertools.product(*values):
        yield {name: float(value) for name, value in zip(COST_PARAMS, combo)}


def optimizer_compact_grid_values(config: Dict[str, Any], points: int) -> Dict[str, List[float]]:
    """Mirror parameter_optimizer.compact_probe_values for an external scan."""
    ranges = config.get("param_ranges") or {}
    initial = config.get("initial_params") or {}
    if points < 1:
        raise ValueError("grid points must be positive")
    result: Dict[str, List[float]] = {}
    for name in COST_PARAMS:
        raw_range = ranges.get(name) or {}
        if "min_value" not in raw_range or "max_value" not in raw_range:
            raise ValueError(f"config param_ranges missing {name}")
        low = float(raw_range["min_value"])
        high = float(raw_range["max_value"])
        discrete_count = max(1, int(raw_range.get("discrete_count", points) or points))
        values = grid_values(low, high, discrete_count, "linear")
        current = float(initial.get(name, values[0]))
        selected = [float(values[0]), current, float(values[-1])]
        if points > 3 and len(values) > 2:
            selected.extend(
                [
                    float(values[len(values) // 4]),
                    float(values[(len(values) * 3) // 4]),
                ]
            )
        deduped: List[float] = []
        seen = set()
        for value in [*selected, *values]:
            rounded = round(float(value), 12)
            if rounded in seen:
                continue
            seen.add(rounded)
            deduped.append(float(value))
            if len(deduped) >= points:
                break
        if len(deduped) != points:
            raise ValueError(
                f"optimizer compact grid for {name} has {len(deduped)} values, expected {points}"
            )
        result[name] = deduped
    return result


def candidate_iter_from_values(values: Dict[str, List[float]]) -> Iterable[Dict[str, float]]:
    for combo in itertools.product(*(values[name] for name in COST_PARAMS)):
        yield {name: float(value) for name, value in zip(COST_PARAMS, combo)}


def parse_pg_endpoint(value: str, base: Dict[str, Any]) -> Dict[str, Any]:
    """Parse host:port[/database] while inheriting user/password from config."""
    endpoint = str(value or "").strip()
    if not endpoint:
        raise ValueError("empty PostgreSQL endpoint")
    host_port, separator, database = endpoint.partition("/")
    host, colon, port = host_port.rpartition(":")
    if not colon or not host or not port:
        raise ValueError(f"invalid PostgreSQL endpoint {endpoint!r}; expected host:port[/database]")
    resolved = dict(base)
    resolved["host"] = host
    resolved["port"] = int(port)
    if separator and database:
        resolved["dbname"] = database
    return resolved


def load_completed_signatures(path: Path) -> set[str]:
    completed: set[str] = set()
    if not path.exists():
        return completed
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            sig = item.get("param_signature")
            if sig:
                completed.add(str(sig))
    return completed


def load_config(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"config is not a JSON object: {path}")
    return data


def apply_resource_model_dir(config: Dict[str, Any], model_dir: Path) -> Dict[str, str]:
    """Point the scanner at baseline-anchored models produced by the current VM run."""
    resolved = model_dir.expanduser().resolve()
    plan_cfg = (config.setdefault("metrics", {})).setdefault("plan_estimator", {})
    paths: Dict[str, str] = {}
    for resource_name in ("cpu", "memory", "io"):
        path = resolved / f"{resource_name}_resource_models.json"
        if not path.is_file():
            raise FileNotFoundError(f"missing {resource_name} model artifact: {path}")
        resource_cfg = plan_cfg.setdefault(resource_name, {})
        learned_cfg = resource_cfg.setdefault("learned_model", {})
        learned_cfg["enabled"] = True
        learned_cfg["required"] = True
        learned_cfg["path"] = str(path)
        paths[resource_name] = str(path)
    model_retraining_cfg = plan_cfg.setdefault("model_retraining", {})
    model_retraining_cfg["enabled"] = False
    model_retraining_cfg["apply_to_runtime"] = False
    return paths


def psql_query(pg: Dict[str, Any], sql: str) -> str:
    env = os.environ.copy()
    configured_password = pg.get("password") or pg.get("pgpassword")
    if configured_password and not env.get("PGPASSWORD"):
        env["PGPASSWORD"] = str(configured_password)
    args = [
        "psql",
        "-h",
        str(pg["host"]),
        "-p",
        str(pg["port"]),
        "-U",
        str(pg["user"]),
        "-d",
        str(pg["dbname"]),
        "-v",
        "ON_ERROR_STOP=1",
        "-At",
        "-F",
        "\t",
        "-c",
        sql,
    ]
    proc = subprocess.run(args, check=False, capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip() or (proc.stdout or "").strip()
        raise RuntimeError(f"psql query failed: {detail}")
    return (proc.stdout or "").strip()


def pg_stat_statements_time_column(pg: Dict[str, Any]) -> str:
    sql = """
        SELECT attname
        FROM pg_attribute
        WHERE attrelid = 'pg_stat_statements'::regclass
          AND attname IN ('total_exec_time', 'total_time')
        ORDER BY CASE attname WHEN 'total_exec_time' THEN 0 ELSE 1 END
        LIMIT 1;
    """
    value = psql_query(pg, sql).strip()
    return value or "total_exec_time"


def load_workload_sql(
    *,
    pg: Dict[str, Any],
    output_path: Path,
    workload_sql_json: Optional[Path],
    limit: int,
) -> List[Dict[str, Any]]:
    if workload_sql_json is not None:
        with workload_sql_json.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            rows = raw.get("queries") or raw.get("workload_sql") or raw.get("items") or []
        else:
            rows = raw
        if not isinstance(rows, list):
            raise ValueError("--workload-sql-json must contain a list or {'queries': [...]}")
        queries = normalize_workload_rows(rows)
        write_json(output_path, {"source": str(workload_sql_json), "queries": queries})
        return queries

    time_col = pg_stat_statements_time_column(pg)
    sql = f"""
        SELECT queryid::text,
               query,
               calls::float8,
               {time_col}::float8 AS total_time_ms
        FROM pg_stat_statements
        WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
          AND query IS NOT NULL
          AND query <> ''
          AND query NOT ILIKE '%pg_stat_statements%'
          AND query NOT ILIKE '%pg_stat_database%'
          AND query NOT ILIKE '%pg_attribute%'
          AND query NOT ILIKE '%SCAN_MARKER_%'
          AND query NOT ILIKE 'EXPLAIN (FORMAT JSON%'
          AND query NOT ILIKE 'BEGIN%'
          AND query NOT ILIKE 'SAVEPOINT%'
          AND query NOT ILIKE 'SET LOCAL%'
          AND query NOT ILIKE 'COMMIT%'
          AND query NOT ILIKE 'ROLLBACK%'
          AND query NOT ILIKE 'SELECT pg_%'
          AND query NOT IN ('SELECT $1', 'SELECT 1')
        ORDER BY {time_col} DESC
        LIMIT {int(limit)};
    """
    raw = psql_query(pg, sql)
    rows: List[Dict[str, Any]] = []
    for line in raw.splitlines():
        parts = line.split("\t", 3)
        if len(parts) != 4:
            continue
        rows.append(
            {
                "queryid": parts[0],
                "query": parts[1],
                "calls": _safe_float(parts[2], 1.0),
                "total_time_ms": _safe_float(parts[3], 0.0),
            }
        )
    queries = normalize_workload_rows(rows)
    write_json(output_path, {"source": "pg_stat_statements", "time_column": time_col, "queries": queries})
    return queries


def refresh_workload_stats(
    queries: Sequence[Dict[str, Any]],
    pg: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Refresh replay weights for an already selected SQL set."""
    numeric_queryids: List[int] = []
    for item in queries:
        try:
            numeric_queryids.append(int(str(item.get("queryid") or "").strip()))
        except (TypeError, ValueError):
            raise ValueError(f"non-numeric queryid cannot be refreshed: {item.get('queryid')!r}")
    if not numeric_queryids:
        raise ValueError("no queryids available for workload-stat refresh")

    time_col = pg_stat_statements_time_column(pg)
    queryid_list = ",".join(str(value) for value in sorted(set(numeric_queryids)))
    sql = f"""
        SELECT queryid::text,
               calls::float8,
               {time_col}::float8 AS total_time_ms
        FROM pg_stat_statements
        WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
          AND queryid IN ({queryid_list});
    """
    stats: Dict[str, Dict[str, float]] = {}
    for line in psql_query(pg, sql).splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        stats[str(parts[0])] = {
            "calls": max(1.0, _safe_float(parts[1], 1.0)),
            "total_time_ms": max(0.0, _safe_float(parts[2], 0.0)),
        }

    missing = sorted(str(value) for value in set(numeric_queryids) if str(value) not in stats)
    if missing:
        raise RuntimeError(
            "workload-stat refresh did not cover every selected queryid: "
            f"missing_count={len(missing)}, missing={missing[:10]}"
        )

    refreshed: List[Dict[str, Any]] = []
    for item in queries:
        row = dict(item)
        values = stats[str(row["queryid"])]
        row["calls"] = values["calls"]
        row["replay_count"] = values["calls"]
        row["total_time_ms"] = values["total_time_ms"]
        refreshed.append(row)
    return refreshed


def normalize_workload_rows(rows: Sequence[Any]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for idx, row in enumerate(rows):
        if isinstance(row, str):
            item = {"queryid": str(idx), "query": row, "calls": 1.0, "total_time_ms": 0.0}
        elif isinstance(row, dict):
            item = dict(row)
        else:
            continue
        query = str(item.get("query") or item.get("sql") or "").strip().rstrip(";")
        query_l = query.lower()
        if not query or not _is_plannable_query(query):
            continue
        if (
            "scan_marker_" in query_l
            or "pg_stat_statements" in query_l
            or "pg_stat_database" in query_l
            or "pg_attribute" in query_l
            or query_l in {"select $1", "select 1"}
            or query_l.startswith("select pg_")
        ):
            continue
        calls = max(1.0, _safe_float(item.get("replay_count", item.get("calls", 1.0)), 1.0))
        result.append(
            {
                "queryid": str(item.get("queryid", item.get("id", len(result)))),
                "query": query,
                "calls": calls,
                "replay_count": calls,
                "total_time_ms": _safe_float(item.get("total_time_ms", item.get("total_exec_time", 0.0)), 0.0),
            }
        )
    return result


def extract_hybench_runtime(workload_cmd: str) -> Dict[str, Any]:
    result: Dict[str, Any] = {"mode": None, "props": {}}
    text = str(workload_cmd or "")
    mode_match = re.search(r"hybench\s+-t\s+(\w+)", text)
    if mode_match:
        result["mode"] = mode_match.group(1)
    prop_keys = ["tpclient", "apclient", "xtpclient", "xapclient", "apRunMins", "tpRunMins", "xpRunMins"]
    props: Dict[str, int] = {}
    try:
        tokens = shlex.split(text)
    except ValueError:
        tokens = []
    for idx, token in enumerate(tokens[:-1]):
        if token != "-c":
            continue
        props_path = Path(tokens[idx + 1]).expanduser()
        if not props_path.is_absolute():
            cd_match = re.search(r"(?:^|&&|;)\s*cd\s+([^;&]+)", text)
            if cd_match:
                try:
                    cd_tokens = shlex.split(cd_match.group(1).strip())
                except ValueError:
                    cd_tokens = []
                if cd_tokens:
                    props_path = Path(cd_tokens[0]).expanduser() / props_path
        try:
            for raw_line in props_path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = (part.strip() for part in line.split("=", 1))
                if key in prop_keys and re.fullmatch(r"[0-9]+", value):
                    props[key] = int(value)
        except OSError:
            pass
        break
    for key in prop_keys:
        matches = re.findall(rf"{re.escape(key)}=\.\*/{re.escape(key)}=([0-9]+)", text)
        if not matches:
            matches = re.findall(rf"{re.escape(key)}=([0-9]+)", text)
        if matches:
            props[key] = int(matches[-1])
        else:
            props.setdefault(key, 0)
    mode = str(result.get("mode") or "")
    if mode == "runxp":
        configured = props.get("xtpclient", 0) + props.get("xapclient", 0)
    elif mode == "runap":
        configured = props.get("apclient", 0)
    elif mode == "runtp":
        configured = props.get("tpclient", 0)
    else:
        configured = props.get("tpclient", 0) + props.get("apclient", 0)
    result["configured_concurrency"] = configured or None
    result["props"] = props
    return result


def build_workload_context(config: Dict[str, Any], queries: Sequence[Dict[str, Any]], pg: Dict[str, Any]) -> Dict[str, Any]:
    workload_cmd = str(((config.get("benchmark") or {}).get("workload_cmd")) or "")
    hybench_runtime = extract_hybench_runtime(workload_cmd)
    props = hybench_runtime.get("props") or {}
    mode_run_key = {
        "runxp": "xpRunMins",
        "runap": "apRunMins",
        "runtp": "tpRunMins",
    }.get(str(hybench_runtime.get("mode") or ""))
    if mode_run_key:
        run_mins = int(props.get(mode_run_key) or 0)
    else:
        run_mins = max(int(props.get(key) or 0) for key in ("xpRunMins", "apRunMins", "tpRunMins"))
    elapsed_seconds = float(run_mins * 60 if run_mins > 0 else 600)
    total_exec_time_ms = sum(max(0.0, _safe_float(item.get("total_time_ms"), 0.0)) for item in queries)
    concurrency_estimate = total_exec_time_ms / max(elapsed_seconds * 1000.0, 1e-6)
    context: Dict[str, Any] = {
        "elapsed_seconds": elapsed_seconds,
        "feature_window_seconds": elapsed_seconds,
        "sql_count": len(queries),
        "workload_cmd": workload_cmd,
        "hybench_runtime": hybench_runtime,
        "hybench_mode": hybench_runtime.get("mode"),
        "concurrency": {
            "configured": hybench_runtime.get("configured_concurrency"),
            "avg_estimated": concurrency_estimate,
            "source": "pg_stat_statements_total_time_over_configured_runtime",
        },
    }
    if hybench_runtime.get("configured_concurrency"):
        context["concurrency"]["avg"] = hybench_runtime.get("configured_concurrency")
        context["concurrency"]["p99"] = hybench_runtime.get("configured_concurrency")
        context["concurrency"]["max"] = hybench_runtime.get("configured_concurrency")
    try:
        context["database_size_bytes"] = _safe_float(
            psql_query(pg, "SELECT pg_database_size(current_database())::float8;"),
            0.0,
        )
    except Exception as exc:
        context["database_size_error"] = str(exc)
    try:
        context["cache_hit_rate"] = _safe_float(
            psql_query(
                pg,
                "SELECT CASE WHEN (blks_hit + blks_read) > 0 "
                "THEN blks_hit::float8 / (blks_hit + blks_read) ELSE 0 END "
                "FROM pg_stat_database WHERE datname = current_database();",
            ),
            0.0,
        )
    except Exception as exc:
        context["cache_hit_rate_error"] = str(exc)
    return context


def load_workload_context(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise ValueError("--workload-context-json must contain a JSON object")
    context = raw.get("workload_context", raw)
    if not isinstance(context, dict):
        raise ValueError("workload_context must be a JSON object")
    required = ("elapsed_seconds", "concurrency", "database_size_bytes", "cache_hit_rate")
    missing = [name for name in required if context.get(name) is None]
    if missing:
        raise ValueError(f"workload context is missing required fields: {missing}")
    return copy.deepcopy(context)


def load_baseline_calibration(path: Optional[Path]) -> Dict[str, Any]:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"baseline calibration must be a JSON object: {path}")
    scales: Dict[str, float] = {}
    for name, raw_value in (payload.get("scales") or {}).items():
        resource = str(name).strip().lower()
        value = _safe_float(raw_value, float("nan"))
        if resource not in {"cpu", "memory", "io"} or not math.isfinite(value) or value <= 0.0:
            raise ValueError(f"invalid baseline correction scale {name}={raw_value!r}: {path}")
        scales[resource] = value
    actual_resource = {
        str(name).strip().lower(): _safe_float(value, float("nan"))
        for name, value in (payload.get("actual_resource") or {}).items()
        if str(name).strip().lower() in {"cpu", "memory", "io"}
    }
    if any(not math.isfinite(value) or value < 0.0 for value in actual_resource.values()):
        raise ValueError(f"invalid actual_resource in baseline calibration: {path}")
    result = dict(payload)
    result["scales"] = scales
    result["actual_resource"] = actual_resource
    result["exempt_resources"] = sorted(
        {
            str(name).strip().lower()
            for name in (payload.get("exempt_resources") or [])
            if str(name).strip().lower() in {"cpu", "memory", "io"}
        }
    )
    replay_tree = copy.deepcopy(payload.get("replay_delta_tree") or {})
    if isinstance(replay_tree, dict) and replay_tree.get("enabled"):
        model = replay_tree.get("model")
        model_path_value = str(replay_tree.get("model_path") or "").strip()
        if model is None and model_path_value:
            model_path = Path(model_path_value)
            if not model_path.is_absolute():
                model_path = (path.parent / model_path).resolve()
                if not model_path.exists():
                    model_path = (ROOT / model_path_value).resolve()
            if not model_path.exists():
                raise FileNotFoundError(f"replay delta tree model not found: {model_path_value}")
            model = json.loads(model_path.read_text(encoding="utf-8"))
            replay_tree["resolved_model_path"] = str(model_path)
        if not isinstance(model, dict):
            if replay_tree.get("required", True):
                raise ValueError(f"invalid replay delta tree model: {path}")
        else:
            replay_tree["model"] = model
        result["replay_delta_tree"] = replay_tree
    result["path"] = str(path.resolve())
    return result


def apply_resource_correction(
    resource: Dict[str, float],
    calibration: Dict[str, Any],
) -> Tuple[Dict[str, float], Dict[str, Any]]:
    corrected = {str(name): float(value) for name, value in resource.items()}
    scales = calibration.get("scales") if isinstance(calibration, dict) else {}
    scales = scales if isinstance(scales, dict) else {}
    exempt = (
        {
            str(name).strip().lower()
            for name in (calibration.get("exempt_resources") or [])
        }
        if isinstance(calibration, dict)
        else set()
    )
    applied: Dict[str, float] = {}
    for name, raw_scale in scales.items():
        resource_name = str(name).strip().lower()
        if resource_name in exempt or resource_name not in corrected:
            continue
        scale = _safe_float(raw_scale, 1.0)
        corrected[resource_name] *= scale
        applied[resource_name] = scale
    return corrected, {
        "applied": bool(applied),
        "scales": applied,
        "exempt_resources": sorted(exempt),
        "source": calibration.get("source") if isinstance(calibration, dict) else None,
        "path": calibration.get("path") if isinstance(calibration, dict) else None,
        "resource_before_correction": dict(resource),
        "resource_after_correction": dict(corrected),
    }


def extract_plan_delta_features(
    explained_queries: Sequence[Dict[str, Any]],
) -> Dict[str, float]:
    features = {
        "weighted_gather_count": 0.0,
        "weighted_gather_workers_planned": 0.0,
        "weighted_parallel_aware_nodes": 0.0,
    }

    def walk(node: Any, replay_count: float) -> None:
        if not isinstance(node, dict):
            return
        node_type = str(node.get("Node Type") or "")
        if node_type in {"Gather", "Gather Merge"}:
            features["weighted_gather_count"] += replay_count
            features["weighted_gather_workers_planned"] += (
                replay_count * max(0.0, _safe_float(node.get("Workers Planned"), 0.0))
            )
        if bool(node.get("Parallel Aware")):
            features["weighted_parallel_aware_nodes"] += replay_count
        for child in node.get("Plans", []) or []:
            walk(child, replay_count)

    for item in explained_queries:
        if item.get("error"):
            continue
        replay_count = max(
            0.0,
            _safe_float(item.get("replay_count", item.get("calls", 1.0)), 1.0),
        )
        walk(item.get("plan_root"), replay_count)
    return features


def apply_paired_delta_calibration(
    resource: Dict[str, float],
    plan_features: Dict[str, float],
    calibration: Dict[str, Any],
    params: Optional[Dict[str, float]] = None,
) -> Tuple[Dict[str, float], Optional[float], Dict[str, Any]]:
    replay_tree = calibration.get("replay_delta_tree") if isinstance(calibration, dict) else {}
    if isinstance(replay_tree, dict) and replay_tree.get("enabled"):
        model = replay_tree.get("model")
        try:
            if not isinstance(model, dict) or not isinstance(params, dict):
                raise ValueError("replay delta tree requires a model and candidate params")
            prediction, tree_detail = predict_replay_delta_tree(model, params, plan_features)
            actual_baseline = calibration.get("actual_resource") or {}
            calibrated = dict(resource)
            applied_resources: Dict[str, Dict[str, float]] = {}
            for name in ("cpu", "memory", "io"):
                baseline_value = _safe_float(actual_baseline.get(name), float("nan"))
                delta_value = _safe_float(prediction.get(f"{name}_delta"), float("nan"))
                if not math.isfinite(baseline_value) or not math.isfinite(delta_value):
                    raise ValueError(f"invalid replay delta tree target for {name}")
                calibrated_value = max(0.0, baseline_value + delta_value)
                calibrated[name] = calibrated_value
                applied_resources[name] = {
                    "baseline": baseline_value,
                    "predicted_delta": delta_value,
                    "calibrated": calibrated_value,
                }
            estimated_qps_delta = _safe_float(
                prediction.get("db_qps_delta_pct"),
                float("nan"),
            )
            if not math.isfinite(estimated_qps_delta):
                raise ValueError("invalid replay delta tree QPS target")
            return calibrated, estimated_qps_delta, {
                "applied": True,
                "source": replay_tree.get("source") or "real_replay_delta_tree",
                "model_type": model.get("model_type"),
                "tree": tree_detail,
                "resources": applied_resources,
                "estimated_db_qps_delta_pct": estimated_qps_delta,
                "teacher_coordinates_used": False,
            }
        except (KeyError, TypeError, ValueError) as exc:
            if replay_tree.get("required", True):
                raise
            replay_tree_error = str(exc)
        else:
            replay_tree_error = ""
    else:
        replay_tree_error = "disabled_or_missing"
    paired = calibration.get("paired_delta_calibration") if isinstance(calibration, dict) else {}
    if not isinstance(paired, dict) or not paired.get("enabled"):
        return dict(resource), None, {
            "applied": False,
            "source": "disabled_or_missing",
            "replay_delta_tree_error": replay_tree_error,
        }
    feature_name = str(paired.get("feature") or "").strip()
    baseline_feature = _safe_float(paired.get("baseline_feature"), float("nan"))
    teacher_feature = _safe_float(paired.get("teacher_feature"), float("nan"))
    current_feature = _safe_float(plan_features.get(feature_name), float("nan"))
    denominator = teacher_feature - baseline_feature
    if (
        not feature_name
        or not math.isfinite(current_feature)
        or not math.isfinite(denominator)
        or abs(denominator) <= 1e-12
    ):
        return dict(resource), None, {
            "applied": False,
            "source": "invalid_feature_calibration",
            "feature": feature_name,
        }
    progress_unclamped = (current_feature - baseline_feature) / denominator
    progress_min = _safe_float(paired.get("progress_min"), -0.25)
    progress_max = max(progress_min, _safe_float(paired.get("progress_max"), 1.5))
    progress = min(progress_max, max(progress_min, progress_unclamped))
    actual_baseline = calibration.get("actual_resource") or {}
    resource_teacher_delta = paired.get("resource_teacher_delta") or {}
    calibrated = dict(resource)
    applied_resources: Dict[str, Dict[str, float]] = {}
    for name, raw_delta in resource_teacher_delta.items():
        resource_name = str(name).strip().lower()
        if resource_name not in {"cpu", "memory", "io"} or resource_name not in actual_baseline:
            continue
        baseline_value = _safe_float(actual_baseline.get(resource_name), float("nan"))
        teacher_delta = _safe_float(raw_delta, float("nan"))
        if not math.isfinite(baseline_value) or not math.isfinite(teacher_delta):
            continue
        calibrated_value = max(0.0, baseline_value + progress * teacher_delta)
        calibrated[resource_name] = calibrated_value
        applied_resources[resource_name] = {
            "baseline": baseline_value,
            "teacher_delta": teacher_delta,
            "calibrated": calibrated_value,
        }
    teacher_qps_delta = _safe_float(
        paired.get("db_qps_teacher_delta_pct"),
        float("nan"),
    )
    estimated_qps_delta = (
        progress * teacher_qps_delta if math.isfinite(teacher_qps_delta) else None
    )
    return calibrated, estimated_qps_delta, {
        "applied": bool(applied_resources),
        "source": paired.get("source"),
        "feature": feature_name,
        "baseline_feature": baseline_feature,
        "teacher_feature": teacher_feature,
        "current_feature": current_feature,
        "progress": progress,
        "progress_unclamped": progress_unclamped,
        "progress_bounds": [progress_min, progress_max],
        "resources": applied_resources,
        "estimated_db_qps_delta_pct": estimated_qps_delta,
        "teacher_coordinates_used": False,
    }


def find_latest_matching_file(patterns: Sequence[Path]) -> Optional[Path]:
    matches: List[Path] = []
    for pattern in patterns:
        matches.extend(pattern.parent.glob(pattern.name))
    matches = [path for path in matches if path.is_file()]
    if not matches:
        return None
    return max(matches, key=lambda path: path.stat().st_mtime)


def find_default_workload_sql_json(scenario: str) -> Optional[Path]:
    return find_latest_matching_file(DEFAULT_PLAN_CAPTURE_GLOBS.get(scenario, []))


def find_none_config() -> Tuple[Path, str]:
    patterns = [
        "*none*.json",
        "*no_bottleneck*.json",
        "*nobottleneck*.json",
        "*no-bottleneck*.json",
    ]
    matches: List[Path] = []
    generated = ROOT / "config" / "generated"
    for pattern in patterns:
        matches.extend(generated.glob(pattern))
    matches = [path for path in matches if path.is_file()]
    if matches:
        return max(matches, key=lambda path: path.stat().st_mtime), "matched_none_config"
    return DEFAULT_CONFIG["cpu"], "fallback_cpu_coarse_config"


def find_none_bonect_md() -> Optional[Path]:
    result_dir = ROOT / "result" / "bonect"
    patterns = ["*none*.md", "*no_bottleneck*.md", "*nobottleneck*.md", "*no-bottleneck*.md"]
    matches: List[Path] = []
    for pattern in patterns:
        matches.extend(result_dir.glob(pattern))
    matches = [path for path in matches if path.is_file()]
    return max(matches, key=lambda path: path.stat().st_mtime) if matches else None


def default_config_for_scenario(scenario: str) -> Tuple[Path, str]:
    if scenario == "none":
        return find_none_config()
    return DEFAULT_CONFIG[scenario], "default_config"


def default_bonect_md_for_scenario(scenario: str) -> Tuple[Optional[Path], str]:
    if scenario == "none":
        found = find_none_bonect_md()
        if found is not None:
            return found, "matched_none_bonect_md"
        return None, "heuristic_wide_log_range"
    return DEFAULT_BONECT_MD[scenario], "default_bonect_md"


def load_scenario_ranges(
    *,
    scenario: str,
    bonect_md: Optional[Path],
    range_json: Optional[Path],
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    metadata: Dict[str, Any] = {}
    # Allow range-json-only when every active cost parameter is specified.
    # This is required for answer-blind binary outer-loop rounds that rebuild a
    # five-point grid inside current intervals without depending on mem.md.
    if range_json is not None and bonect_md is None:
        with range_json.open("r", encoding="utf-8") as handle:
            raw_overrides = json.load(handle)
        if not isinstance(raw_overrides, dict):
            raise ValueError("--range-json must contain a JSON object")
        overrides = select_range_override_payload(raw_overrides, scenario)
        missing = [name for name in COST_PARAMS if name not in overrides]
        if not missing:
            ranges: Dict[str, Dict[str, Any]] = {}
            for name in COST_PARAMS:
                raw = overrides[name]
                if isinstance(raw, list) and len(raw) >= 2:
                    low, high = float(raw[0]), float(raw[1])
                    scale = str(raw[2]) if len(raw) >= 3 else "linear"
                elif isinstance(raw, dict):
                    low = float(raw.get("low", raw.get("min", raw.get("min_value"))))
                    high = float(raw.get("high", raw.get("max", raw.get("max_value"))))
                    scale = str(raw.get("scale", "linear"))
                else:
                    raise ValueError(f"unsupported override for {name}: {raw!r}")
                ranges[name] = {
                    "low": min(low, high),
                    "high": max(low, high),
                    "scale": scale.lower(),
                }
            metadata["range_source"] = f"range_json_only:{range_json}"
            return ranges, metadata
    if scenario == "none" and bonect_md is None:
        ranges = copy.deepcopy(NONE_HEURISTIC_RANGES)
        metadata["none_range_source"] = "heuristic_wide_log_range"
        metadata["range_source"] = "heuristic_wide_log_range"
    else:
        if bonect_md is None:
            raise ValueError(f"--bonect-md is required for scenario={scenario} unless --range-json provides all seven cost params")
        ranges = load_ranges_from_markdown(bonect_md)
        metadata["range_source"] = str(bonect_md)
    ranges = apply_range_override(ranges, range_json, scenario=scenario)
    return ranges, metadata


def expected_candidate_count(ranges: Dict[str, Dict[str, Any]], grid_points: int) -> int:
    total = 1
    for name in COST_PARAMS:
        total *= len(grid_values(ranges[name]["low"], ranges[name]["high"], grid_points, str(ranges[name].get("scale", "linear"))))
    return total


def scan_total(summary: Dict[str, Any]) -> int:
    return int(summary.get("total_candidates", summary.get("total", 0)) or 0)


def scan_completed(summary: Dict[str, Any]) -> int:
    return int(summary.get("completed_candidates", summary.get("completed", 0)) or 0)


def valid_scan_result(summary_path: Path, scenario: str, *, min_total: Optional[int] = None) -> Optional[Dict[str, Any]]:
    try:
        with summary_path.open("r", encoding="utf-8") as f:
            summary = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    try:
        summary_scenario = normalize_scenario(str(summary.get("scenario", "")))
    except ValueError:
        return None
    if summary_scenario != scenario:
        return None
    total = scan_total(summary)
    completed = scan_completed(summary)
    status = str(summary.get("status") or "").lower()
    if total <= 0:
        return None
    if min_total is not None and total < min_total:
        return None
    if completed < total and status not in {"completed", "complete", "finished"}:
        return None
    if str(summary.get("scan_kind") or "full") == "smoke":
        return None
    top_md = summary_path.parent / "top20_distinct_scores.md"
    top_json = summary_path.parent / "top20_distinct_scores.json"
    if not top_md.exists() or top_md.stat().st_size <= 0:
        return None
    if not top_json.exists() or top_json.stat().st_size <= 0:
        return None
    try:
        top_rows = json.load(top_json.open("r", encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not top_rows:
        return None
    summary["_summary_path"] = str(summary_path)
    summary["_output_dir"] = str(summary_path.parent)
    summary["_top20"] = top_rows
    return summary


def find_existing_result(scenario: str, *, min_total: Optional[int] = None) -> Optional[Dict[str, Any]]:
    root = ROOT / "history" / "bonect_full_grid_scan"
    candidates = list(root.glob("**/scan_summary.json"))
    valid: List[Tuple[float, Dict[str, Any]]] = []
    for path in candidates:
        result = valid_scan_result(path, scenario, min_total=min_total)
        if result is not None:
            valid.append((path.stat().st_mtime, result))
    if not valid:
        return None
    return max(valid, key=lambda item: item[0])[1]


def top20_brief(output_dir: Path, limit: int = 3) -> List[Dict[str, Any]]:
    path = output_dir / "top20_distinct_scores.json"
    try:
        rows = json.load(path.open("r", encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    result = []
    for row in rows[:limit]:
        params = row.get("params") or {}
        resource = row.get("estimated_resource") or {}
        result.append(
            {
                "score": row.get("score"),
                "resource_signature": row.get("resource_signature"),
                "plan_hash": row.get("plan_hash"),
                "params": {name: params.get(name) for name in COST_PARAMS},
                "estimated_resource": {name: resource.get(name) for name in ["cpu", "memory", "io"]},
            }
        )
    return result


def write_all_summary(run_dir: Path, scenario_results: Sequence[Dict[str, Any]]) -> None:
    payload = {
        "run_id": run_dir.name,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "scenarios": list(scenario_results),
    }
    write_json(run_dir / "all_scenarios_summary.json", payload)
    lines = [
        "# Bonect estimator-only full-grid scan summary",
        "",
        f"- run_id: `{run_dir.name}`",
        f"- generated_at: `{payload['generated_at']}`",
        "",
        "| scenario | status | completed/total | failure_rate | distinct_score | distinct_resource | distinct_plan | output_dir |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for item in scenario_results:
        summary = item.get("summary") or {}
        completed = scan_completed(summary)
        total = scan_total(summary)
        lines.append(
            "| {scenario} | {status} | {completed}/{total} | {failure_rate:.6f} | {ds} | {dr} | {dp} | `{output}` |".format(
                scenario=item.get("scenario"),
                status=item.get("status"),
                completed=completed,
                total=total,
                failure_rate=float(summary.get("failure_rate", 0.0) or 0.0),
                ds=int(summary.get("distinct_score_count", 0) or 0),
                dr=int(summary.get("distinct_resource_count", 0) or 0),
                dp=int(summary.get("distinct_plan_count", 0) or 0),
                output=item.get("output_dir", ""),
            )
        )
    lines.append("")
    lines.extend(
        [
            "## Score/resource bounds",
            "",
            "| scenario | score_min | score_max | cpu_min | cpu_max | memory_min | memory_max | io_min | io_max |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for item in scenario_results:
        summary = item.get("summary") or {}
        score_bounds = summary.get("score_bounds") or {}
        resource_bounds = summary.get("estimated_resource_bounds") or {}

        def fmt(value: Any) -> str:
            if value is None:
                return ""
            try:
                return f"{float(value):.9g}"
            except (TypeError, ValueError):
                return ""

        lines.append(
            "| {scenario} | {score_min} | {score_max} | {cpu_min} | {cpu_max} | "
            "{mem_min} | {mem_max} | {io_min} | {io_max} |".format(
                scenario=item.get("scenario"),
                score_min=fmt(score_bounds.get("min")),
                score_max=fmt(score_bounds.get("max")),
                cpu_min=fmt((resource_bounds.get("cpu") or {}).get("min")),
                cpu_max=fmt((resource_bounds.get("cpu") or {}).get("max")),
                mem_min=fmt((resource_bounds.get("memory") or {}).get("min")),
                mem_max=fmt((resource_bounds.get("memory") or {}).get("max")),
                io_min=fmt((resource_bounds.get("io") or {}).get("min")),
                io_max=fmt((resource_bounds.get("io") or {}).get("max")),
            )
        )
    lines.append("")
    for item in scenario_results:
        lines.append(f"## {item.get('scenario')} top candidates")
        for rank, row in enumerate(item.get("top20_brief") or [], start=1):
            lines.append(
                f"- {rank}. score=`{row.get('score')}`, resource=`{row.get('resource_signature')}`, "
                f"plan=`{str(row.get('plan_hash') or '')[:12]}`"
            )
        if item.get("none_range_source"):
            lines.append(f"- none_range_source: `{item.get('none_range_source')}`")
        lines.append("")
    (run_dir / "all_scenarios_summary.md").write_text("\n".join(lines), encoding="utf-8")


def default_run_root(args: argparse.Namespace, scenario: str) -> Tuple[str, Path]:
    if args.run_id:
        run_id = str(args.run_id)
    elif args.output_dir:
        run_id = Path(args.output_dir).name
    else:
        run_id = f"{scenario}_grid{args.grid_points}_{time.strftime('%Y%m%d_%H%M%S')}"
    run_root = Path(args.output_dir) if args.output_dir else ROOT / "history" / "bonect_full_grid_scan" / run_id
    return run_id, run_root


def default_scan_output_dir(args: argparse.Namespace, scenario: str) -> Tuple[str, Path]:
    if args.output_dir:
        output_dir = Path(args.output_dir)
        run_id = str(args.run_id or output_dir.name)
        return run_id, output_dir
    run_id, run_root = default_run_root(args, scenario)
    return run_id, run_root / scenario


def resolve_scenario_inputs(args: argparse.Namespace, scenario: str) -> Tuple[Path, Optional[Path], str, str]:
    if args.config:
        config_path = Path(args.config)
        config_source = "cli_config"
    else:
        config_path, config_source = default_config_for_scenario(scenario)
    if args.bonect_md:
        bonect_md = Path(args.bonect_md)
        range_source = "cli_bonect_md"
    else:
        bonect_md, range_source = default_bonect_md_for_scenario(scenario)
    return config_path, bonect_md, config_source, range_source


def result_from_summary(status: str, summary: Dict[str, Any], *, scenario: str) -> Dict[str, Any]:
    output_dir = Path(str(summary.get("_output_dir") or summary.get("output_dir") or ""))
    item = {
        "scenario": scenario,
        "status": status,
        "output_dir": str(output_dir),
        "summary_path": str(summary.get("_summary_path") or (output_dir / "scan_summary.json")),
        "summary": {k: v for k, v in summary.items() if not str(k).startswith("_")},
        "top20_brief": top20_brief(output_dir),
    }
    if summary.get("none_range_source"):
        item["none_range_source"] = summary.get("none_range_source")
    return item


class PsqlPersistentConnection:
    def __init__(self, pg: Dict[str, Any]) -> None:
        env = os.environ.copy()
        configured_password = pg.get("password") or pg.get("pgpassword")
        if configured_password and not env.get("PGPASSWORD"):
            env["PGPASSWORD"] = str(configured_password)
        self.proc = subprocess.Popen(
            [
                "psql",
                "-h",
                str(pg["host"]),
                "-p",
                str(pg["port"]),
                "-U",
                str(pg["user"]),
                "-d",
                str(pg["dbname"]),
                "-X",
                "-q",
                "-v",
                "ON_ERROR_STOP=0",
                "-At",
                "-F",
                "\t",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
        self.execute("SELECT 1;")

    def execute(self, sql: str, *, timeout_s: float = 300.0) -> str:
        if self.proc.poll() is not None:
            raise RuntimeError(f"psql exited with code {self.proc.returncode}")
        marker = "SCAN_MARKER_" + uuid.uuid4().hex
        payload = sql.rstrip().rstrip(";") + f";\nSELECT '{marker}';\n"
        assert self.proc.stdin is not None
        assert self.proc.stdout is not None
        self.proc.stdin.write(payload)
        self.proc.stdin.flush()
        deadline = time.monotonic() + timeout_s
        lines: List[str] = []
        while True:
            if time.monotonic() > deadline:
                raise TimeoutError(f"timed out waiting for psql marker {marker}")
            line = self.proc.stdout.readline()
            if line == "":
                if self.proc.poll() is not None:
                    raise RuntimeError(f"psql exited while waiting for {marker}; output={''.join(lines)[-1000:]}")
                time.sleep(0.01)
                continue
            stripped = line.rstrip("\n")
            if stripped == marker:
                return "\n".join(lines).strip()
            lines.append(stripped)

    def close(self) -> None:
        if self.proc.poll() is None:
            try:
                assert self.proc.stdin is not None
                self.proc.stdin.write("\\q\n")
                self.proc.stdin.flush()
            except Exception:
                pass
            try:
                self.proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                self.proc.kill()


class PsycopgConnection:
    def __init__(self, pg: Dict[str, Any]) -> None:
        self.pg = pg
        self.kind = ""
        self.conn = None
        try:
            import psycopg  # type: ignore

            self.kind = "psycopg"
            self.conn = psycopg.connect(
                host=str(pg["host"]),
                port=int(pg["port"]),
                user=str(pg["user"]),
                dbname=str(pg["dbname"]),
                password=pg.get("password") or os.environ.get("PGPASSWORD"),
                autocommit=True,
            )
            return
        except Exception:
            pass
        try:
            import psycopg2  # type: ignore

            self.kind = "psycopg2"
            self.conn = psycopg2.connect(
                host=str(pg["host"]),
                port=int(pg["port"]),
                user=str(pg["user"]),
                dbname=str(pg["dbname"]),
                password=pg.get("password") or os.environ.get("PGPASSWORD"),
            )
            self.conn.autocommit = True
            return
        except Exception as exc:
            raise RuntimeError(f"psycopg unavailable: {exc}") from exc

    def execute(self, sql: str, *, timeout_s: float = 300.0) -> str:
        _ = timeout_s
        assert self.conn is not None
        with self.conn.cursor() as cur:
            cur.execute(sql)
            try:
                rows = cur.fetchall()
            except Exception:
                return ""
        def serialize(value: Any) -> str:
            if value is None:
                return ""
            if isinstance(value, (dict, list)):
                return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            return str(value)

        return "\n".join("\t".join(serialize(value) for value in row) for row in rows)

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()


def make_connection(pg: Dict[str, Any]) -> Any:
    try:
        return PsycopgConnection(pg)
    except Exception:
        return PsqlPersistentConnection(pg)


def set_local_sql(params: Dict[str, float]) -> str:
    statements = ["BEGIN"]
    for name in sorted(COST_PARAM_SET):
        if name not in params:
            continue
        value = float(params[name])
        if math.isfinite(value):
            statements.append(f"SET LOCAL {name} = {value:.17g}")
    return ";\n".join(statements) + ";"


def build_explained_queries(
    estimator: PlanResourceEstimator,
    conn: Any,
    queries: Sequence[Dict[str, Any]],
    params: Dict[str, float],
) -> List[Dict[str, Any]]:
    explain_mode = estimator.plan_capture_modes[0] if estimator.plan_capture_modes else "generic"
    explained: List[Dict[str, Any]] = []
    conn.execute(set_local_sql(params), timeout_s=120.0)
    try:
        for item in queries:
            query_text = str(item.get("query") or "").strip().rstrip(";")
            if not query_text or not _is_plannable_query(query_text):
                continue
            errors: List[str] = []
            success: Optional[Dict[str, Any]] = None
            for explain_sql, plan_mode in estimator._build_explain_attempts(
                query_text,
                mode=explain_mode,
                queryid=item.get("queryid"),
            ):
                try:
                    # Keep EXPLAIN as the only row-producing statement. Psycopg
                    # exposes the final result set for a multi-statement string,
                    # so appending ROLLBACK used to discard the JSON plan.
                    conn.execute("SAVEPOINT scan_query;", timeout_s=30.0)
                    try:
                        raw = conn.execute(
                            explain_sql.rstrip().rstrip(";") + ";",
                            timeout_s=300.0,
                        )
                    finally:
                        conn.execute("ROLLBACK TO SAVEPOINT scan_query;", timeout_s=30.0)
                    plan_json = estimator._extract_explain_json(raw)
                    plan_doc = plan_json[0] if isinstance(plan_json, list) and plan_json else plan_json
                    if not isinstance(plan_doc, dict):
                        raise RuntimeError("unexpected plan document")
                    root = plan_doc.get("Plan") if isinstance(plan_doc, dict) else plan_doc
                    success = {
                        "queryid": item.get("queryid"),
                        "query": query_text,
                        "calls": item.get("calls"),
                        "replay_count": item.get("replay_count", item.get("calls", 1)),
                        "plan_mode": plan_mode,
                        "plan_doc": plan_doc,
                        "plan_root": root,
                    }
                    break
                except Exception as exc:
                    errors.append(f"{plan_mode}: {exc}")
            if success is not None:
                explained.append(success)
            else:
                explained.append(
                    {
                        "queryid": item.get("queryid"),
                        "query": query_text,
                        "calls": item.get("calls"),
                        "replay_count": item.get("replay_count", item.get("calls", 1)),
                        "error": " ; ".join(errors),
                    }
                )
    finally:
        try:
            conn.execute("ROLLBACK;", timeout_s=30.0)
        except Exception:
            pass
    return explained


def estimate_from_explained(
    estimator: PlanResourceEstimator,
    explained_queries: Sequence[Dict[str, Any]],
    workload_context: Dict[str, Any],
) -> Tuple[Dict[str, float], Dict[str, Any]]:
    cfg = estimator.estimator_cfg
    logging_cfg = cfg.get("logging") or {}
    log_topn = max(0, int(logging_cfg.get("log_topn", 10)))
    resource_types = [
        str(item).strip()
        for item in cfg.get("resource_types", ["memory", "cpu", "io"])
        if str(item).strip()
    ]
    shared_cfg = cfg.get("shared") or {}
    calibration_factor = _safe_float(shared_cfg.get("calibration_factor"), 1.0)
    query_estimates = estimator._build_query_estimates(
        explained_queries=explained_queries,
        workload_context=workload_context,
    )
    failed_estimates = [item for item in query_estimates if item.get("error")]
    if query_estimates and len(failed_estimates) == len(query_estimates):
        raise RuntimeError(
            "all query estimates failed; "
            f"errors={estimator._query_errors(query_estimates)[:3]}"
        )
    resource_avg: Dict[str, float] = {}
    resource_raw: Dict[str, float] = {}
    resource_units: Dict[str, str] = {}
    resource_raw_units: Dict[str, str] = {}
    resource_breakdown: Dict[str, Dict[str, Any]] = {}
    for resource_type in resource_types:
        result = estimator._build_resource_result(
            resource_type=resource_type,
            query_estimates=query_estimates,
            workload_context=workload_context,
            calibration_factor=calibration_factor,
            explain_workers=1,
        )
        resource_avg[resource_type] = float(result.get("value", result.get("raw_value", 0.0)))
        resource_raw[resource_type] = float(result.get("raw_value", resource_avg[resource_type]))
        resource_units[resource_type] = str(result.get("normalized_unit") or result.get("unit") or "")
        resource_raw_units[resource_type] = str(result.get("unit") or "")
        resource_breakdown[resource_type] = estimator._summarize_result_for_logging(result, topn=log_topn)
    metadata = {
        "failed_query_count": len(failed_estimates),
        "query_count": len(query_estimates),
        "estimated_resource_raw": resource_raw,
        "estimated_resource_units": resource_units,
        "estimated_resource_raw_units": resource_raw_units,
        "resource_breakdown": resource_breakdown,
    }
    return resource_avg, metadata


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=json_default)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def resource_signature(resource: Dict[str, float]) -> str:
    return "|".join(f"{name}={float(resource.get(name, 0.0)):.9g}" for name in ["cpu", "memory", "io"])


def worker_init(
    config: Dict[str, Any],
    queries: List[Dict[str, Any]],
    workload_context: Dict[str, Any],
    pg_endpoints: List[Dict[str, Any]],
    plan_capture_modes: List[str],
    custom_plan_params: Dict[str, List[Any]],
    baseline_calibration: Dict[str, Any],
) -> None:
    if not pg_endpoints:
        raise ValueError("at least one PostgreSQL endpoint is required")
    identity = multiprocessing.current_process()._identity  # type: ignore[attr-defined]
    worker_number = int(identity[0]) if identity else 1
    pg = dict(pg_endpoints[(worker_number - 1) % len(pg_endpoints)])
    estimator_cfg = copy.deepcopy(((config.get("metrics") or {}).get("plan_estimator")) or {})
    estimator_cfg.setdefault("shared", {})
    estimator_cfg["shared"]["explain_workers"] = 1
    estimator = PlanResourceEstimator(
        pg_host=str(pg["host"]),
        pg_port=str(pg["port"]),
        pg_user=str(pg["user"]),
        pg_dbname=str(pg["dbname"]),
        plan_capture_modes=plan_capture_modes,
        custom_plan_params=custom_plan_params,
        estimator_cfg=estimator_cfg,
    )
    scoring_cfg = config.get("scoring") or {}
    calculator = ResourceUtilityCalculator(
        reward_type="sigmoid_linear",
        penalty_type="sigmoid_linear",
        overload_threshold=float(scoring_cfg.get("overload_threshold", 0.80)),
        score_mode=str(scoring_cfg.get("score_mode", "band_target")),
        resource_weights=scoring_cfg.get("resource_weights") if isinstance(scoring_cfg.get("resource_weights"), dict) else None,
        resource_reward_weights=(
            scoring_cfg.get("resource_reward_weights")
            if isinstance(scoring_cfg.get("resource_reward_weights"), dict)
            else None
        ),
        resource_penalty_weights=(
            scoring_cfg.get("resource_penalty_weights")
            if isinstance(scoring_cfg.get("resource_penalty_weights"), dict)
            else None
        ),
        resource_target_ranges=(
            scoring_cfg.get("resource_target_ranges")
            if isinstance(scoring_cfg.get("resource_target_ranges"), dict)
            else None
        ),
    )
    _WORKER_STATE.clear()
    _WORKER_STATE.update(
        {
            "config": config,
            "queries": queries,
            "workload_context": workload_context,
            "pg": pg,
            "estimator": estimator,
            "calculator": calculator,
            "capability": {str(k): float(v) for k, v in (config.get("capability") or {}).items()},
            "conn": make_connection(pg),
            "worker_number": worker_number,
            "baseline_calibration": baseline_calibration,
        }
    )


def process_candidate_chunk(chunk: List[Tuple[int, Dict[str, float]]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    estimator: PlanResourceEstimator = _WORKER_STATE["estimator"]
    conn = _WORKER_STATE["conn"]
    queries = _WORKER_STATE["queries"]
    workload_context = _WORKER_STATE["workload_context"]
    calculator: ResourceUtilityCalculator = _WORKER_STATE["calculator"]
    capability = _WORKER_STATE["capability"]

    records: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    for index, params in chunk:
        started = time.perf_counter()
        sig = parameter_signature(params)
        try:
            explained = build_explained_queries(estimator, conn, queries, params)
            resource_before_correction, metadata = estimate_from_explained(
                estimator,
                explained,
                workload_context,
            )
            resource, correction = apply_resource_correction(
                resource_before_correction,
                _WORKER_STATE.get("baseline_calibration") or {},
            )
            metadata["baseline_correction"] = correction
            plan_delta_features = extract_plan_delta_features(explained)
            resource, estimated_db_qps_delta_pct, paired_delta = apply_paired_delta_calibration(
                resource,
                plan_delta_features,
                _WORKER_STATE.get("baseline_calibration") or {},
                params,
            )
            metadata["plan_delta_features"] = plan_delta_features
            metadata["paired_delta_calibration"] = paired_delta
            score = calculator.calculate_utility_score(resource, capability)
            utilization = {
                name: (float(resource.get(name, 0.0)) / float(capability[name]) * 100.0)
                for name in capability
                if name in resource and float(capability[name]) != 0.0
            }
            plan_hash = stable_hash(
                [
                    {"queryid": item.get("queryid"), "plan": item.get("plan_doc")}
                    for item in explained
                    if not item.get("error")
                ]
            )
            rec = {
                "candidate_index": index,
                "param_signature": sig,
                "params": params,
                "estimated_resource": resource,
                "estimated_resource_before_baseline_correction": resource_before_correction,
                "estimated_db_qps_delta_pct": estimated_db_qps_delta_pct,
                "plan_delta_features": plan_delta_features,
                "resource_error_profile": extract_error_profile({"metadata": metadata}),
                "estimated_resource_units": copy.deepcopy(metadata.get("estimated_resource_units") or {}),
                "estimated_resource_raw": copy.deepcopy(metadata.get("estimated_resource_raw") or {}),
                "estimated_resource_raw_units": copy.deepcopy(metadata.get("estimated_resource_raw_units") or {}),
                "utilization_pct": utilization,
                "score": float(score),
                "score_signature": f"{float(score):.12g}",
                "resource_signature": resource_signature(resource),
                "plan_hash": plan_hash,
                "estimation_metadata": metadata,
                "planning_endpoint": {
                    "host": str((_WORKER_STATE.get("pg") or {}).get("host") or ""),
                    "port": int((_WORKER_STATE.get("pg") or {}).get("port") or 0),
                    "dbname": str((_WORKER_STATE.get("pg") or {}).get("dbname") or ""),
                    "worker_number": int(_WORKER_STATE.get("worker_number") or 0),
                },
                "failed_query_count": int(metadata.get("failed_query_count", 0)),
                "query_count": int(metadata.get("query_count", len(queries))),
                "elapsed_ms": (time.perf_counter() - started) * 1000.0,
            }
            records.append(rec)
        except Exception as exc:
            failures.append(
                {
                    "candidate_index": index,
                    "param_signature": sig,
                    "params": params,
                    "error": str(exc),
                    "elapsed_ms": (time.perf_counter() - started) * 1000.0,
                }
            )
    return records, failures


def chunked(items: Sequence[Tuple[int, Dict[str, float]]], size: int) -> Iterable[List[Tuple[int, Dict[str, float]]]]:
    for idx in range(0, len(items), size):
        yield list(items[idx : idx + size])


def select_top_distinct(records: Sequence[Dict[str, Any]], key: str, limit: int = 20) -> List[Dict[str, Any]]:
    seen = set()
    result: List[Dict[str, Any]] = []
    for item in sorted(records, key=lambda row: float(row.get("score", float("-inf"))), reverse=True):
        value = item.get(key)
        if value in seen:
            continue
        seen.add(value)
        result.append(item)
        if len(result) >= limit:
            break
    return result


def select_top_robust_distinct(
    records: Sequence[Dict[str, Any]],
    *,
    variant: str = DEFAULT_ROBUST_VARIANT,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    seen = set()
    result: List[Dict[str, Any]] = []
    for item in sorted(records, key=lambda row: robust_sort_key(row, variant=variant), reverse=True):
        value = (item.get("resource_signature"), item.get("plan_hash"))
        if value in seen:
            continue
        seen.add(value)
        result.append(item)
        if len(result) >= limit:
            break
    return result


def _numeric_stats(values: Sequence[float]) -> Dict[str, Any]:
    nums = [float(value) for value in values if math.isfinite(float(value))]
    if not nums:
        return {"count": 0, "min": None, "max": None, "mean": None}
    return {
        "count": len(nums),
        "min": min(nums),
        "max": max(nums),
        "mean": sum(nums) / len(nums),
    }


def build_score_resource_bounds(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    resource_names = ["cpu", "memory", "io"]
    payload: Dict[str, Any] = {
        "candidate_count": len(records),
        "score": _numeric_stats([float(row.get("score", float("nan"))) for row in records]),
        "estimated_resource": {},
        "utilization_pct": {},
        "failed_query_count": _numeric_stats([float(row.get("failed_query_count", 0)) for row in records]),
        "elapsed_ms": _numeric_stats([float(row.get("elapsed_ms", float("nan"))) for row in records]),
    }
    for name in resource_names:
        payload["estimated_resource"][name] = _numeric_stats(
            [float((row.get("estimated_resource") or {}).get(name, float("nan"))) for row in records]
        )
        payload["utilization_pct"][name] = _numeric_stats(
            [float((row.get("utilization_pct") or {}).get(name, float("nan"))) for row in records]
        )
    return payload


def write_bounds_markdown(path: Path, bounds: Dict[str, Any]) -> None:
    lines = [
        "# Score and Resource Bounds",
        "",
        f"- candidate_count: `{bounds.get('candidate_count', 0)}`",
        "",
        "| metric | count | min | max | mean |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]

    def add_row(name: str, stats: Dict[str, Any]) -> None:
        def fmt(value: Any) -> str:
            if value is None:
                return ""
            return f"{float(value):.9g}"

        lines.append(
            f"| {name} | {int(stats.get('count', 0) or 0)} | {fmt(stats.get('min'))} | "
            f"{fmt(stats.get('max'))} | {fmt(stats.get('mean'))} |"
        )

    add_row("score", bounds.get("score") or {})
    for name, stats in (bounds.get("estimated_resource") or {}).items():
        add_row(f"estimated_resource.{name}", stats)
    for name, stats in (bounds.get("utilization_pct") or {}).items():
        add_row(f"utilization_pct.{name}", stats)
    add_row("failed_query_count", bounds.get("failed_query_count") or {})
    add_row("elapsed_ms", bounds.get("elapsed_ms") or {})
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_score_resource_bounds(output_dir: Path, records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    bounds = build_score_resource_bounds(records)
    write_json(output_dir / "score_resource_bounds.json", bounds)
    write_bounds_markdown(output_dir / "score_resource_bounds.md", bounds)
    return bounds


def build_plateau_summary(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(records)
    score_counts = Counter(str(row.get("score_signature")) for row in records)
    resource_counts = Counter(str(row.get("resource_signature")) for row in records)
    plan_counts = Counter(str(row.get("plan_hash")) for row in records)

    def summarize(counter: Counter[str]) -> Dict[str, Any]:
        distinct = len(counter)
        return {
            "distinct_count": distinct,
            "duplicate_count": max(0, total - distinct),
            "largest_plateaus": [
                {"signature": signature, "count": count}
                for signature, count in counter.most_common(20)
            ],
        }

    return {
        "candidate_count": total,
        "score": summarize(score_counts),
        "resource": summarize(resource_counts),
        "plan": summarize(plan_counts),
    }


def write_plateau_markdown(path: Path, plateau: Dict[str, Any]) -> None:
    lines = [
        "# Plateau Summary",
        "",
        f"- candidate_count: `{plateau.get('candidate_count', 0)}`",
        "",
        "| dimension | distinct_count | duplicate_count | largest_plateau_count |",
        "| --- | ---: | ---: | ---: |",
    ]
    for name in ["score", "resource", "plan"]:
        item = plateau.get(name) or {}
        largest = (item.get("largest_plateaus") or [{}])[0]
        lines.append(
            f"| {name} | {int(item.get('distinct_count', 0) or 0)} | "
            f"{int(item.get('duplicate_count', 0) or 0)} | {int(largest.get('count', 0) or 0)} |"
        )
    lines.append("")
    for name in ["score", "resource", "plan"]:
        lines.append(f"## {name}")
        for row in (plateau.get(name) or {}).get("largest_plateaus", [])[:10]:
            lines.append(f"- count=`{row.get('count')}` signature=`{row.get('signature')}`")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_plateau_summary(output_dir: Path, records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    plateau = build_plateau_summary(records)
    write_json(output_dir / "plateau_summary.json", plateau)
    write_plateau_markdown(output_dir / "plateau_summary.md", plateau)
    return plateau


def write_top_markdown(path: Path, rows: Sequence[Dict[str, Any]]) -> None:
    headers = [
        "rank",
        "score",
        "cpu",
        "memory",
        "io",
        "cpu_util_pct",
        "memory_util_pct",
        "io_util_pct",
        *COST_PARAMS,
        "failed_query_count",
        "plan_hash",
    ]
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for rank, row in enumerate(rows, start=1):
        params = row.get("params") or {}
        res = row.get("estimated_resource") or {}
        util = row.get("utilization_pct") or {}
        values: List[Any] = [
            rank,
            f"{float(row.get('score', 0.0)):.6f}",
            f"{float(res.get('cpu', 0.0)):.6f}",
            f"{float(res.get('memory', 0.0)):.6f}",
            f"{float(res.get('io', 0.0)):.6f}",
            f"{float(util.get('cpu', 0.0)):.3f}",
            f"{float(util.get('memory', 0.0)):.3f}",
            f"{float(util.get('io', 0.0)):.3f}",
        ]
        values.extend(f"{float(params.get(name, 0.0)):.9g}" for name in COST_PARAMS)
        values.append(int(row.get("failed_query_count", 0)))
        values.append(str(row.get("plan_hash", ""))[:12])
        lines.append("| " + " | ".join(str(v) for v in values) + " |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_robust_top_markdown(path: Path, rows: Sequence[Dict[str, Any]], *, variant: str) -> None:
    headers = [
        "rank",
        "robust_score",
        "point_score",
        "bounded_score",
        "target_uncertainty",
        "overload_penalty",
        "cpu",
        "memory",
        "io",
        *COST_PARAMS,
        "plan_hash",
    ]
    lines = [
        f"# Robust Top Candidates ({variant})",
        "",
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for rank, row in enumerate(rows, start=1):
        robust = ((row.get("robust_scores") or {}).get(variant) or row.get("robust_ranking") or {})
        params = row.get("params") or {}
        res = row.get("estimated_resource") or {}
        values: List[Any] = [
            rank,
            f"{float(robust.get('robust_score', 0.0)):.6f}",
            f"{float(robust.get('point_score', row.get('score', 0.0))):.6f}",
            f"{float(robust.get('bounded_score', 0.0)):.6f}",
            f"{float(robust.get('target_uncertainty', 0.0)):.6f}",
            f"{float(robust.get('overload_penalty', 0.0)):.6f}",
            f"{float(res.get('cpu', 0.0)):.6f}",
            f"{float(res.get('memory', 0.0)):.6f}",
            f"{float(res.get('io', 0.0)):.6f}",
        ]
        values.extend(f"{float(params.get(name, 0.0)):.9g}" for name in COST_PARAMS)
        values.append(str(row.get("plan_hash", ""))[:12])
        lines.append("| " + " | ".join(str(v) for v in values) + " |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_top_plan_details(
    path: Path,
    records: Sequence[Dict[str, Any]],
    config: Dict[str, Any],
    queries: List[Dict[str, Any]],
    workload_context: Dict[str, Any],
    pg: Dict[str, Any],
    plan_capture_modes: List[str],
    custom_plan_params: Dict[str, List[Any]],
) -> None:
    estimator_cfg = copy.deepcopy(((config.get("metrics") or {}).get("plan_estimator")) or {})
    estimator_cfg.setdefault("shared", {})
    estimator_cfg["shared"]["explain_workers"] = 1
    estimator = PlanResourceEstimator(
        pg_host=str(pg["host"]),
        pg_port=str(pg["port"]),
        pg_user=str(pg["user"]),
        pg_dbname=str(pg["dbname"]),
        plan_capture_modes=plan_capture_modes,
        custom_plan_params=custom_plan_params,
        estimator_cfg=estimator_cfg,
    )
    conn = make_connection(pg)
    details: List[Dict[str, Any]] = []
    try:
        for row in records:
            explained = build_explained_queries(estimator, conn, queries, row["params"])
            details.append(
                {
                    "candidate_index": row.get("candidate_index"),
                    "score": row.get("score"),
                    "plan_hash": row.get("plan_hash"),
                    "params": row.get("params"),
                    "plans": [
                        {
                            "queryid": item.get("queryid"),
                            "plan_mode": item.get("plan_mode"),
                            "plan_root": item.get("plan_root"),
                            "error": item.get("error"),
                            "plan_doc": item.get("plan_doc"),
                        }
                        for item in explained
                    ],
                }
            )
    finally:
        try:
            conn.close()
        except Exception:
            pass
    write_json(path, details)


def scan(args: argparse.Namespace) -> Dict[str, Any]:
    scenario = normalize_scenario(args.scenario)
    if scenario == "all":
        raise ValueError("scan() only accepts a concrete scenario")
    run_id, run_dir = default_scan_output_dir(args, scenario)
    run_dir.mkdir(parents=True, exist_ok=True)

    if args.exclusive_preflight:
        actions = run_exclusive_preflight(run_dir)
        print(
            "[Preflight] terminated_pids="
            f"{actions.get('terminated_pids', [])}, sigkilled_pids={actions.get('sigkilled_pids', [])}, "
            f"killed_tmux_sessions={[x.get('name') for x in actions.get('killed_tmux_sessions', [])]}",
            flush=True,
        )
    else:
        for name in ["preflight_processes_before.txt", "preflight_processes_after.txt"]:
            path = run_dir / name
            if not path.exists():
                path.write_text("", encoding="utf-8")

    config_path, bonect_md_path, config_source, range_source_hint = resolve_scenario_inputs(args, scenario)
    config = load_config(config_path)
    resource_model_paths: Dict[str, str] = {}
    if args.resource_model_dir:
        resource_model_paths = apply_resource_model_dir(
            config,
            Path(args.resource_model_dir),
        )
    baseline_calibration = load_baseline_calibration(
        Path(args.baseline_calibration_json) if args.baseline_calibration_json else None
    )
    pg = dict(config.get("postgres") or {})
    pg.setdefault("host", os.environ.get("PGHOST", "localhost"))
    pg.setdefault("port", os.environ.get("PGPORT", "5432"))
    pg.setdefault("user", os.environ.get("PGUSER", "postgres"))
    pg.setdefault("dbname", os.environ.get("PGDATABASE", "postgres"))
    pg_endpoints = [parse_pg_endpoint(value, pg) for value in (args.pg_endpoint or [])]
    if not pg_endpoints:
        pg_endpoints = [pg]

    range_json_path = Path(args.range_json) if args.range_json else None
    if args.use_config_param_grid:
        explicit_grid_values = optimizer_compact_grid_values(config, int(args.grid_points))
        ranges = {
            name: {
                "low": min(explicit_grid_values[name]),
                "high": max(explicit_grid_values[name]),
                "scale": "optimizer_compact",
            }
            for name in COST_PARAMS
        }
        range_metadata = {
            "range_source": "config.param_ranges+config.initial_params",
            "optimizer_compact_grid": True,
        }
        full_candidate_space = math.prod(len(explicit_grid_values[name]) for name in COST_PARAMS)
    else:
        ranges, range_metadata = load_scenario_ranges(
            scenario=scenario,
            bonect_md=bonect_md_path,
            range_json=range_json_path,
        )
        explicit_grid_values = {
            name: grid_values(
                ranges[name]["low"],
                ranges[name]["high"],
                args.grid_points,
                str(ranges[name].get("scale", "linear")),
            )
            for name in COST_PARAMS
        }
        full_candidate_space = expected_candidate_count(ranges, int(args.grid_points))
    range_payload = {
        "scenario": scenario,
        "bonect_md": str(bonect_md_path.resolve()) if bonect_md_path else None,
        "range_source": range_metadata.get("range_source", range_source_hint),
        "range_json": str(range_json_path.resolve()) if range_json_path else None,
        "grid_points": args.grid_points,
        "ranges": ranges,
        "grid_values": explicit_grid_values,
        "full_candidate_space": full_candidate_space,
        **range_metadata,
    }
    write_json(run_dir / "ranges.json", range_payload)

    plan_capture_cfg = config.get("plan_capture") or {}
    workload_sql_json = (
        None
        if args.workload_sql_from_pg_statements
        else Path(args.workload_sql_json) if args.workload_sql_json else find_default_workload_sql_json(scenario)
    )
    queries = load_workload_sql(
        pg=pg,
        output_path=run_dir / "workload_sql.json",
        workload_sql_json=workload_sql_json,
        limit=max(1, int(plan_capture_cfg.get("candidate_limit", 400))),
    )
    if not queries:
        raise RuntimeError("no plannable workload SQL found")
    if args.refresh_workload_stats:
        queries = refresh_workload_stats(queries, pg)
        write_json(
            run_dir / "workload_sql.json",
            {
                "source": str(workload_sql_json) if workload_sql_json else "pg_stat_statements",
                "stats_source": {
                    "host": pg.get("host"),
                    "port": pg.get("port"),
                    "dbname": pg.get("dbname"),
                },
                "stats_refreshed": True,
                "queries": queries,
            },
        )
    if args.workload_context_json:
        workload_context = load_workload_context(Path(args.workload_context_json))
    else:
        workload_context = build_workload_context(config, queries, pg)
    write_json(run_dir / "workload_context.json", workload_context)

    all_candidates = list(enumerate(candidate_iter_from_values(explicit_grid_values)))
    if args.limit_candidates:
        all_candidates = all_candidates[: int(args.limit_candidates)]
    shard_count = max(1, int(getattr(args, "shard_count", 1) or 1))
    shard_index = int(getattr(args, "shard_index", 0) or 0)
    if shard_count < 1:
        raise ValueError("--shard-count must be >= 1")
    if shard_index < 0 or shard_index >= shard_count:
        raise ValueError(f"--shard-index must satisfy 0 <= index < shard_count ({shard_count})")
    if shard_count > 1:
        all_candidates = [
            (idx, params)
            for idx, params in all_candidates
            if int(idx) % shard_count == shard_index
        ]
    completed = load_completed_signatures(run_dir / "candidates.jsonl") if args.resume else set()
    pending = [(idx, params) for idx, params in all_candidates if parameter_signature(params) not in completed]
    total = len(all_candidates)
    already_done = total - len(pending)
    scan_kind = "smoke" if args.limit_candidates else "full"
    print(
        f"[Scan] scenario={scenario} run_id={run_id} output={run_dir} total={total} pending={len(pending)} "
        f"already_done={already_done} queries={len(queries)} workers={args.candidate_workers} "
        f"shard={shard_index}/{shard_count}",
        flush=True,
    )

    plan_capture_modes = [
        str(mode).strip().lower()
        for mode in (plan_capture_cfg.get("modes") or ["generic"])
        if str(mode).strip()
    ] or ["generic"]
    custom_plan_params = {
        str(k): list(v) if isinstance(v, list) else [v]
        for k, v in (plan_capture_cfg.get("custom_plan_params") or {}).items()
    }
    write_json(
        run_dir / "scan_config.json",
        {
            "run_id": run_id,
            "scenario": scenario,
            "scan_kind": scan_kind,
            "args": vars(args),
            "config_path": str(config_path),
            "config_source": config_source,
            "resource_model_paths": resource_model_paths,
            "baseline_calibration": baseline_calibration,
            "workload_sql_json": str(workload_sql_json) if workload_sql_json else None,
            "pg": {k: v for k, v in pg.items() if "pass" not in k.lower()},
            "pg_endpoints": [
                {k: v for k, v in endpoint.items() if "pass" not in k.lower()}
                for endpoint in pg_endpoints
            ],
            "plan_capture_modes": plan_capture_modes,
            "explain_workers_per_candidate": args.explain_workers_per_candidate,
            "model_threads_per_candidate_worker": int(_MODEL_THREADS),
        },
    )

    candidates_path = run_dir / "candidates.jsonl"
    failures_path = run_dir / "failures.jsonl"
    candidates_path.touch(exist_ok=True)
    failures_path.touch(exist_ok=True)
    records_for_top: List[Dict[str, Any]] = []
    completed_count = already_done
    failure_count = 0
    best_score = float("-inf")
    start_ts = time.time()
    last_progress_ts = start_ts

    chunks = list(chunked(pending, int(args.candidate_chunk_size)))
    if chunks:
        with concurrent.futures.ProcessPoolExecutor(
            max_workers=max(1, int(args.candidate_workers)),
            initializer=worker_init,
            initargs=(
                config,
                queries,
                workload_context,
                pg_endpoints,
                plan_capture_modes,
                custom_plan_params,
                baseline_calibration,
            ),
        ) as executor:
            future_map = {executor.submit(process_candidate_chunk, chunk): len(chunk) for chunk in chunks}
            for future in concurrent.futures.as_completed(future_map):
                try:
                    records, failures = future.result()
                except Exception as exc:
                    records = []
                    failures = [{"error": f"chunk_failed: {exc}", "chunk_size": future_map[future]}]
                append_jsonl(candidates_path, records)
                append_jsonl(failures_path, failures)
                records_for_top.extend(records)
                completed_count += len(records) + len(failures)
                failure_count += len(failures)
                for row in records:
                    best_score = max(best_score, float(row.get("score", float("-inf"))))
                now = time.time()
                if now - last_progress_ts >= 30.0 or completed_count >= total:
                    elapsed = max(now - start_ts, 1e-9)
                    rate = max(0.0, (completed_count - already_done) / elapsed)
                    remaining = max(0, total - completed_count)
                    eta = remaining / rate if rate > 0 else float("inf")
                    eta_text = f"{eta:.1f}s" if math.isfinite(eta) else "inf"
                    print(
                        f"[Progress] scenario={scenario} completed={completed_count}/{total} "
                        f"rate={rate:.3f} candidates/s ETA={eta_text} "
                        f"failure_count={failure_count} current_best_score={best_score:.9g}",
                        flush=True,
                    )
                    last_progress_ts = now

    if args.resume and candidates_path.exists():
        records_for_top = []
        with candidates_path.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    records_for_top.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    capability = {str(k): float(v) for k, v in (config.get("capability") or {}).items()}
    baseline_resource = dict(baseline_calibration.get("actual_resource") or {})
    if not baseline_resource:
        initial_params = config.get("initial_params") or {}
        for row in records_for_top:
            params = row.get("params") or {}
            if all(
                math.isclose(
                    _safe_float(params.get(name), float("nan")),
                    _safe_float(initial_params.get(name), float("nan")),
                    rel_tol=1e-12,
                    abs_tol=1e-12,
                )
                for name in COST_PARAMS
            ):
                baseline_resource = dict(row.get("estimated_resource") or {})
                break
    for row in records_for_top:
        row["baseline_resource"] = dict(baseline_resource)
    records_for_top = attach_robust_scores(
        records_for_top,
        capability=capability,
        scenario=scenario,
        variants=ROBUST_VARIANTS,
        selected_variant=DEFAULT_ROBUST_VARIANT,
        baseline_resource=baseline_resource,
    )

    top_scores = select_top_distinct(records_for_top, "score_signature", limit=20)
    top_resources = select_top_distinct(records_for_top, "resource_signature", limit=20)
    top_plans = select_top_distinct(records_for_top, "plan_hash", limit=20)
    top_robust = select_top_robust_distinct(
        records_for_top,
        variant=DEFAULT_ROBUST_VARIANT,
        limit=20,
    )
    write_json(run_dir / "top20_distinct_scores.json", top_scores)
    write_top_markdown(run_dir / "top20_distinct_scores.md", top_scores)
    write_json(run_dir / "top20_distinct_resources.json", top_resources)
    write_json(run_dir / "top20_distinct_plans.json", top_plans)
    write_json(run_dir / f"top20_robust_{DEFAULT_ROBUST_VARIANT}.json", top_robust)
    write_robust_top_markdown(
        run_dir / f"top20_robust_{DEFAULT_ROBUST_VARIANT}.md",
        top_robust,
        variant=DEFAULT_ROBUST_VARIANT,
    )
    bounds = write_score_resource_bounds(run_dir, records_for_top)
    plateau = write_plateau_summary(run_dir, records_for_top)

    if args.save_top_plan_details and top_plans:
        write_top_plan_details(
            run_dir / "top20_plan_details.json",
            top_plans,
            config,
            queries,
            workload_context,
            pg,
            plan_capture_modes,
            custom_plan_params,
        )

    distinct_scores = {row.get("score_signature") for row in records_for_top}
    distinct_resources = {row.get("resource_signature") for row in records_for_top}
    distinct_plans = {row.get("plan_hash") for row in records_for_top}
    summary = {
        "run_id": run_id,
        "scenario": scenario,
        "status": "completed" if completed_count >= total and records_for_top else ("failed" if not records_for_top else "running"),
        "scan_kind": scan_kind,
        "output_dir": str(run_dir),
        "config_path": str(config_path),
        "config_source": config_source,
        "resource_model_paths": resource_model_paths,
        "baseline_calibration": baseline_calibration,
        "baseline_resource_for_ranking": baseline_resource,
        "workload_sql_json": str(workload_sql_json) if workload_sql_json else None,
        "range_source": range_payload.get("range_source"),
        "range_json": str(range_json_path) if range_json_path else None,
        "full_candidate_space": full_candidate_space,
        "total": total,
        "completed": completed_count,
        "total_candidates": total,
        "completed_candidates": completed_count,
        "successful_count": len(records_for_top),
        "failure_count": failure_count,
        "failure_rate": failure_count / max(1, completed_count),
        "distinct_score_count": len(distinct_scores),
        "distinct_resource_count": len(distinct_resources),
        "distinct_plan_count": len(distinct_plans),
        "best_score": max([float(row.get("score", float("-inf"))) for row in records_for_top], default=None),
        "robust_ranking": {
            "selected_variant": DEFAULT_ROBUST_VARIANT,
            "variants": list(ROBUST_VARIANTS),
            "best_robust_score": max(
                [
                    float(
                        ((row.get("robust_scores") or {}).get(DEFAULT_ROBUST_VARIANT) or {}).get(
                            "robust_score",
                            float("-inf"),
                        )
                    )
                    for row in records_for_top
                ],
                default=None,
            ),
            "top20_path": str(run_dir / f"top20_robust_{DEFAULT_ROBUST_VARIANT}.json"),
        },
        "score_bounds": bounds.get("score"),
        "estimated_resource_bounds": bounds.get("estimated_resource"),
        "utilization_pct_bounds": bounds.get("utilization_pct"),
        "plateau_summary": {
            "score": {
                "distinct_count": (plateau.get("score") or {}).get("distinct_count"),
                "duplicate_count": (plateau.get("score") or {}).get("duplicate_count"),
            },
            "resource": {
                "distinct_count": (plateau.get("resource") or {}).get("distinct_count"),
                "duplicate_count": (plateau.get("resource") or {}).get("duplicate_count"),
            },
            "plan": {
                "distinct_count": (plateau.get("plan") or {}).get("distinct_count"),
                "duplicate_count": (plateau.get("plan") or {}).get("duplicate_count"),
            },
        },
        "query_count": len(queries),
        "candidate_workers": int(args.candidate_workers),
        "model_threads_per_candidate_worker": int(_MODEL_THREADS),
        "started_at": start_ts,
        "finished_at": time.time(),
        "elapsed_s": time.time() - start_ts,
    }
    if range_payload.get("none_range_source"):
        summary["none_range_source"] = range_payload.get("none_range_source")
    write_json(run_dir / "scan_summary.json", summary)
    print(
        f"[Done] scenario={scenario} completed={summary['completed']} failures={summary['failure_count']} "
        f"distinct_scores={summary['distinct_score_count']} "
        f"distinct_resources={summary['distinct_resource_count']} distinct_plans={summary['distinct_plan_count']} "
        f"output={run_dir}",
        flush=True,
    )
    return summary


def expected_total_for_scenario(args: argparse.Namespace, scenario: str) -> Tuple[int, Dict[str, Any]]:
    config_path, bonect_md_path, config_source, range_source_hint = resolve_scenario_inputs(args, scenario)
    if args.use_config_param_grid:
        config = load_config(config_path)
        values = optimizer_compact_grid_values(config, int(args.grid_points))
        full_candidate_space = math.prod(len(values[name]) for name in COST_PARAMS)
        metadata = {
            "range_source": "config.param_ranges+config.initial_params",
            "optimizer_compact_grid": True,
        }
    else:
        ranges, metadata = load_scenario_ranges(
            scenario=scenario,
            bonect_md=bonect_md_path,
            range_json=Path(args.range_json) if args.range_json else None,
        )
        full_candidate_space = expected_candidate_count(ranges, int(args.grid_points))
    total = full_candidate_space
    if args.limit_candidates:
        total = min(total, int(args.limit_candidates))
    payload = {
        "config_path": str(config_path),
        "config_source": config_source,
        "bonect_md": str(bonect_md_path) if bonect_md_path else None,
        "range_source": metadata.get("range_source", range_source_hint),
        "full_candidate_space": full_candidate_space,
    }
    payload.update(metadata)
    return total, payload


def scan_all(args: argparse.Namespace) -> List[Dict[str, Any]]:
    run_id, run_root = default_run_root(args, "all")
    run_root.mkdir(parents=True, exist_ok=True)
    scenario_results: List[Dict[str, Any]] = []
    missing: List[Tuple[str, int, Dict[str, Any]]] = []

    for scenario in SCENARIO_ORDER:
        expected_total, metadata = expected_total_for_scenario(args, scenario)
        existing = find_existing_result(scenario, min_total=expected_total)
        if existing is not None:
            item = result_from_summary("reused_existing_result", existing, scenario=scenario)
            item.update({k: v for k, v in metadata.items() if k not in item})
            scenario_results.append(item)
            continue
        if args.check_existing_only:
            summary = {
                "scenario": scenario,
                "status": "missing",
                "total_candidates": expected_total,
                "completed_candidates": 0,
                "failure_rate": 0.0,
                "distinct_score_count": 0,
                "distinct_resource_count": 0,
                "distinct_plan_count": 0,
            }
            item = {
                "scenario": scenario,
                "status": "missing",
                "output_dir": str(run_root / scenario),
                "summary": summary,
                "top20_brief": [],
            }
            item.update(metadata)
            scenario_results.append(item)
        else:
            missing.append((scenario, expected_total, metadata))

    if args.check_existing_only:
        write_all_summary(run_root, scenario_results)
        print(f"[CheckExistingOnly] output={run_root}", flush=True)
        return scenario_results

    if not missing:
        write_all_summary(run_root, scenario_results)
        print(f"[All] all scenarios reused; output={run_root}", flush=True)
        return scenario_results

    if args.exclusive_preflight:
        actions = run_exclusive_preflight(run_root)
        print(
            "[Preflight] terminated_pids="
            f"{actions.get('terminated_pids', [])}, sigkilled_pids={actions.get('sigkilled_pids', [])}, "
            f"killed_tmux_sessions={[x.get('name') for x in actions.get('killed_tmux_sessions', [])]}",
            flush=True,
        )

    for scenario, _expected_total, metadata in missing:
        child_args = copy.copy(args)
        child_args.scenario = scenario
        child_args.output_dir = str(run_root / scenario)
        child_args.run_id = run_id
        child_args.resume = True
        child_args.exclusive_preflight = False
        child_args.check_existing_only = False
        if args.scenario == "all":
            child_args.config = None
            child_args.bonect_md = None
        print(f"[All] starting scenario={scenario} output={child_args.output_dir}", flush=True)
        try:
            summary = scan(child_args)
            item = result_from_summary(str(summary.get("status") or "completed"), summary, scenario=scenario)
            item.update({k: v for k, v in metadata.items() if k not in item})
            if summary.get("none_range_source"):
                item["none_range_source"] = summary.get("none_range_source")
            scenario_results.append(item)
        except Exception as exc:
            failed_summary = {
                "scenario": scenario,
                "status": "failed",
                "error": str(exc),
                "total_candidates": _expected_total,
                "completed_candidates": 0,
                "failure_rate": 1.0,
                "distinct_score_count": 0,
                "distinct_resource_count": 0,
                "distinct_plan_count": 0,
            }
            item = {
                "scenario": scenario,
                "status": "failed",
                "output_dir": str(run_root / scenario),
                "summary": failed_summary,
                "top20_brief": [],
                "error": str(exc),
            }
            item.update(metadata)
            scenario_results.append(item)
            write_json(run_root / scenario / "scan_summary.json", failed_summary)
            print(f"[All][ERROR] scenario={scenario} error={exc}", flush=True)
        write_all_summary(run_root, scenario_results)

    order = {name: idx for idx, name in enumerate(SCENARIO_ORDER)}
    scenario_results.sort(key=lambda item: order.get(str(item.get("scenario")), 999))
    write_all_summary(run_root, scenario_results)
    print(f"[All] output={run_root}", flush=True)
    return scenario_results


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bonect estimator-only full-grid scanner")
    parser.add_argument("--scenario", required=True, choices=["cpu", "memory", "mem", "io", "none", "all"])
    parser.add_argument("--bonect-md")
    parser.add_argument("--config")
    parser.add_argument(
        "--resource-model-dir",
        help="Directory containing baseline-anchored cpu/memory/io_resource_models.json.",
    )
    parser.add_argument(
        "--baseline-calibration-json",
        help=(
            "Real-baseline correction scales and actual resource values. "
            "Used for estimator fallback formulas and robust direction ranking."
        ),
    )
    parser.add_argument("--range-json")
    parser.add_argument("--grid-points", type=int, default=5)
    parser.add_argument(
        "--use-config-param-grid",
        action="store_true",
        help="Mirror the optimizer's min/current/max/quarter compact grid from config.",
    )
    parser.add_argument(
        "--pg-endpoint",
        action="append",
        help="Planning endpoint host:port[/database]; repeat to distribute workers.",
    )
    parser.add_argument("--workload-sql-json")
    parser.add_argument(
        "--workload-sql-from-pg-statements",
        action="store_true",
        help="Ignore historical/default workload SQL artifacts and read the current config PostgreSQL endpoint.",
    )
    parser.add_argument(
        "--refresh-workload-stats",
        action="store_true",
        help="Strictly refresh calls and total time for the selected queryids from the config PostgreSQL endpoint.",
    )
    parser.add_argument(
        "--workload-context-json",
        help="Frozen baseline workload context; avoids changing elapsed window, concurrency, and cache-hit semantics.",
    )
    parser.add_argument("--candidate-workers", type=int, default=16)
    parser.add_argument("--candidate-chunk-size", type=int, default=64)
    parser.add_argument("--explain-workers-per-candidate", type=int, default=1)
    parser.add_argument("--limit-candidates", type=int)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--shard-count",
        type=int,
        default=1,
        help="Split the full factorial candidate space across isolated scanner processes by candidate index.",
    )
    parser.add_argument(
        "--shard-index",
        type=int,
        default=0,
        help="Zero-based shard id; keep candidates where index %% shard_count == shard_index. Merge by signature.",
    )
    parser.add_argument("--run-id")
    parser.add_argument("--output-dir")
    parser.add_argument("--exclusive-preflight", action="store_true")
    parser.add_argument("--save-top-plan-details", action="store_true")
    parser.add_argument("--check-existing-only", action="store_true")
    return parser


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()
    if args.workload_sql_from_pg_statements and args.workload_sql_json:
        raise SystemExit("--workload-sql-from-pg-statements and --workload-sql-json are mutually exclusive")
    if args.grid_points < 1:
        raise SystemExit("--grid-points must be >= 1")
    if int(args.shard_count) < 1:
        raise SystemExit("--shard-count must be >= 1")
    if int(args.shard_index) < 0 or int(args.shard_index) >= int(args.shard_count):
        raise SystemExit("--shard-index must satisfy 0 <= index < --shard-count")
    if args.explain_workers_per_candidate != 1:
        print(
            "[WARN] nested explain concurrency is intentionally disabled; "
            "--explain-workers-per-candidate is recorded but workers stay at 1",
            flush=True,
        )
    args.scenario = normalize_scenario(args.scenario)
    if args.scenario == "all":
        args.resume = True
        if not args.check_existing_only:
            args.exclusive_preflight = True
        scan_all(args)
    else:
        scan(args)


if __name__ == "__main__":
    main()

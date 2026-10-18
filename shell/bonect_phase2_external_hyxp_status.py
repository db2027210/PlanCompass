#!/usr/bin/env python3
"""Summarize external HyBenchXP occupancy while Bonect phase-2 waits."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import signal
import subprocess
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


DEFAULT_JSONL = Path(
    "/disk2/resource_adaptive/binary_plan_tuning_hybenchxp/runs/"
    "hyxp_sequence_full_tmux_20260702_002109/cpu_binary_search_hybenchxp.jsonl"
)
DEFAULT_CONFIG = Path("config/binary_plan_tuning_hybenchxp.json")
DEFAULT_CURRENT = Path("report/current_bonect_run.json")
DEFAULT_RUN_DIR = Path(
    "history/pg_benchmark_bottleneck_matrix/"
    "pg_bonect_phase2_dbqps_amp_binary_top20_prune_v122_20260702_025943"
)


class ProcCmdlineReadTimeout(TimeoutError):
    pass


def _raise_proc_cmdline_timeout(signum: int, frame: Any) -> None:
    raise ProcCmdlineReadTimeout()


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_text(text, encoding="utf-8")
    tmp_path.replace(path)


def load_pid_file(path: Path) -> List[int]:
    data = load_json(path)
    raw_values: List[Any] = []
    if isinstance(data, dict):
        if isinstance(data.get("pids"), list):
            raw_values.extend(data.get("pids") or [])
        elif data.get("pid") is not None:
            raw_values.append(data.get("pid"))
    elif isinstance(data, list):
        raw_values.extend(data)
    else:
        try:
            raw_values.extend(path.read_text(encoding="utf-8").replace(",", "\n").splitlines())
        except Exception:
            pass
    pids: List[int] = []
    for value in raw_values:
        try:
            pid = int(str(value).strip())
        except Exception:
            continue
        if pid > 0 and pid not in pids:
            pids.append(pid)
    return pids


def iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if isinstance(obj, dict):
                yield obj


def finite_float(value: Any, default: float = float("nan")) -> float:
    try:
        parsed = float(value)
    except Exception:
        return default
    return parsed if math.isfinite(parsed) else default


def metric_scope_for_candidate(event: Dict[str, Any]) -> str:
    label = str(event.get("label") or "")
    target = str(event.get("target") or "").lower()
    if target != "memory" and not label.startswith("memory_"):
        return "current_metric"
    resource_metric = event.get("resource_metric") if isinstance(event.get("resource_metric"), dict) else {}
    memory_metric = resource_metric.get("memory") if isinstance(resource_metric.get("memory"), dict) else {}
    if (
        memory_metric.get("mode_effective") == "cgroup_working_set"
        and memory_metric.get("source") == "docker_cgroup_working_set"
    ):
        return "clean_working_set"
    return "legacy_or_mixed"


def read_proc_cmdline(path: Path, timeout_s: float = 0.25) -> str:
    old_handler = signal.getsignal(signal.SIGALRM)
    old_timer = signal.getitimer(signal.ITIMER_REAL)
    signal.signal(signal.SIGALRM, _raise_proc_cmdline_timeout)
    try:
        signal.setitimer(signal.ITIMER_REAL, timeout_s)
        raw = path.read_bytes()
    finally:
        signal.setitimer(signal.ITIMER_REAL, old_timer[0], old_timer[1])
        signal.signal(signal.SIGALRM, old_handler)
    return raw.replace(b"\0", b" ").decode("utf-8", "replace")


def _arg_value(parts: List[str], flag: str) -> Optional[str]:
    try:
        idx = parts.index(flag)
    except ValueError:
        return None
    if idx + 1 >= len(parts):
        return None
    return parts[idx + 1]


def _props_urls(path_text: str) -> List[str]:
    urls: List[str] = []
    try:
        with Path(path_text).open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                stripped = line.strip()
                if stripped.startswith(("url=", "url_tp=", "url_ap=")):
                    urls.append(stripped.split("=", 1)[1].strip())
    except Exception:
        pass
    return urls


def _config_postgres_targets(path_text: str) -> List[Dict[str, Any]]:
    payload = load_json(Path(path_text))
    if not isinstance(payload, dict):
        return []
    postgres = payload.get("postgres") or {}
    if not isinstance(postgres, dict):
        return []
    return [
        {
            "host": str(postgres.get("host") or ""),
            "port": str(postgres.get("port") or ""),
            "dbname": str(postgres.get("dbname") or ""),
            "source": str(path_text),
        }
    ]


def _url_matches_target(url: str, target_host: str, target_port: str, target_db: str) -> bool:
    if target_db and f"/{target_db}" not in url:
        return False
    host_port_matches = (
        bool(target_host and target_port and f"{target_host}:{target_port}" in url)
        or bool(target_port and f":{target_port}/" in url)
    )
    return bool(host_port_matches)


def _target_matches_external(
    raw: str,
    parts: List[str],
    *,
    target_host: str,
    target_port: str,
    target_db: str,
) -> Dict[str, Any]:
    props_path = _arg_value(parts, "-c")
    config_path = _arg_value(parts, "--config")
    urls = _props_urls(props_path) if props_path else []
    config_targets = _config_postgres_targets(config_path) if config_path else []

    url_match = any(_url_matches_target(url, target_host, target_port, target_db) for url in urls)
    config_match = any(
        str(row.get("host") or "") == target_host
        and str(row.get("port") or "") == target_port
        and (not target_db or str(row.get("dbname") or "") == target_db)
        for row in config_targets
    )
    raw_match = bool(
        target_db
        and target_host
        and target_port
        and target_db in raw
        and f"{target_host}:{target_port}" in raw
    )
    checked = bool(urls or config_targets or raw_match)
    return {
        "target_match": bool(url_match or config_match or raw_match),
        "target_checked": checked,
        "target_host": target_host,
        "target_port": target_port,
        "target_db": target_db,
        "props_path": props_path or "",
        "props_urls": urls[:6],
        "config_path": config_path or "",
        "config_targets": config_targets[:3],
        "target_match_reason": (
            "same_target"
            if (url_match or config_match or raw_match)
            else ("non_target" if checked else "unknown_target")
        ),
    }


def external_process_row(
    pid: int,
    raw: str,
    *,
    target_host: str,
    target_port: str,
    target_db: str,
) -> Optional[Dict[str, Any]]:
    parts = raw.split()
    parent = (
        "--output-dir" in parts
        and "binary-search" in parts
        and "/disk2/resource_adaptive/binary_plan_tuning_hybenchxp/runs/" in raw
    )
    runxp_child = (
        "-t" in parts
        and "runxp" in parts
        and (
            "/hybench_runtime/HyBench-2024-master/hybench_unblocked" in raw
            or "com.hybench.HyBench" in raw
        )
        and "binary_plan_tuning_hybenchxp" in raw
    )
    if not parent and not runxp_child:
        return None
    target_info = _target_matches_external(
        raw,
        parts,
        target_host=target_host,
        target_port=target_port,
        target_db=target_db,
    )
    return {
        "pid": pid,
        "kind": "parent" if parent else "runxp_child",
        "cmd": raw[:900],
        **target_info,
    }


def scan_external_processes_with_status(
    *,
    timeout_s: float = 2.0,
    target_host: str,
    target_port: str,
    target_db: str,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    timeout_s = max(1.0, float(timeout_s))
    try:
        completed = subprocess.run(
            ["ps", "-eo", "pid=,args="],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return rows, {
            "source": "ps",
            "status": "timeout",
            "timeout_s": timeout_s,
            "reliable_count": False,
        }
    except Exception:
        return rows, {
            "source": "ps",
            "status": "error",
            "timeout_s": timeout_s,
            "reliable_count": False,
        }
    if completed.returncode != 0:
        return rows, {
            "source": "ps",
            "status": "nonzero_exit",
            "returncode": completed.returncode,
            "timeout_s": timeout_s,
            "reliable_count": False,
        }
    for line in completed.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split(None, 1)
        if len(parts) != 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        row = external_process_row(
            pid,
            parts[1],
            target_host=target_host,
            target_port=target_port,
            target_db=target_db,
        )
        if row:
            rows.append(row)
    rows.sort(key=lambda item: int(item["pid"]))
    # Do not fall back to /proc/*/cmdline here. On this host some process-table
    # reads can block long enough to wedge handoff checks.
    return rows, {
        "source": "ps",
        "status": "ok",
        "timeout_s": timeout_s,
        "reliable_count": True,
    }


def scan_external_processes() -> List[Dict[str, Any]]:
    rows, _status = scan_external_processes_with_status(
        target_host="192.0.2.8",
        target_port="25432",
        target_db="hybench_sf10x",
    )
    return rows


def parse_pid_file(path: Path) -> Optional[int]:
    try:
        raw = path.read_text(encoding="utf-8").strip().splitlines()[0]
        pid = int(raw)
    except Exception:
        return None
    return pid if pid > 0 else None


def process_alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False
    return True


def pid_file_status(run_dir: Path, name: str) -> Dict[str, Any]:
    path = run_dir / name
    pid = parse_pid_file(path) if path.exists() else None
    alive = process_alive(pid)
    cmd = ""
    cmdline_read_error: Optional[str] = None
    if alive and pid is not None:
        try:
            cmd = read_proc_cmdline(Path("/proc") / str(pid) / "cmdline")
        except Exception as exc:
            cmdline_read_error = type(exc).__name__

    run_id = run_dir.name
    match_terms = [run_id, str(run_dir)]
    if name == "supervisor.pid":
        match_terms.append("bonect_supervisor.py")
    elif name == "runner.pid":
        match_terms.extend(["run_pg_benchmark_bottleneck_matrix.py", "parameter_optimizer.py"])
    cmd_matches_run = bool(cmd and any(term in cmd for term in match_terms if term))
    live_for_run = bool(alive and cmd_matches_run)
    return {
        "path": str(path),
        "exists": path.exists(),
        "pid": pid,
        "alive": alive,
        "cmd_matches_run": cmd_matches_run,
        "live_for_run": live_for_run,
        "cmd": cmd[:900],
        "cmdline_read_error": cmdline_read_error,
    }


def external_known_pid_status(
    pid: int,
    *,
    target_host: str,
    target_port: str,
    target_db: str,
) -> Dict[str, Any]:
    alive = process_alive(pid)
    cmd = ""
    cmdline_read_error: Optional[str] = None
    matches_external = False
    if alive:
        try:
            cmd = read_proc_cmdline(Path("/proc") / str(pid) / "cmdline")
        except Exception as exc:
            cmdline_read_error = type(exc).__name__
        matches_external = bool(
            "binary_plan_resource_tuner.py" in cmd
            and "binary_plan_tuning_hybenchxp" in cmd
        )
    target_info = _target_matches_external(
        cmd,
        cmd.split(),
        target_host=target_host,
        target_port=target_port,
        target_db=target_db,
    ) if matches_external else {
        "target_match": False,
        "target_checked": False,
        "target_host": target_host,
        "target_port": target_port,
        "target_db": target_db,
        "target_match_reason": "not_external_hyxp",
    }
    children: List[Dict[str, Any]] = []
    if alive:
        pid_text = str(pid)
        for child in Path("/proc").iterdir():
            if not child.name.isdigit():
                continue
            try:
                lines = (child / "status").read_text(
                    encoding="utf-8",
                    errors="replace",
                ).splitlines()
            except Exception:
                continue
            ppid = ""
            state = ""
            for line in lines:
                if line.startswith("PPid:"):
                    parts = line.split()
                    ppid = parts[1] if len(parts) > 1 else ""
                elif line.startswith("State:"):
                    state = line.split(None, 1)[1] if " " in line else ""
            if ppid != pid_text:
                continue
            child_cmd = ""
            child_error: Optional[str] = None
            try:
                child_cmd = read_proc_cmdline(child / "cmdline")
            except Exception as exc:
                child_error = type(exc).__name__
            children.append(
                {
                    "pid": int(child.name),
                    "state": state,
                    "cmd": child_cmd[:500],
                    "cmdline_read_error": child_error,
                }
            )
    children.sort(key=lambda item: int(item["pid"]))
    return {
        "pid": pid,
        "alive": alive,
        "cmd_matches_external_hyxp": matches_external,
        **target_info,
        "child_count": len(children),
        "children": children[:20],
        "cmd": cmd[:900],
        "cmdline_read_error": cmdline_read_error,
    }


def parse_launch_wait_log(
    run_dir: Path,
    *,
    target_host: str,
    target_port: str,
    target_db: str,
) -> Dict[str, Any]:
    path = run_dir / "launch_wait.log"
    if not path.exists():
        return {
            "path": str(path),
            "exists": False,
            "last_busy_at": None,
            "last_busy_count": None,
            "busy_rows": [],
        }
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 262144), os.SEEK_SET)
            text = handle.read().decode("utf-8", "replace")
    except Exception:
        return {
            "path": str(path),
            "exists": True,
            "last_busy_at": None,
            "last_busy_count": None,
            "busy_rows": [],
        }

    last_busy_at: Optional[str] = None
    last_busy_count: Optional[int] = None
    rows: List[Dict[str, Any]] = []
    current_rows: List[Dict[str, Any]] = []
    busy_re = re.compile(r"^\[(?P<ts>[^\]]+)\] external hybenchxp busy count=(?P<count>\d+)")
    busy_row_re = re.compile(r"^\[busy\]\s+(?P<pid>\d+)\s+(?P<cmd>.*)$")
    timestamp_re = re.compile(r"^\[\d{4}-\d{2}-\d{2} ")
    for line in text.splitlines():
        match = busy_re.match(line)
        if match:
            last_busy_at = match.group("ts")
            last_busy_count = int(match.group("count"))
            current_rows = []
            rows = current_rows
            continue
        if timestamp_re.match(line) and not line.startswith("[busy]"):
            current_rows = []
            continue
        row_match = busy_row_re.match(line)
        if row_match and last_busy_at is not None:
            cmd = row_match.group("cmd")
            kind = "parent" if " binary-search " in f" {cmd} " else "runxp_child"
            target_info = _target_matches_external(
                cmd,
                cmd.split(),
                target_host=target_host,
                target_port=target_port,
                target_db=target_db,
            )
            current_rows.append(
                {
                    "pid": int(row_match.group("pid")),
                    "kind": kind,
                    "cmd": cmd[:900],
                    **target_info,
                }
            )
            rows = current_rows

    return {
        "path": str(path),
        "exists": True,
        "last_busy_at": last_busy_at,
        "last_busy_count": last_busy_count,
        "busy_rows": rows,
    }


def summarize_jsonl(path: Path, config_path: Path) -> Dict[str, Any]:
    config = load_json(config_path) if config_path.exists() else {}
    search_cfg = (((config or {}).get("binary_plan_tuning") or {}).get("binary_search") or {})
    max_rounds = int(search_cfg.get("max_rounds") or 0) or None
    max_candidates = int(search_cfg.get("max_candidates_per_round") or 0) or None

    by_round: Dict[str, int] = {}
    clean_by_round: Dict[str, int] = {}
    candidate_rows: List[Dict[str, Any]] = []
    clean_candidate_rows: List[Dict[str, Any]] = []
    legacy_or_mixed_candidate_rows: List[Dict[str, Any]] = []
    accepted: List[Dict[str, Any]] = []
    legacy_or_mixed_accepted: List[Dict[str, Any]] = []
    fallback_high_dbqps: List[Dict[str, Any]] = []
    high_dbqps_rejected: List[Dict[str, Any]] = []
    legacy_or_mixed_high_dbqps_rejected: List[Dict[str, Any]] = []
    metric_scope_counts: Counter[str] = Counter()
    round_events: List[Dict[str, Any]] = []
    started_rounds: set[int] = set()
    completed_rounds: set[int] = set()
    completed_round_elapsed: List[float] = []

    for event in iter_jsonl(path):
        kind = event.get("event")
        if kind == "candidate_validation":
            round_key = str(event.get("round"))
            by_round[round_key] = by_round.get(round_key, 0) + 1
            decision = event.get("decision") or {}
            metric_scope = metric_scope_for_candidate(event)
            metric_scope_counts[metric_scope] += 1
            resource_metric = event.get("resource_metric") if isinstance(event.get("resource_metric"), dict) else {}
            memory_metric = resource_metric.get("memory") if isinstance(resource_metric.get("memory"), dict) else {}
            if metric_scope != "legacy_or_mixed":
                clean_by_round[round_key] = clean_by_round.get(round_key, 0) + 1
            row = {
                "round": event.get("round"),
                "candidate_index": event.get("candidate_index"),
                "label": event.get("label"),
                "accepted": bool(event.get("accepted")),
                "metric_scope": metric_scope,
                "memory_mode_effective": memory_metric.get("mode_effective"),
                "memory_source": memory_metric.get("source"),
                "reject_reason": event.get("accept_reject_reason") or event.get("reject_reason"),
                "db_qps": event.get("db_qps"),
                "db_qps_delta_pct": decision.get("db_qps_delta_pct"),
                "target_delta_pct": decision.get("target_delta_pct"),
                "xp_qps_delta_pct": decision.get("hybench_xp_qps_delta_pct"),
                "xp_tps_delta_pct": decision.get("hybench_xp_tps_delta_pct"),
                "params": event.get("effective_params") or event.get("params"),
                "failed_checks": [
                    name
                    for name in (
                        "status_ok",
                        "target_ok",
                        "db_qps_ok",
                        "hybench_qps_guard_ok",
                        "hybench_tps_guard_ok",
                        "p95_guard_ok",
                        "p99_guard_ok",
                        "non_target_ok",
                        "resource_explanation_ok",
                    )
                    if decision.get(name) is not True
                ],
            }
            candidate_rows.append(row)
            if metric_scope == "legacy_or_mixed":
                legacy_or_mixed_candidate_rows.append(row)
            else:
                clean_candidate_rows.append(row)
            if row["accepted"] and metric_scope != "legacy_or_mixed":
                accepted.append(row)
            elif row["accepted"]:
                legacy_or_mixed_accepted.append(row)
            elif (
                metric_scope != "legacy_or_mixed"
                and all(
                    decision.get(name) is True
                    for name in (
                        "status_ok",
                        "target_ok",
                        "db_qps_ok",
                        "hybench_qps_guard_ok",
                        "hybench_tps_guard_ok",
                        "p95_guard_ok",
                        "p99_guard_ok",
                        "non_target_ok",
                    )
                )
                and decision.get("resource_explanation_ok") is not True
                and finite_float(row.get("db_qps_delta_pct"), -1e9) > 0.0
                and finite_float(row.get("target_delta_pct"), 1e9) < 0.0
            ):
                fallback_high_dbqps.append(row)
            elif metric_scope != "legacy_or_mixed" and finite_float(row.get("db_qps_delta_pct"), -1e9) >= 10.0:
                high_dbqps_rejected.append(row)
            elif metric_scope == "legacy_or_mixed" and finite_float(row.get("db_qps_delta_pct"), -1e9) >= 10.0:
                legacy_or_mixed_high_dbqps_rejected.append(row)
        elif kind in {"round_start", "round_parallel_validation_start", "round_summary", "round_stop"}:
            try:
                round_num = int(event.get("round"))
            except Exception:
                round_num = 0
            if round_num > 0 and kind in {"round_start", "round_parallel_validation_start"}:
                started_rounds.add(round_num)
                by_round.setdefault(str(round_num), by_round.get(str(round_num), 0))
            row = {
                key: event.get(key)
                for key in [
                    "event",
                    "round",
                    "candidate_count",
                    "validation_count",
                    "accepted_count",
                    "round_elapsed_s",
                    "range_update_anchor",
                    "reason",
                ]
                if key in event
            }
            round_events.append(row)
            if kind == "round_summary":
                if round_num > 0:
                    started_rounds.add(round_num)
                    completed_rounds.add(round_num)
                elapsed = finite_float(event.get("round_elapsed_s"))
                if math.isfinite(elapsed):
                    completed_round_elapsed.append(elapsed)

    accepted_by_db = sorted(
        accepted,
        key=lambda item: finite_float(item.get("db_qps"), -1e9),
        reverse=True,
    )
    accepted_by_target_drop = sorted(
        accepted,
        key=lambda item: finite_float(item.get("target_delta_pct"), 1e9),
    )
    fallback_high_dbqps = sorted(
        fallback_high_dbqps,
        key=lambda item: finite_float(item.get("db_qps"), -1e9),
        reverse=True,
    )
    high_dbqps_rejected = sorted(
        high_dbqps_rejected,
        key=lambda item: finite_float(item.get("db_qps_delta_pct"), -1e9),
        reverse=True,
    )
    legacy_or_mixed_high_dbqps_rejected = sorted(
        legacy_or_mixed_high_dbqps_rejected,
        key=lambda item: finite_float(item.get("db_qps_delta_pct"), -1e9),
        reverse=True,
    )

    seen_rounds = [
        int(k)
        for k in by_round
        if str(k).isdigit()
    ] + sorted(started_rounds)
    current_round = max(seen_rounds, default=None)
    current_round_validated = by_round.get(str(current_round), 0) if current_round is not None else 0
    current_round_rows = [
        row for row in candidate_rows
        if row.get("round") == current_round
    ]
    current_round_clean_rows = [
        row for row in current_round_rows
        if row.get("metric_scope") != "legacy_or_mixed"
    ]
    current_round_accepted = [
        row for row in current_round_clean_rows
        if row.get("accepted")
    ]
    current_round_rejected = [
        row for row in current_round_clean_rows
        if not row.get("accepted")
    ]
    current_round_reject_reason_counts = Counter(
        str(row.get("reject_reason") or "unknown")
        for row in current_round_rejected
    )
    current_round_high_dbqps_rejected = sorted(
        [
            row for row in current_round_rejected
            if finite_float(row.get("db_qps_delta_pct"), -1e9) >= 10.0
        ],
        key=lambda item: finite_float(item.get("db_qps_delta_pct"), -1e9),
        reverse=True,
    )
    avg_round_elapsed = (
        sum(completed_round_elapsed) / len(completed_round_elapsed)
        if completed_round_elapsed
        else None
    )
    eta_upper_bound_s: Optional[float] = None
    if max_rounds and max_candidates and current_round and avg_round_elapsed:
        current_fraction_remaining = max(0.0, (max_candidates - current_round_validated) / max_candidates)
        future_full_rounds = max(0, max_rounds - current_round)
        eta_upper_bound_s = avg_round_elapsed * (current_fraction_remaining + future_full_rounds)

    return {
        "jsonl": str(path),
        "jsonl_exists": path.exists(),
        "jsonl_mtime": (
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(path.stat().st_mtime))
            if path.exists()
            else None
        ),
        "max_rounds": max_rounds,
        "max_candidates_per_round": max_candidates,
        "candidate_by_round": by_round,
        "current_metric_candidate_by_round": clean_by_round,
        "candidate_metric_scope_counts": dict(metric_scope_counts),
        "current_metric_candidate_count": len(clean_candidate_rows),
        "legacy_or_mixed_candidate_count": len(legacy_or_mixed_candidate_rows),
        "started_rounds": sorted(started_rounds),
        "completed_rounds": sorted(completed_rounds),
        "accepted_count": len(accepted),
        "accepted_count_scope": "current_metric_only",
        "legacy_or_mixed_accepted_count": len(legacy_or_mixed_accepted),
        "accepted_by_db_qps_top": accepted_by_db[:10],
        "accepted_by_target_drop_top": accepted_by_target_drop[:10],
        "fallback_high_dbqps_policy": (
            "current_metric_rejected_target_down_dbqps_up_all_guards_pass_except_resource_explanation"
        ),
        "fallback_high_dbqps_top": fallback_high_dbqps[:10],
        "high_dbqps_rejected_top": high_dbqps_rejected[:10],
        "legacy_or_mixed_high_dbqps_rejected_top": legacy_or_mixed_high_dbqps_rejected[:10],
        "last_round_events": round_events[-12:],
        "current_round": current_round,
        "current_round_validated": current_round_validated,
        "current_round_current_metric_validated": len(current_round_clean_rows),
        "current_round_accepted": current_round_accepted,
        "current_round_accepted_count": len(current_round_accepted),
        "current_round_rejected_count": len(current_round_rejected),
        "current_round_reject_reason_counts": dict(current_round_reject_reason_counts),
        "current_round_high_dbqps_rejected_top": current_round_high_dbqps_rejected[:10],
        "current_round_latest_validations": current_round_rows[-12:],
        "avg_completed_round_elapsed_s": avg_round_elapsed,
        "eta_upper_bound_s": eta_upper_bound_s,
    }


def format_pct(value: Any) -> str:
    parsed = finite_float(value)
    if not math.isfinite(parsed):
        return "n/a"
    return f"{parsed:+.2f}%"


def write_markdown(path: Path, payload: Dict[str, Any]) -> None:
    external = payload["external"]
    current = payload.get("current_bonect") or {}
    procs = payload.get("external_processes") or []
    proc_count = payload.get("external_process_count")
    effective = payload.get("effective_wait_state") or {}
    pid_status = payload.get("run_dir_pid_status") or {}
    known_pid_status = payload.get("known_external_pid_status") or []
    external_scan = payload.get("external_process_scan") or {}
    lines: List[str] = [
        "# Bonect phase-2 external HyBenchXP status",
        "",
        f"- generated_at: `{payload['generated_at']}`",
        f"- target: `{payload.get('target')}`",
        f"- run_dir_run: `{payload.get('run_dir_run_id')}`",
        f"- current_file_run: `{current.get('run_id')}`",
        f"- current_file_matches_run_dir: `{payload.get('current_matches_run_dir')}`",
        f"- current_file_status: `{current.get('status')}`",
        f"- run_dir: `{payload.get('run_dir')}`",
        f"- effective_wait_state: `{effective.get('state')}`",
        f"- effective_wait_reason: `{effective.get('reason')}`",
        f"- replay_safe: `{payload.get('replay_safe')}`",
        f"- launcher_pid: `{current.get('launcher_pid')}`",
        f"- runner_pid: `{current.get('runner_pid')}`",
        f"- supervisor_pid: `{current.get('supervisor_pid')}`",
        f"- external_process_count: `{proc_count}`",
        f"- blocking_external_process_count: `{payload.get('blocking_external_process_count')}`",
        f"- external_process_source: `{payload.get('external_process_source')}`",
        f"- external_process_scan_status: `{external_scan.get('status')}`",
        f"- external_process_count_reliable: `{external_scan.get('reliable_count')}`",
        f"- external_jsonl_mtime: `{external.get('jsonl_mtime')}`",
        "",
        "## Run Directory PID Files",
        "",
        "| file | exists | pid | alive | matches run | live for run |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name in ["launcher.pid", "runner.pid", "supervisor.pid"]:
        row = pid_status.get(name) or {}
        lines.append(
            f"| `{name}` | `{bool(row.get('exists'))}` | `{row.get('pid')}` | "
            f"`{bool(row.get('alive'))}` | `{bool(row.get('cmd_matches_run'))}` | "
            f"`{bool(row.get('live_for_run'))}` |"
        )
    if known_pid_status:
        lines.extend(
            [
                "",
                "## Known External PID Checks",
                "",
                "| pid | alive | matches external HYXP | child count | read error |",
                "|---:|---:|---:|---:|---|",
            ]
        )
        for row in known_pid_status:
            lines.append(
                f"| `{row.get('pid')}` | `{bool(row.get('alive'))}` | "
                f"`{bool(row.get('cmd_matches_external_hyxp'))}` | "
                f"`{row.get('child_count')}` | "
                f"`{row.get('cmdline_read_error')}` |"
            )
        child_rows: List[Dict[str, Any]] = []
        for row in known_pid_status:
            child_rows.extend(row.get("children") or [])
        if child_rows:
            lines.extend(
                [
                    "",
                    "### Known External Direct Children",
                    "",
                ]
            )
            for child in child_rows[:20]:
                lines.append(
                    f"- `{child.get('pid')}` `{child.get('state')}` {child.get('cmd')}"
                )
    lines.extend(
        [
            "",
        "## External Progress",
        "",
        f"- max_rounds: `{external.get('max_rounds')}`",
        f"- max_candidates_per_round: `{external.get('max_candidates_per_round')}`",
        f"- candidate_by_round: `{external.get('candidate_by_round')}`",
        f"- current_metric_candidate_by_round: `{external.get('current_metric_candidate_by_round')}`",
        f"- candidate_metric_scope_counts: `{external.get('candidate_metric_scope_counts')}`",
        f"- current_metric_candidate_count: `{external.get('current_metric_candidate_count')}`",
        f"- legacy_or_mixed_candidate_count: `{external.get('legacy_or_mixed_candidate_count')}`",
        f"- started_rounds: `{external.get('started_rounds')}`",
        f"- completed_rounds: `{external.get('completed_rounds')}`",
        f"- current_round: `{external.get('current_round')}`",
        f"- current_round_validated: `{external.get('current_round_validated')}`",
        f"- current_round_current_metric_validated: `{external.get('current_round_current_metric_validated')}`",
        f"- accepted_count: `{external.get('accepted_count')}`",
        f"- accepted_count_scope: `{external.get('accepted_count_scope')}`",
        f"- legacy_or_mixed_accepted_count: `{external.get('legacy_or_mixed_accepted_count')}`",
        ]
    )
    eta = external.get("eta_upper_bound_s")
    if eta is not None:
        lines.append(f"- rough_upper_bound_eta_hours_if_max_rounds: `{eta / 3600.0:.2f}`")

    lines.extend(
        [
            "",
            "## Current Round Diagnostics",
            "",
            f"- current_round_accepted_count: `{external.get('current_round_accepted_count')}`",
            f"- current_round_rejected_count: `{external.get('current_round_rejected_count')}`",
            f"- current_round_reject_reason_counts: `{external.get('current_round_reject_reason_counts')}`",
        ]
    )
    current_round_accepted = external.get("current_round_accepted") or []
    if current_round_accepted:
        lines.extend(
            [
                "",
                "### Current Round Accepted",
                "",
                "| label | DB QPS | DB QPS delta | target delta | XP QPS delta | XP TPS delta |",
                "|---|---:|---:|---:|---:|---:|",
            ]
        )
        for row in current_round_accepted:
            lines.append(
                f"| `{row.get('label')}` | `{row.get('db_qps')}` | {format_pct(row.get('db_qps_delta_pct'))} | "
                f"{format_pct(row.get('target_delta_pct'))} | {format_pct(row.get('xp_qps_delta_pct'))} | "
                f"{format_pct(row.get('xp_tps_delta_pct'))} |"
            )
    current_round_high = external.get("current_round_high_dbqps_rejected_top") or []
    if current_round_high:
        lines.extend(
            [
                "",
                "### Current Round High DB QPS Rejected",
                "",
                "| label | DB QPS delta | target delta | reason |",
                "|---|---:|---:|---|",
            ]
        )
        for row in current_round_high:
            lines.append(
                f"| `{row.get('label')}` | {format_pct(row.get('db_qps_delta_pct'))} | "
                f"{format_pct(row.get('target_delta_pct'))} | `{row.get('reject_reason')}` |"
            )

    lines.extend(["", "## Accepted By DB QPS", "", "| rank | label | DB QPS | DB QPS delta | target delta | XP QPS delta | XP TPS delta |", "|---:|---|---:|---:|---:|---:|---:|"])
    for idx, row in enumerate(external.get("accepted_by_db_qps_top") or [], start=1):
        lines.append(
            f"| {idx} | `{row.get('label')}` | `{row.get('db_qps')}` | {format_pct(row.get('db_qps_delta_pct'))} | "
            f"{format_pct(row.get('target_delta_pct'))} | {format_pct(row.get('xp_qps_delta_pct'))} | "
            f"{format_pct(row.get('xp_tps_delta_pct'))} |"
        )

    lines.extend(
        [
            "",
            "## Fallback High DB QPS (Not Accepted)",
            "",
            "These points use the current metric, reduce the target resource, improve DB QPS, and pass all guards except resource explanation.",
            "",
            "| rank | label | DB QPS | DB QPS delta | target delta | XP QPS delta | XP TPS delta | failed checks |",
            "|---:|---|---:|---:|---:|---:|---:|---|",
        ]
    )
    for idx, row in enumerate(external.get("fallback_high_dbqps_top") or [], start=1):
        lines.append(
            f"| {idx} | `{row.get('label')}` | `{row.get('db_qps')}` | "
            f"{format_pct(row.get('db_qps_delta_pct'))} | {format_pct(row.get('target_delta_pct'))} | "
            f"{format_pct(row.get('xp_qps_delta_pct'))} | {format_pct(row.get('xp_tps_delta_pct'))} | "
            f"`{row.get('failed_checks')}` |"
        )

    lines.extend(["", "## High DB QPS Rejected", "", "| rank | label | DB QPS delta | target delta | reason |", "|---:|---|---:|---:|---|"])
    for idx, row in enumerate(external.get("high_dbqps_rejected_top") or [], start=1):
        lines.append(
            f"| {idx} | `{row.get('label')}` | {format_pct(row.get('db_qps_delta_pct'))} | "
            f"{format_pct(row.get('target_delta_pct'))} | `{row.get('reject_reason')}` |"
        )

    legacy_high = external.get("legacy_or_mixed_high_dbqps_rejected_top") or []
    if legacy_high:
        lines.extend(
            [
                "",
                "## Legacy/Mixed High DB QPS Rejected Audit",
                "",
                "| rank | label | DB QPS delta | target delta | reason | scope |",
                "|---:|---|---:|---:|---|---|",
            ]
        )
        for idx, row in enumerate(legacy_high, start=1):
            lines.append(
                f"| {idx} | `{row.get('label')}` | {format_pct(row.get('db_qps_delta_pct'))} | "
                f"{format_pct(row.get('target_delta_pct'))} | `{row.get('reject_reason')}` | "
                f"`{row.get('metric_scope')}` |"
            )

    lines.extend(["", "## Process Snapshot", ""])
    for row in procs[:20]:
        lines.append(
            f"- `{row.get('pid')}` `{row.get('kind')}` "
            f"target_match=`{row.get('target_match')}` "
            f"target_reason=`{row.get('target_match_reason')}` {row.get('cmd')}"
        )
    blocking = payload.get("blocking_external_processes") or []
    if blocking:
        lines.extend(["", "## Blocking Same-Target Processes", ""])
        for row in blocking[:20]:
            lines.append(f"- `{row.get('pid')}` `{row.get('kind')}` {row.get('cmd')}")
    atomic_write_text(path, "\n".join(lines) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jsonl", type=Path, default=DEFAULT_JSONL)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--current", type=Path, default=DEFAULT_CURRENT)
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    parser.add_argument("--json-output", type=Path, default=Path("report/bonect/phase2_external_hyxp_status_latest.json"))
    parser.add_argument("--md-output", type=Path, default=Path("report/bonect/phase2_external_hyxp_status_latest.md"))
    parser.add_argument(
        "--scan-proc",
        action="store_true",
        help="Scan /proc/*/cmdline directly. Default uses launcher log to avoid process-table hangs.",
    )
    parser.add_argument(
        "--known-external-pid",
        type=int,
        action="append",
        default=[],
        help="Known external HyBenchXP tuner parent PID to verify without scanning the full process table.",
    )
    parser.add_argument(
        "--known-external-pid-file",
        type=Path,
        action="append",
        default=[],
        help="JSON or text file containing known external HyBenchXP tuner parent PID(s).",
    )
    parser.add_argument(
        "--require-replay-safe",
        action="store_true",
        help="Exit non-zero unless the effective wait state proves replay is safe.",
    )
    parser.add_argument(
        "--process-scan-timeout-sec",
        type=float,
        default=8.0,
        help="Timeout for ps-based external HyBenchXP process scan when --scan-proc is used.",
    )
    parser.add_argument("--target-host", default="192.0.2.8")
    parser.add_argument("--target-port", default="25432")
    parser.add_argument("--target-db", default="hybench_sf10x")
    args = parser.parse_args()

    current = load_json(args.current) if args.current.exists() else {}
    target_host = str(args.target_host)
    target_port = str(args.target_port)
    target_db = str(args.target_db)
    launcher_wait = parse_launch_wait_log(
        args.run_dir,
        target_host=target_host,
        target_port=target_port,
        target_db=target_db,
    )
    external_process_scan: Dict[str, Any] = {
        "source": "launch_wait_log",
        "status": "not_requested",
        "reliable_count": True,
    }
    if args.scan_proc:
        external_processes, external_process_scan = scan_external_processes_with_status(
            timeout_s=args.process_scan_timeout_sec,
            target_host=target_host,
            target_port=target_port,
            target_db=target_db,
        )
        external_process_source = str(external_process_scan.get("source") or "ps")
        external_process_count = len(external_processes)
    else:
        external_processes = launcher_wait.get("busy_rows") or []
        external_process_source = "launch_wait_log"
        external_process_count = launcher_wait.get("last_busy_count")
        if external_process_count is None:
            external_process_count = len(external_processes)
    blocking_external_processes = [
        row for row in external_processes
        if isinstance(row, dict) and bool(row.get("target_match"))
    ]
    blocking_external_process_count = len(blocking_external_processes)
    run_dir_run_id = args.run_dir.name
    current_run_id = str((current or {}).get("run_id") or "")
    current_matches_run_dir = bool(current_run_id and current_run_id == run_dir_run_id)
    current_status = str((current or {}).get("status") or "") if current_matches_run_dir else ""
    current_external_count = 0
    if current_matches_run_dir:
        try:
            current_external_count = int((current or {}).get("last_external_hyxp_count") or 0)
        except Exception:
            current_external_count = 0
    pid_status = {
        name: pid_file_status(args.run_dir, name)
        for name in ["launcher.pid", "runner.pid", "supervisor.pid"]
    }
    known_external_pids: List[int] = []
    for pid in args.known_external_pid:
        if int(pid) > 0 and int(pid) not in known_external_pids:
            known_external_pids.append(int(pid))
    for pid_file in args.known_external_pid_file:
        for pid in load_pid_file(pid_file):
            if pid not in known_external_pids:
                known_external_pids.append(pid)

    known_external_pid_status = [
        external_known_pid_status(
            pid,
            target_host=target_host,
            target_port=target_port,
            target_db=target_db,
        )
        for pid in known_external_pids
        if int(pid) > 0
    ]
    live_known_external = [
        row for row in known_external_pid_status
        if (
            row.get("alive")
            and row.get("cmd_matches_external_hyxp")
            and row.get("target_match")
        )
    ]
    live_pid_rows = [
        row for row in pid_status.values()
        if row.get("live_for_run")
    ]
    if live_pid_rows:
        effective_wait_state = {
            "state": "bonect_started",
            "reason": "runner/supervisor pid file is alive and matches current run",
        }
    elif live_known_external:
        effective_wait_state = {
            "state": "external_busy",
            "reason": "known external HyBenchXP tuner parent PID is alive and matches expected command",
        }
    elif not bool(external_process_scan.get("reliable_count", True)):
        effective_wait_state = {
            "state": "external_scan_inconclusive",
            "reason": (
                "external process snapshot failed or timed out; do not treat zero process "
                "count as replay-safe without a successful follow-up check"
            ),
        }
    elif blocking_external_process_count:
        effective_wait_state = {
            "state": "external_busy",
            "reason": (
                "same-target external binary-plan runxp processes are present; "
                "only matching PG host/port/db blocks Bonect startup"
            ),
        }
    elif current_status == "waiting_external_hyxp_busy" or current_external_count > 0:
        effective_wait_state = {
            "state": "non_target_external_busy_ignored",
            "reason": (
                "launcher/current status reports external HYXP, but no same-target "
                "PG host/port/db process is present in the latest snapshot"
            ),
        }
    elif current_status == "waiting_external_hyxp_detector_retry":
        effective_wait_state = {
            "state": "detector_retry_no_external_seen",
            "reason": "launcher detector timed out and this status script saw no external process",
        }
    else:
        effective_wait_state = {
            "state": "idle_or_starting",
            "reason": "no external process seen by status script",
        }

    replay_safe = bool(
        effective_wait_state.get("state") in {"idle_or_starting", "non_target_external_busy_ignored"}
        and not blocking_external_process_count
        and bool(external_process_scan.get("reliable_count", True))
        and not live_known_external
        and not live_pid_rows
    )

    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "run_dir_run_id": run_dir_run_id,
        "current_matches_run_dir": current_matches_run_dir,
        "current_bonect": current if isinstance(current, dict) else {},
        "run_dir": str(args.run_dir),
        "run_dir_files": {
            name: (args.run_dir / name).exists()
            for name in ["launcher.pid", "runner.pid", "supervisor.pid", "runner.log", "supervisor.log"]
        },
        "run_dir_pid_status": pid_status,
        "known_external_pids": known_external_pids,
        "known_external_pid_status": known_external_pid_status,
        "external_processes": external_processes,
        "external_process_count": external_process_count,
        "blocking_external_processes": blocking_external_processes,
        "blocking_external_process_count": blocking_external_process_count,
        "target": {
            "host": target_host,
            "port": target_port,
            "dbname": target_db,
        },
        "external_process_source": external_process_source,
        "external_process_scan_status": external_process_scan.get("status"),
        "external_process_count_reliable": external_process_scan.get("reliable_count"),
        "external_process_scan": external_process_scan,
        "launcher_wait": launcher_wait,
        "effective_wait_state": effective_wait_state,
        "replay_safe": replay_safe,
        "external": summarize_jsonl(args.jsonl, args.config),
    }

    atomic_write_text(args.json_output, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    write_markdown(args.md_output, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.require_replay_safe and not replay_safe:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

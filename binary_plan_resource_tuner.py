#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import itertools
import json
import math
import os
import random
import re
import shlex
import signal
import statistics
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

try:
    import fcntl  # type: ignore
except ImportError:  # pragma: no cover
    fcntl = None


ROOT = Path(
    os.environ.get("BINARY_PLAN_RESOURCE_TUNER_ROOT")
    or Path(__file__).resolve().parent
).resolve()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from extract_plan_trees import normalize_plan_structure
from bonect_interval_lineage import (
    expand_interval_around_point,
    interval_midpoint,
    interval_pair_points,
    split_interval_for_value,
)
from plan_resource_estimator import (
    PlanResourceEstimator,
    _build_exact_plan_fingerprint_metadata,
)
from system_resource_monitor import _cgroup_snapshot, _stats, parse_hybench_benchmark_output
from common_outer_loop import outer_acceptance_required_tier
from same_ssd_concurrency import choose_same_ssd_parallelism
from operator_resource_rules import (
    evaluate_rule_gate,
    load_rule_gate,
    operator_changes_from_feature_inputs,
    summarize_rule_gate,
)


COST_PARAMS = [
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
]

_DOCKER_CLI_LOCK = threading.Lock()
_FADVISE_HELPER_INSTALL_LOCK = threading.Lock()
_FADVISE_HELPER_INSTALLED: Set[str] = set()
_EARLY_EXTERNAL_STOP_COUNTS: Dict[str, int] = {}
_GLOBAL_RESTORE_SLOT_DEFAULT = 2
_GLOBAL_RESTORE_SLOT_ROOT_DEFAULT = "/tmp/resource_adaptive_pg_restore_slots_v1"
_SHARED_SSD_COORDINATION_LOCK_DEFAULT = "/tmp/resource_adaptive_pg_ssd_measurement_v1.lock"
_SHARED_SSD_COORDINATION_CONDITION = threading.Condition()
_SHARED_SSD_COORDINATION_HANDLE: Optional[Any] = None
_SHARED_SSD_COORDINATION_MODE: Optional[str] = None
_SHARED_SSD_COORDINATION_ACQUIRING_MODE: Optional[str] = None
_SHARED_SSD_COORDINATION_REFCOUNT = 0
_SHARED_SSD_RELEASE_YIELD_S = 0.25


def acquire_shared_ssd_coordination(
    mode: str,
    *,
    timeout_s: Optional[float] = None,
    cancel_requested: Optional[Callable[[], bool]] = None,
) -> Tuple[Optional[Any], float]:
    """Coordinate physical SSD measurement windows across PostgreSQL lanes.

    ``shared`` is used by low-I/O/non-target measurements that may coexist.
    ``exclusive`` is used by JOB, XP-I/O and database clones.  Parallel replay
    workers in one controller share one process-local lease, so the required
    two-point physical parallelism is preserved while other controllers still
    observe a real cross-process read/write lock.
    """

    normalized = str(mode or "").strip().lower()
    if normalized in {"", "off", "disabled", "none"}:
        return None, 0.0
    if normalized not in {"shared", "exclusive"}:
        raise ValueError(f"invalid shared SSD coordination mode: {mode!r}")
    if fcntl is None:  # pragma: no cover - Windows fallback
        return None, 0.0
    lock_path = Path(
        os.environ.get("BONECT_SHARED_SSD_COORDINATION_LOCK_PATH")
        or _SHARED_SSD_COORDINATION_LOCK_DEFAULT
    )
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    effective_timeout = (
        max(0.0, float(timeout_s)) if timeout_s is not None else max(
            1.0,
            safe_float(
                os.environ.get("BONECT_SHARED_SSD_COORDINATION_TIMEOUT_S"),
                21600.0,
            ),
        )
    )
    global _SHARED_SSD_COORDINATION_ACQUIRING_MODE
    global _SHARED_SSD_COORDINATION_HANDLE
    global _SHARED_SSD_COORDINATION_MODE
    global _SHARED_SSD_COORDINATION_REFCOUNT

    with _SHARED_SSD_COORDINATION_CONDITION:
        while True:
            if cancel_requested is not None and cancel_requested():
                raise RuntimeError("shared SSD coordination acquisition cancelled")
            elapsed = time.perf_counter() - started
            if elapsed >= effective_timeout:
                raise TimeoutError(
                    "shared SSD coordination acquisition timed out after "
                    f"{effective_timeout:.3f}s mode={normalized}"
                )
            if (
                _SHARED_SSD_COORDINATION_HANDLE is not None
                and _SHARED_SSD_COORDINATION_MODE == normalized
            ):
                _SHARED_SSD_COORDINATION_REFCOUNT += 1
                return {"mode": normalized, "joined_process_lease": True}, elapsed
            if (
                _SHARED_SSD_COORDINATION_HANDLE is None
                and _SHARED_SSD_COORDINATION_ACQUIRING_MODE is None
            ):
                _SHARED_SSD_COORDINATION_ACQUIRING_MODE = normalized
                break
            remaining = max(0.0, effective_timeout - elapsed)
            _SHARED_SSD_COORDINATION_CONDITION.wait(timeout=min(0.1, remaining))

    operation = fcntl.LOCK_SH if normalized == "shared" else fcntl.LOCK_EX
    raw_handle = lock_path.open("a+", encoding="utf-8")
    try:
        while True:
            if cancel_requested is not None and cancel_requested():
                raise RuntimeError("shared SSD coordination acquisition cancelled")
            elapsed = time.perf_counter() - started
            if elapsed >= effective_timeout:
                raise TimeoutError(
                    "shared SSD coordination acquisition timed out after "
                    f"{effective_timeout:.3f}s mode={normalized}"
                )
            try:
                fcntl.flock(raw_handle.fileno(), operation | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(min(0.1, max(0.0, effective_timeout - elapsed)))
    except Exception:
        raw_handle.close()
        with _SHARED_SSD_COORDINATION_CONDITION:
            _SHARED_SSD_COORDINATION_ACQUIRING_MODE = None
            _SHARED_SSD_COORDINATION_CONDITION.notify_all()
        raise

    with _SHARED_SSD_COORDINATION_CONDITION:
        _SHARED_SSD_COORDINATION_HANDLE = raw_handle
        _SHARED_SSD_COORDINATION_MODE = normalized
        _SHARED_SSD_COORDINATION_REFCOUNT = 1
        _SHARED_SSD_COORDINATION_ACQUIRING_MODE = None
        _SHARED_SSD_COORDINATION_CONDITION.notify_all()
    return {"mode": normalized, "joined_process_lease": False}, time.perf_counter() - started


def release_shared_ssd_coordination(token: Optional[Any]) -> None:
    if token is None:
        return
    global _SHARED_SSD_COORDINATION_HANDLE
    global _SHARED_SSD_COORDINATION_MODE
    global _SHARED_SSD_COORDINATION_REFCOUNT

    released_process_lease = False
    with _SHARED_SSD_COORDINATION_CONDITION:
        if _SHARED_SSD_COORDINATION_REFCOUNT <= 0:
            return
        _SHARED_SSD_COORDINATION_REFCOUNT -= 1
        if _SHARED_SSD_COORDINATION_REFCOUNT > 0:
            return
        raw_handle = _SHARED_SSD_COORDINATION_HANDLE
        try:
            if raw_handle is not None and fcntl is not None:
                fcntl.flock(raw_handle.fileno(), fcntl.LOCK_UN)
        finally:
            if raw_handle is not None:
                raw_handle.close()
            _SHARED_SSD_COORDINATION_HANDLE = None
            _SHARED_SSD_COORDINATION_MODE = None
            _SHARED_SSD_COORDINATION_CONDITION.notify_all()
            released_process_lease = True
    if released_process_lease:
        # The cross-process lock is acquired with cancellable non-blocking
        # polling.  Without a short post-release yield, the controller that
        # just completed a wave can repeatedly win its next restore/workload
        # acquisition and starve lanes already waiting on disk1.  Waiting
        # controllers poll at 100 ms, so 250 ms gives them a deterministic
        # hand-off window while adding negligible overhead to multi-minute
        # physical replays.
        time.sleep(_SHARED_SSD_RELEASE_YIELD_S)


def acquire_configured_shared_ssd_workload_coordination(
    *,
    cancel_requested: Optional[Callable[[], bool]] = None,
) -> Tuple[Optional[Any], float]:
    """Acquire the scene's configured workload lease before a physical wave."""

    if not to_bool(
        os.environ.get("BONECT_SHARED_SSD_COORDINATION_ENABLED"), False
    ):
        return None, 0.0
    mode = str(
        os.environ.get("BONECT_SHARED_SSD_WORKLOAD_LOCK_MODE") or "shared"
    ).strip().lower()
    return acquire_shared_ssd_coordination(
        mode,
        cancel_requested=cancel_requested,
    )


def _signal_name(signum: int) -> str:
    try:
        return signal.Signals(signum).name
    except Exception:
        return str(signum)


def install_early_external_stop_handler() -> None:
    """Keep the top-level tuner alive if a non-owner guard sends TERM/HUP during startup."""

    def _continue(signum: int, _frame: Any) -> None:
        name = _signal_name(signum)
        _EARLY_EXTERNAL_STOP_COUNTS[name] = _EARLY_EXTERNAL_STOP_COUNTS.get(name, 0) + 1

    for stop_signal in (signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(stop_signal, _continue)
        except Exception:
            pass


@dataclass
class PgConn:
    """PostgreSQL connection settings."""

    host: str
    port: str
    user: str
    dbname: str
    password: str = ""
    connect_timeout_s: int = 5


@dataclass
class DbSnapshot:
    """Database statistics snapshot."""

    ts: float
    xact_total: int
    blks_read: int
    blks_hit: int
    statements_calls: Optional[int]
    active_sessions: int


def now_tag() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


HYBENCH_TECHNICAL_FAILURE_PATTERNS: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    ("java_null_pointer_exception", re.compile(r"\bjava\.lang\.NullPointerException\b")),
    ("java_out_of_memory", re.compile(r"\bjava\.lang\.OutOfMemoryError\b")),
    ("java_stack_overflow", re.compile(r"\bjava\.lang\.StackOverflowError\b")),
    ("uncaught_java_exception", re.compile(r"\bException in thread\b")),
    ("postgres_fatal_error", re.compile(r"\borg\.postgresql\.util\.PSQLException:\s+ERROR:")),
    ("storage_no_space", re.compile(r"\bNo space left on device\b", re.IGNORECASE)),
)
HYBENCH_AT_PERCENT_KEYS = tuple(f"at{index}_percent" for index in range(1, 7))
HYBENCH_FORBIDDEN_QUERY_SELECTION_KEYS = (
    "ap_query_ids",
    "ap_query_weights",
    "ap_query_sequence",
    "ap_query_sequence_limit",
)


def detect_hybench_technical_failures(stdout_lines: Sequence[str]) -> List[Dict[str, Any]]:
    """Extract serious workload failures that HyBench may hide behind exit code zero."""
    failures: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, str]] = set()
    for line_number, raw_line in enumerate(stdout_lines, start=1):
        line = str(raw_line).strip()
        if not line:
            continue
        for failure_type, pattern in HYBENCH_TECHNICAL_FAILURE_PATTERNS:
            if not pattern.search(line):
                continue
            signature = (failure_type, line)
            if signature in seen:
                continue
            seen.add(signature)
            failures.append(
                {
                    "type": failure_type,
                    "line_number": line_number,
                    "message": line[-1000:],
                }
            )
    return failures


def workload_completeness_audit(
    cfg: Dict[str, Any],
    benchmark: Dict[str, Any],
    stdout_lines: Sequence[str],
    run_config: Dict[str, Any],
) -> Dict[str, Any]:
    """Verify that one physical replay used the authoritative full workload."""
    workload = workload_name(cfg)
    errors: List[str] = []
    details: Dict[str, Any] = {"workload": workload}
    target_wall_time_s = safe_float(benchmark.get("target_wall_time_s"), 0.0)
    if workload in {"job", "tpch"} and target_wall_time_s > 0.0:
        completed_within_target = benchmark.get("completed_within_target_wall_time") is True
        if not completed_within_target:
            errors.append(f"{workload}_target_wall_time_exceeded")
        details.update(
            {
                "target_wall_time_s": target_wall_time_s,
                "observed_wall_time_s": benchmark.get("wall_time_s"),
                "completed_within_target_wall_time": completed_within_target,
            }
        )
    if workload == "job":
        query_counts = benchmark.get("query_counts") if isinstance(benchmark.get("query_counts"), dict) else {}
        expected_count = 113
        non_unit = sorted(
            str(name) for name, count in query_counts.items() if safe_int(count, 0) != 1
        )
        if safe_int(benchmark.get("query_count"), 0) != expected_count:
            errors.append("job_query_count_not_113")
        if len(query_counts) != expected_count or non_unit:
            errors.append("job_query_counts_not_exactly_once")
        if benchmark.get("all_ap_queries_completed") is not True:
            errors.append("job_full_completion_marker_missing")
        if benchmark.get("exactly_once") is not True:
            errors.append("job_exactly_once_marker_missing")
        details.update(
            {
                "expected_query_count": expected_count,
                "observed_query_count": benchmark.get("query_count"),
                "distinct_query_count": len(query_counts),
                "non_unit_query_counts": non_unit,
            }
        )
    elif workload == "tpch":
        expected_sequence = [f"Q{index}" for index in range(1, 23)]
        observed_sequence = [str(item) for item in (benchmark.get("query_sequence") or [])]
        query_counts = benchmark.get("query_counts") if isinstance(benchmark.get("query_counts"), dict) else {}
        execution_mode = str(benchmark.get("execution_mode") or "")
        parallel_execution_mode = "native_tpch_parallel_lpt_q1_q22_exactly_once"
        validation_cfg = (
            ((cfg.get("binary_plan_tuning") or {}).get("validation") or {}).get(
                "hybench_xp"
            )
            or {}
        )
        expected_worker_count = max(1, safe_int(validation_cfg.get("tpclient"), 1))
        non_unit = [
            query_name
            for query_name in expected_sequence
            if safe_int(query_counts.get(query_name), 0) != 1
        ]
        unexpected_query_names = sorted(
            str(query_name)
            for query_name in query_counts
            if str(query_name) not in expected_sequence
        )
        lane_assignments = benchmark.get("lane_assignments")
        observed_lane_sequence: List[str] = []
        lane_assignment_shape_valid = isinstance(lane_assignments, list) and all(
            isinstance(lane, list) and bool(lane) for lane in (lane_assignments or [])
        )
        if lane_assignment_shape_valid:
            observed_lane_sequence = [
                str(query_name)
                for lane in lane_assignments
                for query_name in lane
            ]
        parallel_contract = execution_mode == parallel_execution_mode
        if parallel_contract:
            if safe_int(benchmark.get("worker_count"), 0) != expected_worker_count:
                errors.append("tpch_parallel_worker_count_mismatch")
            if len(lane_assignments or []) != expected_worker_count:
                errors.append("tpch_parallel_lane_count_mismatch")
            if (
                not lane_assignment_shape_valid
                or len(observed_lane_sequence) != len(expected_sequence)
                or sorted(observed_lane_sequence) != sorted(expected_sequence)
            ):
                errors.append("tpch_parallel_lane_assignments_not_q1_q22_exactly_once")
        elif observed_sequence != expected_sequence:
            errors.append("tpch_not_serial_q1_q22_exactly_once")
        if safe_int(benchmark.get("query_count"), 0) != len(expected_sequence):
            errors.append("tpch_query_count_not_22")
        if non_unit:
            errors.append("tpch_query_counts_not_exactly_once")
        if unexpected_query_names:
            errors.append("tpch_unexpected_query_names")
        if benchmark.get("all_ap_queries_completed") is not True:
            errors.append("tpch_full_completion_marker_missing")
        if benchmark.get("exactly_once") is not True:
            errors.append("tpch_exactly_once_marker_missing")
        details.update(
            {
                "execution_mode": execution_mode,
                "parallel_contract": parallel_contract,
                "expected_worker_count": expected_worker_count,
                "observed_worker_count": benchmark.get("worker_count"),
                "expected_query_sequence": expected_sequence,
                "observed_query_sequence": observed_sequence,
                "lane_assignments": lane_assignments,
                "observed_lane_sequence": observed_lane_sequence,
                "non_unit_query_counts": non_unit,
                "unexpected_query_names": unexpected_query_names,
            }
        )
    elif workload in {"hybench_xp", "hybenchxp"}:
        at_percentages = (
            run_config.get("at_percentages")
            if isinstance(run_config.get("at_percentages"), dict)
            else {}
        )
        if (
            any(safe_int(at_percentages.get(key), -1) < 0 for key in HYBENCH_AT_PERCENT_KEYS)
            or sum(safe_int(at_percentages.get(key), 0) for key in HYBENCH_AT_PERCENT_KEYS) != 100
        ):
            errors.append("xp_at_percentages_invalid")
        if safe_int(run_config.get("xtpclient"), 0) <= 0 or safe_int(run_config.get("xapclient"), 0) <= 0:
            errors.append("xp_clients_not_both_positive")
        forbidden_present = [
            key for key in HYBENCH_FORBIDDEN_QUERY_SELECTION_KEYS if run_config.get(key) not in (None, "")
        ]
        if forbidden_present:
            errors.append("xp_forbidden_query_selection_present")
        observed_iq: Set[int] = set()
        observed_at: Set[int] = set()
        iq_re = re.compile(
            r"Intera(?:tive|ctive) Query\s+([1-6])\s*:.*?max rt\s*:\s*([0-9.]+).*?avg rt\s*:\s*([0-9.]+)",
            re.IGNORECASE,
        )
        at_re = re.compile(
            r"Analytical Transaction AT([1-6])\s*:.*?max rt\s*:\s*([0-9.]+).*?avg rt\s*:\s*([0-9.]+)",
            re.IGNORECASE,
        )
        for raw_line in stdout_lines:
            line = str(raw_line)
            iq_match = iq_re.search(line)
            if iq_match and (safe_float(iq_match.group(2), 0.0) > 0.0 or safe_float(iq_match.group(3), 0.0) > 0.0):
                observed_iq.add(int(iq_match.group(1)))
            at_match = at_re.search(line)
            if at_match and (safe_float(at_match.group(2), 0.0) > 0.0 or safe_float(at_match.group(3), 0.0) > 0.0):
                observed_at.add(int(at_match.group(1)))
        expected_iq = set(range(1, 7))
        expected_at = {
            index
            for index, key in enumerate(HYBENCH_AT_PERCENT_KEYS, start=1)
            if safe_int(at_percentages.get(key), 0) > 0
        }
        if observed_iq != expected_iq:
            errors.append("xp_iq1_iq6_not_all_observed")
        if not expected_at.issubset(observed_at):
            errors.append("xp_positive_weight_at_not_all_observed")
        expected_statement_sha = str(run_config.get("expected_statement_file_sha256") or "")
        actual_statement_sha = str(run_config.get("statement_file_sha256") or "")
        if not expected_statement_sha:
            errors.append("xp_expected_statement_file_sha256_missing")
        elif actual_statement_sha != expected_statement_sha:
            errors.append("xp_statement_file_sha256_mismatch")
        expected_jar_sha = str(run_config.get("expected_native_jar_sha256") or "")
        actual_jar_sha = str(run_config.get("native_jar_sha256") or "")
        if not expected_jar_sha:
            errors.append("xp_expected_native_jar_sha256_missing")
        elif actual_jar_sha != expected_jar_sha:
            errors.append("xp_native_jar_sha256_mismatch")
        details.update(
            {
                "xtpclient": run_config.get("xtpclient"),
                "xapclient": run_config.get("xapclient"),
                "at_percentages": at_percentages,
                "forbidden_query_selection_fields_present": forbidden_present,
                "expected_iq": sorted(expected_iq),
                "observed_iq": sorted(observed_iq),
                "expected_positive_weight_at": sorted(expected_at),
                "observed_at": sorted(observed_at),
                "expected_statement_file_sha256": expected_statement_sha,
                "actual_statement_file_sha256": actual_statement_sha,
                "native_jar_file": run_config.get("native_jar_file"),
                "expected_native_jar_sha256": expected_jar_sha,
                "actual_native_jar_sha256": actual_jar_sha,
            }
        )
    else:
        errors.append("unknown_workload_completeness_contract")
    return {
        **details,
        "passed": not errors,
        "errors": errors,
        "physical_replay_valid_only_if_passed": True,
    }


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def to_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "f", "no", "n", "off"}:
        return False
    return default


def read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object at {path}")
    return data


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def append_jsonl(path: Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = dict(row)
    record.setdefault("timestamp", time.strftime("%Y-%m-%d %H:%M:%S"))
    with path.open("a", encoding="utf-8") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True, default=str) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def resolve_path(value: Any) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else ROOT / path


def output_dir_from_args(cfg: Dict[str, Any], args_output_dir: str) -> Path:
    if args_output_dir:
        return Path(args_output_dir).resolve()
    bpt = cfg.get("binary_plan_tuning") or {}
    value = bpt.get("output_dir") or "/disk2/resource_adaptive/binary_plan_tuning/runs/manual"
    return Path(str(value)).resolve()


def workload_name(cfg: Dict[str, Any]) -> str:
    bpt = cfg.get("binary_plan_tuning") or {}
    workload_cfg = bpt.get("workload") if isinstance(bpt.get("workload"), dict) else {}
    explicit = str(workload_cfg.get("name") or bpt.get("workload_name") or "").strip().lower()
    if explicit:
        return explicit
    validation_cfg = bpt.get("validation") if isinstance(bpt.get("validation"), dict) else {}
    hybench_cfg = validation_cfg.get("hybench_xp") if isinstance(validation_cfg.get("hybench_xp"), dict) else {}
    if to_bool(hybench_cfg.get("enabled"), False):
        return "hybenchxp"
    plan_file = str(workload_cfg.get("plan_sql_file") or "").lower()
    if "hybench" in plan_file and "xp" in plan_file:
        return "hybenchxp"
    return "pgbench"


def workload_log_suffix(cfg: Dict[str, Any]) -> str:
    return sanitize_label(workload_name(cfg).replace("_", "")).lower()


def with_workload(cfg: Dict[str, Any], row: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(row)
    payload.setdefault("workload", workload_name(cfg))
    return payload


def load_config(path: str) -> Dict[str, Any]:
    cfg_path = resolve_path(path)
    cfg = read_json(cfg_path)
    cfg["_config_path"] = str(cfg_path)
    return cfg


def exclusive_vm_disk_lock_error(cfg: Dict[str, Any], target: str) -> Optional[str]:
    """Block Docker IO searches while a four-VM run owns the shared image disk."""
    if target != "io":
        return None
    lock_path = Path(
        os.environ.get(
            "BINARY_PLAN_FOUR_VM_EXCLUSIVE_LOCK",
            "/disk2/resource_adaptive/binary_plan_tuning_hybenchxp_vm/FOUR_VM_EXCLUSIVE_DISK1.lock",
        )
    )
    if not lock_path.is_file():
        return None
    bpt = cfg.get("binary_plan_tuning") or {}
    validation_cfg = bpt.get("validation") if isinstance(bpt.get("validation"), dict) else {}
    vm_replay = validation_cfg.get("vm_replay") if isinstance(validation_cfg.get("vm_replay"), dict) else {}
    if to_bool(vm_replay.get("enabled"), False):
        return None
    try:
        owner = lock_path.read_text(encoding="utf-8").strip()
    except OSError:
        owner = "unreadable lock owner"
    return f"exclusive four-VM /disk1 lock blocks non-vm_replay IO search: {lock_path} ({owner})"


def pg_conn_from_config(cfg: Dict[str, Any]) -> PgConn:
    postgres_cfg = cfg.get("postgres") or {}
    return PgConn(
        host=str(os.environ.get("PGHOST", postgres_cfg.get("host", "127.0.0.1"))),
        port=str(os.environ.get("PGPORT", postgres_cfg.get("port", "5432"))),
        user=str(os.environ.get("PGUSER", postgres_cfg.get("user", "postgres"))),
        dbname=str(os.environ.get("PGDATABASE", postgres_cfg.get("dbname", "postgres"))),
        password=str(os.environ.get("PGPASSWORD", postgres_cfg.get("password", ""))),
        connect_timeout_s=max(1, safe_int(os.environ.get("PGCONNECT_TIMEOUT"), 5)),
    )


def pg_conn_from_worker(cfg: Dict[str, Any], worker: Optional[Dict[str, Any]]) -> PgConn:
    base = pg_conn_from_config(cfg)
    if not worker:
        return base
    return PgConn(
        host=str(worker.get("host", base.host)),
        port=str(worker.get("port", base.port)),
        user=str(worker.get("user", base.user)),
        dbname=str(worker.get("dbname", base.dbname)),
        password=str(worker.get("password", base.password)),
        connect_timeout_s=max(1, safe_int(worker.get("connect_timeout_s"), base.connect_timeout_s)),
    )


def is_none_target(target: str) -> bool:
    return target in {"none", "no_bottleneck", "no-bottleneck"}


def stage_targets() -> Tuple[str, ...]:
    return ("cpu", "memory", "io", "none")


def validation_workers(cfg: Dict[str, Any], target: str) -> List[Dict[str, Any]]:
    bpt = cfg.get("binary_plan_tuning") or {}
    validation_cfg = bpt.get("validation") or {}
    workers_cfg = validation_cfg.get("docker_workers") or {}
    if not to_bool(workers_cfg.get("enabled"), False):
        return []
    all_workers = workers_cfg.get("workers") or []
    worker_config_path = str(os.environ.get("BINARY_PLAN_TUNING_WORKER_CONFIG") or "").strip()
    if worker_config_path:
        worker_cfg = read_json(Path(worker_config_path))
        external_workers_cfg = (
            (((worker_cfg.get("binary_plan_tuning") or {}).get("validation") or {}).get("docker_workers") or {})
            if isinstance(worker_cfg, dict)
            else {}
        )
        external_workers = external_workers_cfg.get("workers") or []
        if not isinstance(external_workers, list) or not external_workers:
            raise ValueError(f"external worker config has no workers: {worker_config_path}")
        all_workers = external_workers
    if not isinstance(all_workers, list):
        return []
    selected: List[Dict[str, Any]] = []
    if is_none_target(target):
        none_workers: List[Dict[str, Any]] = []
        fallback_workers: List[Dict[str, Any]] = []
        for item in all_workers:
            if not isinstance(item, dict):
                continue
            targets = {str(value).strip().lower() for value in (item.get("targets") or [])}
            if not item.get("container") or not item.get("port"):
                continue
            if "none" in targets:
                none_workers.append(dict(item))
            elif targets & {"cpu", "memory"}:
                fallback_workers.append(dict(item))
        selected = none_workers or fallback_workers
    else:
        worker_match_targets = {target}
        for item in all_workers:
            if not isinstance(item, dict):
                continue
            targets = item.get("targets")
            if targets and not (worker_match_targets & {str(value).strip().lower() for value in targets}):
                continue
            if not item.get("container") or not item.get("port"):
                continue
            selected.append(dict(item))
    override_names_raw = os.environ.get(
        "BINARY_PLAN_TUNING_IO_WORKER_NAMES" if target == "io" else "BINARY_PLAN_TUNING_WORKER_NAMES",
        "",
    )
    override_names = [name.strip() for name in override_names_raw.split(",") if name.strip()]
    if override_names:
        workers_by_name = {
            str(item.get("name")): dict(item)
            for item in all_workers
            if isinstance(item, dict) and item.get("name") and item.get("container") and item.get("port")
        }
        missing = [name for name in override_names if name not in workers_by_name]
        if missing:
            raise ValueError(f"unknown validation worker override(s): {', '.join(missing)}")
        selected = [workers_by_name[name] for name in override_names]
    if target == "io":
        configured = safe_int(
            os.environ.get("BINARY_PLAN_TUNING_IO_PARALLELISM")
            or os.environ.get("BINARY_PLAN_TUNING_HYBENCHXP_IO_PARALLELISM"),
            safe_int(workers_cfg.get("io_parallelism"), 2),
        )
        parallelism = min(2, max(1, configured))
    elif is_none_target(target):
        configured = safe_int(
            os.environ.get("BINARY_PLAN_TUNING_NONE_PARALLELISM")
            or os.environ.get("BINARY_PLAN_TUNING_HYBENCHXP_NONE_PARALLELISM"),
            safe_int(workers_cfg.get("none_parallelism"), 1),
        )
        parallelism = max(1, configured)
    else:
        configured = safe_int(
            os.environ.get("BINARY_PLAN_TUNING_CPU_MEMORY_PARALLELISM")
            or os.environ.get("BINARY_PLAN_TUNING_HYBENCHXP_CPU_MEMORY_PARALLELISM"),
            safe_int(workers_cfg.get("cpu_memory_parallelism"), len(selected) or 1),
        )
        parallelism = max(1, configured)
    adaptive_cfg = same_ssd_adaptive_config(cfg, target=target, workers=selected)
    if to_bool(adaptive_cfg.get("enabled"), False):
        stable_cap = max(1, min(12, safe_int(adaptive_cfg.get("stable_cap"), 12)))
        return selected[:stable_cap]
    return selected[:parallelism]


def same_ssd_adaptive_config(
    cfg: Dict[str, Any],
    *,
    target: str = "",
    workers: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    validation_cfg = ((cfg.get("binary_plan_tuning") or {}).get("validation") or {})
    workers_cfg = validation_cfg.get("docker_workers") or {}
    raw = workers_cfg.get("same_ssd_adaptive_parallelism") or {}
    if isinstance(raw, dict) and "enabled" in raw:
        return dict(raw)
    selected = list(workers or [])
    if not selected:
        selected = [row for row in (workers_cfg.get("workers") or []) if isinstance(row, dict)]
    observed_devices: List[str] = []
    for worker in selected:
        devices = _disk_devices_for_root(str(worker.get("disk_root") or ""))
        if len(devices) != 1:
            observed_devices = []
            break
        observed_devices.append(devices[0])
    auto_enabled = bool(
        len(selected) > 1
        and observed_devices
        and set(observed_devices) == {"nvme1n1"}
        and "hybench" in workload_name(cfg).lower()
        and "xp" in workload_name(cfg).lower()
    )
    result = dict(raw) if isinstance(raw, dict) else {}
    result.setdefault("enabled", auto_enabled)
    result.setdefault("activation", "auto_nvme1n1_hybench_xp" if auto_enabled else "disabled")
    result.setdefault("target", target)
    result.setdefault("observed_devices", observed_devices)
    return result


def initial_validation_parallelism(cfg: Dict[str, Any], target: str, worker_count: int) -> int:
    workers_cfg = ((((cfg.get("binary_plan_tuning") or {}).get("validation") or {}).get("docker_workers") or {}))
    if target == "io":
        configured = safe_int(workers_cfg.get("io_parallelism"), 2)
    elif is_none_target(target):
        configured = safe_int(workers_cfg.get("none_parallelism"), 1)
    else:
        configured = safe_int(workers_cfg.get("cpu_memory_parallelism"), worker_count or 1)
    return max(1, min(max(1, worker_count), configured))


def ssd_utilization_from_validations(validations: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    means: List[float] = []
    peaks: List[float] = []
    for validation in validations:
        if validation.get("status") != "ok":
            continue
        resource_stats = validation.get("resource_stats") or {}
        stats = resource_stats.get("ssd_util_percent") or {}
        sample_counts = resource_stats.get("stable_sample_count") or {}
        count = safe_int(sample_counts.get("io_util_pct"), 0)
        if count <= 0:
            continue
        means.append(safe_float(stats.get("avg"), 0.0))
        peaks.append(safe_float(stats.get("max"), 0.0))
    return {
        "valid": bool(means and peaks),
        "worker_observation_count": min(len(means), len(peaks)),
        "mean_util_pct": statistics.median(means) if means else None,
        "peak_util_pct": max(peaks) if peaks else None,
        "aggregation": "median_worker_mean_and_max_worker_peak",
    }


def worker_identity(worker: Optional[Dict[str, Any]]) -> str:
    if not isinstance(worker, dict):
        return ""
    for key in ("name", "container"):
        value = str(worker.get(key) or "").strip()
        if value:
            return value
    host = str(worker.get("host") or "").strip()
    port = str(worker.get("port") or "").strip()
    if host or port:
        return f"{host}:{port}"
    data_dir = str(worker.get("data_dir") or "").strip()
    if data_dir:
        return data_dir
    return ""


def validation_worker_identity(validation: Dict[str, Any]) -> str:
    worker = validation.get("worker")
    return worker_identity(worker if isinstance(worker, dict) else None)


def validation_is_baseline(validation: Dict[str, Any]) -> bool:
    """Return true for aggregate or per-worker baseline validation rows."""
    phase = str(validation.get("phase") or "").strip().lower()
    event = str(validation.get("event") or "").strip().lower()
    label = str(validation.get("label") or "").strip().lower()
    if phase == "baseline":
        return True
    if event in {"baseline_validation", "parallel_baseline_validation_summary"}:
        return True
    return "baseline" in label


def acceptance_policy_for_target(cfg: Dict[str, Any], target: str) -> Dict[str, Any]:
    """Return acceptance policy with target-specific overrides applied."""
    base = ((cfg.get("binary_plan_tuning") or {}).get("acceptance_policy") or {})
    if not isinstance(base, dict):
        return {}
    overrides = base.get("target_overrides") if isinstance(base.get("target_overrides"), dict) else {}
    target_policy = overrides.get(target) if isinstance(overrides.get(target), dict) else {}
    if is_none_target(target):
        none_policy = overrides.get("none") if isinstance(overrides.get("none"), dict) else {}
        target_policy = {**target_policy, **none_policy}
    return {**base, **target_policy}


def binary_tuner_method_identity_error(cfg: Dict[str, Any]) -> Optional[str]:
    """Reject configs that falsely identify binary interval search as Algorithm C."""
    metadata = ((cfg.get("binary_plan_tuning") or {}).get("experiment_meta") or {})
    if not isinstance(metadata, dict):
        return None
    method = str(metadata.get("method") or "").strip().lower()
    exp_id = str(metadata.get("exp_id") or "").strip().lower()
    claims_algorithm_c = "algorithm_c" in method or "alg-c" in exp_id
    if not claims_algorithm_c:
        return None
    return (
        "method_identity_mismatch: binary_plan_resource_tuner binary-search "
        "enumerates L/R interval corners and cannot be logged as Algorithm C; "
        "use parameter_optimizer.py experience-table subset SGD for Algorithm C"
    )


def worker_baseline_policy(cfg: Dict[str, Any], target: str, workers: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    validation_cfg = ((cfg.get("binary_plan_tuning") or {}).get("validation") or {})
    policy_cfg = validation_cfg.get("parallel_baseline") or {}
    if not isinstance(policy_cfg, dict):
        policy_cfg = {}
    target_overrides = policy_cfg.get("target_overrides") if isinstance(policy_cfg.get("target_overrides"), dict) else {}
    target_policy = target_overrides.get(target) if isinstance(target_overrides.get(target), dict) else {}
    if is_none_target(target):
        none_policy = target_overrides.get("none") if isinstance(target_overrides.get("none"), dict) else {}
        target_policy = {**target_policy, **none_policy}
    effective_policy = {**policy_cfg, **target_policy}
    env_enabled = os.environ.get("BINARY_PLAN_TUNING_PARALLEL_BASELINE")
    enabled = to_bool(effective_policy.get("enabled"), False)
    if env_enabled is not None:
        enabled = to_bool(env_enabled, enabled)
    compare_mode = str(effective_policy.get("compare_mode") or "single_baseline").strip().lower()
    if compare_mode not in {"single_baseline", "same_worker"}:
        compare_mode = "single_baseline"
    max_parallelism = len(workers)
    configured_parallelism = safe_int(
        os.environ.get("BINARY_PLAN_TUNING_PARALLEL_BASELINE_PARALLELISM"),
        safe_int(effective_policy.get("parallelism"), max_parallelism),
    )
    parallelism = max(1, min(max_parallelism or 1, configured_parallelism))
    required = to_bool(effective_policy.get("required"), enabled)
    return {
        "enabled": bool(enabled and len(workers) > 1),
        "compare_mode": compare_mode,
        "required": required,
        "parallelism": parallelism if workers else 0,
        "worker_count": len(workers),
        "target": target,
    }


def worker_with_prepared_restore(worker: Dict[str, Any]) -> Dict[str, Any]:
    prepared = copy.deepcopy(worker)
    prepared["_restore_prepared"] = True
    return prepared


def acquire_global_restore_slot(
    *,
    timeout_s: Optional[float] = None,
    cancel_requested: Optional[Callable[[], bool]] = None,
) -> Tuple[Optional[Any], Optional[int], float]:
    """Acquire one cross-process PostgreSQL database-clone slot.

    Real workloads remain fully independent across lanes.  Only physical
    ``CREATE DATABASE ... TEMPLATE`` copies share this small host-wide pool;
    allowing five or six copies on the same SSD made each copy an order of
    magnitude slower.  ``flock`` releases automatically if a controller dies,
    so the limiter cannot leave a stale semaphore behind.
    """
    if fcntl is None:  # pragma: no cover - Windows fallback
        return None, None, 0.0
    configured = safe_int(
        os.environ.get("PG_ALGORITHM_C_GLOBAL_RESTORE_PARALLELISM"),
        _GLOBAL_RESTORE_SLOT_DEFAULT,
    )
    slot_count = max(1, configured)
    slot_root = Path(
        os.environ.get("PG_ALGORITHM_C_GLOBAL_RESTORE_SLOT_ROOT")
        or _GLOBAL_RESTORE_SLOT_ROOT_DEFAULT
    )
    slot_root.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    effective_timeout = None if timeout_s is None else max(0.0, float(timeout_s))
    while True:
        if cancel_requested is not None and cancel_requested():
            raise RuntimeError("global PostgreSQL restore slot acquisition cancelled")
        elapsed = time.perf_counter() - started
        if effective_timeout is not None and elapsed >= effective_timeout:
            raise TimeoutError(
                "global PostgreSQL restore slot acquisition timed out after "
                f"{effective_timeout:.3f}s"
            )
        for slot_index in range(slot_count):
            handle = (slot_root / f"slot_{slot_index:02d}.lock").open(
                "a+", encoding="utf-8"
            )
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                handle.close()
                continue
            handle.seek(0)
            handle.truncate()
            handle.write(f"pid={os.getpid()} acquired={time.time():.6f}\n")
            handle.flush()
            return handle, slot_index, time.perf_counter() - started
        remaining = (
            None
            if effective_timeout is None
            else max(0.0, effective_timeout - (time.perf_counter() - started))
        )
        time.sleep(0.1 if remaining is None else min(0.1, remaining))


def release_global_restore_slot(handle: Optional[Any]) -> None:
    if handle is None:
        return
    try:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def restore_memory_headroom_snapshot() -> Dict[str, Any]:
    """Return host and per-NUMA free-memory evidence for a database clone.

    PostgreSQL template copies can allocate almost entirely from the NUMA node
    selected by the worker cpuset.  A healthy host-wide ``MemAvailable`` value
    therefore does not by itself prevent a local-node OOM.  Keep both views in
    the restore receipt so a deferred clone is auditable.
    """

    def _read_value_kib(path: Path, key: str) -> Optional[int]:
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                normalized = line.strip()
                match = re.search(
                    rf"(?:^|\s){re.escape(key)}\s+(\d+)(?:\s|$)",
                    normalized,
                )
                if match is not None:
                    return int(match.group(1))
        except OSError:
            return None
        return None

    host_available_kib = _read_value_kib(Path("/proc/meminfo"), "MemAvailable:")
    numa_free_gib: Dict[str, float] = {}
    node_root = Path("/sys/devices/system/node")
    try:
        node_paths = sorted(node_root.glob("node[0-9]*"))
    except OSError:
        node_paths = []
    for node_path in node_paths:
        free_kib = _read_value_kib(node_path / "meminfo", "MemFree:")
        if free_kib is not None:
            numa_free_gib[node_path.name] = float(free_kib) / (1024.0 * 1024.0)
    return {
        "host_mem_available_gib": (
            None
            if host_available_kib is None
            else float(host_available_kib) / (1024.0 * 1024.0)
        ),
        "numa_mem_free_gib": numa_free_gib,
        "minimum_numa_mem_free_gib": (
            min(numa_free_gib.values()) if numa_free_gib else None
        ),
    }


def wait_for_restore_memory_headroom(
    *,
    timeout_s: float,
    cancel_requested: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    """Wait until both host-wide and per-NUMA restore headroom are safe."""
    minimum_host_gib = max(
        0.0,
        safe_float(os.environ.get("PG_ALGORITHM_C_RESTORE_MIN_HOST_AVAILABLE_GIB"), 0.0),
    )
    minimum_numa_gib = max(
        0.0,
        safe_float(os.environ.get("PG_ALGORITHM_C_RESTORE_MIN_NUMA_FREE_GIB"), 0.0),
    )
    poll_s = max(
        0.1,
        safe_float(os.environ.get("PG_ALGORITHM_C_RESTORE_MEMORY_POLL_S"), 2.0),
    )
    started = time.perf_counter()
    last_snapshot: Dict[str, Any] = {}
    while True:
        if cancel_requested is not None and cancel_requested():
            raise RuntimeError("PostgreSQL restore memory-headroom wait cancelled")
        last_snapshot = restore_memory_headroom_snapshot()
        host_available = last_snapshot.get("host_mem_available_gib")
        minimum_numa_free = last_snapshot.get("minimum_numa_mem_free_gib")
        host_ok = (
            minimum_host_gib <= 0.0
            or (isinstance(host_available, (int, float)) and host_available >= minimum_host_gib)
        )
        numa_ok = (
            minimum_numa_gib <= 0.0
            or (isinstance(minimum_numa_free, (int, float)) and minimum_numa_free >= minimum_numa_gib)
        )
        elapsed_s = time.perf_counter() - started
        if host_ok and numa_ok:
            return {
                **last_snapshot,
                "status": "ok" if (minimum_host_gib > 0.0 or minimum_numa_gib > 0.0) else "disabled",
                "minimum_host_available_gib": minimum_host_gib,
                "minimum_numa_free_gib": minimum_numa_gib,
                "wait_s": elapsed_s,
            }
        if elapsed_s >= max(0.0, timeout_s):
            raise TimeoutError(
                "PostgreSQL restore memory-headroom wait timed out: "
                f"required_host_gib={minimum_host_gib:.3f} "
                f"required_numa_gib={minimum_numa_gib:.3f} "
                f"last_snapshot={last_snapshot}"
            )
        time.sleep(min(poll_s, max(0.1, timeout_s - elapsed_s)))


def restore_worker_from_hybench_template(
    cfg: Dict[str, Any],
    worker: Dict[str, Any],
    *,
    label: str,
    global_slot_timeout_s: Optional[float] = None,
    cancel_requested: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    validation_cfg = ((cfg.get("binary_plan_tuning") or {}).get("validation") or {})
    hybench_cfg = hybench_xp_config_for_label(validation_cfg.get("hybench_xp") or {}, label)
    if not to_bool(hybench_cfg.get("restore_from_template_before_validation"), True):
        return {
            "worker": worker_identity(worker),
            "status": "skipped",
            "reason": "restore_from_template_before_validation_disabled",
        }
    pg = pg_conn_from_worker(cfg, worker)
    template_db = str(hybench_cfg.get("template_db") or f"{pg.dbname}_base")
    if global_slot_timeout_s is None:
        global_slot_timeout_s = max(
            1.0,
            safe_float(
                os.environ.get("PG_ALGORITHM_C_GLOBAL_RESTORE_SLOT_TIMEOUT_S"),
                1200.0,
            ),
        )
    coordination_enabled = to_bool(
        os.environ.get("BONECT_SHARED_SSD_COORDINATION_ENABLED"), False
    )
    coordination_handle: Optional[Any] = None
    coordination_wait_s = 0.0
    memory_headroom: Dict[str, Any] = {}
    restore_strategy = "default"
    started = time.perf_counter()
    try:
        if coordination_enabled:
            coordination_handle, coordination_wait_s = acquire_shared_ssd_coordination(
                "exclusive",
                timeout_s=global_slot_timeout_s,
                cancel_requested=cancel_requested,
            )
        slot_handle, slot_index, slot_wait_s = acquire_global_restore_slot(
            timeout_s=global_slot_timeout_s,
            cancel_requested=cancel_requested,
        )
        try:
            memory_headroom = wait_for_restore_memory_headroom(
                timeout_s=global_slot_timeout_s,
                cancel_requested=cancel_requested,
            )
            restore_strategy = restore_database_from_template(pg, template_db)
        finally:
            release_global_restore_slot(slot_handle)
    finally:
        release_shared_ssd_coordination(coordination_handle)
    return {
        "worker": worker_identity(worker),
        "status": "ok",
        "template_db": template_db,
        "elapsed_s": time.perf_counter() - started,
        "global_restore_parallelism": max(
            1,
            safe_int(
                os.environ.get("PG_ALGORITHM_C_GLOBAL_RESTORE_PARALLELISM"),
                _GLOBAL_RESTORE_SLOT_DEFAULT,
            ),
        ),
        "global_restore_slot": slot_index,
        "global_restore_slot_wait_s": slot_wait_s,
        "restore_strategy": restore_strategy,
        "memory_headroom": memory_headroom,
        "shared_ssd_coordination": {
            "enabled": coordination_enabled,
            "mode": "exclusive" if coordination_enabled else "disabled",
            "wait_s": coordination_wait_s,
            "lock_path": os.environ.get("BONECT_SHARED_SSD_COORDINATION_LOCK_PATH")
            or _SHARED_SSD_COORDINATION_LOCK_DEFAULT,
        },
    }


def baseline_map_from_validations(validations: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for validation in validations:
        if validation.get("status") != "ok":
            continue
        key = validation_worker_identity(validation)
        if key:
            result[key] = copy.deepcopy(validation)
    return result


def aggregate_parallel_baseline_validation(validations: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    ok_rows = [row for row in validations if row.get("status") == "ok"]
    if not ok_rows:
        return copy.deepcopy(validations[0]) if validations else {}

    aggregate = copy.deepcopy(ok_rows[0])
    resource_keys = ("cpu", "memory", "io", "io_read", "io_write")
    resources: Dict[str, float] = {}
    for key in resource_keys:
        values = [safe_float((row.get("resources") or {}).get(key), math.nan) for row in ok_rows]
        values = [value for value in values if math.isfinite(value)]
        if values:
            resources[key] = sum(values) / len(values)
    if resources:
        aggregate["resources"] = resources

    db_qps_values = [safe_float(row.get("db_qps"), math.nan) for row in ok_rows]
    db_qps_values = [value for value in db_qps_values if math.isfinite(value)]
    if db_qps_values:
        aggregate["db_qps"] = sum(db_qps_values) / len(db_qps_values)

    for latency_key in ("p95_ms", "p99_ms"):
        values = [safe_float(row.get(latency_key), math.nan) for row in ok_rows]
        values = [value for value in values if math.isfinite(value)]
        if values:
            aggregate[latency_key] = sum(values) / len(values)

    aggregate_hybench = copy.deepcopy(aggregate.get("hybench") or {})
    for key in ("xp_qps", "xp_tps"):
        values = [safe_float((row.get("hybench") or {}).get(key), math.nan) for row in ok_rows]
        values = [value for value in values if math.isfinite(value)]
        if values:
            aggregate_hybench[key] = sum(values) / len(values)
    if aggregate_hybench:
        aggregate["hybench"] = aggregate_hybench

    sample_keys = ("cpu_cores", "memory_bytes", "io_bps", "io_read_bps", "io_write_bps")
    resource_samples: Dict[str, int] = {}
    for key in sample_keys:
        values = [safe_int((row.get("resource_samples") or {}).get(key), -1) for row in ok_rows]
        values = [value for value in values if value >= 0]
        if values:
            resource_samples[key] = min(values)
    if resource_samples:
        aggregate["resource_samples"] = resource_samples

    aggregate["label"] = "parallel_baseline_aggregate"
    aggregate["worker"] = None
    aggregate["worker_key"] = "parallel_baseline_aggregate"
    aggregate["worker_slot"] = None
    aggregate["parallel_worker_aggregate"] = {
        "method": "mean_resources_min_samples",
        "worker_count": len(ok_rows),
        "worker_keys": [validation_worker_identity(row) for row in ok_rows],
        "io_sources": sorted(
            {
                str((row.get("resource_stats") or {}).get("io_source") or "")
                for row in ok_rows
                if str((row.get("resource_stats") or {}).get("io_source") or "")
            }
        ),
    }
    return aggregate


def resolve_worker_baseline(
    validation: Dict[str, Any],
    default_baseline: Dict[str, Any],
    baseline_by_worker: Dict[str, Dict[str, Any]],
) -> Tuple[Dict[str, Any], str, bool]:
    key = validation_worker_identity(validation)
    if key and key in baseline_by_worker:
        return baseline_by_worker[key], key, True
    return default_baseline, key, False


def resolve_same_worker_outer_references(
    candidate: Dict[str, Any],
    current_best: Dict[str, Any],
    default_baseline: Dict[str, Any],
    baseline_by_worker: Dict[str, Dict[str, Any]],
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """Resolve an incumbent comparison on the candidate's physical worker scale.

    A cross-worker accepted incumbent is mapped by its measured improvement ratio
    relative to its own worker baseline. This keeps the incumbent semantics while
    avoiding direct comparisons between workers with different baseline throughput.
    """
    candidate_baseline, candidate_worker_key, candidate_matched = resolve_worker_baseline(
        candidate,
        default_baseline,
        baseline_by_worker,
    )
    metadata: Dict[str, Any] = {
        "mode": "same_worker",
        "candidate_worker_key": candidate_worker_key,
        "candidate_baseline_matched": candidate_matched,
        "candidate_baseline_label": candidate_baseline.get("label"),
        "current_best_label": current_best.get("label"),
        "current_best_worker_key": validation_worker_identity(current_best),
        "normalized": False,
        "valid": bool(candidate_matched),
    }
    if not candidate_matched:
        metadata["reason"] = "candidate_worker_baseline_missing"
        return copy.deepcopy(current_best), candidate_baseline, metadata

    if validation_is_baseline(current_best):
        metadata.update(
            {
                "reason": "baseline_incumbent_uses_candidate_worker_baseline",
                "reference_label": candidate_baseline.get("label"),
            }
        )
        return candidate_baseline, candidate_baseline, metadata

    current_worker_key = validation_worker_identity(current_best)
    if current_worker_key and current_worker_key == candidate_worker_key:
        metadata.update(
            {
                "reason": "accepted_incumbent_already_on_candidate_worker",
                "reference_label": current_best.get("label"),
            }
        )
        return current_best, candidate_baseline, metadata

    source_baseline = baseline_by_worker.get(current_worker_key)
    if source_baseline is None:
        metadata.update(
            {
                "valid": False,
                "reason": "accepted_incumbent_source_worker_baseline_missing",
            }
        )
        return copy.deepcopy(current_best), candidate_baseline, metadata

    normalized_reference = copy.deepcopy(current_best)
    normalized_reference["label"] = (
        f"{current_best.get('label') or 'accepted_incumbent'}"
        f"__normalized_to__{candidate_worker_key}"
    )
    normalized_reference["worker"] = copy.deepcopy(candidate_baseline.get("worker"))
    ratios: Dict[str, float] = {}
    source_qps = safe_float(source_baseline.get("db_qps"), math.nan)
    current_qps = safe_float(current_best.get("db_qps"), math.nan)
    candidate_baseline_qps = safe_float(candidate_baseline.get("db_qps"), math.nan)
    if (
        math.isfinite(source_qps)
        and source_qps > 0.0
        and math.isfinite(current_qps)
        and math.isfinite(candidate_baseline_qps)
        and candidate_baseline_qps > 0.0
    ):
        ratios["db_qps"] = current_qps / source_qps
        normalized_reference["db_qps"] = candidate_baseline_qps * ratios["db_qps"]

    source_resources = source_baseline.get("resources") or {}
    current_resources = current_best.get("resources") or {}
    candidate_baseline_resources = candidate_baseline.get("resources") or {}
    normalized_resources = copy.deepcopy(current_resources)
    for resource_name in ("cpu", "memory", "io"):
        source_value = safe_float(source_resources.get(resource_name), math.nan)
        current_value = safe_float(current_resources.get(resource_name), math.nan)
        candidate_baseline_value = safe_float(
            candidate_baseline_resources.get(resource_name), math.nan
        )
        if (
            math.isfinite(source_value)
            and source_value > 0.0
            and math.isfinite(current_value)
            and math.isfinite(candidate_baseline_value)
            and candidate_baseline_value > 0.0
        ):
            ratios[resource_name] = current_value / source_value
            normalized_resources[resource_name] = (
                candidate_baseline_value * ratios[resource_name]
            )
    normalized_reference["resources"] = normalized_resources
    required_ratios = {"db_qps", "cpu", "memory", "io"}
    metadata.update(
        {
            "normalized": True,
            "valid": required_ratios.issubset(ratios),
            "reason": "accepted_incumbent_ratio_normalized_to_candidate_worker",
            "source_baseline_label": source_baseline.get("label"),
            "source_worker_key": current_worker_key,
            "reference_label": normalized_reference.get("label"),
            "normalization_ratios": ratios,
        }
    )
    if not metadata["valid"]:
        metadata["reason"] = "accepted_incumbent_cross_worker_normalization_incomplete"
    normalized_reference["same_worker_reference_normalization"] = copy.deepcopy(metadata)
    return normalized_reference, candidate_baseline, metadata


def psql_env(
    pg: PgConn,
    environment_overrides: Optional[Mapping[str, str]] = None,
) -> Dict[str, str]:
    env = os.environ.copy()
    if environment_overrides:
        env.update(
            {
                str(name): str(value)
                for name, value in environment_overrides.items()
            }
        )
    env["PGCONNECT_TIMEOUT"] = str(pg.connect_timeout_s)
    if pg.password:
        env["PGPASSWORD"] = pg.password
    return env


def psql_args(pg: PgConn, sql: str, *, dbname: Optional[str] = None) -> List[str]:
    return [
        "psql",
        "-h",
        pg.host,
        "-p",
        str(pg.port),
        "-U",
        pg.user,
        "-d",
        dbname or pg.dbname,
        "-X",
        "-q",
        "-v",
        "ON_ERROR_STOP=1",
        "-At",
        "-F",
        "\t",
        "-c",
        sql,
    ]


def ignore_external_stop_cmd(cmd: Sequence[str]) -> List[str]:
    """Run a child command with TERM/HUP ignored; parent timeouts still escalate to SIGKILL."""
    return ["bash", "-c", "trap '' TERM HUP; exec \"$@\"", "bonect-child", *map(str, cmd)]


def subprocess_detail(proc: subprocess.CompletedProcess) -> str:
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    parts = [f"rc={proc.returncode}"]
    if stderr:
        parts.append(f"stderr={stderr[-1000:]}")
    if stdout:
        parts.append(f"stdout={stdout[-1000:]}")
    return " ".join(parts)


def run_docker_cmd(
    cmd: Sequence[str],
    *,
    timeout_s: Optional[float] = None,
    retries: int = 3,
    serialize: bool = True,
) -> subprocess.CompletedProcess:
    """Run a Docker CLI command with bounded retry and useful diagnostics."""
    attempts = max(1, int(retries))
    last_proc: Optional[subprocess.CompletedProcess] = None
    last_timeout: Optional[subprocess.TimeoutExpired] = None
    for attempt in range(attempts):
        try:
            if serialize:
                with _DOCKER_CLI_LOCK:
                    proc = subprocess.run(
                        ignore_external_stop_cmd(cmd),
                        text=True,
                        capture_output=True,
                        check=False,
                        timeout=timeout_s,
                        start_new_session=True,
                    )
            else:
                proc = subprocess.run(
                    ignore_external_stop_cmd(cmd),
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=timeout_s,
                    start_new_session=True,
                )
        except subprocess.TimeoutExpired as exc:
            last_timeout = exc
            last_proc = None
        else:
            last_proc = proc
            last_timeout = None
            if proc.returncode == 0:
                return proc
        if attempt + 1 < attempts:
            time.sleep(min(5.0, 0.5 * float(attempt + 1)))
    if last_proc is not None:
        return last_proc
    if last_timeout is not None:
        raise RuntimeError(f"docker command timed out after {timeout_s}s: {' '.join(map(str, cmd))}")
    raise RuntimeError(f"docker command did not run: {' '.join(map(str, cmd))}")


def _find_first_existing(base_dirs: List[str], filenames: List[str]) -> Optional[str]:
    for base in base_dirs:
        for filename in filenames:
            candidate = os.path.join(base, filename)
            try:
                with open(candidate, "r", encoding="utf-8", errors="replace") as handle:
                    handle.read(1)
                return candidate
            except OSError:
                continue
    return None


def _find_cgroup_paths(container_name: str) -> Dict[str, str]:
    """Locate cgroup v1/v2 paths with Docker CLI retry for parallel validations."""
    proc = run_docker_cmd(
        ["docker", "inspect", "--format", "{{.Id}}", container_name],
        timeout_s=20,
        retries=5,
        serialize=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"docker inspect failed for {container_name}: {subprocess_detail(proc)}")
    cid = (proc.stdout or "").strip()
    if not cid:
        raise RuntimeError(f"docker inspect returned empty container id for {container_name}")

    v2_bases = [
        f"/sys/fs/cgroup/system.slice/docker-{cid}.scope",
        f"/sys/fs/cgroup/docker/{cid}",
        f"/sys/fs/cgroup/docker-limited/{cid}",
        f"/sys/fs/cgroup/{cid}",
    ]
    v2_cpu = _find_first_existing(v2_bases, ["cpu.stat"])
    v2_mem = _find_first_existing(v2_bases, ["memory.current"])
    if v2_cpu and v2_mem:
        return {
            "version": "v2",
            "cpu": v2_cpu,
            "mem": v2_mem,
            "mem_dir": os.path.dirname(v2_mem),
            "io": _find_first_existing(v2_bases, ["io.stat"]),
        }

    v1_cpu_bases = [
        f"/sys/fs/cgroup/cpu,cpuacct/docker-limited/{cid}",
        f"/sys/fs/cgroup/cpu,cpuacct/docker/{cid}",
        f"/sys/fs/cgroup/cpuacct/docker/{cid}",
    ]
    v1_mem_bases = [
        f"/sys/fs/cgroup/memory/docker-limited/{cid}",
        f"/sys/fs/cgroup/memory/docker/{cid}",
    ]
    v1_blkio_bases = [
        f"/sys/fs/cgroup/blkio/docker-limited/{cid}",
        f"/sys/fs/cgroup/blkio/docker/{cid}",
        f"/sys/fs/cgroup/blkio/{cid}",
    ]

    cpu_path = _find_first_existing(v1_cpu_bases, ["cpuacct.usage"])
    mem_path = _find_first_existing(v1_mem_bases, ["memory.usage_in_bytes"])
    io_bytes_path = _find_first_existing(
        v1_blkio_bases,
        [
            "blkio.throttle.io_service_bytes",
            "blkio.throttle.io_service_bytes_recursive",
            "blkio.io_service_bytes",
            "blkio.io_service_bytes_recursive",
        ],
    )
    io_ios_path = _find_first_existing(
        v1_blkio_bases,
        [
            "blkio.throttle.io_serviced",
            "blkio.throttle.io_serviced_recursive",
            "blkio.io_serviced",
            "blkio.io_serviced_recursive",
        ],
    )

    if not cpu_path or not mem_path:
        raise RuntimeError(
            f"cgroup paths not found for container={container_name} cid={cid}: "
            f"cpu={cpu_path}, mem={mem_path}, io_bytes={io_bytes_path}, io_ios={io_ios_path}"
        )
    return {
        "version": "v1",
        "cpu": cpu_path,
        "mem": mem_path,
        "mem_dir": os.path.dirname(mem_path),
        "io_bytes": io_bytes_path or "",
        "io_ios": io_ios_path or "",
    }


def psql_query(pg: PgConn, sql: str, *, dbname: Optional[str] = None, timeout_s: float = 60.0) -> str:
    proc = subprocess.run(
        ignore_external_stop_cmd(psql_args(pg, sql, dbname=dbname)),
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_s,
        env=psql_env(pg),
        start_new_session=True,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"psql query failed rc={proc.returncode}: {detail}")
    return (proc.stdout or "").strip()


def _is_transient_psql_transport_error(exc: BaseException) -> bool:
    """Return whether a psql failure is a transient connection/transport error."""
    if isinstance(exc, subprocess.TimeoutExpired):
        return True
    detail = str(exc).lower()
    return any(
        marker in detail
        for marker in (
            "connection to server",
            "could not connect to server",
            "connection refused",
            "timeout expired",
            "server closed the connection unexpectedly",
            "terminating connection due to administrator command",
        )
    )


def psql_query_with_transient_retry(
    pg: PgConn,
    sql: str,
    *,
    dbname: Optional[str] = None,
    timeout_s: float = 60.0,
    attempts: int = 3,
    retry_delay_s: float = 0.25,
    retry_context: str = "plan_probe",
) -> str:
    """
    Retry only transient PostgreSQL transport failures.

    Callers using this helper for writes must ensure that replaying the SQL is
    idempotent. Deterministic SQL errors still fail closed on the first attempt.
    """
    bounded_attempts = max(1, int(attempts))
    last_error: Optional[BaseException] = None
    for attempt in range(1, bounded_attempts + 1):
        try:
            return psql_query(pg, sql, dbname=dbname, timeout_s=timeout_s)
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            last_error = exc
            if not _is_transient_psql_transport_error(exc) or attempt >= bounded_attempts:
                raise
            print(
                f"{retry_context}_transient_psql_retry "
                f"attempt={attempt + 1}/{bounded_attempts} error={exc}",
                file=sys.stderr,
                flush=True,
            )
            if retry_delay_s > 0.0:
                time.sleep(retry_delay_s * attempt)
    if last_error is not None:  # pragma: no cover - the loop either returns or raises.
        raise last_error
    raise RuntimeError("psql retry loop did not execute")  # pragma: no cover


def checkpoint_postgres(pg: PgConn, *, timeout_s: float = 300.0, settle_s: float = 0.0) -> None:
    psql_query(pg, "CHECKPOINT;", timeout_s=timeout_s)
    if settle_s > 0.0:
        time.sleep(settle_s)


def quote_pg_value(value: Any) -> str:
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return f"{float(value):.17g}"


def wait_for_postgres(pg: PgConn, timeout_s: float = 180.0) -> None:
    deadline = time.time() + timeout_s
    last_error = ""
    while time.time() < deadline:
        try:
            psql_query(pg, "SELECT 1;", timeout_s=10.0)
            return
        except Exception as exc:
            last_error = str(exc)
            time.sleep(2.0)
    raise RuntimeError(f"PostgreSQL did not become ready: {last_error}")


def normalize_params(params: Dict[str, Any]) -> Dict[str, float]:
    return {name: float(params[name]) for name in COST_PARAMS if name in params}


def initial_params(cfg: Dict[str, Any]) -> Dict[str, float]:
    params = normalize_params(cfg.get("initial_params") or {})
    missing = [name for name in COST_PARAMS if name not in params]
    if missing:
        raise ValueError(f"initial_params missing: {', '.join(missing)}")
    return params


def param_ranges(cfg: Dict[str, Any]) -> Dict[str, Tuple[float, float]]:
    ranges_cfg = cfg.get("param_ranges") or {}
    ranges: Dict[str, Tuple[float, float]] = {}
    for name in COST_PARAMS:
        item = ranges_cfg.get(name) or {}
        if "min_value" not in item or "max_value" not in item:
            raise ValueError(f"param_ranges.{name} missing min_value/max_value")
        low = float(item["min_value"])
        high = float(item["max_value"])
        if high < low:
            raise ValueError(f"invalid range for {name}: {low} > {high}")
        ranges[name] = (low, high)
    return ranges


def apply_pg_params(pg: PgConn, params: Dict[str, float]) -> Dict[str, str]:
    for name in COST_PARAMS:
        if name in params:
            psql_query_with_transient_retry(
                pg,
                f"ALTER SYSTEM SET {name} = {quote_pg_value(params[name])};",
                dbname="postgres",
                attempts=5,
                retry_delay_s=1.0,
                retry_context="parameter_apply",
            )
    psql_query_with_transient_retry(
        pg,
        "SELECT pg_reload_conf();",
        dbname="postgres",
        attempts=5,
        retry_delay_s=1.0,
        retry_context="parameter_reload",
    )
    time.sleep(0.5)
    return read_effective_params(pg, COST_PARAMS)


def restore_database_from_template(pg: PgConn, template_db: str) -> str:
    target_db = pg.dbname
    safe_target = re.sub(r"[^A-Za-z0-9_]", "", target_db)
    safe_template = re.sub(r"[^A-Za-z0-9_]", "", template_db)
    if not safe_target or not safe_template or safe_target != target_db or safe_template != template_db:
        raise ValueError(f"unsafe database/template name: {target_db!r}/{template_db!r}")
    escaped_target = safe_target.replace("'", "''")
    escaped_template = safe_template.replace("'", "''")
    exists = psql_query_with_transient_retry(
        pg,
        f"SELECT 1 FROM pg_database WHERE datname = '{escaped_template}' LIMIT 1;",
        dbname="postgres",
        attempts=5,
        retry_delay_s=1.0,
        retry_context="database_restore_template_check",
    ).strip()
    if exists != "1":
        raise RuntimeError(f"template database {safe_template} does not exist on {pg.host}:{pg.port}")
    requested_strategy = str(
        os.environ.get("PG_ALGORITHM_C_RESTORE_DATABASE_STRATEGY") or "default"
    ).strip().lower().replace("-", "_")
    strategy_clauses = {
        "default": "",
        "wal_log": " STRATEGY = WAL_LOG",
        "file_copy": " STRATEGY = FILE_COPY",
    }
    if requested_strategy not in strategy_clauses:
        raise ValueError(
            "PG_ALGORITHM_C_RESTORE_DATABASE_STRATEGY must be one of "
            "default, wal_log, file_copy"
        )
    strategy_clause = strategy_clauses[requested_strategy]
    last_error: Optional[BaseException] = None
    for attempt in range(1, 6):
        try:
            psql_query(
                pg,
                f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{escaped_target}';",
                dbname="postgres",
                timeout_s=120.0,
            )
            psql_query(pg, f"DROP DATABASE IF EXISTS {safe_target} WITH (FORCE);", dbname="postgres", timeout_s=300.0)
            psql_query(
                pg,
                f"CREATE DATABASE {safe_target} WITH TEMPLATE {safe_template} "
                f"OWNER {pg.user}{strategy_clause};",
                dbname="postgres",
                timeout_s=900.0,
            )
            return requested_strategy
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            last_error = exc
            message = str(exc).lower()
            duplicate_database = (
                "already exists" in message or "duplicate key" in message
            )
            transient_transport = _is_transient_psql_transport_error(exc)
            if attempt >= 5 or not (duplicate_database or transient_transport):
                raise
            print(
                "database_restore_transient_psql_retry "
                f"attempt={attempt + 1}/5 error={exc}",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(float(min(5, attempt)))
    if last_error is not None:
        raise last_error


def read_effective_params(pg: PgConn, names: Sequence[str]) -> Dict[str, str]:
    quoted = ", ".join("'" + name.replace("'", "''") + "'" for name in names)
    sql = (
        "SELECT name, setting, COALESCE(unit, '') "
        f"FROM pg_settings WHERE name IN ({quoted}) ORDER BY name;"
    )
    out = psql_query_with_transient_retry(
        pg,
        sql,
        attempts=5,
        retry_delay_s=1.0,
        retry_context="parameter_readback",
    )
    result: Dict[str, str] = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            unit = parts[2] if len(parts) >= 3 else ""
            result[parts[0]] = f"{parts[1]}{unit}".strip()
    return result


def has_pg_stat_statements(pg: PgConn) -> bool:
    sql = (
        "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_class c "
        "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
        "WHERE c.relname = 'pg_stat_statements');"
    )
    try:
        return psql_query(pg, sql).strip().lower() in {"t", "true", "1"}
    except Exception:
        return False


def fetch_pg_stat_statements_executor_telemetry(
    pg: PgConn,
    *,
    block_size: int = 8192,
) -> Dict[str, Any]:
    """Collect post-reset executor counters without executing the workload again.

    The validation path resets ``pg_stat_statements`` after warmup.  Therefore
    these totals are labels for the one physical workload invocation, not
    pre-replay candidate features.  Administrative monitoring queries are
    excluded so they cannot become calibration work.
    """
    try:
        columns_out = psql_query_with_transient_retry(
            pg,
            "SELECT attname FROM pg_attribute "
            "WHERE attrelid = 'pg_stat_statements'::regclass "
            "AND attnum > 0 AND NOT attisdropped ORDER BY attnum;",
            timeout_s=30.0,
            attempts=3,
            retry_delay_s=1.0,
            retry_context="executor_telemetry_column_discovery",
        )
    except Exception as exc:
        return {
            "available": False,
            "source": "pg_stat_statements_post_reset_aggregate_v1",
            "error": f"column_discovery_failed:{type(exc).__name__}:{exc}",
        }
    columns = {line.strip() for line in columns_out.splitlines() if line.strip()}
    metric_columns = (
        "calls",
        "rows",
        "shared_blks_hit",
        "shared_blks_read",
        "shared_blks_dirtied",
        "shared_blks_written",
        "local_blks_hit",
        "local_blks_read",
        "local_blks_dirtied",
        "local_blks_written",
        "temp_blks_read",
        "temp_blks_written",
        "blk_read_time",
        "blk_write_time",
        "wal_records",
        "wal_fpi",
        "wal_bytes",
    )
    expressions = [
        f"COALESCE(SUM({name}), 0)::numeric" if name in columns else "0::numeric"
        for name in metric_columns
    ]
    filters = [
        "dbid = (SELECT oid FROM pg_database WHERE datname = current_database())",
        "query NOT ILIKE '%pg_stat_statements%'",
        "query NOT ILIKE '%pg_stat_reset%'",
        "query NOT ILIKE 'SHOW %'",
        "query NOT ILIKE 'SET %'",
        "query NOT ILIKE 'ALTER SYSTEM %'",
        "query NOT ILIKE 'CHECKPOINT%'",
    ]
    sql = "SELECT " + ", ".join(expressions) + " FROM pg_stat_statements WHERE " + " AND ".join(filters) + ";"
    try:
        raw = psql_query_with_transient_retry(
            pg,
            sql,
            timeout_s=30.0,
            attempts=3,
            retry_delay_s=1.0,
            retry_context="executor_telemetry_aggregate",
        )
        parts = raw.split("\t")
        if len(parts) != len(metric_columns):
            raise RuntimeError(
                f"unexpected executor telemetry field count: {len(parts)} != {len(metric_columns)}"
            )
        metrics = {
            name: safe_float(parts[index], 0.0)
            for index, name in enumerate(metric_columns)
        }
    except Exception as exc:
        return {
            "available": False,
            "source": "pg_stat_statements_post_reset_aggregate_v1",
            "columns": sorted(columns),
            "error": f"aggregate_failed:{type(exc).__name__}:{exc}",
        }
    shared_accesses = metrics["shared_blks_hit"] + metrics["shared_blks_read"]
    cache_hit_rate = (
        metrics["shared_blks_hit"] / shared_accesses
        if shared_accesses > 0.0
        else None
    )
    block_bytes = max(1, int(block_size))
    temp_blocks = metrics["temp_blks_read"] + metrics["temp_blks_written"]
    return {
        "available": True,
        "source": "pg_stat_statements_post_reset_aggregate_v1",
        "semantics": "post_warmup_single_physical_workload_label_not_candidate_score_input",
        "columns": sorted(columns.intersection(metric_columns)),
        "metrics": metrics,
        "derived": {
            "shared_cache_hit_rate": cache_hit_rate,
            "shared_block_accesses": shared_accesses,
            "temp_blocks": temp_blocks,
            "temp_bytes": temp_blocks * block_bytes,
            "shared_read_bytes": metrics["shared_blks_read"] * block_bytes,
            "shared_written_bytes": metrics["shared_blks_written"] * block_bytes,
            "block_size_bytes": block_bytes,
        },
        "qps_used_for_resource_prediction": False,
    }


def reset_pg_stats(pg: PgConn) -> None:
    for sql in ("SELECT pg_stat_reset();", "SELECT pg_stat_statements_reset();"):
        try:
            psql_query(pg, sql, timeout_s=30.0)
        except Exception:
            pass


def fetch_db_snapshot(pg: PgConn, include_statements: bool) -> DbSnapshot:
    statements_expr = (
        "(SELECT COALESCE(SUM(calls),0)::bigint FROM pg_stat_statements "
        "WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database()))"
        if include_statements
        else "NULL::bigint"
    )
    sql = f"""
        SELECT
            EXTRACT(EPOCH FROM clock_timestamp())::float8,
            COALESCE(d.xact_commit,0) + COALESCE(d.xact_rollback,0),
            COALESCE(d.blks_read,0),
            COALESCE(d.blks_hit,0),
            {statements_expr},
            (
                SELECT COUNT(*)::bigint
                FROM pg_stat_activity
                WHERE datname = current_database()
                  AND state = 'active'
                  AND pid <> pg_backend_pid()
            )
        FROM pg_stat_database d
        WHERE d.datname = current_database();
    """
    raw = psql_query(pg, sql, timeout_s=20.0)
    parts = raw.split("\t")
    if len(parts) != 6:
        raise RuntimeError(f"unexpected db snapshot: {raw!r}")
    return DbSnapshot(
        ts=float(parts[0]),
        xact_total=int(parts[1]),
        blks_read=int(parts[2]),
        blks_hit=int(parts[3]),
        statements_calls=int(parts[4]) if parts[4] else None,
        active_sessions=int(parts[5]),
    )


def db_rate(new_value: Optional[int], old_value: Optional[int], elapsed: float) -> Optional[float]:
    if new_value is None or old_value is None or elapsed <= 0:
        return None
    return max(0.0, float(new_value - old_value) / elapsed)


def db_row(prev: DbSnapshot, cur: DbSnapshot) -> Dict[str, Any]:
    elapsed = max(0.000001, cur.ts - prev.ts)
    return {
        "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(cur.ts)),
        "elapsed_s": elapsed,
        "qps_statements": db_rate(cur.statements_calls, prev.statements_calls, elapsed),
        "xact_tps": db_rate(cur.xact_total, prev.xact_total, elapsed),
        "block_read_ps": db_rate(cur.blks_read, prev.blks_read, elapsed),
        "block_hit_ps": db_rate(cur.blks_hit, prev.blks_hit, elapsed),
        "active_sessions": cur.active_sessions,
    }


def summarize_db_rows(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    numeric_keys = ["qps_statements", "xact_tps", "block_read_ps", "block_hit_ps", "active_sessions"]
    summary: Dict[str, Any] = {"sample_count": len(rows)}
    for key in numeric_keys:
        values = [safe_float(row.get(key), math.nan) for row in rows if row.get(key) is not None]
        values = [value for value in values if math.isfinite(value)]
        stats = _stats(values)
        summary[key] = stats
    summary["db_qps"] = (summary.get("qps_statements") or {}).get("avg")
    summary["db_xact_tps"] = (summary.get("xact_tps") or {}).get("avg")
    return summary


def db_sampler(
    pg: PgConn,
    include_statements: bool,
    interval_s: float,
    stop_event: threading.Event,
    rows: List[Dict[str, Any]],
    errors: List[str],
) -> None:
    try:
        prev = fetch_db_snapshot(pg, include_statements)
    except Exception as exc:
        errors.append(str(exc))
        return
    while not stop_event.wait(interval_s):
        try:
            cur = fetch_db_snapshot(pg, include_statements)
            rows.append(db_row(prev, cur))
            prev = cur
        except Exception as exc:
            if len(errors) < 8:
                errors.append(str(exc))


def normalize_docker_memory_mode(memory_mode: str) -> str:
    normalized = (memory_mode or "usage").strip().lower()
    if normalized in ("docker_usage", "cgroup_usage"):
        return "usage"
    if normalized in ("docker_stats", "working-set", "workingset"):
        return "working_set"
    return normalized


def resource_metric_meta(runtime_meta: Dict[str, Any], *, fallback_memory_mode: str) -> Dict[str, Any]:
    memory_mode_effective = runtime_meta.get("memory_mode_effective")
    memory_source = runtime_meta.get("memory_source")
    if not memory_mode_effective:
        normalized = normalize_docker_memory_mode(fallback_memory_mode)
        memory_mode_effective = f"cgroup_{normalized}"
    return {
        "memory": {
            "unit": "GiB",
            "mode_requested": runtime_meta.get("memory_mode_requested") or (fallback_memory_mode or "usage"),
            "mode_effective": memory_mode_effective,
            "source": memory_source or "",
        },
        "cpu": {"unit": "cores"},
        "io": {"unit": "MiB/s"},
        "io_read": {"unit": "MiB/s"},
        "io_write": {"unit": "MiB/s"},
    }


def is_container_pg_proc_io_mode(io_mode: str) -> bool:
    return str(io_mode or "").strip().lower() in {
        "pg_proc",
        "pg_proc_io",
        "process_io",
        "container_proc_io",
        "container_pg_proc_io",
        "postgres_proc_io",
        "postgres_process_io",
    }


def is_container_cgroup_io_mode(io_mode: str) -> bool:
    return str(io_mode or "").strip().lower() in {
        "",
        "docker",
        "cgroup",
        "docker_cgroup",
        "cgroup_blkio",
        "blkio",
        "io.stat",
        "io_stat",
        "cgroup_io",
    }


def _read_container_postgres_proc_io(container_name: str) -> Dict[str, float]:
    script = r'''
pids="$(pgrep -x postgres || true)"
if [ -z "$pids" ]; then
  printf "read_bytes=0\nwrite_bytes=0\npid_count=0\n"
  exit 0
fi
read_sum=0
write_sum=0
pid_count=0
for pid in $pids; do
  pid_count=$((pid_count + 1))
  while read -r key value; do
    case "$key" in
      read_bytes:) read_sum=$((read_sum + value)) ;;
      write_bytes:) write_sum=$((write_sum + value)) ;;
    esac
  done < "/proc/$pid/io"
done
printf "read_bytes=%s\nwrite_bytes=%s\npid_count=%s\n" "$read_sum" "$write_sum" "$pid_count"
'''
    try:
        proc = run_docker_cmd(
            ["docker", "exec", "-u", "postgres", container_name, "bash", "-lc", script],
            timeout_s=60,
            retries=3,
            serialize=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(subprocess_detail(proc))
    except Exception as exc:
        return {
            "io_read_bytes": 0.0,
            "io_write_bytes": 0.0,
            "io_pid_count": 0.0,
            "io_source": "container_procfs_pg_io_error",
            "io_error": str(exc),
        }
    counters = {
        "io_read_bytes": 0.0,
        "io_write_bytes": 0.0,
        "io_pid_count": 0.0,
        "io_source": "container_procfs_pg_io",
    }
    for line in proc.stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        try:
            numeric = float(value.strip())
        except ValueError:
            continue
        if key.strip() == "read_bytes":
            counters["io_read_bytes"] = numeric
        elif key.strip() == "write_bytes":
            counters["io_write_bytes"] = numeric
        elif key.strip() == "pid_count":
            counters["io_pid_count"] = numeric
    return counters


def _normalize_disk_device_name(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("/dev/"):
        text = text.rsplit("/", 1)[-1]
    return text


def _disk_devices_for_root(disk_root: str) -> List[str]:
    root = str(disk_root or "").strip()
    if not root:
        return []
    try:
        proc = subprocess.run(
            ["findmnt", "-T", root, "-no", "SOURCE"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except Exception:
        return []
    devices: List[str] = []
    for line in proc.stdout.splitlines():
        name = _normalize_disk_device_name(line)
        if name and name not in devices:
            devices.append(name)
    return devices


def _configured_disk_devices(io_mode: str, disk_root: str = "", disk_devices: Any = None) -> List[str]:
    mode = str(io_mode or "").strip().lower()
    if mode not in {"disk_device", "host_disk", "host_disk_device", "vm_disk_device"}:
        return []
    devices: List[str] = []
    raw_items: List[Any]
    if isinstance(disk_devices, str):
        raw_items = [item.strip() for item in disk_devices.split(",")]
    elif isinstance(disk_devices, Sequence):
        raw_items = list(disk_devices)
    else:
        raw_items = []
    for item in raw_items:
        name = _normalize_disk_device_name(str(item))
        if name and name not in devices:
            devices.append(name)
    for name in _disk_devices_for_root(disk_root):
        if name and name not in devices:
            devices.append(name)
    return devices


def _physical_observer_disk_devices(
    disk_root: str,
    disk_devices: Any = None,
) -> List[str]:
    """Resolve the host SSD observer independently from container I/O mode.

    Container read/write throughput may intentionally use cgroup accounting,
    while the orthogonal SSD-utilization qualification still needs a frozen
    physical device.  Prefer an explicitly configured device so a transient
    ``findmnt`` failure cannot silently remove the formal observer.
    """

    raw_items: List[Any]
    if isinstance(disk_devices, str):
        raw_items = [item.strip() for item in disk_devices.split(",")]
    elif isinstance(disk_devices, Sequence):
        raw_items = list(disk_devices)
    else:
        raw_items = []
    devices: List[str] = []
    for item in raw_items:
        name = _normalize_disk_device_name(str(item))
        if name and name not in devices:
            devices.append(name)
    if devices:
        return devices
    return _disk_devices_for_root(disk_root)


def _diskstats_snapshot(devices: Sequence[str]) -> Dict[str, float]:
    wanted = {_normalize_disk_device_name(item) for item in devices if _normalize_disk_device_name(item)}
    result = {"io_read_bytes": 0.0, "io_write_bytes": 0.0, "io_busy_ms": 0.0}
    if not wanted:
        return result
    try:
        lines = Path("/proc/diskstats").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return result
    for line in lines:
        parts = line.split()
        if len(parts) < 10 or parts[2] not in wanted:
            continue
        result["io_read_bytes"] += safe_float(parts[5], 0.0) * 512.0
        result["io_write_bytes"] += safe_float(parts[9], 0.0) * 512.0
        if len(parts) > 12:
            result["io_busy_ms"] += safe_float(parts[12], 0.0)
    return result


def docker_sampler(
    container_name: str,
    interval_s: float,
    warmup_s: float,
    memory_mode: str,
    io_mode: str,
    max_cpu_cores: float,
    disk_root: str,
    disk_devices: Any,
    stop_event: threading.Event,
    samples: Dict[str, List[float]],
    meta: Dict[str, Any],
    errors: List[str],
) -> None:
    try:
        cgroup_paths = _find_cgroup_paths(container_name)
    except Exception as exc:
        errors.append(str(exc))
        return
    start_ts = time.monotonic()
    prev_snap: Optional[Dict[str, float]] = None
    prev_ts: Optional[float] = None
    pg_proc_io_start: Optional[Dict[str, float]] = None
    pg_proc_io_start_ts: Optional[float] = None
    requested_memory_mode = (memory_mode or "usage").strip().lower()
    effective_memory_mode = normalize_docker_memory_mode(memory_mode)
    requested_io_mode = (io_mode or "docker").strip().lower()
    use_pg_proc_io = is_container_pg_proc_io_mode(requested_io_mode)
    host_disk_devices = (
        []
        if use_pg_proc_io
        else _configured_disk_devices(requested_io_mode, disk_root=disk_root, disk_devices=disk_devices)
    )
    physical_disk_devices = host_disk_devices or _physical_observer_disk_devices(
        disk_root,
        disk_devices,
    )
    meta["resource_scope"] = "docker_container"
    meta["container_name"] = container_name
    meta["memory_mode_requested"] = requested_memory_mode
    meta["memory_mode_effective"] = f"cgroup_{effective_memory_mode}"
    meta["io_mode_requested"] = requested_io_mode
    if use_pg_proc_io:
        meta["io_mode_effective"] = "container_pg_proc_io"
    else:
        meta["io_mode_effective"] = "host_disk_device" if host_disk_devices else "docker_cgroup"
    if host_disk_devices:
        meta["io_disk_devices"] = list(host_disk_devices)
    if physical_disk_devices:
        meta["ssd_observer_disk_devices"] = list(physical_disk_devices)
        meta["ssd_util_source"] = "host_diskstats_io_busy_ms"
    meta["max_cpu_cores"] = max(0.0, float(max_cpu_cores or 0.0))
    meta["cpu_outlier_count"] = 0
    meta["cpu_outlier_max"] = 0.0
    meta["cpu_outlier_policy"] = "clip_to_allocated_core_limit"
    meta["cpu_outlier_clipped_count"] = 0
    meta["short_rate_interval_count"] = 0
    meta["note"] = "runtime resources are sampled only from the target Docker database container"

    def read_pg_proc_io_marker(marker: str) -> Optional[Dict[str, float]]:
        snap = _read_container_postgres_proc_io(container_name)
        now = time.monotonic()
        meta["io_source"] = str(snap.get("io_source") or "container_procfs_pg_io")
        meta["io_pg_pid_count"] = snap.get("io_pid_count", 0.0)
        if snap.get("io_error"):
            if len(errors) < 8:
                errors.append(str(snap.get("io_error")))
            return None
        meta[f"io_{marker}_monotonic_ts"] = now
        meta[f"io_{marker}_read_bytes"] = safe_float(snap.get("io_read_bytes"), 0.0)
        meta[f"io_{marker}_write_bytes"] = safe_float(snap.get("io_write_bytes"), 0.0)
        return snap

    def append_pg_proc_io_average() -> None:
        if not use_pg_proc_io or pg_proc_io_start is None or pg_proc_io_start_ts is None:
            return
        end_snap = read_pg_proc_io_marker("stable_end")
        if end_snap is None:
            return
        end_ts = safe_float(meta.get("io_stable_end_monotonic_ts"), time.monotonic())
        dt = max(1e-6, end_ts - pg_proc_io_start_ts)
        read_bps = max(
            0.0,
            safe_float(end_snap.get("io_read_bytes"), 0.0)
            - safe_float(pg_proc_io_start.get("io_read_bytes"), 0.0),
        ) / dt
        write_bps = max(
            0.0,
            safe_float(end_snap.get("io_write_bytes"), 0.0)
            - safe_float(pg_proc_io_start.get("io_write_bytes"), 0.0),
        ) / dt
        sample_count = max(len(samples.get("cpu_cores", [])), len(samples.get("memory_bytes", [])))
        if sample_count <= 0:
            sample_count = 1
        samples["io_read_bps"].extend([read_bps] * sample_count)
        samples["io_write_bps"].extend([write_bps] * sample_count)
        samples["io_bps"].extend([read_bps + write_bps] * sample_count)
        meta["io_sample_strategy"] = "container_pg_proc_io_stable_window_average"
        meta["io_stable_window_s"] = dt

    def collect_once() -> None:
        nonlocal prev_snap, prev_ts, pg_proc_io_start, pg_proc_io_start_ts
        snap = _cgroup_snapshot(
            cgroup_paths,
            container_name=None,
            memory_mode=effective_memory_mode,
        )
        now = time.monotonic()
        meta["memory_source"] = "docker_" + str(snap.get("memory_source", "unknown"))
        meta["memory_pg_pid_count"] = 0.0
        disk_snap = _diskstats_snapshot(physical_disk_devices) if physical_disk_devices else {}
        if not use_pg_proc_io:
            meta["io_source"] = (
                "host_disk_device:" + ",".join(host_disk_devices)
                if host_disk_devices
                else "docker_" + str(snap.get("io_source", "unknown"))
            )
        stable = (now - start_ts) >= max(0.0, warmup_s)
        if stable and use_pg_proc_io and pg_proc_io_start is None:
            pg_proc_io_start = read_pg_proc_io_marker("stable_start")
            if pg_proc_io_start is not None:
                pg_proc_io_start_ts = safe_float(meta.get("io_stable_start_monotonic_ts"), time.monotonic())
        if stable:
            samples["memory_bytes"].append(float(snap.get("memory_bytes", 0.0) or 0.0))
        if prev_snap is not None and prev_ts is not None:
            dt = max(1e-6, now - prev_ts)
            cpu_cores = max(0.0, snap["cpu_ns"] - prev_snap["cpu_ns"]) / (dt * 1e9)
            io_util_pct: Optional[float] = None
            if physical_disk_devices:
                busy_ms = max(
                    0.0,
                    disk_snap.get("io_busy_ms", 0.0) - prev_snap.get("disk_io_busy_ms", 0.0),
                )
                io_util_pct = min(100.0, busy_ms / (dt * 10.0))
            if host_disk_devices:
                read_bps = max(0.0, disk_snap.get("io_read_bytes", 0.0) - prev_snap.get("disk_io_read_bytes", 0.0)) / dt
                write_bps = max(0.0, disk_snap.get("io_write_bytes", 0.0) - prev_snap.get("disk_io_write_bytes", 0.0)) / dt
            elif not use_pg_proc_io:
                read_bps = max(0.0, snap["io_read_bytes"] - prev_snap.get("io_read_bytes", 0.0)) / dt
                write_bps = max(0.0, snap["io_write_bytes"] - prev_snap.get("io_write_bytes", 0.0)) / dt
            if stable:
                min_rate_interval = max(0.05, max(0.1, interval_s) * 0.5)
                if dt < min_rate_interval:
                    meta["short_rate_interval_count"] = int(meta.get("short_rate_interval_count") or 0) + 1
                else:
                    if io_util_pct is not None:
                        samples.setdefault("io_util_pct", []).append(io_util_pct)
                    cpu_limit = float(meta.get("max_cpu_cores") or 0.0)
                    if cpu_limit > 0.0 and cpu_cores > cpu_limit:
                        meta["cpu_outlier_count"] = int(meta.get("cpu_outlier_count") or 0) + 1
                        meta["cpu_outlier_max"] = max(float(meta.get("cpu_outlier_max") or 0.0), cpu_cores)
                        meta["cpu_outlier_clipped_count"] = int(
                            meta.get("cpu_outlier_clipped_count") or 0
                        ) + 1
                        samples["cpu_cores"].append(cpu_limit)
                    else:
                        samples["cpu_cores"].append(cpu_cores)
                    if not use_pg_proc_io:
                        samples["io_read_bps"].append(read_bps)
                        samples["io_write_bps"].append(write_bps)
                        samples["io_bps"].append(read_bps + write_bps)
        if physical_disk_devices:
            snap["disk_io_busy_ms"] = disk_snap.get("io_busy_ms", 0.0)
        if host_disk_devices:
            snap["disk_io_read_bytes"] = disk_snap.get("io_read_bytes", 0.0)
            snap["disk_io_write_bytes"] = disk_snap.get("io_write_bytes", 0.0)
        prev_snap = snap
        prev_ts = now

    while not stop_event.is_set():
        try:
            collect_once()
        except Exception as exc:
            if len(errors) < 8:
                errors.append(str(exc))
        stop_event.wait(max(0.1, interval_s))
    try:
        collect_once()
    except Exception as exc:
        if len(errors) < 8:
            errors.append(str(exc))
    append_pg_proc_io_average()


def _read_postmaster_pid(data_dir: str) -> Optional[int]:
    if not data_dir:
        return None
    pid_path = Path(data_dir) / "postmaster.pid"
    try:
        first_line = pid_path.read_text(encoding="utf-8", errors="replace").splitlines()[0].strip()
        pid = int(first_line)
    except (OSError, IndexError, ValueError):
        return None
    return pid if (Path("/proc") / str(pid)).exists() else None


def _proc_ppid(pid: int) -> Optional[int]:
    try:
        text = (Path("/proc") / str(pid) / "stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    end = text.rfind(")")
    if end < 0:
        return None
    fields = text[end + 2 :].split()
    if len(fields) < 2:
        return None
    try:
        return int(fields[1])
    except ValueError:
        return None


def _pid_descendants(root_pid: int) -> List[int]:
    children: Dict[int, List[int]] = {}
    for proc_dir in Path("/proc").iterdir():
        if not proc_dir.name.isdigit():
            continue
        pid = int(proc_dir.name)
        ppid = _proc_ppid(pid)
        if ppid is None:
            continue
        children.setdefault(ppid, []).append(pid)
    seen: Set[int] = set()
    stack = [root_pid]
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        stack.extend(children.get(pid, []))
    return sorted(seen)


def _proc_cpu_ticks(pid: int) -> float:
    try:
        text = (Path("/proc") / str(pid) / "stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0.0
    end = text.rfind(")")
    if end < 0:
        return 0.0
    fields = text[end + 2 :].split()
    if len(fields) <= 12:
        return 0.0
    try:
        return float(int(fields[11]) + int(fields[12]))
    except ValueError:
        return 0.0


def _proc_io_bytes(pid: int) -> Tuple[float, float]:
    read_bytes = 0.0
    write_bytes = 0.0
    try:
        lines = (Path("/proc") / str(pid) / "io").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return read_bytes, write_bytes
    for line in lines:
        if line.startswith("read_bytes:"):
            read_bytes = safe_float(line.split(":", 1)[1].strip(), 0.0)
        elif line.startswith("write_bytes:"):
            write_bytes = safe_float(line.split(":", 1)[1].strip(), 0.0)
    return read_bytes, write_bytes


def _proc_memory_bytes(pid: int, memory_mode: str) -> Tuple[float, str]:
    mode = (memory_mode or "pss").strip().lower()
    if mode in {"pss", "process_pss", "pg_proc_pss"}:
        try:
            for line in (Path("/proc") / str(pid) / "smaps_rollup").read_text(
                encoding="utf-8",
                errors="replace",
            ).splitlines():
                if line.startswith("Pss:"):
                    return safe_float(line.split()[1], 0.0) * 1024.0, "process_pss"
        except OSError:
            pass
    try:
        for line in (Path("/proc") / str(pid) / "status").read_text(
            encoding="utf-8",
            errors="replace",
        ).splitlines():
            if line.startswith("VmRSS:"):
                return safe_float(line.split()[1], 0.0) * 1024.0, "process_rss"
    except OSError:
        pass
    return 0.0, "process_unreadable"


def _local_pg_process_snapshot(data_dir: str, memory_mode: str) -> Dict[str, float]:
    root_pid = _read_postmaster_pid(data_dir)
    if root_pid is None:
        raise RuntimeError(f"postmaster pid not found for data_dir={data_dir}")
    pids = _pid_descendants(root_pid)
    cpu_ticks = 0.0
    memory_bytes = 0.0
    io_read_bytes = 0.0
    io_write_bytes = 0.0
    memory_source = "process_unreadable"
    memory_pid_count = 0
    io_pid_count = 0
    for pid in pids:
        cpu_ticks += _proc_cpu_ticks(pid)
        mem_value, mem_source = _proc_memory_bytes(pid, memory_mode)
        if mem_value > 0.0:
            memory_pid_count += 1
            memory_bytes += mem_value
            if mem_source == "process_pss":
                memory_source = "process_pss"
            elif memory_source != "process_pss":
                memory_source = mem_source
        read_bytes, write_bytes = _proc_io_bytes(pid)
        if read_bytes > 0.0 or write_bytes > 0.0:
            io_pid_count += 1
            io_read_bytes += read_bytes
            io_write_bytes += write_bytes
    return {
        "root_pid": float(root_pid),
        "pid_count": float(len(pids)),
        "cpu_ticks": cpu_ticks,
        "clock_ticks": float(os.sysconf(os.sysconf_names.get("SC_CLK_TCK", "SC_CLK_TCK"))),
        "memory_bytes": memory_bytes,
        "memory_pid_count": float(memory_pid_count),
        "memory_source": memory_source,
        "io_read_bytes": io_read_bytes,
        "io_write_bytes": io_write_bytes,
        "io_pid_count": float(io_pid_count),
    }


def process_sampler(
    data_dir: str,
    interval_s: float,
    warmup_s: float,
    memory_mode: str,
    stop_event: threading.Event,
    samples: Dict[str, List[float]],
    meta: Dict[str, Any],
    errors: List[str],
) -> None:
    start_ts = time.time()
    prev_snap: Optional[Dict[str, float]] = None
    prev_ts: Optional[float] = None
    meta["resource_scope"] = "local_pg_process"
    meta["process_data_dir"] = data_dir
    meta["memory_mode_requested"] = (memory_mode or "process_pss").strip().lower()
    meta["memory_mode_effective"] = "process_pss_or_rss"
    meta["io_mode_requested"] = "process_io"
    meta["io_mode_effective"] = "process_io"
    meta["note"] = "runtime resources are sampled from the local PostgreSQL postmaster process tree"

    def collect_once() -> None:
        nonlocal prev_snap, prev_ts
        now = time.time()
        snap = _local_pg_process_snapshot(data_dir, memory_mode)
        meta["postmaster_pid"] = snap.get("root_pid")
        meta["memory_source"] = snap.get("memory_source")
        meta["memory_pg_pid_count"] = snap.get("memory_pid_count")
        meta["io_source"] = "process_io"
        meta["io_pg_pid_count"] = snap.get("io_pid_count")
        stable = (now - start_ts) >= max(0.0, warmup_s)
        if stable:
            samples["memory_bytes"].append(float(snap.get("memory_bytes", 0.0) or 0.0))
        if prev_snap is not None and prev_ts is not None:
            dt = max(1e-6, now - prev_ts)
            clock_ticks = max(1.0, float(snap.get("clock_ticks", 100.0) or 100.0))
            cpu_cores = max(0.0, snap.get("cpu_ticks", 0.0) - prev_snap.get("cpu_ticks", 0.0)) / (dt * clock_ticks)
            read_bps = max(0.0, snap.get("io_read_bytes", 0.0) - prev_snap.get("io_read_bytes", 0.0)) / dt
            write_bps = max(0.0, snap.get("io_write_bytes", 0.0) - prev_snap.get("io_write_bytes", 0.0)) / dt
            if stable:
                samples["cpu_cores"].append(cpu_cores)
                samples["io_read_bps"].append(read_bps)
                samples["io_write_bps"].append(write_bps)
                samples["io_bps"].append(read_bps + write_bps)
        prev_snap = snap
        prev_ts = now

    while not stop_event.is_set():
        try:
            collect_once()
        except Exception as exc:
            if len(errors) < 8:
                errors.append(str(exc))
        stop_event.wait(max(0.1, interval_s))
    try:
        collect_once()
    except Exception as exc:
        if len(errors) < 8:
            errors.append(str(exc))


def runtime_sample_counts(samples: Dict[str, List[float]]) -> Dict[str, int]:
    return {
        "cpu_cores": len(samples.get("cpu_cores", [])),
        "io_bps": len(samples.get("io_bps", [])),
        "io_read_bps": len(samples.get("io_read_bps", [])),
        "io_write_bps": len(samples.get("io_write_bps", [])),
        "memory_bytes": len(samples.get("memory_bytes", [])),
        "io_util_pct": len(samples.get("io_util_pct", [])),
    }


def cpuset_cpu_count(cpuset: Any) -> int:
    total = 0
    for part in str(cpuset or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            if "-" in part:
                lo_text, hi_text = part.split("-", 1)
                lo = int(lo_text)
                hi = int(hi_text)
                total += max(0, hi - lo + 1)
            else:
                int(part)
                total += 1
        except ValueError:
            return 0
    return total


def resource_monitor_error(samples: Dict[str, List[float]], min_samples: int) -> Optional[str]:
    threshold = max(1, int(min_samples))
    counts = runtime_sample_counts(samples)
    missing = [name for name in ("cpu_cores", "memory_bytes", "io_bps") if counts.get(name, 0) < threshold]
    if not missing:
        return None
    parts = ", ".join(f"{name}={counts.get(name, 0)}" for name in missing)
    return f"insufficient_runtime_samples(min={threshold}, {parts})"


def vm_stable_sample_error(stable_samples: Dict[str, Any], min_samples: int) -> Optional[str]:
    """Fail closed when a VM replay has too few stable resource samples."""
    threshold = max(1, int(min_samples))
    missing = [
        name
        for name in ("cpu", "memory", "io")
        if safe_int(stable_samples.get(name), 0) < threshold
    ]
    if not missing:
        return None
    parts = ", ".join(
        f"{name}={safe_int(stable_samples.get(name), 0)}" for name in missing
    )
    return f"insufficient_vm_stable_samples(min={threshold}, {parts})"


def percentile(values: Sequence[float], p: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(float(v) for v in values)
    idx = min(max(int(math.ceil(len(ordered) * p)) - 1, 0), len(ordered) - 1)
    return ordered[idx]


def parse_pgbench_stdout(lines: Sequence[str]) -> Dict[str, Any]:
    text = "\n".join(lines)
    result: Dict[str, Any] = {"stdout_tail": "\n".join(lines[-30:])}
    tps_matches = re.findall(r"tps\s*=\s*([0-9.]+)\s*\(", text)
    if tps_matches:
        result["pgbench_tps"] = safe_float(tps_matches[-1], 0.0)
    latency_match = re.search(r"latency average\s*=\s*([0-9.]+)\s*ms", text)
    if latency_match:
        result["latency_avg_ms"] = safe_float(latency_match.group(1), 0.0)
    return result


def parse_pgbench_latency_logs(log_prefix: Path) -> Dict[str, Any]:
    latencies_ms: List[float] = []
    for path in sorted(log_prefix.parent.glob(log_prefix.name + "*")):
        if not path.is_file():
            continue
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    text = line.strip()
                    if not text or text.startswith("#"):
                        continue
                    parts = text.split()
                    if len(parts) < 3:
                        continue
                    try:
                        latency_us = float(parts[2])
                    except ValueError:
                        continue
                    if latency_us >= 0.0 and math.isfinite(latency_us):
                        latencies_ms.append(latency_us / 1000.0)
        except OSError:
            continue
    return {
        "latency_sample_count": len(latencies_ms),
        "p95_ms": percentile(latencies_ms, 0.95),
        "p99_ms": percentile(latencies_ms, 0.99),
    }


def sanitize_label(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")[:160] or "case"


def run_streamed_process(
    cmd: Sequence[str],
    *,
    cwd: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    label: str = "",
    timeout_s: Optional[float] = None,
) -> Tuple[int, List[str], Dict[str, Any]]:
    """Run a subprocess while continuously draining stdout."""
    stdout_lines: List[str] = []
    reader_errors: List[str] = []
    meta: Dict[str, Any] = {"timed_out": False, "timeout_s": timeout_s}
    proc = subprocess.Popen(
        ignore_external_stop_cmd(cmd),
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=env,
        start_new_session=True,
    )
    stdout_closed_by_parent = False
    console_output_failed = False
    console_output_errors: List[str] = []

    def reader() -> None:
        nonlocal console_output_failed
        assert proc.stdout is not None
        try:
            for raw in proc.stdout:
                line = raw.rstrip("\n")
                stdout_lines.append(line)
                if not console_output_failed:
                    try:
                        if label:
                            print(f"[{label}] {line}", flush=True)
                        else:
                            print(line, flush=True)
                    except (ValueError, OSError) as exc:
                        console_output_failed = True
                        if len(console_output_errors) < 3:
                            console_output_errors.append(f"{type(exc).__name__}: {exc}")
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            if not (
                stdout_closed_by_parent
                and isinstance(exc, ValueError)
                and "I/O operation on closed file" in str(exc)
            ):
                reader_errors.append(message)

    reader_thread = threading.Thread(target=reader, daemon=True)
    reader_thread.start()
    try:
        ret = proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        meta["timed_out"] = True
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except Exception:
            proc.terminate()
        try:
            ret = proc.wait(timeout=20.0)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except Exception:
                proc.kill()
            ret = proc.wait()
    except BaseException as exc:
        meta["interrupted"] = f"{type(exc).__name__}: {exc}"
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except Exception:
                proc.terminate()
            try:
                proc.wait(timeout=10.0)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except Exception:
                    proc.kill()
                proc.wait()
        raise
    finally:
        reader_thread.join(timeout=5.0)
        if reader_thread.is_alive() and proc.stdout is not None:
            stdout_closed_by_parent = True
            try:
                proc.stdout.close()
            except Exception:
                pass
            reader_thread.join(timeout=5.0)
    if reader_errors:
        meta["reader_errors"] = reader_errors
    if console_output_errors:
        meta["console_output_errors"] = console_output_errors
    return ret, stdout_lines, meta


def detect_external_hybench_xp_blocker(hybench_cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Detect external guards that intentionally block HyBench XP runs."""
    if to_bool(os.environ.get("BINARY_PLAN_TUNING_HYBENCHXP_IGNORE_EXTERNAL_GUARD"), False):
        return {"blocked": False, "override": "BINARY_PLAN_TUNING_HYBENCHXP_IGNORE_EXTERNAL_GUARD"}
    if not to_bool(hybench_cfg.get("respect_external_runxp_guard"), False):
        return {"blocked": False, "override": "respect_external_runxp_guard=false"}

    blocker_paths = hybench_cfg.get("external_runxp_block_files")
    if not isinstance(blocker_paths, list) or not blocker_paths:
        blocker_paths = ["/tmp/bonect_block_external_runxp"]

    reasons: List[str] = []
    details: Dict[str, Any] = {"block_files": [], "processes": []}
    for raw_path in blocker_paths:
        path = Path(str(raw_path))
        if not path.exists():
            continue
        text = ""
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            text = ""
        details["block_files"].append({"path": str(path), "message": text})
        reasons.append(f"block_file:{path}")

    proc = subprocess.run(["ps", "-eo", "pid=,ppid=,cmd="], text=True, capture_output=True, check=False)
    self_pid = os.getpid()
    for line in (proc.stdout or "").splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        if pid == self_pid:
            continue
        cmd = parts[2]
        matched = ""
        if "bonect_external_xp_guard.py" in cmd:
            matched = "external_xp_guard"
        elif "HyBench" in cmd and "-t runap" in cmd:
            matched = "active_hybench_runap"
        elif "hybench -t runap" in cmd:
            matched = "active_hybench_runap"
        if not matched:
            continue
        details["processes"].append({"pid": pid, "kind": matched, "cmd": cmd[:500]})
        if matched not in reasons:
            reasons.append(matched)

    return {
        "blocked": bool(reasons),
        "reason": ", ".join(reasons),
        "details": details,
    }


def run_pgbench_validation(
    cfg: Dict[str, Any],
    params: Dict[str, float],
    *,
    output_dir: Path,
    label: str,
    worker: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    pg = pg_conn_from_worker(cfg, worker)
    bpt = cfg.get("binary_plan_tuning") or {}
    validation_cfg = bpt.get("validation") or {}
    docker_cfg = validation_cfg.get("docker_runtime") or {}
    pgbench_cfg = validation_cfg.get("pgbench") or {}
    container_name = str((worker or {}).get("container") or docker_cfg.get("container", "binary_plan_tuning_pg17"))
    memory_mode = str(docker_cfg.get("memory_mode", "pss"))
    interval_s = safe_float(docker_cfg.get("interval_sec"), 1.0)
    warmup_s = safe_float(docker_cfg.get("warmup_sec"), 1.0)
    io_mode = str(docker_cfg.get("io_mode", "cgroup"))
    min_stable_samples = max(1, safe_int(docker_cfg.get("min_stable_samples"), 3))

    workload_sql = resolve_path(pgbench_cfg.get("workload_sql_file", "shell/binary_plan_tuning_pgbench_workload.sql"))
    pgbench_bin = str(pgbench_cfg.get("bin", "pgbench"))
    clients = max(1, safe_int(pgbench_cfg.get("clients"), 4))
    threads = max(1, safe_int(pgbench_cfg.get("threads"), min(clients, 4)))
    duration_s = max(1, safe_int(pgbench_cfg.get("duration_s"), 20))
    extra_args = [str(item) for item in (pgbench_cfg.get("extra_args") or [])]

    effective_params = apply_pg_params(pg, params)
    reset_pg_stats(pg)
    include_statements = has_pg_stat_statements(pg)

    log_dir = output_dir / "pgbench_logs" / sanitize_label(label)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_prefix = log_dir / "pgbench"
    stdout_path = log_dir / "stdout.log"
    db_csv_path = output_dir / "db_monitor.csv"

    cmd = [
        pgbench_bin,
        "-h",
        pg.host,
        "-p",
        str(pg.port),
        "-U",
        pg.user,
        "-d",
        pg.dbname,
        "-n",
        "-c",
        str(clients),
        "-j",
        str(threads),
        "-T",
        str(duration_s),
        "-f",
        str(workload_sql),
        "-r",
        "--log",
        "--log-prefix",
        str(log_prefix),
        *extra_args,
    ]

    stop_event = threading.Event()
    db_rows: List[Dict[str, Any]] = []
    db_errors: List[str] = []
    runtime_errors: List[str] = []
    runtime_meta: Dict[str, Any] = {}
    samples = {
        "cpu_cores": [],
        "memory_bytes": [],
        "io_read_bps": [],
        "io_write_bps": [],
        "io_bps": [],
        "io_util_pct": [],
    }
    db_thread = threading.Thread(
        target=db_sampler,
        args=(pg, include_statements, 1.0, stop_event, db_rows, db_errors),
        daemon=True,
    )
    runtime_thread = threading.Thread(
        target=docker_sampler,
        args=(
            container_name,
            interval_s,
            warmup_s,
            memory_mode,
            io_mode,
            float(cpuset_cpu_count((worker or {}).get("cpuset_cpus"))),
            str((worker or {}).get("disk_root") or docker_cfg.get("disk_root") or ""),
            (worker or {}).get("disk_devices") or docker_cfg.get("disk_devices"),
            stop_event,
            samples,
            runtime_meta,
            runtime_errors,
        ),
        daemon=True,
    )
    db_thread.start()
    runtime_thread.start()

    timeout_s = safe_float(pgbench_cfg.get("timeout_s"), float(duration_s + 300))
    start_ts = time.time()
    try:
        ret, stdout_lines, process_meta = run_streamed_process(
            cmd,
            env=psql_env(pg),
            timeout_s=timeout_s,
        )
    finally:
        stop_event.set()
        db_thread.join(timeout=3.0)
        runtime_thread.join(timeout=3.0)
    end_ts = time.time()
    stdout_path.write_text("\n".join(stdout_lines) + "\n", encoding="utf-8")

    with db_csv_path.open("a", newline="", encoding="utf-8") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "case_label",
                "time",
                "elapsed_s",
                "qps_statements",
                "xact_tps",
                "block_read_ps",
                "block_hit_ps",
                "active_sessions",
            ],
        )
        if handle.tell() == 0:
            writer.writeheader()
        for row in db_rows:
            csv_row = dict(row)
            csv_row["case_label"] = label
            writer.writerow(csv_row)
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    cpu_stats = _stats(samples["cpu_cores"])
    memory_stats = _stats(samples["memory_bytes"])
    io_stats = _stats(samples["io_bps"])
    io_read_stats = _stats(samples["io_read_bps"])
    io_write_stats = _stats(samples["io_write_bps"])
    io_util_stats = _stats(samples["io_util_pct"])
    db_summary = summarize_db_rows(db_rows)
    pgbench_summary = parse_pgbench_stdout(stdout_lines)
    latency_summary = parse_pgbench_latency_logs(log_prefix)

    resources = {
        "cpu": cpu_stats["avg"],
        "memory": memory_stats["avg"] / (1024.0 ** 3),
        "io": io_stats["avg"] / (1024.0 ** 2),
        "io_read": io_read_stats["avg"] / (1024.0 ** 2),
        "io_write": io_write_stats["avg"] / (1024.0 ** 2),
    }
    primary_qps = safe_float(db_summary.get("db_qps"), 0.0)
    if primary_qps <= 0.0:
        primary_qps = safe_float(pgbench_summary.get("pgbench_tps"), 0.0)

    process_error = ""
    if process_meta.get("timed_out"):
        process_error = f"pgbench timed out after {timeout_s:.1f}s"
    elif process_meta.get("reader_errors"):
        process_error = "; ".join(str(item) for item in process_meta.get("reader_errors") or [])
    monitor_error = resource_monitor_error(samples, min_stable_samples)
    status = "ok" if ret == 0 and monitor_error is None and not process_error else "failed"
    result = {
        "event": "real_validation",
        "workload": workload_name(cfg),
        "validation_backend": "pgbench",
        "label": label,
        "status": status,
        "returncode": ret,
        "error": monitor_error if monitor_error else (process_error or (f"pgbench exited with {ret}" if ret != 0 else "")),
        "command": shlex.join(cmd),
        "process": process_meta,
        "window": [start_ts, end_ts],
        "elapsed_s": max(0.0, end_ts - start_ts),
        "params": dict(params),
        "effective_params": effective_params,
        "resources": resources,
        "resource_metric": resource_metric_meta(runtime_meta, fallback_memory_mode=memory_mode),
        "resource_stats": {
            "cpu": cpu_stats,
            "memory_bytes": memory_stats,
            "io_bytes_s": io_stats,
            "io_read_bytes_s": io_read_stats,
            "io_write_bytes_s": io_write_stats,
            "ssd_util_percent": io_util_stats,
            "resource_scope": runtime_meta.get("resource_scope"),
            "container_name": runtime_meta.get("container_name"),
            "memory_mode_requested": runtime_meta.get("memory_mode_requested"),
            "memory_mode_effective": runtime_meta.get("memory_mode_effective"),
            "io_mode_requested": runtime_meta.get("io_mode_requested"),
            "io_mode_effective": runtime_meta.get("io_mode_effective"),
            "ssd_observer_disk_devices": runtime_meta.get("ssd_observer_disk_devices"),
            "ssd_util_source": runtime_meta.get("ssd_util_source"),
            "memory_source": runtime_meta.get("memory_source"),
            "memory_pg_pid_count": runtime_meta.get("memory_pg_pid_count"),
            "io_source": runtime_meta.get("io_source"),
            "io_pg_pid_count": runtime_meta.get("io_pg_pid_count"),
            "max_cpu_cores": runtime_meta.get("max_cpu_cores"),
            "cpu_outlier_count": runtime_meta.get("cpu_outlier_count", 0),
            "cpu_outlier_max": runtime_meta.get("cpu_outlier_max", 0.0),
            "cpu_outlier_policy": runtime_meta.get("cpu_outlier_policy"),
            "cpu_outlier_clipped_count": runtime_meta.get("cpu_outlier_clipped_count", 0),
            "short_rate_interval_count": runtime_meta.get("short_rate_interval_count", 0),
            "stable_sample_count": runtime_sample_counts(samples),
            "min_stable_samples": min_stable_samples,
            "resource_monitor_ok": monitor_error is None,
        },
        "db_view_monitor": db_summary,
        "db_qps": primary_qps,
        "pgbench": pgbench_summary,
        "latency": latency_summary,
        "p95_ms": latency_summary.get("p95_ms"),
        "p99_ms": latency_summary.get("p99_ms"),
        "db_errors": db_errors,
        "runtime_errors": runtime_errors,
        "stdout_path": str(stdout_path),
        "pgbench_log_prefix": str(log_prefix),
    }
    if worker:
        result["worker"] = {
            "name": worker.get("name"),
            "container": worker.get("container"),
            "host": pg.host,
            "port": pg.port,
            "data_dir": worker.get("data_dir"),
            "disk_root": worker.get("disk_root"),
            "cpuset_cpus": worker.get("cpuset_cpus"),
        }
    return result


def replace_or_append_props_line(lines: List[str], key: str, value: Any) -> None:
    prefix = f"{key}="
    replacement = f"{key}={value}"
    for idx, line in enumerate(lines):
        if line.strip().startswith(prefix):
            lines[idx] = replacement
            return
    lines.append(replacement)


def props_line_value(lines: Sequence[str], key: str) -> Optional[str]:
    prefix = f"{key}="
    for line in lines:
        text = line.strip()
        if text.startswith(prefix):
            return text.split("=", 1)[1].strip()
    return None


def statement_override_items(value: Any, *, option_name: str) -> List[Tuple[str, str]]:
    """Parse config statement overrides as either {section: value} or SECTION=VALUE list."""
    items: List[Tuple[str, str]] = []
    if isinstance(value, dict):
        for section, raw_value in value.items():
            section_text = str(section).strip()
            if section_text and raw_value not in (None, ""):
                items.append((section_text, str(raw_value)))
        return items
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = value
    else:
        return []
    for raw_item in values:
        item = str(raw_item)
        if "=" not in item:
            raise RuntimeError(f"{option_name} must be SECTION=VALUE, got: {item}")
        section, raw_value = item.split("=", 1)
        section_text = section.strip()
        if not section_text:
            raise RuntimeError(f"{option_name} section is empty: {item}")
        items.append((section_text, raw_value))
    return items


def replace_statement_sql(toml_text: str, section: str, sql: str) -> str:
    """Replace one HyBench statement TOML section's sql field."""
    if '"""' in sql:
        raise RuntimeError(f"SQL override for {section} must not contain triple double quotes")
    section_pattern = re.compile(rf"(?m)^\[{re.escape(section)}\]\s*$")
    section_match = section_pattern.search(toml_text)
    if not section_match:
        raise RuntimeError(f"statement section not found: [{section}]")
    next_section_match = re.search(r"(?m)^\[[^\]]+\]\s*$", toml_text[section_match.end():])
    body_start = section_match.end()
    body_end = section_match.end() + next_section_match.start() if next_section_match else len(toml_text)
    body = toml_text[body_start:body_end]
    sql_pattern = re.compile(
        r"(?ms)^sql\s*=\s*(?:\"\"\".*?\"\"\"|\"(?:\\.|[^\"\\])*\"|\[.*?\])\s*",
        re.MULTILINE,
    )
    replacement = f'sql = """{sql.strip()}"""\n'
    if sql_pattern.search(body):
        new_body = sql_pattern.sub(replacement, body, count=1)
    else:
        if body and not body.endswith("\n"):
            body += "\n"
        new_body = body + replacement
    return toml_text[:body_start] + new_body + toml_text[body_end:]


def hybench_statement_file_for_config(
    hybench_cfg: Dict[str, Any],
    *,
    hybench_dir: Path,
    output_dir: Path,
    label: str,
) -> Tuple[Path, Dict[str, Any]]:
    """Return the HyBench statement file, generating one when SQL overrides are configured."""
    base_statement_file = Path(str(hybench_cfg.get("statement_file") or hybench_dir / "conf/stmt_postgres.toml"))
    sql_overrides = statement_override_items(hybench_cfg.get("statement_sql"), option_name="statement_sql")
    sql_file_overrides = statement_override_items(
        hybench_cfg.get("statement_sql_files") or hybench_cfg.get("statement_sql_file_overrides"),
        option_name="statement_sql_files",
    )
    meta = {
        "base_statement_file": str(base_statement_file),
        "statement_sql_sections": [section for section, _ in sql_overrides],
        "statement_sql_file_sections": [section for section, _ in sql_file_overrides],
        "generated": False,
    }
    if not sql_overrides and not sql_file_overrides:
        return base_statement_file, meta
    if not base_statement_file.exists():
        raise RuntimeError(f"statement file not found: {base_statement_file}")
    text = base_statement_file.read_text(encoding="utf-8")
    for section, sql in sql_overrides:
        text = replace_statement_sql(text, section, sql)
    for section, raw_path in sql_file_overrides:
        path = resolve_path(raw_path)
        if not path.exists():
            raise RuntimeError(f"statement SQL file not found for {section}: {path}")
        text = replace_statement_sql(text, section, path.read_text(encoding="utf-8"))
    profile_dir = output_dir / "generated_statement_profiles"
    profile_dir.mkdir(parents=True, exist_ok=True)
    profile_path = profile_dir / f"{sanitize_label(label)}.toml"
    profile_path.write_text(text, encoding="utf-8")
    meta["generated"] = True
    meta["statement_file"] = str(profile_path)
    return profile_path, meta


def infer_target_from_label(label: str) -> str:
    """Infer binary-search target from generated validation labels."""
    text = str(label or "").strip().lower()
    for target in ("cpu", "memory", "io", "none", "no_bottleneck", "no-bottleneck"):
        if text == target or text.startswith(f"{target}_"):
            return "none" if is_none_target(target) else target
    return ""


def hybench_xp_config_for_label(hybench_cfg: Dict[str, Any], label: str) -> Dict[str, Any]:
    """Apply optional target-specific HyBench XP overrides without changing other stages."""
    effective = copy.deepcopy(hybench_cfg)
    target = infer_target_from_label(label)
    overrides = hybench_cfg.get("target_overrides") if isinstance(hybench_cfg.get("target_overrides"), dict) else {}
    target_override = overrides.get(target) if target and isinstance(overrides.get(target), dict) else {}
    for key, value in target_override.items():
        if key != "target_overrides":
            effective[key] = copy.deepcopy(value)
    return effective


def write_hybench_props(
    cfg: Dict[str, Any],
    pg: PgConn,
    output_dir: Path,
    label: str,
) -> Path:
    validation_cfg = ((cfg.get("binary_plan_tuning") or {}).get("validation") or {})
    hybench_cfg = hybench_xp_config_for_label(validation_cfg.get("hybench_xp") or {}, label)
    base_props = Path(str(hybench_cfg.get("base_props_file") or "/home/anonymous/HyBench-2024-master/conf/pg.props"))
    if base_props.exists():
        lines = base_props.read_text(encoding="utf-8").splitlines()
    else:
        lines = []
    extra_props = hybench_cfg.get("extra_props") if isinstance(hybench_cfg.get("extra_props"), dict) else {}
    native_xp = workload_name(cfg) in {"hybench_xp", "hybenchxp"}
    if native_xp:
        expected_base_props_sha = str(hybench_cfg.get("base_props_sha256") or "")
        if not expected_base_props_sha:
            raise ValueError("native XP requires a SHA-bound base props file")
        try:
            actual_base_props_sha = hashlib.sha256(base_props.read_bytes()).hexdigest()
        except OSError as exc:
            raise ValueError(f"cannot read native XP base props: {base_props}") from exc
        if actual_base_props_sha != expected_base_props_sha:
            raise ValueError(
                "native XP base props SHA mismatch: "
                f"expected={expected_base_props_sha} actual={actual_base_props_sha}"
            )
        if extra_props:
            raise ValueError(
                "native XP extra_props are prohibited; only xtpclient/xapclient "
                "and at1_percent..at6_percent may alter workload semantics"
            )
    at_percentages: Dict[str, int] = {}
    if native_xp:
        forbidden_configured = [
            key
            for key in HYBENCH_FORBIDDEN_QUERY_SELECTION_KEYS
            if hybench_cfg.get(key) not in (None, "") or extra_props.get(key) not in (None, "")
        ]
        if forbidden_configured:
            raise ValueError(
                "native XP forbids AP/IQ query selection properties: "
                + ",".join(forbidden_configured)
            )
        at_percentages = {
            key: safe_int(
                hybench_cfg.get(key),
                safe_int(props_line_value(lines, key), 0),
            )
            for key in HYBENCH_AT_PERCENT_KEYS
        }
        if any(value < 0 for value in at_percentages.values()) or sum(at_percentages.values()) != 100:
            raise ValueError(
                "native XP at1_percent..at6_percent must be non-negative integers "
                f"summing to 100: {at_percentages}"
            )
    xtpclient = safe_int(
        hybench_cfg.get("xtpclient"),
        safe_int(props_line_value(lines, "xtpclient"), 16),
    )
    xapclient = safe_int(
        hybench_cfg.get("xapclient"),
        safe_int(props_line_value(lines, "xapclient"), 4),
    )
    if native_xp and (xtpclient <= 0 or xapclient <= 0):
        raise ValueError(
            "full native XP requires positive xtpclient and xapclient: "
            f"xtpclient={xtpclient}, xapclient={xapclient}"
        )
    jdbc_url = f"jdbc:postgresql://{pg.host}:{pg.port}/{pg.dbname}"
    replacements = {
        "sf": hybench_cfg.get("sf", "10x"),
        "db": "postgreSQL",
        "classname": "org.postgresql.Driver",
        "classname_ap": "org.postgresql.Driver",
        "username": pg.user,
        "password": pg.password,
        "username_ap": pg.user,
        "password_ap": pg.password,
        "url": jdbc_url,
        "url_tp": jdbc_url,
        "url_ap": jdbc_url,
        "xtpclient": xtpclient,
        "xapclient": xapclient,
        "xpRunMins": safe_int(hybench_cfg.get("xp_run_mins"), safe_int(props_line_value(lines, "xpRunMins"), 5)),
        "fresh_interval": safe_int(
            hybench_cfg.get("fresh_interval"),
            safe_int(props_line_value(lines, "fresh_interval"), 20),
        ),
    }
    if native_xp:
        replacements.update(at_percentages)
    optional_prop_keys = [] if native_xp else [
        "random_seed",
        "ap_query_ids",
        "ap_query_weights",
        "ap_query_sequence",
        "ap_query_sequence_limit",
        "progress_windows",
        "risk_rate",
        "power_alpha",
        "dist",
        "contention_num",
    ]
    for key in optional_prop_keys:
        if key in hybench_cfg and hybench_cfg.get(key) not in (None, ""):
            replacements[key] = hybench_cfg[key]
    for key, value in extra_props.items():
        if value not in (None, ""):
            replacements[str(key)] = value
    for key, value in replacements.items():
        replace_or_append_props_line(lines, key, value)

    props_dir_cfg = str(hybench_cfg.get("props_output_dir") or "hybench_props")
    props_dir = output_dir / props_dir_cfg
    props_dir.mkdir(parents=True, exist_ok=True)
    props_path = props_dir / f"{sanitize_label(label)}.props"
    props_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return props_path


def hybench_run_config_from_props(
    props_path: Path,
    hybench_cfg: Dict[str, Any],
    *,
    hybench_dir: Path,
    launcher: str,
    statement_file: Path,
    restore_prepared: bool = False,
) -> Dict[str, Any]:
    """Return the actual HyBench XP properties that make a validation auditable."""
    try:
        lines = props_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    xp_run_mins = safe_int(props_line_value(lines, "xpRunMins"), safe_int(hybench_cfg.get("xp_run_mins"), 0))
    run_config = {
        "workload": "hybenchxp",
        "mode": "runxp",
        "sf": props_line_value(lines, "sf") or hybench_cfg.get("sf"),
        "xtpclient": safe_int(props_line_value(lines, "xtpclient"), safe_int(hybench_cfg.get("xtpclient"), 0)),
        "xapclient": safe_int(props_line_value(lines, "xapclient"), safe_int(hybench_cfg.get("xapclient"), 0)),
        "xpRunMins": xp_run_mins,
        "xp_run_mins": xp_run_mins,
        "fresh_interval": safe_int(
            props_line_value(lines, "fresh_interval"),
            safe_int(hybench_cfg.get("fresh_interval"), 0),
        ),
        "random_seed": props_line_value(lines, "random_seed") or hybench_cfg.get("random_seed"),
        "ap_query_ids": props_line_value(lines, "ap_query_ids"),
        "ap_query_weights": props_line_value(lines, "ap_query_weights"),
        "ap_query_sequence": props_line_value(lines, "ap_query_sequence"),
        "ap_query_sequence_limit": props_line_value(lines, "ap_query_sequence_limit"),
        "progress_windows": props_line_value(lines, "progress_windows"),
        "hybench_dir": str(hybench_dir),
        "launcher": launcher,
        "statement_file": str(statement_file),
        "props_path": str(props_path),
        "restore_from_template_before_validation": to_bool(
            hybench_cfg.get("restore_from_template_before_validation"),
            True,
        ) and not restore_prepared,
        "restore_prepared": restore_prepared,
        "template_db": hybench_cfg.get("template_db"),
        "setup_sql_files": list(hybench_cfg.get("setup_sql_files") or []),
        "setup_sql_count": len(hybench_setup_sql_items(hybench_cfg)),
        "post_evict_warmup_sql_files": list(hybench_cfg.get("post_evict_warmup_sql_files") or []),
        "post_evict_warmup_sql_count": len(hybench_post_evict_warmup_sql_items(hybench_cfg)),
    }
    run_config["at_percentages"] = {
        key: safe_int(props_line_value(lines, key), safe_int(hybench_cfg.get(key), 0))
        for key in HYBENCH_AT_PERCENT_KEYS
    }
    run_config["allowed_variable_workload_fields"] = [
        "xtpclient",
        "xapclient",
        *HYBENCH_AT_PERCENT_KEYS,
    ]
    run_config["forbidden_query_selection_fields_absent"] = all(
        not run_config.get(key) for key in HYBENCH_FORBIDDEN_QUERY_SELECTION_KEYS
    )
    try:
        run_config["statement_file_sha256"] = hashlib.sha256(statement_file.read_bytes()).hexdigest()
    except OSError:
        run_config["statement_file_sha256"] = ""
    native_jar = Path(str(hybench_cfg.get("native_jar_file") or ""))
    run_config["native_jar_file"] = str(native_jar) if str(native_jar) != "." else ""
    run_config["expected_native_jar_sha256"] = str(
        hybench_cfg.get("native_jar_sha256") or ""
    )
    try:
        run_config["native_jar_sha256"] = hashlib.sha256(native_jar.read_bytes()).hexdigest()
    except OSError:
        run_config["native_jar_sha256"] = ""
    return run_config


def hybench_config_sql_items(
    hybench_cfg: Dict[str, Any],
    *,
    sql_key: str,
    files_key: str,
) -> List[Tuple[str, str]]:
    """Return inline and file-backed SQL snippets for one HyBench lifecycle phase."""
    items: List[Tuple[str, str]] = []
    raw_sql = hybench_cfg.get(sql_key)
    if isinstance(raw_sql, str) and raw_sql.strip():
        items.append(("inline", raw_sql))
    elif isinstance(raw_sql, list):
        for index, sql in enumerate(raw_sql):
            if isinstance(sql, str) and sql.strip():
                items.append((f"inline[{index}]", sql))

    raw_files = hybench_cfg.get(files_key)
    if isinstance(raw_files, str):
        file_values = [raw_files]
    elif isinstance(raw_files, list):
        file_values = raw_files
    else:
        file_values = []
    for raw_path in file_values:
        path = resolve_path(str(raw_path))
        sql = path.read_text(encoding="utf-8")
        if sql.strip():
            items.append((str(path), sql))
    return items


def hybench_setup_sql_items(hybench_cfg: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Return setup SQL snippets to execute after restore and before cache eviction."""
    return hybench_config_sql_items(
        hybench_cfg,
        sql_key="setup_sql",
        files_key="setup_sql_files",
    )


def hybench_post_evict_warmup_sql_items(hybench_cfg: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Return warmup SQL snippets to execute after cache eviction and before monitoring."""
    return hybench_config_sql_items(
        hybench_cfg,
        sql_key="post_evict_warmup_sql",
        files_key="post_evict_warmup_sql_files",
    )


def evict_file_cache_under_path(
    path: str,
    *,
    max_files: int = 0,
    max_bytes: int = 0,
) -> Dict[str, Any]:
    """Drop clean page-cache entries for files under one worker data directory."""
    root = Path(path).resolve() if path else Path()
    result: Dict[str, Any] = {
        "path": str(root) if path else "",
        "ok": False,
        "enabled": bool(path),
        "method": "posix_fadvise_DONTNEED",
        "files": 0,
        "bytes": 0,
        "errors": [],
        "skipped": "",
    }
    if not path:
        result["skipped"] = "data_dir_empty"
        return result
    if not root.exists():
        result["skipped"] = "data_dir_missing"
        return result
    if not hasattr(os, "posix_fadvise") or not hasattr(os, "POSIX_FADV_DONTNEED"):
        result["skipped"] = "posix_fadvise_unavailable"
        return result

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    max_files = max(0, int(max_files))
    max_bytes = max(0, int(max_bytes))
    error_items: List[str] = []

    def record_walk_error(exc: OSError) -> None:
        """Keep directory traversal failures visible to the fail-closed gate."""
        if len(error_items) < 20:
            error_items.append(str(exc))

    file_count = 0
    byte_count = 0
    for dirpath, dirnames, filenames in os.walk(
        root,
        followlinks=False,
        onerror=record_walk_error,
    ):
        dirnames[:] = [name for name in dirnames if name not in {"pg_wal_archive"}]
        for filename in filenames:
            if max_files and file_count >= max_files:
                break
            file_path = Path(dirpath) / filename
            try:
                stat = file_path.stat()
                if not file_path.is_file():
                    continue
                if max_bytes and byte_count >= max_bytes:
                    break
                fd = os.open(str(file_path), flags)
                try:
                    os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
                finally:
                    os.close(fd)
                file_count += 1
                byte_count += max(0, int(stat.st_size))
            except OSError as exc:
                if len(error_items) < 20:
                    error_items.append(f"{file_path}: {exc}")
        if max_files and file_count >= max_files:
            break
        if max_bytes and byte_count >= max_bytes:
            break
    result.update(
        {
            "ok": file_count > 0,
            "files": file_count,
            "bytes": byte_count,
            "errors": error_items,
        }
    )
    if file_count <= 0:
        result["skipped"] = "no_files_evicted"
    return result


def evict_file_cache_under_path_sudo(
    path: str,
    *,
    max_files: int = 0,
    max_bytes: int = 0,
    timeout_s: float = 600.0,
) -> Dict[str, Any]:
    """Run the local fadvise helper via sudo for root-owned PostgreSQL data dirs."""
    root = Path(path).resolve() if path else Path()
    result: Dict[str, Any] = {
        "path": str(root) if path else "",
        "ok": False,
        "enabled": bool(path),
        "method": "host_sudo_posix_fadvise_DONTNEED",
        "files": 0,
        "bytes": 0,
        "errors": [],
        "skipped": "",
    }
    if not path:
        result["skipped"] = "data_dir_empty"
        return result
    if not root.exists():
        result["skipped"] = "data_dir_missing"
        return result
    helper, helper_error = ensure_fadvise_helper()
    if helper is None:
        result["skipped"] = helper_error
        return result
    try:
        proc = subprocess.run(
            [
                "sudo",
                "-n",
                str(helper),
                str(root),
                str(max(0, int(max_files))),
                str(max(0, int(max_bytes))),
            ],
            text=True,
            capture_output=True,
            timeout=max(1.0, safe_float(timeout_s, 600.0)),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        result["skipped"] = "sudo_helper_timeout"
        result["errors"].append(str(exc))
        return result
    except OSError as exc:
        result["skipped"] = "sudo_helper_exec_failed"
        result["errors"].append(str(exc))
        return result

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    if stdout:
        try:
            helper_result = json.loads(stdout.splitlines()[-1])
            result["files"] = int(helper_result.get("files") or 0)
            result["bytes"] = int(helper_result.get("bytes") or 0)
            result["helper_rc"] = helper_result.get("rc")
            helper_errors = int(helper_result.get("errors") or 0)
            if helper_errors:
                result["errors"].append(f"helper_errors={helper_errors}")
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            result["errors"].append(f"helper_json_parse_failed:{exc}:{stdout[-500:]}")
    if stderr:
        result["errors"].append(stderr[-1000:])
    result["ok"] = proc.returncode == 0 and result["files"] > 0
    if proc.returncode != 0:
        result["errors"].append(f"sudo_helper_rc={proc.returncode}")
    if result["files"] <= 0 and not result["skipped"]:
        result["skipped"] = "sudo_helper_no_files_evicted"
    return result


def _fadvise_helper_paths() -> Tuple[Path, Path]:
    return ROOT / "tools" / "bonect_fadvise_dontneed.c", Path("/tmp/bonect_fadvise_dontneed")


def ensure_fadvise_helper() -> Tuple[Optional[Path], str]:
    src, binary = _fadvise_helper_paths()
    if not src.exists():
        return None, f"helper_source_missing:{src}"
    try:
        needs_build = not binary.exists() or binary.stat().st_mtime < src.stat().st_mtime
    except OSError:
        needs_build = True
    if not needs_build:
        return binary, ""
    proc = subprocess.run(
        ["gcc", "-O2", "-Wall", "-Wextra", "-o", str(binary), str(src)],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return None, f"helper_compile_failed:{proc.stderr.strip() or proc.stdout.strip()}"
    return binary, ""


def evict_file_cache_in_container(
    container: str,
    *,
    container_path: str = "/var/lib/postgresql/data",
    max_files: int = 0,
    max_bytes: int = 0,
    container_user: str = "0",
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "path": container_path,
        "ok": False,
        "enabled": bool(container),
        "method": (
            "container_postgres_posix_fadvise_DONTNEED"
            if container_user == "postgres"
            else "container_posix_fadvise_DONTNEED"
        ),
        "container": container,
        "container_user": container_user,
        "files": 0,
        "bytes": 0,
        "errors": [],
        "skipped": "",
    }
    if not container:
        result["skipped"] = "container_empty"
        return result
    helper, helper_error = ensure_fadvise_helper()
    if helper is None:
        result["skipped"] = helper_error
        return result

    container_helper = "/tmp/bonect_fadvise_dontneed"
    if container not in _FADVISE_HELPER_INSTALLED:
        with _FADVISE_HELPER_INSTALL_LOCK:
            if container not in _FADVISE_HELPER_INSTALLED:
                check_proc = run_docker_cmd(
                    [
                        "docker",
                        "exec",
                        "-u",
                        "0",
                        container,
                        "test",
                        "-x",
                        container_helper,
                    ],
                    timeout_s=safe_float(
                        os.environ.get("BINARY_PLAN_TUNING_HYBENCHXP_DOCKER_HELPER_CHECK_TIMEOUT_S"),
                        # Four PostgreSQL lanes can concurrently walk tens of
                        # GiB of data during cache eviction.  The Docker daemon
                        # may therefore take longer than one minute to answer
                        # even this read-only existence probe.  Keep the probe
                        # bounded, but do not turn transient daemon latency into
                        # a false workload technical failure.
                        300.0,
                    ),
                    retries=5,
                    serialize=True,
                )
                if check_proc.returncode != 0:
                    copy_proc = run_docker_cmd(
                        ["docker", "cp", str(helper), f"{container}:{container_helper}"],
                        timeout_s=120,
                        retries=5,
                        serialize=True,
                    )
                    if copy_proc.returncode != 0:
                        result["errors"].append(subprocess_detail(copy_proc))
                        result["skipped"] = "docker_cp_failed"
                        return result
                _FADVISE_HELPER_INSTALLED.add(container)

    exec_command = [
        "docker",
        "exec",
        "-u",
        container_user,
        container,
        container_helper,
        container_path,
        str(max(0, int(max_files))),
        str(max(0, int(max_bytes))),
    ]
    semantic_attempts: List[Dict[str, Any]] = []
    for attempt in range(1, 4):
        exec_proc = run_docker_cmd(
            exec_command,
            timeout_s=600,
            retries=2,
            serialize=False,
        )
        stdout = (exec_proc.stdout or "").strip()
        stderr = (exec_proc.stderr or "").strip()
        attempt_files = 0
        attempt_bytes = 0
        attempt_helper_rc: Any = None
        attempt_errors: List[str] = []
        if stdout:
            try:
                helper_result = json.loads(stdout.splitlines()[-1])
                attempt_files = int(helper_result.get("files") or 0)
                attempt_bytes = int(helper_result.get("bytes") or 0)
                attempt_helper_rc = helper_result.get("rc")
                helper_errors = int(helper_result.get("errors") or 0)
                if helper_errors:
                    attempt_errors.append(f"helper_errors={helper_errors}")
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                attempt_errors.append(
                    f"helper_json_parse_failed:{exc}:{stdout[-500:]}"
                )
        else:
            attempt_errors.append("helper_stdout_empty")
        if stderr:
            attempt_errors.append(stderr[-1000:])
        if exec_proc.returncode != 0:
            attempt_errors.append(f"docker_exec_rc={exec_proc.returncode}")
        semantic_attempts.append(
            {
                "attempt": attempt,
                "returncode": exec_proc.returncode,
                "files": attempt_files,
                "bytes": attempt_bytes,
                "errors": attempt_errors,
            }
        )
        if exec_proc.returncode == 0 and attempt_files > 0:
            result["files"] = attempt_files
            result["bytes"] = attempt_bytes
            result["helper_rc"] = attempt_helper_rc
            result["ok"] = True
            break
        if attempt < 3:
            time.sleep(float(attempt))
    if len(semantic_attempts) > 1:
        result["semantic_retry_attempts"] = semantic_attempts
    if result["ok"] is not True:
        for item in semantic_attempts:
            result["errors"].extend(str(value) for value in item["errors"])
    if result["files"] <= 0 and not result["skipped"]:
        result["skipped"] = "container_no_files_evicted"
    return result


def _run_hybench_xp_validation_uncoordinated(
    cfg: Dict[str, Any],
    params: Dict[str, float],
    *,
    output_dir: Path,
    label: str,
    worker: Optional[Dict[str, Any]] = None,
    environment_overrides: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    pg = pg_conn_from_worker(cfg, worker)
    bpt = cfg.get("binary_plan_tuning") or {}
    validation_cfg = bpt.get("validation") or {}
    executor_telemetry_cfg = (
        validation_cfg.get("executor_telemetry")
        if isinstance(validation_cfg.get("executor_telemetry"), dict)
        else {}
    )
    executor_telemetry_required = to_bool(
        executor_telemetry_cfg.get("required"), False
    )
    docker_cfg = validation_cfg.get("docker_runtime") or {}
    process_cfg = validation_cfg.get("process_runtime") or {}
    hybench_cfg = hybench_xp_config_for_label(validation_cfg.get("hybench_xp") or {}, label)
    use_process_runtime = to_bool(process_cfg.get("enabled"), False)
    if worker and worker.get("container"):
        use_process_runtime = False
    container_name = str((worker or {}).get("container") or docker_cfg.get("container", "binary_plan_tuning_pg17"))
    process_data_dir = str((worker or {}).get("data_dir") or process_cfg.get("data_dir") or "")
    runtime_cfg = process_cfg if use_process_runtime else docker_cfg
    memory_mode = str(runtime_cfg.get("memory_mode", "process_pss" if use_process_runtime else "pss"))
    interval_s = safe_float(runtime_cfg.get("interval_sec"), 1.0)
    warmup_s = safe_float(runtime_cfg.get("warmup_sec"), 1.0)
    io_mode = str(runtime_cfg.get("io_mode", "process_io" if use_process_runtime else "cgroup"))
    min_stable_samples = max(1, safe_int(runtime_cfg.get("min_stable_samples"), 3))
    physical_pg_proc_io = (not use_process_runtime) and is_container_pg_proc_io_mode(io_mode)
    physical_cgroup_io = (not use_process_runtime) and is_container_cgroup_io_mode(io_mode)
    checkpoint_before_validation = to_bool(
        runtime_cfg.get("checkpoint_before_validation"),
        physical_pg_proc_io or physical_cgroup_io,
    )
    checkpoint_after_validation = to_bool(
        runtime_cfg.get("checkpoint_after_validation"),
        physical_pg_proc_io or physical_cgroup_io,
    )
    evict_data_file_cache_before_validation = to_bool(
        runtime_cfg.get("evict_data_file_cache_before_validation"),
        physical_pg_proc_io or physical_cgroup_io,
    )
    evict_data_file_cache_host_sudo = to_bool(runtime_cfg.get("evict_data_file_cache_host_sudo"), False)
    evict_data_file_cache_container_fallback = to_bool(
        runtime_cfg.get("evict_data_file_cache_container_fallback"),
        True,
    )
    evict_data_file_cache_postgres_program_fallback = to_bool(
        runtime_cfg.get("evict_data_file_cache_postgres_program_fallback"),
        False,
    )
    evict_data_file_cache_require_success = to_bool(
        runtime_cfg.get("evict_data_file_cache_require_success"),
        False,
    )
    evict_data_file_cache_max_files = max(0, safe_int(runtime_cfg.get("evict_data_file_cache_max_files"), 0))
    evict_data_file_cache_max_bytes = max(0, safe_int(runtime_cfg.get("evict_data_file_cache_max_bytes"), 0))
    checkpoint_settle_s = max(0.0, safe_float(runtime_cfg.get("checkpoint_settle_sec"), 2.0))
    setup_sql_timeout_s = max(1.0, safe_float(hybench_cfg.get("setup_sql_timeout_s"), 300.0))
    setup_sql_items = hybench_setup_sql_items(hybench_cfg)
    post_evict_warmup_sql_items = hybench_post_evict_warmup_sql_items(hybench_cfg)
    restore_prepared = bool((worker or {}).get("_restore_prepared"))

    hybench_dir = Path(str(hybench_cfg.get("hybench_dir") or "/home/anonymous/HyBench-2024-master"))
    launcher = str(hybench_cfg.get("launcher") or "hybench")
    statement_file, statement_meta = hybench_statement_file_for_config(
        hybench_cfg,
        hybench_dir=hybench_dir,
        output_dir=output_dir,
        label=label,
    )
    log_dir = output_dir / "hybench_logs" / sanitize_label(label)
    log_dir.mkdir(parents=True, exist_ok=True)
    progress_path = log_dir / "progress.jsonl"

    def log_progress(event: str, **extra: Any) -> None:
        payload = {
            "time": time.time(),
            "event": event,
            "workload": workload_name(cfg),
            "label": label,
            "worker": (worker or {}).get("name"),
            "container": container_name,
            "host": pg.host,
            "port": pg.port,
            "dbname": pg.dbname,
        }
        payload.update(extra)
        try:
            with progress_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        except Exception:
            pass

    log_progress(
        "validation_start",
        hybench_dir=str(hybench_dir),
        launcher=launcher,
        statement_file=str(statement_file),
        statement_meta=statement_meta,
    )
    props_path = write_hybench_props(cfg, pg, output_dir, label)
    hybench_run_config = hybench_run_config_from_props(
        props_path,
        hybench_cfg,
        hybench_dir=hybench_dir,
        launcher=launcher,
        statement_file=statement_file,
        restore_prepared=restore_prepared,
    )
    hybench_run_config["statement_meta"] = statement_meta
    hybench_run_config["expected_statement_file_sha256"] = str(
        hybench_cfg.get("statement_file_sha256") or ""
    )
    log_progress("props_written", props_path=str(props_path))
    cmd = ["bash", launcher, "-t", "runxp", "-c", str(props_path), "-f", str(statement_file)]

    blocker = detect_external_hybench_xp_blocker(hybench_cfg)
    if blocker.get("blocked"):
        error = "external_hybench_xp_blocked: " + str(blocker.get("reason") or "unknown")
        log_progress("external_guard_blocked", error=error, blocker=blocker, command=shlex.join(cmd))
        zero_stats = _stats([])
        zero_sample_counts = {
            "cpu_cores": 0,
            "io_bps": 0,
            "io_read_bps": 0,
            "io_write_bps": 0,
            "memory_bytes": 0,
        }
        result = {
            "event": "real_validation",
            "workload": workload_name(cfg),
            "validation_backend": "hybench_xp",
            "hybench_run_config": hybench_run_config,
            "label": label,
            "status": "failed",
            "returncode": None,
            "error": error,
            "process_error": error,
            "monitor_error": "",
            "command": shlex.join(cmd),
            "cwd": str(hybench_dir),
            "process": {"preflight_blocked": True, "blocker": blocker},
            "window": [time.time(), time.time()],
            "elapsed_s": 0.0,
            "params": dict(params),
            "effective_params": {},
            "resources": {"cpu": 0.0, "memory": 0.0, "io": 0.0, "io_read": 0.0, "io_write": 0.0},
            "resource_metric": resource_metric_meta(
                {
                    "memory_mode_requested": memory_mode,
                    "memory_mode_effective": (
                        "process_pss_or_rss" if use_process_runtime
                        else f"cgroup_{normalize_docker_memory_mode(memory_mode)}"
                    ),
                    "memory_source": "",
                },
                fallback_memory_mode=memory_mode,
            ),
            "resource_stats": {
                "cpu": zero_stats,
                "memory_bytes": zero_stats,
                "io_bytes_s": zero_stats,
                "io_read_bytes_s": zero_stats,
                "io_write_bytes_s": zero_stats,
                "resource_scope": "local_pg_process" if use_process_runtime else "docker_container",
                "container_name": container_name,
                "process_data_dir": process_data_dir if use_process_runtime else "",
                "memory_mode_requested": memory_mode,
                "memory_mode_effective": (
                    "process_pss_or_rss" if use_process_runtime
                    else f"cgroup_{normalize_docker_memory_mode(memory_mode)}"
                ),
                "io_mode_requested": io_mode,
                "io_mode_effective": "process_io" if use_process_runtime else "docker_cgroup",
                "memory_source": "",
                "memory_pg_pid_count": 0,
                "io_source": "",
                "stable_sample_count": zero_sample_counts,
                "min_stable_samples": min_stable_samples,
                "resource_monitor_ok": False,
            },
            "resource_samples": zero_sample_counts,
            "db_view_monitor": {"sample_count": 0},
            "db_qps": 0.0,
            "hybench": {
                "benchmark": {},
                "benchmark_latency": {},
                "xp_qps": 0.0,
                "xp_tps": 0.0,
                "run_config": hybench_run_config,
            },
            "p95_ms": 0.0,
            "p99_ms": 0.0,
            "db_errors": [],
            "runtime_errors": [],
            "stdout_path": "",
            "hybench_props": str(props_path),
        }
        if worker:
            result["worker"] = {
                "name": worker.get("name"),
                "container": worker.get("container"),
                "host": pg.host,
                "port": pg.port,
                "data_dir": worker.get("data_dir"),
                "disk_root": worker.get("disk_root"),
                "cpuset_cpus": worker.get("cpuset_cpus"),
            }
        return result

    if to_bool(hybench_cfg.get("restore_from_template_before_validation"), True) and not restore_prepared:
        template_db = str(hybench_cfg.get("template_db") or f"{pg.dbname}_base")
        log_progress("restore_start", template_db=template_db)
        restore_database_from_template(pg, template_db)
        log_progress("restore_done", template_db=template_db)
    elif restore_prepared:
        log_progress("restore_skipped_prepared_worker")
    for index, (source, sql) in enumerate(setup_sql_items):
        log_progress("setup_sql_start", index=index, source=source, timeout_s=setup_sql_timeout_s)
        psql_query(pg, sql, timeout_s=setup_sql_timeout_s)
        log_progress("setup_sql_done", index=index, source=source)
    log_progress("apply_params_start")
    effective_params = apply_pg_params(pg, params)
    log_progress("apply_params_done", effective_params=effective_params)
    if checkpoint_before_validation:
        log_progress("checkpoint_before_validation_start", settle_s=checkpoint_settle_s)
        checkpoint_postgres(pg, settle_s=checkpoint_settle_s)
        log_progress("checkpoint_before_validation_done")
    cache_evict_meta: Dict[str, Any] = {"enabled": False}
    if evict_data_file_cache_before_validation:
        evict_path = str((worker or {}).get("data_dir") or process_data_dir or "")
        log_progress(
            "evict_data_file_cache_start",
            path=evict_path,
            max_files=evict_data_file_cache_max_files,
            max_bytes=evict_data_file_cache_max_bytes,
        )
        cache_evict_meta = evict_file_cache_under_path(
            evict_path,
            max_files=evict_data_file_cache_max_files,
            max_bytes=evict_data_file_cache_max_bytes,
        )
        if cache_evict_meta.get("files", 0) <= 0 and evict_data_file_cache_host_sudo:
            sudo_evict_meta = evict_file_cache_under_path_sudo(
                evict_path,
                max_files=evict_data_file_cache_max_files,
                max_bytes=evict_data_file_cache_max_bytes,
            )
            cache_evict_meta = {
                **sudo_evict_meta,
                "host_attempt": cache_evict_meta,
            }
        worker_container = str((worker or {}).get("container") or "")
        if (
            cache_evict_meta.get("files", 0) <= 0
            and worker_container
            and evict_data_file_cache_postgres_program_fallback
        ):
            container_evict_meta = evict_file_cache_in_container(
                worker_container,
                container_path=str(
                    (worker or {}).get("container_data_dir")
                    or "/var/lib/postgresql/data"
                ),
                max_files=evict_data_file_cache_max_files,
                max_bytes=evict_data_file_cache_max_bytes,
                container_user="postgres",
            )
            cache_evict_meta = {
                **container_evict_meta,
                "host_attempt": cache_evict_meta,
            }
        if (
            cache_evict_meta.get("files", 0) <= 0
            and worker_container
            and evict_data_file_cache_container_fallback
        ):
            container_evict_meta = evict_file_cache_in_container(
                worker_container,
                container_path=str(
                    (worker or {}).get("container_data_dir")
                    or "/var/lib/postgresql/data"
                ),
                max_files=evict_data_file_cache_max_files,
                max_bytes=evict_data_file_cache_max_bytes,
            )
            cache_evict_meta = {
                **container_evict_meta,
                "host_attempt": cache_evict_meta,
            }
        log_progress("evict_data_file_cache_done", **cache_evict_meta)
        if evict_data_file_cache_require_success and (
            cache_evict_meta.get("ok") is not True
            or int(cache_evict_meta.get("files") or 0) <= 0
        ):
            raise RuntimeError(
                "required data-file cache eviction failed: "
                + json.dumps(cache_evict_meta, sort_keys=True)
            )
    for index, (source, sql) in enumerate(post_evict_warmup_sql_items):
        log_progress("post_evict_warmup_sql_start", index=index, source=source, timeout_s=setup_sql_timeout_s)
        psql_query(pg, sql, timeout_s=setup_sql_timeout_s)
        log_progress("post_evict_warmup_sql_done", index=index, source=source)
    log_progress("reset_stats_start")
    reset_pg_stats(pg)
    log_progress("reset_stats_done")
    include_statements = has_pg_stat_statements(pg)
    log_progress("pg_stat_statements_checked", include_statements=include_statements)

    stdout_path = log_dir / "stdout.log"
    db_csv_path = output_dir / "db_monitor.csv"

    db_stop_event = threading.Event()
    runtime_stop_event = threading.Event()
    db_rows: List[Dict[str, Any]] = []
    db_errors: List[str] = []
    runtime_errors: List[str] = []
    runtime_meta: Dict[str, Any] = {}
    samples = {
        "cpu_cores": [],
        "memory_bytes": [],
        "io_read_bps": [],
        "io_write_bps": [],
        "io_bps": [],
        "io_util_pct": [],
    }
    db_thread = threading.Thread(
        target=db_sampler,
        args=(pg, include_statements, 1.0, db_stop_event, db_rows, db_errors),
        daemon=True,
    )
    runtime_thread = threading.Thread(
        target=process_sampler if use_process_runtime else docker_sampler,
        args=(
            (
                process_data_dir,
                interval_s,
                warmup_s,
                memory_mode,
                runtime_stop_event,
                samples,
                runtime_meta,
                runtime_errors,
            )
            if use_process_runtime
            else (
                container_name,
                interval_s,
                warmup_s,
                memory_mode,
                io_mode,
                float(cpuset_cpu_count((worker or {}).get("cpuset_cpus"))),
                str((worker or {}).get("disk_root") or docker_cfg.get("disk_root") or ""),
                (worker or {}).get("disk_devices") or docker_cfg.get("disk_devices"),
                runtime_stop_event,
                samples,
                runtime_meta,
                runtime_errors,
            )
        ),
        daemon=True,
    )
    runtime_meta["checkpoint_before_validation"] = checkpoint_before_validation
    runtime_meta["checkpoint_after_validation"] = checkpoint_after_validation
    runtime_meta["checkpoint_settle_sec"] = checkpoint_settle_s
    runtime_meta["evict_data_file_cache_before_validation"] = evict_data_file_cache_before_validation
    runtime_meta["evict_data_file_cache_host_sudo"] = evict_data_file_cache_host_sudo
    runtime_meta["evict_data_file_cache_container_fallback"] = evict_data_file_cache_container_fallback
    runtime_meta["evict_data_file_cache_postgres_program_fallback"] = (
        evict_data_file_cache_postgres_program_fallback
    )
    runtime_meta["evict_data_file_cache_require_success"] = (
        evict_data_file_cache_require_success
    )
    runtime_meta["evict_data_file_cache"] = cache_evict_meta
    db_thread.start()
    runtime_thread.start()
    log_progress("samplers_started", include_statements=include_statements)

    xp_run_mins = max(1, safe_int(hybench_cfg.get("xp_run_mins"), 5))
    timeout_grace_s = max(60, safe_int(hybench_cfg.get("timeout_grace_s"), 600))
    timeout_s = safe_float(hybench_cfg.get("timeout_s"), float(xp_run_mins * 60 + timeout_grace_s))
    start_ts = time.time()
    log_progress("hybench_start", command=shlex.join(cmd), timeout_s=timeout_s, xp_run_mins=xp_run_mins)
    hybench_env = psql_env(pg, environment_overrides)
    # HyBench launcher uses $PWD in its Java classpath. Preserve a symlink cwd
    # when callers route the runtime through /tmp to avoid external process guards.
    hybench_env["PWD"] = str(hybench_dir)
    try:
        ret, stdout_lines, process_meta = run_streamed_process(
            cmd,
            cwd=str(hybench_dir),
            env=hybench_env,
            label=label,
            timeout_s=timeout_s,
        )
    finally:
        db_stop_event.set()
        db_thread.join(timeout=3.0)
        if checkpoint_after_validation:
            try:
                log_progress("checkpoint_after_validation_start", settle_s=checkpoint_settle_s)
                checkpoint_postgres(pg, settle_s=checkpoint_settle_s)
                log_progress("checkpoint_after_validation_done")
            except Exception as exc:
                runtime_errors.append(f"checkpoint_after_validation_failed: {exc}")
                log_progress("checkpoint_after_validation_failed", error=str(exc))
        runtime_stop_event.set()
        runtime_thread.join(timeout=70.0 if physical_pg_proc_io else 3.0)
    end_ts = time.time()
    log_progress("hybench_done", returncode=ret, elapsed_s=max(0.0, end_ts - start_ts), process=process_meta)
    stdout_path.write_text("\n".join(stdout_lines) + "\n", encoding="utf-8")
    plan_estimator_cfg = ((cfg.get("metrics") or {}).get("plan_estimator") or {})
    estimator_shared_cfg = plan_estimator_cfg.get("shared") or {}
    executor_telemetry = (
        fetch_pg_stat_statements_executor_telemetry(
            pg,
            block_size=max(1, safe_int(estimator_shared_cfg.get("block_size"), 8192)),
        )
        if include_statements
        else {
            "available": False,
            "source": "pg_stat_statements_post_reset_aggregate_v1",
            "error": "pg_stat_statements_unavailable",
        }
    )
    executor_telemetry["required"] = executor_telemetry_required
    log_progress(
        "executor_telemetry_collected",
        available=executor_telemetry.get("available"),
        required=executor_telemetry_required,
        source=executor_telemetry.get("source"),
        error=executor_telemetry.get("error"),
    )

    with db_csv_path.open("a", newline="", encoding="utf-8") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "case_label",
                "time",
                "elapsed_s",
                "qps_statements",
                "xact_tps",
                "block_read_ps",
                "block_hit_ps",
                "active_sessions",
            ],
        )
        if handle.tell() == 0:
            writer.writeheader()
        for row in db_rows:
            csv_row = dict(row)
            csv_row["case_label"] = label
            writer.writerow(csv_row)
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    cpu_stats = _stats(samples["cpu_cores"])
    memory_stats = _stats(samples["memory_bytes"])
    io_stats = _stats(samples["io_bps"])
    io_read_stats = _stats(samples["io_read_bps"])
    io_write_stats = _stats(samples["io_write_bps"])
    io_util_stats = _stats(samples["io_util_pct"])
    sample_counts = runtime_sample_counts(samples)
    db_summary = summarize_db_rows(db_rows)
    hybench_summary = parse_hybench_benchmark_output(stdout_lines)
    workload_technical_failures = detect_hybench_technical_failures(stdout_lines)
    benchmark = hybench_summary.get("benchmark") or {}
    benchmark_latency = hybench_summary.get("benchmark_latency") or {}
    completeness_audit = workload_completeness_audit(
        cfg,
        benchmark,
        stdout_lines,
        hybench_run_config,
    )
    reader_errors = [str(item) for item in (process_meta.get("reader_errors") or [])]
    if ret == 0 and reader_errors:
        closed_file_reader_only = all("closed file" in item.lower() for item in reader_errors)
        summary_seen = bool(benchmark) or any(
            "Test Summary" in line or "XP Workload is done" in line or "Congs~ Test is done" in line
            for line in stdout_lines[-100:]
        )
        if closed_file_reader_only and summary_seen:
            process_meta["ignored_reader_errors"] = reader_errors
            process_meta.pop("reader_errors", None)

    resources = {
        "cpu": cpu_stats["avg"],
        "memory": memory_stats["avg"] / (1024.0 ** 3),
        "io": io_stats["avg"] / (1024.0 ** 2),
        "io_read": io_read_stats["avg"] / (1024.0 ** 2),
        "io_write": io_write_stats["avg"] / (1024.0 ** 2),
    }
    primary_qps = safe_float(db_summary.get("db_qps"), 0.0)
    workload_qps = safe_float(benchmark.get("xp_qps"), 0.0)
    if primary_qps <= 0.0:
        primary_qps = workload_qps

    process_error = ""
    if process_meta.get("timed_out"):
        process_error = f"HyBench XP timed out after {timeout_s:.1f}s"
    elif process_meta.get("reader_errors"):
        process_error = "; ".join(str(item) for item in process_meta.get("reader_errors") or [])
    if ret != 0:
        exit_error = f"HyBench XP exited with {ret}"
        process_error = f"{exit_error}; {process_error}" if process_error else exit_error
    if workload_technical_failures:
        failure_types = sorted(
            {str(item.get("type") or "unknown") for item in workload_technical_failures}
        )
        workload_error = (
            "HyBench XP reported serious workload failure(s) despite process exit status: "
            + ",".join(failure_types)
        )
        process_error = f"{process_error}; {workload_error}" if process_error else workload_error
        runtime_errors.extend(
            f"hybench_workload_technical_failure:{item.get('type')}:{item.get('message')}"
            for item in workload_technical_failures
        )
    if not completeness_audit.get("passed"):
        workload_error = (
            "workload completeness contract failed: "
            + ",".join(str(item) for item in completeness_audit.get("errors") or [])
        )
        process_error = f"{process_error}; {workload_error}" if process_error else workload_error
        runtime_errors.extend(
            f"workload_completeness_failure:{item}"
            for item in completeness_audit.get("errors") or []
        )
    executor_telemetry_failure = bool(
        executor_telemetry_required
        and executor_telemetry.get("available") is not True
    )
    if executor_telemetry_failure:
        workload_error = (
            "required executor telemetry unavailable: "
            + str(executor_telemetry.get("error") or "unknown")
        )
        process_error = f"{process_error}; {workload_error}" if process_error else workload_error
        runtime_errors.append(f"executor_telemetry_failure:{workload_error}")
    monitor_error = resource_monitor_error(samples, min_stable_samples)
    error_parts = [part for part in (process_error, monitor_error) if part]
    status = "ok" if ret == 0 and monitor_error is None and not process_error else "failed"
    result = {
        "event": "real_validation",
        "workload": workload_name(cfg),
        "validation_backend": "hybench_xp",
        "hybench_run_config": hybench_run_config,
        "label": label,
        "status": status,
        "returncode": ret,
        "error": "; ".join(error_parts),
        "process_error": process_error,
        "monitor_error": monitor_error or "",
        "technical_failure": bool(
            workload_technical_failures
            or not completeness_audit.get("passed")
            or executor_telemetry_failure
        ),
        "workload_technical_failures": workload_technical_failures,
        "workload_completeness": completeness_audit,
        "physical_workload_invocations": 1,
        "valid_replay_count": 1 if status == "ok" else 0,
        "command": shlex.join(cmd),
        "cwd": str(hybench_dir),
        "process": process_meta,
        "window": [start_ts, end_ts],
        "elapsed_s": max(0.0, end_ts - start_ts),
        "params": dict(params),
        "effective_params": effective_params,
        "resources": resources,
        "resource_metric": resource_metric_meta(runtime_meta, fallback_memory_mode=memory_mode),
        "resource_stats": {
            "cpu": cpu_stats,
            "memory_bytes": memory_stats,
            "io_bytes_s": io_stats,
            "io_read_bytes_s": io_read_stats,
            "io_write_bytes_s": io_write_stats,
            "ssd_util_percent": io_util_stats,
            "resource_scope": runtime_meta.get("resource_scope"),
            "container_name": runtime_meta.get("container_name"),
            "process_data_dir": runtime_meta.get("process_data_dir"),
            "postmaster_pid": runtime_meta.get("postmaster_pid"),
            "memory_mode_requested": runtime_meta.get("memory_mode_requested"),
            "memory_mode_effective": runtime_meta.get("memory_mode_effective"),
            "io_mode_requested": runtime_meta.get("io_mode_requested"),
            "io_mode_effective": runtime_meta.get("io_mode_effective"),
            "ssd_observer_disk_devices": runtime_meta.get("ssd_observer_disk_devices"),
            "ssd_util_source": runtime_meta.get("ssd_util_source"),
            "memory_source": runtime_meta.get("memory_source"),
            "memory_pg_pid_count": runtime_meta.get("memory_pg_pid_count"),
            "io_source": runtime_meta.get("io_source"),
            "io_pg_pid_count": runtime_meta.get("io_pg_pid_count"),
            "max_cpu_cores": runtime_meta.get("max_cpu_cores"),
            "cpu_outlier_count": runtime_meta.get("cpu_outlier_count", 0),
            "cpu_outlier_max": runtime_meta.get("cpu_outlier_max", 0.0),
            "cpu_outlier_policy": runtime_meta.get("cpu_outlier_policy"),
            "cpu_outlier_clipped_count": runtime_meta.get("cpu_outlier_clipped_count", 0),
            "short_rate_interval_count": runtime_meta.get("short_rate_interval_count", 0),
            "io_sample_strategy": runtime_meta.get("io_sample_strategy"),
            "io_stable_window_s": runtime_meta.get("io_stable_window_s"),
            "io_stable_start_read_bytes": runtime_meta.get("io_stable_start_read_bytes"),
            "io_stable_start_write_bytes": runtime_meta.get("io_stable_start_write_bytes"),
            "io_stable_end_read_bytes": runtime_meta.get("io_stable_end_read_bytes"),
            "io_stable_end_write_bytes": runtime_meta.get("io_stable_end_write_bytes"),
            "checkpoint_before_validation": runtime_meta.get("checkpoint_before_validation"),
            "checkpoint_after_validation": runtime_meta.get("checkpoint_after_validation"),
            "checkpoint_settle_sec": runtime_meta.get("checkpoint_settle_sec"),
            "evict_data_file_cache_before_validation": runtime_meta.get(
                "evict_data_file_cache_before_validation"
            ),
            "evict_data_file_cache": runtime_meta.get("evict_data_file_cache"),
            "stable_sample_count": sample_counts,
            "min_stable_samples": min_stable_samples,
            "resource_monitor_ok": monitor_error is None,
        },
        "resource_samples": sample_counts,
        "db_view_monitor": db_summary,
        "executor_telemetry": executor_telemetry,
        "db_qps": primary_qps,
        "hybench": {
            "benchmark": benchmark,
            "benchmark_latency": benchmark_latency,
            "xp_qps": workload_qps,
            "xp_tps": safe_float(benchmark.get("xp_tps"), 0.0),
            "run_config": hybench_run_config,
            "workload_output_tail": hybench_summary.get("workload_output"),
            "technical_failures": workload_technical_failures,
            "workload_completeness": completeness_audit,
        },
        "p90_ms": safe_float(benchmark.get("latency_p90"), 0.0),
        "p95_ms": safe_float(benchmark.get("latency_p95"), 0.0),
        "p99_ms": safe_float(benchmark.get("latency_p99"), 0.0),
        "db_errors": db_errors,
        "runtime_errors": runtime_errors,
        "stdout_path": str(stdout_path),
        "hybench_props": str(props_path),
    }
    if worker:
        result["worker"] = {
            "name": worker.get("name"),
            "container": worker.get("container"),
            "host": pg.host,
            "port": pg.port,
            "data_dir": worker.get("data_dir"),
            "disk_root": worker.get("disk_root"),
            "cpuset_cpus": worker.get("cpuset_cpus"),
        }
    log_progress("validation_done", status=status, error=result.get("error"), elapsed_s=result.get("elapsed_s"))
    return result


def run_hybench_xp_validation(
    cfg: Dict[str, Any],
    params: Dict[str, float],
    *,
    output_dir: Path,
    label: str,
    worker: Optional[Dict[str, Any]] = None,
    environment_overrides: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    """Run one physical validation in an auditable shared-SSD window."""

    enabled = to_bool(
        os.environ.get("BONECT_SHARED_SSD_COORDINATION_ENABLED"), False
    )
    mode = str(
        os.environ.get("BONECT_SHARED_SSD_WORKLOAD_LOCK_MODE") or "shared"
    ).strip().lower()
    handle: Optional[Any] = None
    wait_s = 0.0
    try:
        if enabled:
            handle, wait_s = acquire_configured_shared_ssd_workload_coordination()
        result = _run_hybench_xp_validation_uncoordinated(
            cfg,
            params,
            output_dir=output_dir,
            label=label,
            worker=worker,
            environment_overrides=environment_overrides,
        )
        result["shared_ssd_coordination"] = {
            "contract": "pg_algorithm_c_shared_ssd_measurement_rwlock_yield_v2",
            "enabled": enabled,
            "mode": mode if enabled else "disabled",
            "wait_s": wait_s,
            "lock_path": os.environ.get("BONECT_SHARED_SSD_COORDINATION_LOCK_PATH")
            or _SHARED_SSD_COORDINATION_LOCK_DEFAULT,
            "covers_cache_eviction_and_physical_workload": True,
            "qps_or_resource_state_mutation": False,
        }
        return result
    finally:
        release_shared_ssd_coordination(handle)


def resolve_vm_replay_db_qps(
    db_delta: Mapping[str, Any],
    benchmark: Mapping[str, Any],
) -> Tuple[float, str]:
    """Resolve a positive replay QPS without turning a missing extension into zero load.

    ``pg_stat_statements`` remains the preferred database-side counter.  Some
    freshly provisioned BenchBase databases do not have that extension, while
    the benchmark emits an independently measured request throughput.  Record
    the fallback source explicitly so runs remain comparable and auditable.
    """
    choices = (
        (db_delta.get("db_qps"), "pg_stat_statements"),
        (benchmark.get("throughput_qps"), "benchmark_throughput_qps"),
        (benchmark.get("goodput_qps"), "benchmark_goodput_qps"),
        (benchmark.get("xp_qps"), "hybench_xp_qps"),
        (db_delta.get("db_xact_tps"), "pg_stat_database_xact_tps_fallback"),
    )
    for raw_value, source in choices:
        value = safe_float(raw_value, 0.0)
        if value > 0.0:
            return value, source
    return 0.0, "unavailable"


def run_vm_replay_validation(
    cfg: Dict[str, Any],
    params: Dict[str, float],
    *,
    output_dir: Path,
    label: str,
) -> Dict[str, Any]:
    """Validate one binary-search point on its target-owned libvirt VM."""
    validation_cfg = ((cfg.get("binary_plan_tuning") or {}).get("validation") or {})
    replay_cfg = validation_cfg.get("vm_replay") or {}
    target = infer_target_from_label(label)
    worker_paths = replay_cfg.get("worker_configs") or {}
    raw_worker_path = worker_paths.get(target)
    if not target or not raw_worker_path:
        raise ValueError(f"vm_replay worker config is missing for label={label!r} target={target!r}")

    shell_dir = ROOT / "shell"
    if str(shell_dir) not in sys.path:
        sys.path.insert(0, str(shell_dir))
    from bonect_parallel_vm_replay import (  # type: ignore
        drop_worker_cache,
        effective_worker_vm_resource,
        live_disk_throttle,
        resolve_restore_io_boost,
        restore_worker_database,
    )
    from bonect_real_validate_candidates import (  # type: ignore
        load_worker_config,
        scenario_context,
        validate_candidate,
    )

    worker_path = resolve_path(raw_worker_path)
    worker = load_worker_config(str(worker_path))
    restore_timeout_s = max(1.0, safe_float(replay_cfg.get("snapshot_restore_timeout_s"), 600.0))
    drop_cache_timeout_s = max(1.0, safe_float(replay_cfg.get("drop_cache_timeout_s"), 120.0))
    run_mins_by_target = replay_cfg.get("run_mins_by_target") or {}
    run_mins_value = run_mins_by_target.get(target, replay_cfg.get("run_mins"))
    run_mins = safe_float(run_mins_value, 0.0) if run_mins_value is not None else None
    if run_mins is not None and run_mins <= 0.0:
        run_mins = None
    workload_timeout_s = max(1.0, safe_float(replay_cfg.get("workload_timeout_s"), 300.0))
    if run_mins is not None:
        # A duration override must also extend the transport timeout. Otherwise
        # a valid 10-minute replay inherits an older 4/5-minute worker timeout
        # and is killed before its measurement window closes.
        workload_timeout_s = max(workload_timeout_s, run_mins * 60.0 + 180.0)

    # VM replay historically reused the worker's frozen command verbatim and only
    # rewrote its duration.  That silently ignored target_overrides from the binary
    # tuner config (most visibly the None AP-6 physical-I/O profile).  Materialize
    # the effective target profile into run-owned props/statement/setup artifacts
    # before both warmup and measurement so the real replay has the same declared
    # workload semantics as the small-loop stage.
    hybench_base_cfg = validation_cfg.get("hybench_xp") or {}
    hybench_cfg = hybench_xp_config_for_label(hybench_base_cfg, label)
    target_overrides = (
        hybench_base_cfg.get("target_overrides")
        if isinstance(hybench_base_cfg.get("target_overrides"), dict)
        else {}
    )
    target_hybench_override = (
        target_overrides.get(target)
        if isinstance(target_overrides.get(target), dict)
        else {}
    )
    scenario_overrides = worker.setdefault("scenario_overrides", {})
    scenario_override = scenario_overrides.setdefault(target, {})
    if run_mins is not None:
        metrics_override = scenario_override.setdefault("metrics", {})
        vm_runtime_override = metrics_override.setdefault("vm_runtime", {})
        vm_runtime_override["max_runtime_s"] = max(
            safe_float(vm_runtime_override.get("max_runtime_s"), 0.0),
            run_mins * 60.0 + 60.0,
        )
    benchmark_override = scenario_override.setdefault("benchmark", {})
    base_context = scenario_context(target, worker)
    base_benchmark = base_context.get("config", {}).get("benchmark") or {}
    workload_cmd = str(base_benchmark.get("workload_cmd") or "")
    props_match = re.search(r"(?P<prefix>(?:^|\s)-c\s+)(?P<path>[^\s]+)", workload_cmd)
    if not props_match:
        raise ValueError(f"vm_replay workload command has no -c props path: {workload_cmd}")
    source_props = resolve_path(props_match.group("path"))
    props_lines = source_props.read_text(encoding="utf-8").splitlines()
    if run_mins is not None:
        run_value = str(int(run_mins)) if float(run_mins).is_integer() else str(float(run_mins))
        replace_or_append_props_line(props_lines, "xpRunMins", run_value)
        replace_or_append_props_line(props_lines, "apRunMins", run_value)

    explicit_prop_keys = [
        "sf",
        "xtpclient",
        "xapclient",
        "fresh_interval",
        "random_seed",
        "ap_query_ids",
        "ap_query_weights",
        "ap_query_sequence",
        "ap_query_sequence_limit",
        "progress_windows",
        "risk_rate",
        "power_alpha",
        "dist",
        "contention_num",
    ]
    for key in explicit_prop_keys:
        value = target_hybench_override.get(key)
        if key in target_hybench_override and value not in (None, ""):
            replace_or_append_props_line(props_lines, key, value)
    target_extra_props = (
        target_hybench_override.get("extra_props")
        if isinstance(target_hybench_override.get("extra_props"), dict)
        else {}
    )
    for key, value in target_extra_props.items():
        if value not in (None, ""):
            replace_or_append_props_line(props_lines, str(key), value)

    props_dir = output_dir / "vm_replay_props"
    props_dir.mkdir(parents=True, exist_ok=True)
    props_path = props_dir / f"{sanitize_label(label)}.props"
    props_path.write_text("\n".join(props_lines) + "\n", encoding="utf-8")
    workload_cmd = (
        workload_cmd[: props_match.start("path")]
        + str(props_path)
        + workload_cmd[props_match.end("path") :]
    )

    statement_match = re.search(r"(?P<prefix>(?:^|\s)-f\s+)(?P<path>[^\s]+)", workload_cmd)
    statement_meta: Dict[str, Any] = {"generated": False}
    if statement_match:
        statement_cfg = copy.deepcopy(hybench_cfg)
        source_statement = resolve_path(statement_match.group("path"))
        statement_cfg["statement_file"] = str(source_statement)
        statement_path, statement_meta = hybench_statement_file_for_config(
            statement_cfg,
            hybench_dir=Path(str(hybench_cfg.get("hybench_dir") or source_statement.parent.parent)),
            output_dir=output_dir,
            label=label,
        )
        workload_cmd = (
            workload_cmd[: statement_match.start("path")]
            + str(statement_path)
            + workload_cmd[statement_match.end("path") :]
        )
    elif hybench_cfg.get("statement_sql") or hybench_cfg.get("statement_sql_files"):
        raise ValueError(f"vm_replay workload command has no -f statement path: {workload_cmd}")
    else:
        statement_path = Path("")

    setup_items = hybench_setup_sql_items(hybench_cfg)
    if setup_items:
        setup_dir = output_dir / "vm_replay_setup_sql"
        setup_dir.mkdir(parents=True, exist_ok=True)
        setup_paths: List[str] = []
        for index, (_, sql) in enumerate(setup_items):
            setup_path = setup_dir / f"{sanitize_label(label)}_{index:02d}.sql"
            setup_path.write_text(sql.rstrip() + "\n", encoding="utf-8")
            setup_paths.append(str(setup_path))
        benchmark_override["pre_workload_sql_files"] = setup_paths
        benchmark_override["pre_workload_sql_timeout_s"] = max(
            1.0,
            safe_float(hybench_cfg.get("setup_sql_timeout_s"), 300.0),
        )

    benchmark_override["workload_cmd"] = workload_cmd
    if to_bool(base_benchmark.get("validation_warmup_enabled"), False):
        # Historical VM worker configs may still point their warmup command at
        # an obsolete Docker-forwarded JDBC endpoint.  Reuse the run-owned
        # profile so warmup and measurement address the same VM and workload.
        benchmark_override["validation_warmup_cmd"] = workload_cmd
        benchmark_override["validation_warmup_required"] = True
        warmup_timeout_s = max(
            1.0,
            safe_float(base_benchmark.get("validation_warmup_timeout_sec"), 180.0),
        )
        benchmark_override["validation_warmup_min_duration_sec"] = max(
            1.0,
            warmup_timeout_s * 0.8,
        )
    scenario_overrides[target] = scenario_override

    vm_hybench_run_config = hybench_run_config_from_props(
        props_path,
        hybench_cfg,
        hybench_dir=Path(str(hybench_cfg.get("hybench_dir") or source_props.parent.parent)),
        launcher=str(hybench_cfg.get("launcher") or "hybench"),
        statement_file=statement_path,
        restore_prepared=True,
    )
    vm_hybench_run_config.update(
        {
            "source_props_path": str(source_props),
            "target_override_keys": sorted(str(key) for key in target_hybench_override),
            "statement_meta": statement_meta,
        }
    )

    recovery: Dict[str, Any] = {
        "worker_config": str(worker_path),
        "target": target,
        "label": label,
        "run_mins": run_mins,
    }
    boost = resolve_restore_io_boost(worker, target)
    vm_resource = effective_worker_vm_resource(worker, target) or {}
    if boost is not None:
        recovery["restore_io_boost"] = live_disk_throttle(
            str(vm_resource.get("vm_name") or worker.get("vm_name") or worker.get("worker_id")),
            str(vm_resource.get("disk_dev") or "vda"),
            read_mb=int(boost["read_mb"]),
            write_mb=int(boost["write_mb"]),
            verify=True,
        )
    recovery["snapshot_restore"] = restore_worker_database(worker, target, restore_timeout_s)
    if boost is not None:
        recovery["measurement_io_throttle"] = live_disk_throttle(
            str(vm_resource.get("vm_name") or worker.get("vm_name") or worker.get("worker_id")),
            str(vm_resource.get("disk_dev") or "vda"),
            read_mb=max(1, safe_int(vm_resource.get("read_mb"), 100)),
            write_mb=max(0, safe_int(vm_resource.get("write_mb"), 100)),
            verify=True,
        )
    recovery["cache_drop"] = drop_worker_cache(worker, target, drop_cache_timeout_s)

    context = scenario_context(target, worker)
    candidate_id = sanitize_label(label)
    raw = validate_candidate(
        {
            "candidate_id": candidate_id,
            "scenario": target,
            "source": "binary_plan_vm_replay",
            "params": dict(params),
        },
        context,
        run_mins=run_mins,
        workload_timeout_s=workload_timeout_s,
    )
    raw.update(
        {
            "binary_plan_label": label,
            "binary_plan_recovery": recovery,
            "binary_plan_hybench_run_config": vm_hybench_run_config,
        }
    )
    raw_stats = raw.get("raw_resource_stats") or {}
    workload_output = str(raw_stats.get("workload_output") or "")
    workload_error_patterns = (
        "org.postgresql.util.PSQLException: ERROR:",
        "java.lang.OutOfMemoryError",
        "Exception in thread",
        "No space left on device",
        "canceling statement due to statement timeout",
    )
    workload_output_error = next(
        (pattern for pattern in workload_error_patterns if pattern in workload_output),
        None,
    )
    stable_samples = raw_stats.get("stable_sample_count") or {}
    min_stable_samples = max(
        1,
        safe_int((validation_cfg.get("docker_runtime") or {}).get("min_stable_samples"), 3),
    )
    monitor_error = vm_stable_sample_error(stable_samples, min_stable_samples)
    raw_stats["min_stable_samples"] = min_stable_samples
    raw_stats["resource_monitor_ok"] = monitor_error is None
    raw_stats["workload_output_error"] = workload_output_error
    if raw.get("status") == "ok" and monitor_error:
        raw["status"] = "failed"
        raw["error"] = monitor_error
    if raw.get("status") == "ok" and workload_output_error:
        raw["status"] = "failed"
        raw["error"] = (
            "benchmark workload reported a serious runtime error: "
            f"{workload_output_error}"
        )
    resources = dict(raw.get("actual_resource") or {})
    db_delta_row = raw.get("db_delta") or {}
    benchmark = raw.get("benchmark") or {}
    benchmark_latency = raw.get("benchmark_latency") or {}
    db_qps, db_qps_source = resolve_vm_replay_db_qps(db_delta_row, benchmark)
    raw["db_qps_resolution"] = {
        "value": db_qps,
        "source": db_qps_source,
        "pg_stat_statements_available": db_qps_source == "pg_stat_statements",
    }
    append_jsonl(output_dir / f"{target}_vm_replay_raw.jsonl", raw)
    p95_ms = safe_float(benchmark.get("latency_p95"), 0.0)
    p99_ms = safe_float(benchmark.get("latency_p99"), 0.0)
    result: Dict[str, Any] = {
        "event": "real_validation",
        "workload": workload_name(cfg),
        "validation_backend": "vm_replay",
        "label": label,
        "target": target,
        "status": raw.get("status"),
        "error": raw.get("error"),
        "window": [raw.get("started_at"), time.time()],
        "elapsed_s": raw.get("elapsed_s"),
        "params": dict(params),
        "resources": resources,
        "actual_utilization_pct": raw.get("actual_utilization_pct") or {},
        "runtime_capability": context.get("capability") or {},
        "runtime_scoring": (context.get("config") or {}).get("scoring") or {},
        "resource_stats": {
            "resource_monitor_ok": raw.get("status") == "ok" and monitor_error is None,
            "stable_sample_count": stable_samples,
            "min_stable_samples": min_stable_samples,
            "cpu_source": raw_stats.get("cpu_source"),
            "memory_source": raw_stats.get("memory_source"),
            "io_source": raw_stats.get("io_source"),
            "io_disk_devices": raw_stats.get("io_disk_devices"),
            "workload_exit": raw_stats.get("workload_exit"),
            "workload_output_error": workload_output_error,
            "vm_host": raw_stats.get("vm_host"),
        },
        "resource_samples": {
            "cpu_cores": safe_int(stable_samples.get("cpu"), 0),
            "memory_bytes": safe_int(stable_samples.get("memory"), 0),
            "io_bps": safe_int(stable_samples.get("io"), 0),
        },
        "db_qps": db_qps,
        "db_qps_source": db_qps_source,
        "db_xact_tps": safe_float(db_delta_row.get("db_xact_tps"), 0.0),
        "hybench": {
            "benchmark": benchmark,
            "benchmark_latency": benchmark_latency,
            "xp_qps": safe_float(benchmark.get("xp_qps"), 0.0),
            "xp_tps": safe_float(benchmark.get("xp_tps"), 0.0),
            "run_config": vm_hybench_run_config,
        },
        "p90_ms": safe_float(benchmark.get("latency_p90"), 0.0),
        "p95_ms": p95_ms,
        "p99_ms": p99_ms,
        "worker": {
            "name": worker.get("worker_id") or worker.get("vm_name"),
            "host": ((context.get("pg") or {}).get("host")),
            "port": ((context.get("pg") or {}).get("port")),
            "vm_name": worker.get("vm_name"),
        },
        "vm_replay": {
            "raw_log": str(output_dir / f"{target}_vm_replay_raw.jsonl"),
            "worker_config": str(worker_path),
            "recovery": recovery,
            "actual_utilization_pct": raw.get("actual_utilization_pct") or {},
        },
    }
    return result


def run_real_validation(
    cfg: Dict[str, Any],
    params: Dict[str, float],
    *,
    output_dir: Path,
    label: str,
    worker: Optional[Dict[str, Any]] = None,
    environment_overrides: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    validation_cfg = ((cfg.get("binary_plan_tuning") or {}).get("validation") or {})
    vm_replay_cfg = validation_cfg.get("vm_replay") or {}
    if to_bool(vm_replay_cfg.get("enabled"), False):
        if worker is not None:
            raise ValueError("vm_replay owns one VM per target and does not accept Docker workers")
        return run_vm_replay_validation(cfg, params, output_dir=output_dir, label=label)
    hybench_cfg = validation_cfg.get("hybench_xp") or {}
    if to_bool(hybench_cfg.get("enabled"), False):
        return run_hybench_xp_validation(
            cfg,
            params,
            output_dir=output_dir,
            label=label,
            worker=worker,
            environment_overrides=environment_overrides,
        )
    return run_pgbench_validation(cfg, params, output_dir=output_dir, label=label, worker=worker)


def failed_validation_result(
    cfg: Dict[str, Any],
    params: Dict[str, float],
    *,
    label: str,
    error: Exception,
    started_at: float,
    worker: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "event": "real_validation",
        "workload": workload_name(cfg),
        "validation_backend": "hybench_xp"
        if to_bool((((cfg.get("binary_plan_tuning") or {}).get("validation") or {}).get("hybench_xp") or {}).get("enabled"), False)
        else "pgbench",
        "label": label,
        "status": "failed",
        "returncode": None,
        "window": [started_at, time.time()],
        "elapsed_s": max(0.0, time.time() - started_at),
        "params": dict(params),
        "resources": {"cpu": 0.0, "memory": 0.0, "io": 0.0, "io_read": 0.0, "io_write": 0.0},
        "resource_samples": {
            "cpu_cores": 0,
            "io_bps": 0,
            "io_read_bps": 0,
            "io_write_bps": 0,
            "memory_bytes": 0,
        },
        "db_qps": 0.0,
        "p95_ms": None,
        "p99_ms": None,
        "error": str(error),
    }
    if worker:
        result["worker"] = {
            "name": worker.get("name"),
            "container": worker.get("container"),
            "host": worker.get("host"),
            "port": worker.get("port"),
            "data_dir": worker.get("data_dir"),
            "disk_root": worker.get("disk_root"),
            "cpuset_cpus": worker.get("cpuset_cpus"),
        }
    return result


def run_real_validation_safe(
    cfg: Dict[str, Any],
    params: Dict[str, float],
    *,
    output_dir: Path,
    label: str,
    worker: Optional[Dict[str, Any]] = None,
    force_new_replay: bool = False,
    environment_overrides: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    started_at = time.time()
    try:
        result = run_real_validation(
            cfg,
            params,
            output_dir=output_dir,
            label=label,
            worker=worker,
            environment_overrides=environment_overrides,
        )
        result.setdefault("workload", workload_name(cfg))
        # An explicit marker lets post-convergence confirmation prove that it
        # came from the transport instead of an exact-vector replay cache.
        result["used_real_replay"] = True
        result["forced_new_real_replay"] = bool(force_new_replay)
        return result
    except Exception as exc:
        result = failed_validation_result(
            cfg,
            params,
            label=label,
            error=exc,
            started_at=started_at,
            worker=worker,
        )
        result["used_real_replay"] = True
        result["forced_new_real_replay"] = bool(force_new_replay)
        return result


def load_sql_statements(path: Path) -> List[str]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    statements: List[str] = []
    current: List[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("--") or line.startswith("\\"):
            continue
        current.append(raw_line)
        if ";" in raw_line:
            joined = "\n".join(current).strip()
            parts = joined.split(";")
            for part in parts[:-1]:
                stmt = part.strip()
                if stmt:
                    statements.append(stmt)
            current = [parts[-1]] if parts[-1].strip() else []
    tail = "\n".join(current).strip()
    if tail:
        statements.append(tail.rstrip(";"))
    return statements


def fetch_pg_stat_statements(pg: PgConn, limit: int) -> List[Dict[str, Any]]:
    try:
        cols_out = psql_query(
            pg,
            "SELECT attname FROM pg_attribute "
            "WHERE attrelid = 'pg_stat_statements'::regclass "
            "AND attnum > 0 AND NOT attisdropped ORDER BY attnum;",
        )
    except Exception:
        return []
    cols = set(line.strip() for line in cols_out.splitlines() if line.strip())
    total_col = "total_exec_time" if "total_exec_time" in cols else "total_time"
    mean_col = "mean_exec_time" if "mean_exec_time" in cols else "mean_time"
    queryid_expr = "queryid" if "queryid" in cols else "NULL::text AS queryid"
    rows_expr = "rows" if "rows" in cols else "NULL::bigint AS rows"
    filters = [
        "dbid = (SELECT oid FROM pg_database WHERE datname = current_database())",
        "query NOT ILIKE 'EXPLAIN (FORMAT JSON%'",
        "query NOT ILIKE '%pg_stat_statements%'",
        "query NOT ILIKE 'SELECT attname FROM pg_attribute%'",
        "query NOT ILIKE 'DISCARD ALL%'",
    ]
    sql = (
        f"SELECT {queryid_expr}, calls, {total_col}, {mean_col}, {rows_expr}, "
        "replace(replace(query, E'\\t', ' '), E'\\n', ' ') "
        "FROM pg_stat_statements WHERE "
        + " AND ".join(filters)
        + f" ORDER BY {total_col} DESC LIMIT {int(limit)};"
    )
    out = psql_query(pg, sql)
    rows: List[Dict[str, Any]] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 6:
            continue
        rows.append(
            {
                "queryid": parts[0],
                "calls": safe_float(parts[1], 1.0),
                "total_time_ms": safe_float(parts[2], 0.0),
                "mean_time_ms": safe_float(parts[3], 0.0),
                "rows": safe_float(parts[4], 0.0),
                "query": parts[5].strip().rstrip(";"),
            }
        )
    return rows


class PlanFeatureProbe:
    """Plan-tree and resource-input feature probe for candidate cost parameters."""

    def __init__(self, cfg: Dict[str, Any]) -> None:
        self.cfg = cfg
        self.pg = pg_conn_from_config(cfg)
        plan_capture_cfg = cfg.get("plan_capture") or {}
        metrics_cfg = cfg.get("metrics") or {}
        estimator_cfg = metrics_cfg.get("plan_estimator") or {}
        self.workload_context = dict((cfg.get("binary_plan_tuning") or {}).get("workload_context") or {})
        self.estimator = PlanResourceEstimator(
            pg_host=self.pg.host,
            pg_port=self.pg.port,
            pg_user=self.pg.user,
            pg_dbname=self.pg.dbname,
            plan_capture_modes=plan_capture_cfg.get("modes") or ["generic"],
            custom_plan_params=plan_capture_cfg.get("custom_plan_params") or {},
            estimator_cfg=estimator_cfg,
            psql_query_func=lambda sql: psql_query_with_transient_retry(
                self.pg,
                sql,
                timeout_s=120.0,
                attempts=3,
            ),
        )
        self.explain_workers = max(1, safe_int((estimator_cfg.get("shared") or {}).get("explain_workers"), 4))
        self.calibration_factor = safe_float((estimator_cfg.get("shared") or {}).get("calibration_factor"), 1.0)

    def probe(self, queries: Sequence[Dict[str, Any]], params: Dict[str, float]) -> Dict[str, Any]:
        explained = self.estimator._collect_query_plans(  # pylint: disable=protected-access
            queries,
            explain_workers=self.explain_workers,
            params=params,
        )
        query_estimates = self.estimator._build_query_estimates(  # pylint: disable=protected-access
            explained_queries=explained,
            workload_context=self.workload_context,
        )
        resource_breakdown: Dict[str, Any] = {}
        resource_avg: Dict[str, float] = {}
        for resource_type in ("cpu", "memory", "io"):
            result = self.estimator._build_resource_result(  # pylint: disable=protected-access
                resource_type=resource_type,
                query_estimates=query_estimates,
                workload_context=self.workload_context,
                calibration_factor=self.calibration_factor,
                explain_workers=self.explain_workers,
            )
            resource_breakdown[resource_type] = result
            resource_avg[resource_type] = safe_float(result.get("value"), 0.0)

        plan_signatures = self._plan_signatures(explained)
        exact_plan_metadata = _build_exact_plan_fingerprint_metadata(
            workload_query_count=len(queries),
            plan_queries=queries,
            explained_queries=explained,
        )
        feature_inputs = {
            "cpu": self.estimator._aggregate_cpu_model_features(  # pylint: disable=protected-access
                query_estimates,
                self.workload_context,
            ),
            "memory": self._memory_feature_inputs(query_estimates, resource_breakdown["memory"]),
            "io": self.estimator._aggregate_io_model_features(  # pylint: disable=protected-access
                query_estimates,
                self.workload_context,
            ),
        }
        return {
            "params": dict(params),
            "resource_avg": resource_avg,
            "resource_error_profile": summarize_resource_error_profile(resource_breakdown),
            "plan_signatures": plan_signatures,
            "exact_plan_metadata": exact_plan_metadata,
            "feature_inputs": feature_inputs,
            "query_count": len(queries),
            "explained_query_count": len([item for item in explained if not item.get("error")]),
            "errors": [item for item in explained if item.get("error")][:20],
            "physical_model_enabled": self.estimator._physical_model_enabled(),  # pylint: disable=protected-access
            "resource_breakdown_summary": {
                resource: {
                    "formula_name": data.get("formula_name"),
                    "query_count": data.get("query_count"),
                    "error_count": len(data.get("errors") or []),
                    "physical_direction_supported": data.get(
                        "physical_direction_supported"
                    ),
                    "optimistic_interval_pruning_allowed": data.get(
                        "optimistic_interval_pruning_allowed"
                    ),
                }
                for resource, data in resource_breakdown.items()
            },
        }

    def _memory_feature_inputs(
        self,
        query_estimates: Sequence[Dict[str, Any]],
        memory_result: Dict[str, Any],
    ) -> Dict[str, float]:
        features: Dict[str, float] = {}
        for section in ("planning_breakdown", "concurrency_breakdown"):
            data = memory_result.get(section) or {}
            if isinstance(data, dict):
                for key, value in data.items():
                    if isinstance(value, (int, float)):
                        features[f"memory_{key}"] = float(value)
        features.update(
            {
                f"memory_shape_{key}": float(value)
                for key, value in self.estimator._aggregate_plan_shape_features(  # pylint: disable=protected-access
                    query_estimates,
                    self.workload_context,
                ).items()
            }
        )
        learned = ((memory_result.get("calibration_breakdown") or {}).get("learned_model") or {})
        model_features = learned.get("features")
        if isinstance(model_features, dict):
            for key, value in model_features.items():
                features[f"memory_model_{key}"] = safe_float(value, 0.0)
        return features

    def _plan_signatures(self, explained: Sequence[Dict[str, Any]]) -> Dict[str, str]:
        signatures: Dict[str, str] = {}
        for idx, item in enumerate(explained):
            query_key = str(item.get("queryid") or item.get("query") or idx)
            if item.get("error"):
                signatures[query_key] = "ERROR:" + stable_hash(str(item.get("error")))
                continue
            normalized = normalize_plan_structure(item.get("plan_root"))
            signatures[query_key] = stable_hash(json.dumps(normalized, sort_keys=True, ensure_ascii=False))
        return signatures


def stable_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def plan_probe_coverage_audit(
    cfg: Dict[str, Any],
    probe_result: Dict[str, Any],
) -> Dict[str, Any]:
    """Fail-closed audit for workload-wide plan-behavior fingerprints."""
    bpt = cfg.get("binary_plan_tuning") or {}
    workload_cfg = bpt.get("workload") if isinstance(bpt.get("workload"), dict) else {}
    gate_cfg = (
        workload_cfg.get("plan_coverage_gate")
        if isinstance(workload_cfg.get("plan_coverage_gate"), dict)
        else {}
    )
    enabled = to_bool(gate_cfg.get("enabled"), False)
    expected_query_count = max(
        0,
        safe_int(
            gate_cfg.get("expected_query_count"),
            safe_int((bpt.get("workload_context") or {}).get("sql_count"), 0),
        ),
    )
    actual_query_count = max(0, safe_int(probe_result.get("query_count"), 0))
    explained_query_count = max(
        0, safe_int(probe_result.get("explained_query_count"), 0)
    )
    signatures = (
        probe_result.get("plan_signatures")
        if isinstance(probe_result.get("plan_signatures"), dict)
        else {}
    )
    actual_query_ids = sorted(str(key) for key in signatures)
    configured_query_ids = gate_cfg.get("expected_query_ids")
    expected_query_ids = (
        sorted(str(key) for key in configured_query_ids)
        if isinstance(configured_query_ids, list)
        else []
    )
    query_ids_match = bool(
        not expected_query_ids or actual_query_ids == expected_query_ids
    )
    plan_sql_value = workload_cfg.get("plan_sql_file")
    expected_plan_sql_sha256 = str(gate_cfg.get("plan_sql_sha256") or "")
    actual_plan_sql_sha256 = ""
    plan_sql_sha256_error = ""
    if expected_plan_sql_sha256:
        try:
            plan_sql_path = resolve_path(plan_sql_value)
            actual_plan_sql_sha256 = hashlib.sha256(plan_sql_path.read_bytes()).hexdigest()
        except (OSError, TypeError, ValueError) as exc:
            plan_sql_sha256_error = str(exc)
    plan_sql_sha256_match = bool(
        not expected_plan_sql_sha256
        or actual_plan_sql_sha256 == expected_plan_sql_sha256
    )
    catalog_complete = to_bool(gate_cfg.get("catalog_complete"), False)
    block_physical_base_topk = to_bool(
        gate_cfg.get("block_physical_base_topk"),
        to_bool(gate_cfg.get("fail_closed_before_physical_workload"), False),
    )
    errors = probe_result.get("errors") if isinstance(probe_result.get("errors"), list) else []
    checks = {
        "catalog_declared_complete": catalog_complete,
        "expected_query_count_positive": expected_query_count > 0,
        "query_count_exact": actual_query_count == expected_query_count,
        "all_queries_explained": explained_query_count == expected_query_count,
        "one_signature_per_query": len(signatures) == expected_query_count,
        "no_probe_errors": not errors,
        "query_ids_exact": query_ids_match,
        "plan_sql_sha256_exact": plan_sql_sha256_match,
    }
    passed = bool(enabled and all(checks.values()))
    return {
        "enabled": enabled,
        "passed": passed,
        "fail_closed": enabled,
        "block_physical_base_topk": block_physical_base_topk,
        "coverage_scope": gate_cfg.get("coverage_scope"),
        "expected_query_count": expected_query_count,
        "actual_query_count": actual_query_count,
        "explained_query_count": explained_query_count,
        "plan_signature_count": len(signatures),
        "expected_query_ids": expected_query_ids,
        "actual_query_ids": actual_query_ids,
        "expected_plan_sql_sha256": expected_plan_sql_sha256,
        "actual_plan_sql_sha256": actual_plan_sql_sha256,
        "plan_sql_sha256_error": plan_sql_sha256_error,
        "checks": checks,
        "errors": errors[:20],
    }


def flatten_numeric(prefix: str, value: Any, out: Dict[str, float]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            flatten_numeric(f"{prefix}.{key}" if prefix else str(key), child, out)
    elif isinstance(value, (int, float)):
        numeric = float(value)
        if math.isfinite(numeric):
            out[prefix] = numeric


def feature_diff(
    left: Dict[str, Any],
    right: Dict[str, Any],
    *,
    abs_tol: float = 1e-9,
    rel_tol: float = 1e-6,
    max_keys: int = 40,
) -> Dict[str, Any]:
    left_flat: Dict[str, float] = {}
    right_flat: Dict[str, float] = {}
    flatten_numeric("", left, left_flat)
    flatten_numeric("", right, right_flat)
    changed: List[Dict[str, float]] = []
    for key in sorted(set(left_flat) | set(right_flat)):
        lv = left_flat.get(key, 0.0)
        rv = right_flat.get(key, 0.0)
        if abs(lv - rv) > max(abs_tol, rel_tol * max(abs(lv), abs(rv), 1.0)):
            changed.append({"feature": key, "baseline": lv, "candidate": rv, "delta": rv - lv})
    changed.sort(key=lambda item: abs(item["delta"]), reverse=True)
    return {
        "changed": bool(changed),
        "changed_count": len(changed),
        "changed_keys": [item["feature"] for item in changed[:max_keys]],
        "top_changes": changed[:max_keys],
    }


def plan_diff(left: Dict[str, str], right: Dict[str, str]) -> Dict[str, Any]:
    changed_keys = [key for key in sorted(set(left) & set(right)) if left.get(key) != right.get(key)]
    missing_left = [key for key in sorted(set(right) - set(left))]
    missing_right = [key for key in sorted(set(left) - set(right))]
    return {
        "changed": bool(changed_keys or missing_left or missing_right),
        "changed_count": len(changed_keys),
        "changed_keys": changed_keys[:40],
        "missing_from_baseline": missing_left[:20],
        "missing_from_candidate": missing_right[:20],
    }


def summarize_probe_changes(baseline: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
    resource_feature_changes = {
        resource: feature_diff(
            baseline.get("feature_inputs", {}).get(resource, {}),
            candidate.get("feature_inputs", {}).get(resource, {}),
        )
        for resource in ("cpu", "memory", "io")
    }
    plan_changes = plan_diff(
        baseline.get("plan_signatures") or {},
        candidate.get("plan_signatures") or {},
    )
    operator_changes = operator_changes_from_feature_inputs(
        baseline.get("feature_inputs", {}).get("cpu", {}),
        candidate.get("feature_inputs", {}).get("cpu", {}),
    )
    return {
        "plan": plan_changes,
        "features": resource_feature_changes,
        "operator_changes": operator_changes,
        "any_changed": bool(plan_changes.get("changed"))
        or any(item.get("changed") for item in resource_feature_changes.values()),
    }


def workload_queries_with_sampling(
    cfg: Dict[str, Any],
    probe: Optional[PlanFeatureProbe] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    bpt = cfg.get("binary_plan_tuning") or {}
    screening_cfg = bpt.get("endpoint_screening") or {}
    workload_cfg = bpt.get("workload") or {}
    max_queries = max(1, safe_int(screening_cfg.get("max_queries"), 80))

    rows: List[Dict[str, Any]] = []
    source_details: List[str] = []
    if isinstance(workload_cfg.get("queries"), list):
        for idx, query in enumerate(workload_cfg["queries"], start=1):
            text = str(query).strip().rstrip(";")
            if text:
                rows.append({"queryid": f"configured_{idx}", "query": text, "calls": 1, "replay_count": 1})
        source_details.append("configured_queries")

    sql_file = workload_cfg.get("plan_sql_file")
    if sql_file:
        sql_path = resolve_path(sql_file)
        for idx, statement in enumerate(load_sql_statements(sql_path), start=1):
            rows.append({"queryid": f"file_{idx}", "query": statement, "calls": 1, "replay_count": 1})
        source_details.append(f"sql_file:{sql_path}")

    if not rows:
        pg = pg_conn_from_config(cfg)
        candidate_limit = max(max_queries, safe_int(screening_cfg.get("pg_stat_statements_limit"), 200))
        rows = fetch_pg_stat_statements(pg, candidate_limit)
        source_details.append(f"pg_stat_statements_limit:{candidate_limit}")

    rows = dedupe_queries(rows)
    source_count = len(rows)
    sampling_meta: Dict[str, Any] = {
        "workload": workload_name(cfg),
        "max_queries": max_queries,
        "source_count": source_count,
        "source_details": source_details,
        "strategy": [
            "top_slow_sql",
            "high_calls_sql",
            "plan_estimator_resource_contribution_sql",
            "random_fill",
            "dedupe",
        ],
    }
    if len(rows) <= max_queries:
        sampling_meta.update(
            {
                "sampling_applied": False,
                "sampled_count": len(rows),
                "top_slow_selected": 0,
                "high_calls_selected": 0,
                "resource_contribution_selected": 0,
                "random_fill_selected": 0,
            }
        )
        return rows, sampling_meta

    selected: List[Dict[str, Any]] = []
    slow_n = max(0, safe_int(screening_cfg.get("top_slow_sql"), max_queries // 4))
    calls_n = max(0, safe_int(screening_cfg.get("high_calls_sql"), max_queries // 4))
    top_slow = sorted(rows, key=lambda item: safe_float(item.get("total_time_ms"), 0.0), reverse=True)[:slow_n]
    high_calls = sorted(rows, key=lambda item: safe_float(item.get("calls"), 0.0), reverse=True)[:calls_n]
    selected.extend(top_slow)
    selected.extend(high_calls)

    contribution_selected: List[Dict[str, Any]] = []
    if probe is not None and screening_cfg.get("resource_contribution_sql", True):
        try:
            baseline = probe.probe(rows[: max(max_queries, 200)], initial_params(cfg))
            contribution_selected = resource_contribution_queries(rows, baseline, max_queries=max_queries // 2)
            selected.extend(contribution_selected)
        except Exception as exc:
            sampling_meta["resource_contribution_error"] = str(exc)

    selected = dedupe_queries(selected)
    remaining = [item for item in rows if query_identity(item) not in {query_identity(x) for x in selected}]
    rng = random.Random(int(safe_int(screening_cfg.get("random_seed"), 20260701)))
    rng.shuffle(remaining)
    random_fill = remaining[: max(0, max_queries - len(selected))]
    selected.extend(random_fill)
    sampled = dedupe_queries(selected)[:max_queries]
    sampling_meta.update(
        {
            "sampling_applied": True,
            "sampled_count": len(sampled),
            "top_slow_selected": len(dedupe_queries(top_slow)),
            "high_calls_selected": len(dedupe_queries(high_calls)),
            "resource_contribution_selected": len(dedupe_queries(contribution_selected)),
            "random_fill_selected": len(dedupe_queries(random_fill)),
            "random_seed": safe_int(screening_cfg.get("random_seed"), 20260701),
        }
    )
    return sampled, sampling_meta


def workload_queries(cfg: Dict[str, Any], probe: Optional[PlanFeatureProbe] = None) -> List[Dict[str, Any]]:
    queries, _ = workload_queries_with_sampling(cfg, probe)
    return queries


def query_identity(item: Dict[str, Any]) -> str:
    queryid = str(item.get("queryid") or "").strip()
    if queryid:
        return "queryid:" + queryid
    return "query:" + stable_hash(str(item.get("query") or ""))


def dedupe_queries(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: Set[str] = set()
    result: List[Dict[str, Any]] = []
    for item in rows:
        text = str(item.get("query") or "").strip().rstrip(";")
        if not text:
            continue
        key = query_identity(item)
        if key in seen:
            continue
        seen.add(key)
        row = dict(item)
        row["query"] = text
        row.setdefault("replay_count", max(1, safe_int(row.get("calls"), 1)))
        result.append(row)
    return result


def resource_contribution_queries(
    rows: Sequence[Dict[str, Any]],
    baseline_probe: Dict[str, Any],
    *,
    max_queries: int,
) -> List[Dict[str, Any]]:
    del baseline_probe
    # The detailed per-query contribution is available in the estimator internals,
    # but the screening payload intentionally stores only compact summaries.
    # Keep this conservative fallback deterministic: total time approximates
    # CPU/memory/IO contribution when pg_stat_statements is the source.
    return sorted(rows, key=lambda item: safe_float(item.get("total_time_ms"), 0.0), reverse=True)[:max_queries]


def endpoint_probe_task(
    cfg: Dict[str, Any],
    queries: Sequence[Dict[str, Any]],
    baseline: Dict[str, Any],
    base_params: Dict[str, float],
    param_name: str,
    endpoint_name: str,
    value: float,
) -> Dict[str, Any]:
    started = time.perf_counter()
    params = dict(base_params)
    params[param_name] = float(value)
    probe = PlanFeatureProbe(cfg)
    candidate = probe.probe(queries, params)
    changes = summarize_probe_changes(baseline, candidate)
    return {
        "endpoint": endpoint_name,
        "value": value,
        "params": params,
        "resource_avg": candidate.get("resource_avg"),
        "changes": changes,
        "elapsed_s": time.perf_counter() - started,
        "status": "ok",
    }


def endpoint_screening(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    out_dir = output_dir_from_args(cfg, args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    error_bound_artifact_path = install_replay_error_bound_artifact(cfg, out_dir)
    suffix = workload_log_suffix(cfg)
    log_path = out_dir / f"endpoint_screening_{suffix}.jsonl"
    log_path.write_text("", encoding="utf-8")

    stage_started = time.perf_counter()
    probe = PlanFeatureProbe(cfg)
    queries, sampling_meta = workload_queries_with_sampling(cfg, probe)
    base_params = initial_params(cfg)
    ranges = param_ranges(cfg)
    screening_cfg = (cfg.get("binary_plan_tuning") or {}).get("endpoint_screening") or {}
    endpoint_workers = max(1, safe_int(screening_cfg.get("endpoint_workers"), 4))
    endpoint_workers = min(endpoint_workers, max(1, len(COST_PARAMS) * 2))
    append_jsonl(
        log_path,
        with_workload(cfg, {
            "event": "screening_start",
            "config_path": cfg.get("_config_path"),
            "output_dir": str(out_dir),
            "query_count": len(queries),
            "sampling": sampling_meta,
            "params": base_params,
            "endpoint_parallelism": endpoint_workers,
            "explain_workers_per_probe": probe.explain_workers,
        }),
    )
    append_jsonl(log_path, with_workload(cfg, {"event": "workload_sampling", **sampling_meta}))
    write_json(out_dir / "screening_workload_queries.json", {"queries": queries, "sampling": sampling_meta})

    baseline = probe.probe(queries, base_params)
    append_jsonl(
        log_path,
        with_workload(cfg, {
            "event": "baseline_probe",
            "params": base_params,
            "resource_avg": baseline.get("resource_avg"),
            "query_count": baseline.get("query_count"),
            "explained_query_count": baseline.get("explained_query_count"),
            "errors": baseline.get("errors"),
        }),
    )

    endpoint_results_by_param: Dict[str, List[Dict[str, Any]]] = {name: [] for name in COST_PARAMS}
    tasks: List[Tuple[str, str, float]] = []
    for name in COST_PARAMS:
        tasks.append((name, "min", ranges[name][0]))
        tasks.append((name, "max", ranges[name][1]))

    with ThreadPoolExecutor(max_workers=endpoint_workers) as executor:
        future_map = {
            executor.submit(endpoint_probe_task, cfg, queries, baseline, base_params, name, endpoint_name, value): (
                name,
                endpoint_name,
                value,
            )
            for name, endpoint_name, value in tasks
        }
        for future in as_completed(future_map):
            name, endpoint_name, value = future_map[future]
            try:
                result = future.result()
            except Exception as exc:
                params = dict(base_params)
                params[name] = float(value)
                result = {
                    "endpoint": endpoint_name,
                    "value": value,
                    "params": params,
                    "resource_avg": {},
                    "changes": {"any_changed": True, "error": str(exc)},
                    "status": "failed",
                    "error": str(exc),
                    "elapsed_s": None,
                }
            endpoint_results_by_param[name].append(result)
            append_jsonl(
                log_path,
                with_workload(cfg, {
                    "event": "endpoint_probe",
                    "param": name,
                    **result,
                }),
            )

    active_params: List[str] = []
    removed_params: List[str] = []
    param_results: Dict[str, Any] = {}
    for name in COST_PARAMS:
        endpoint_results = sorted(endpoint_results_by_param[name], key=lambda item: str(item.get("endpoint")))
        keep = False
        reasons: List[str] = []
        for result in endpoint_results:
            endpoint_name = str(result.get("endpoint"))
            changes = result.get("changes") or {}
            if result.get("status") != "ok":
                keep = True
                reasons.append(f"{endpoint_name}:probe_failed_conservative_keep")
            if changes.get("any_changed"):
                keep = True
            if changes.get("plan", {}).get("changed"):
                reasons.append(f"{endpoint_name}:plan_tree_changed")
            for resource, detail in (changes.get("features") or {}).items():
                if isinstance(detail, dict) and detail.get("changed"):
                    reasons.append(f"{endpoint_name}:{resource}_input_features_changed")
        if keep:
            active_params.append(name)
        else:
            removed_params.append(name)
            reasons.append("min/max endpoints did not change plan tree or any resource input features")
        param_results[name] = {
            "active": keep,
            "reasons": sorted(set(reasons)),
            "endpoints": endpoint_results,
        }
        append_jsonl(
            log_path,
            with_workload(cfg, {
                "event": "param_screening_result",
                "param": name,
                "active": keep,
                "reasons": sorted(set(reasons)),
            }),
        )

    summary = {
        "event": "screening_summary",
        "workload": workload_name(cfg),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "config_path": cfg.get("_config_path"),
        "output_dir": str(out_dir),
        "active_params": active_params,
        "removed_params": removed_params,
        "param_results": param_results,
        "query_count": len(queries),
        "sampling": sampling_meta,
        "baseline_resource_avg": baseline.get("resource_avg"),
        "endpoint_parallelism": endpoint_workers,
        "elapsed_s": time.perf_counter() - stage_started,
    }
    write_json(out_dir / "active_params.json", summary)
    write_json(out_dir / "screening_summary.json", summary)
    append_jsonl(log_path, with_workload(cfg, summary))
    print(f"endpoint screening wrote {log_path}", flush=True)
    print(f"active_params={active_params}", flush=True)
    print(f"removed_params={removed_params}", flush=True)
    return 0


def param_signature(params: Dict[str, float]) -> str:
    return "|".join(f"{name}={float(params[name]):.12g}" for name in COST_PARAMS)


def load_active_params(out_dir: Path, args_active_params: str) -> List[str]:
    if args_active_params:
        return [name.strip() for name in args_active_params.split(",") if name.strip()]
    path = out_dir / "active_params.json"
    if not path.exists():
        return list(COST_PARAMS)
    data = read_json(path)
    active = [str(name) for name in data.get("active_params") or [] if str(name) in COST_PARAMS]
    return active or list(COST_PARAMS)


def candidate_mask_values(
    active: Sequence[str],
    ranges: Dict[str, Tuple[float, float]],
) -> List[Tuple[str, Dict[str, float]]]:
    choices: List[List[Tuple[str, float]]] = []
    for name in active:
        low, high = ranges[name]
        lower_point, upper_point = interval_pair_points(low, high)
        choices.append([("L", lower_point), ("R", upper_point)])
    result: List[Tuple[str, Dict[str, float]]] = []
    for combo in itertools.product(*choices):
        mask = "".join(item[0] for item in combo)
        values = {name: combo[idx][1] for idx, name in enumerate(active)}
        result.append((mask, values))
    return result


def reduce_candidates(
    candidates: List[Dict[str, Any]],
    *,
    max_candidates: int,
) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    if max_candidates <= 0 or len(candidates) <= max_candidates:
        return candidates, None
    keep: List[Dict[str, Any]] = []
    by_mask = {candidate["mask"]: candidate for candidate in candidates}
    masks = sorted(by_mask)
    preferred = []
    if masks:
        width = len(masks[0])
        preferred.extend(["L" * width, "R" * width])
        preferred.extend("".join("L" if (idx + bit) % 2 == 0 else "R" for bit in range(width)) for idx in range(2))
    for mask in preferred:
        if mask in by_mask and by_mask[mask] not in keep:
            keep.append(by_mask[mask])
    remaining = [candidate for candidate in candidates if candidate not in keep]
    if remaining and len(keep) < max_candidates:
        step = max(1, len(remaining) // max(1, max_candidates - len(keep)))
        keep.extend(remaining[::step][: max_candidates - len(keep)])
    keep = keep[:max_candidates]
    return keep, {
        "applied": True,
        "reason": "candidate_count_exceeded_resource_budget",
        "strategy": "deterministic_mask_subset_all_left_all_right_alternating_even_spacing",
        "original_candidate_count": len(candidates),
        "validated_candidate_count": len(keep),
    }


def explanation_other_resources(target: str, cfg: Optional[Dict[str, Any]] = None) -> Tuple[str, ...]:
    """Resources allowed to explain load shifting for a bottleneck target."""
    if cfg is not None:
        policy = ((cfg.get("binary_plan_tuning") or {}).get("acceptance_policy") or {})
        required = policy.get("target_required_other_up_resources")
        if isinstance(required, dict):
            configured = required.get(target)
            if isinstance(configured, list):
                resources = tuple(
                    str(item).strip().lower()
                    for item in configured
                    if str(item).strip().lower() in {"cpu", "memory", "io"}
                )
                if resources:
                    return resources
    if target in ("cpu", "io"):
        return tuple(resource for resource in ("cpu", "memory", "io") if resource != target)
    if target == "memory":
        return ("cpu", "io")
    return ("cpu", "memory", "io")


def resource_up(
    candidate_value: float,
    reference_value: float,
    *,
    min_other_up: float,
) -> bool:
    if reference_value > 0.0:
        threshold = reference_value * (1.0 + min_other_up)
        return candidate_value >= threshold if min_other_up > 0.0 else candidate_value > threshold
    return candidate_value > 0.0


def none_other_up_delta_weight(resource: str, cfg: Optional[Dict[str, Any]]) -> float:
    if cfg is None:
        return 1.0
    policy = ((cfg.get("binary_plan_tuning") or {}).get("acceptance_policy") or {})
    weights = policy.get("none_other_up_delta_weights")
    if not isinstance(weights, dict):
        return 1.0
    return max(0.0, safe_float(weights.get(resource), 1.0))


def resource_explanation_details(
    candidate_resources: Dict[str, Any],
    reference_resources: Dict[str, Any],
    *,
    target: str,
    min_target_drop: float = 0.0,
    max_target_drop: Optional[float] = None,
    min_other_up: float = 0.0,
    cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """DB QPS is primary; resource deltas must explain the change."""
    if is_none_target(target):
        up_details = []
        weighted_up_deltas = []
        for resource in ("cpu", "memory", "io"):
            candidate_value = safe_float(candidate_resources.get(resource), 0.0)
            reference_value = safe_float(reference_resources.get(resource), 0.0)
            delta_pct = pct_delta(candidate_value, reference_value) if reference_value > 0.0 else None
            up = resource_up(candidate_value, reference_value, min_other_up=min_other_up)
            weight = none_other_up_delta_weight(resource, cfg)
            weighted_delta_pct = safe_float(delta_pct, 0.0) * weight if delta_pct is not None else None
            if weighted_delta_pct is not None:
                weighted_up_deltas.append(weighted_delta_pct)
            up_details.append(
                {
                    "resource": resource,
                    "candidate": candidate_value,
                    "reference": reference_value,
                    "delta_pct": delta_pct,
                    "delta_weight": weight,
                    "weighted_delta_pct": weighted_delta_pct,
                    "up": up,
                }
            )
        up_resources = [item["resource"] for item in up_details if item.get("up")]
        policy = acceptance_policy_for_target(cfg or {}, "none")
        configured_required = policy.get("none_required_up_resources")
        required_up_resources = (
            [
                str(resource).strip().lower()
                for resource in configured_required
                if str(resource).strip().lower() in {"cpu", "memory", "io"}
            ]
            if isinstance(configured_required, list)
            else []
        )
        missing_required_up_resources = sorted(set(required_up_resources) - set(up_resources))
        require_resource_up = to_bool(policy.get("none_require_resource_up"), True)
        up_ok = (
            (not missing_required_up_resources if required_up_resources else bool(up_resources))
            if require_resource_up
            else True
        )
        return {
            "target_direction_ok": True,
            "other_up_ok": up_ok,
            "overall_up_ok": up_ok,
            "other_up_details": up_details,
            "other_up_resources": up_resources,
            "required_other_up_resources": required_up_resources,
            "missing_required_other_up_resources": missing_required_up_resources,
            "require_resource_up": require_resource_up,
            "raw_best_other_up_delta_pct": max(
                (
                    safe_float(item.get("delta_pct"), float("-inf"))
                    for item in up_details
                    if item.get("delta_pct") is not None
                ),
                default=None,
            ),
            "best_other_up_delta_pct": max(weighted_up_deltas, default=None),
        }

    candidate_target = safe_float(candidate_resources.get(target), math.inf)
    reference_target = safe_float(reference_resources.get(target), math.inf)
    policy = acceptance_policy_for_target(cfg or {}, target)
    require_other_resource_up = to_bool(policy.get("require_other_resource_up"), True)
    target_drop = (
        (reference_target - candidate_target) / reference_target
        if math.isfinite(reference_target) and reference_target > 0.0 and math.isfinite(candidate_target)
        else math.nan
    )
    target_direction_ok = bool(
        math.isfinite(target_drop)
        and target_drop >= min_target_drop
        and (max_target_drop is None or target_drop <= max_target_drop)
    )
    other_details = []
    other_up_ok = False
    for resource in explanation_other_resources(target, cfg):
        candidate_value = safe_float(candidate_resources.get(resource), 0.0)
        reference_value = safe_float(reference_resources.get(resource), 0.0)
        delta_pct = pct_delta(candidate_value, reference_value) if reference_value > 0.0 else None
        up = resource_up(candidate_value, reference_value, min_other_up=min_other_up)
        other_up_ok = other_up_ok or up
        other_details.append(
            {
                "resource": resource,
                "candidate": candidate_value,
                "reference": reference_value,
                "delta_pct": delta_pct,
                "up": up,
            }
        )
    if not require_other_resource_up:
        other_up_ok = True
    return {
        "target_direction_ok": target_direction_ok,
        "target_drop_ratio": target_drop if math.isfinite(target_drop) else None,
        "target_drop_pct": target_drop * 100.0 if math.isfinite(target_drop) else None,
        "min_target_drop_pct": min_target_drop * 100.0,
        "max_target_drop_pct": max_target_drop * 100.0 if max_target_drop is not None else None,
        "other_up_ok": other_up_ok,
        "require_other_resource_up": require_other_resource_up,
        "overall_up_ok": False,
        "other_up_details": other_details,
        "other_up_resources": [item["resource"] for item in other_details if item.get("up")],
        "best_other_up_delta_pct": max(
            (
                safe_float(item.get("delta_pct"), float("-inf"))
                for item in other_details
                if item.get("delta_pct") is not None
            ),
            default=None,
        ),
    }


def resource_band_value(
    validation: Dict[str, Any],
    resource: str,
    capacity: Dict[str, Any],
) -> Tuple[float, str]:
    resources = validation.get("resources") or {}
    resource_stats = validation.get("resource_stats") or {}
    if resource == "cpu":
        value = safe_float(resources.get("cpu"), math.nan)
        max_cpu = safe_float(capacity.get("cpu_cores"), math.nan)
        if not math.isfinite(max_cpu) or max_cpu <= 0.0:
            max_cpu = safe_float(resource_stats.get("max_cpu_cores"), math.nan)
        if not math.isfinite(max_cpu) or max_cpu <= 0.0:
            max_cpu = float(cpuset_cpu_count((validation.get("worker") or {}).get("cpuset_cpus")))
        return ((value / max_cpu * 100.0) if math.isfinite(value) and max_cpu > 0.0 else math.nan, "pct")
    if resource == "memory":
        value = safe_float(resources.get("memory"), math.nan)
        memory_capacity_gib = safe_float(capacity.get("memory_gib"), math.nan)
        return (
            (value / memory_capacity_gib * 100.0)
            if math.isfinite(value) and memory_capacity_gib > 0.0
            else math.nan,
            "pct",
        )
    if resource == "io":
        value = safe_float(resources.get("io"), math.nan)
        io_capacity_mib_s = safe_float(capacity.get("io_mib_s"), math.nan)
        if math.isfinite(io_capacity_mib_s) and io_capacity_mib_s > 0.0:
            return (
                (value / io_capacity_mib_s * 100.0) if math.isfinite(value) else math.nan,
                "pct",
            )
        return value, "MiB/s"
    return math.nan, ""


def resource_band_details(
    validation: Dict[str, Any],
    cfg: Dict[str, Any],
    *,
    band_cfg: Dict[str, Any],
) -> Dict[str, Any]:
    policy = acceptance_policy_for_target(cfg, str(validation.get("target") or ""))
    capacity = policy.get("resource_capacity") if isinstance(policy.get("resource_capacity"), dict) else {}
    legacy_capacity = policy.get("none_resource_capacity") if isinstance(policy.get("none_resource_capacity"), dict) else {}
    capacity = {**legacy_capacity, **capacity}
    checks: List[Dict[str, Any]] = []
    rejects: List[str] = []
    for resource in ("cpu", "memory", "io"):
        lower = safe_float(band_cfg.get(f"{resource}_min_pct"), math.nan)
        upper = safe_float(band_cfg.get(f"{resource}_max_pct"), math.nan)
        if resource == "io":
            lower = safe_float(band_cfg.get("io_min_mib_s"), lower)
            upper = safe_float(band_cfg.get("io_max_mib_s"), upper)
        if not math.isfinite(lower) and not math.isfinite(upper):
            continue
        value, unit = resource_band_value(validation, resource, capacity)
        ok = math.isfinite(value)
        if ok and math.isfinite(lower):
            ok = value >= lower
        if ok and math.isfinite(upper):
            ok = value <= upper
        check = {
            "resource": resource,
            "value": value,
            "unit": unit,
            "floor": lower if math.isfinite(lower) else None,
            "ceiling": upper if math.isfinite(upper) else None,
            "ok": ok,
        }
        checks.append(check)
        if not ok:
            if not math.isfinite(value):
                rejects.append(f"{resource}_{unit or 'value'}_not_finite")
            elif math.isfinite(lower) and value < lower:
                rejects.append(f"{resource}_{unit}_below_{lower:g}")
            elif math.isfinite(upper) and value > upper:
                rejects.append(f"{resource}_{unit}_above_{upper:g}")
    return {"enabled": bool(checks), "ok": not rejects, "checks": checks, "rejects": rejects}


def target_baseline_band_details(
    validation: Dict[str, Any],
    cfg: Dict[str, Any],
    target: str,
) -> Dict[str, Any]:
    """Require a bottleneck baseline to start inside its configured fair-strength band."""
    policy = acceptance_policy_for_target(cfg, target)
    bands = policy.get("target_baseline_band") if isinstance(policy.get("target_baseline_band"), dict) else {}
    target_cfg = bands.get(target) if isinstance(bands.get(target), dict) else {}
    if not target_cfg:
        return {"enabled": False, "ok": True, "checks": [], "rejects": []}
    band_cfg: Dict[str, Any] = {}
    for key in ("min_pct", "max_pct", "min_mib_s", "max_mib_s"):
        if key in target_cfg:
            band_cfg[f"{target}_{key}"] = target_cfg.get(key)
    details = resource_band_details(validation, cfg, band_cfg=band_cfg)
    details["target"] = target
    return details


def none_resource_ceiling_details(validation: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Check the no-bottleneck resource band, with optional candidate floor enforcement."""
    policy = acceptance_policy_for_target(cfg, "none")
    ceilings = policy.get("none_resource_ceiling") if isinstance(policy.get("none_resource_ceiling"), dict) else {}
    baseline_ceilings = (
        policy.get("none_baseline_resource_ceiling")
        if isinstance(policy.get("none_baseline_resource_ceiling"), dict)
        else {}
    )
    if validation_is_baseline(validation) and baseline_ceilings:
        ceilings = baseline_ceilings
    floors = policy.get("none_resource_floor") if isinstance(policy.get("none_resource_floor"), dict) else {}
    if not validation_is_baseline(validation) and not to_bool(
        policy.get("none_candidate_enforce_resource_floor"),
        True,
    ):
        floors = {}
    band_cfg: Dict[str, Any] = {}
    for resource in ("cpu", "memory"):
        if f"{resource}_pct" in floors:
            band_cfg[f"{resource}_min_pct"] = floors.get(f"{resource}_pct")
        if f"{resource}_pct" in ceilings:
            band_cfg[f"{resource}_max_pct"] = ceilings.get(f"{resource}_pct")
    if "io_mib_s" in floors:
        band_cfg["io_min_mib_s"] = floors.get("io_mib_s")
    if "io_mib_s" in ceilings:
        band_cfg["io_max_mib_s"] = ceilings.get("io_mib_s")
    if not band_cfg:
        return {"enabled": False, "ok": True, "checks": [], "rejects": []}
    return resource_band_details(validation, cfg, band_cfg=band_cfg)


def none_sweet_spot_details(validation: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    """For no-bottleneck stages, require at least N resources to land in their sweet band."""
    policy = acceptance_policy_for_target(cfg, "none")
    enabled = to_bool(policy.get("none_require_any_sweet_spot"), True)
    if not enabled:
        return {"enabled": False, "ok": True, "checks": [], "rejects": [], "passed_resources": []}
    sweet = policy.get("target_sweet_spot") if isinstance(policy.get("target_sweet_spot"), dict) else {}
    min_count = max(1, safe_int(policy.get("none_min_sweet_resources"), 1))
    checks: List[Dict[str, Any]] = []
    passed_resources: List[str] = []
    resource_rejects: List[str] = []
    for resource in ("cpu", "memory", "io"):
        target_cfg = sweet.get(resource) if isinstance(sweet.get(resource), dict) else {}
        if not target_cfg:
            continue
        band_cfg = {f"{resource}_{key}": value for key, value in target_cfg.items()}
        details = resource_band_details(validation, cfg, band_cfg=band_cfg)
        for check in details.get("checks") or []:
            checks.append(check)
            if check.get("ok"):
                passed_resources.append(str(check.get("resource") or resource))
        resource_rejects.extend(str(item) for item in (details.get("rejects") or []))
    if not checks:
        return {"enabled": False, "ok": True, "checks": [], "rejects": [], "passed_resources": []}
    ok = len(set(passed_resources)) >= min_count
    rejects = [] if ok else [f"none_sweet_resources_below_{min_count}"]
    if not ok:
        rejects.extend(resource_rejects)
    return {
        "enabled": True,
        "ok": ok,
        "checks": checks,
        "rejects": rejects,
        "passed_resources": sorted(set(passed_resources)),
        "required_count": min_count,
    }


def target_sweet_spot_details(validation: Dict[str, Any], cfg: Dict[str, Any], target: str) -> Dict[str, Any]:
    """For bottleneck stages, the target resource must land in the configured sweet spot."""
    if is_none_target(target):
        return none_sweet_spot_details(validation, cfg)
    policy = acceptance_policy_for_target(cfg, target)
    sweet = policy.get("target_sweet_spot") if isinstance(policy.get("target_sweet_spot"), dict) else {}
    target_cfg = sweet.get(target) if isinstance(sweet.get(target), dict) else {}
    if not target_cfg:
        return {"enabled": False, "ok": True, "checks": [], "rejects": []}
    return resource_band_details(validation, cfg, band_cfg={f"{target}_{k}": v for k, v in target_cfg.items()})


def bottleneck_other_sweet_spot_details(
    validation: Dict[str, Any],
    cfg: Dict[str, Any],
    target: str,
) -> Dict[str, Any]:
    """For bottleneck stages, require at least N non-target resources to reach their sweet band."""
    if is_none_target(target):
        return {"enabled": False, "ok": True, "checks": [], "rejects": [], "passed_resources": []}
    policy = acceptance_policy_for_target(cfg, target)
    min_count = safe_int(policy.get("target_min_other_sweet_resources"), 0)
    if min_count <= 0:
        return {"enabled": False, "ok": True, "checks": [], "rejects": [], "passed_resources": []}
    sweet = policy.get("target_sweet_spot") if isinstance(policy.get("target_sweet_spot"), dict) else {}
    checks: List[Dict[str, Any]] = []
    passed_resources: List[str] = []
    resource_rejects: List[str] = []
    for resource in ("cpu", "memory", "io"):
        if resource == target:
            continue
        target_cfg = sweet.get(resource) if isinstance(sweet.get(resource), dict) else {}
        if not target_cfg:
            continue
        details = resource_band_details(
            validation,
            cfg,
            band_cfg={f"{resource}_{key}": value for key, value in target_cfg.items()},
        )
        for check in details.get("checks") or []:
            checks.append(check)
            if check.get("ok"):
                passed_resources.append(str(check.get("resource") or resource))
        resource_rejects.extend(str(item) for item in (details.get("rejects") or []))
    if not checks:
        return {
            "enabled": True,
            "ok": False,
            "checks": [],
            "rejects": ["other_sweet_spot_not_configured"],
            "passed_resources": [],
            "required_count": min_count,
        }
    ok = len(set(passed_resources)) >= min_count
    rejects = [] if ok else [f"other_sweet_resources_below_{min_count}", *resource_rejects]
    return {
        "enabled": True,
        "ok": ok,
        "checks": checks,
        "rejects": rejects,
        "passed_resources": sorted(set(passed_resources)),
        "required_count": min_count,
    }


def apply_non_target_drop_guard_mode(
    rejects: Sequence[str],
    *,
    policy: Dict[str, Any],
    other_up_resources: Sequence[str],
    other_sweet_resources: Sequence[str],
) -> Tuple[List[str], Dict[str, Any]]:
    """Apply the configured scope for drops in resources other than the target."""
    mode = str(policy.get("non_target_drop_guard_mode") or "strict_each").strip().lower()
    original_rejects = list(rejects)
    up_sweet_resources = sorted(set(other_up_resources) & set(other_sweet_resources))
    waived = bool(
        original_rejects
        and mode == "require_any_up_sweet"
        and up_sweet_resources
    )
    return ([] if waived else original_rejects), {
        "mode": mode,
        "waived": waived,
        "original_rejects": original_rejects,
        "up_sweet_resources": up_sweet_resources,
    }


def compare_actual(
    candidate: Dict[str, Any],
    current_best: Dict[str, Any],
    baseline: Dict[str, Any],
    *,
    target: str,
    cfg: Dict[str, Any],
) -> Tuple[bool, str, Dict[str, Any]]:
    bpt = cfg.get("binary_plan_tuning") or {}
    _ = bpt
    policy = acceptance_policy_for_target(cfg, target)
    if to_bool(policy.get("compare_goal_against_baseline"), False):
        current_best = baseline
    min_other_up = safe_float(policy.get("min_other_up_pct"), 0.0) / 100.0
    min_target_drop = safe_float(policy.get("min_target_drop_pct"), 0.0) / 100.0
    max_target_drop_pct = safe_float(policy.get("max_target_drop_pct"), math.nan)
    max_target_drop = max_target_drop_pct / 100.0 if math.isfinite(max_target_drop_pct) else None
    min_qps_gain = safe_float(policy.get("min_db_qps_gain_pct"), 0.0) / 100.0
    non_target_drop_guard = safe_float(policy.get("non_target_drop_guard_pct"), 20.0) / 100.0
    p95_guard = safe_float(policy.get("p95_guard_regression_pct"), 50.0) / 100.0
    p99_guard = safe_float(policy.get("p99_guard_regression_pct"), 75.0) / 100.0
    hybench_qps_guard = safe_float(policy.get("hybench_qps_guard_drop_pct", 10.0), 10.0) / 100.0
    hybench_tps_guard = safe_float(policy.get("hybench_tps_guard_drop_pct", 10.0), 10.0) / 100.0

    cand_resources = candidate.get("resources") or {}
    best_resources = current_best.get("resources") or {}
    cand_target = safe_float(cand_resources.get(target), 0.0)
    best_target = safe_float(best_resources.get(target), 0.0)
    cand_qps = safe_float(candidate.get("db_qps"), 0.0)
    best_qps = safe_float(current_best.get("db_qps"), 0.0)

    explanation = resource_explanation_details(
        cand_resources,
        best_resources,
        target=target,
        min_target_drop=min_target_drop,
        max_target_drop=max_target_drop,
        min_other_up=min_other_up,
        cfg=cfg,
    )
    target_ok = bool(explanation.get("target_direction_ok"))
    other_up_ok = bool(explanation.get("other_up_ok"))
    qps_ok = cand_qps >= best_qps * (1.0 + min_qps_gain) if best_qps > 0 else cand_qps > 0
    other_sweet = bottleneck_other_sweet_spot_details(candidate, cfg, target)
    other_sweet_resources = set(other_sweet.get("passed_resources") or [])
    non_target_rejects: List[str] = []
    non_target_guard_waived_sweet_resources: List[str] = []
    if not is_none_target(target):
        for resource in ("cpu", "memory", "io"):
            if resource == target:
                continue
            cand_value = safe_float(cand_resources.get(resource), 0.0)
            best_value = safe_float(best_resources.get(resource), 0.0)
            if best_value > 0 and cand_value < best_value * (1.0 - non_target_drop_guard):
                if resource in other_sweet_resources:
                    non_target_guard_waived_sweet_resources.append(resource)
                    continue
                non_target_rejects.append(
                    f"{resource}_dropped_{(best_value - cand_value) / best_value * 100.0:.2f}%"
                )
    non_target_rejects, non_target_drop_guard_details = apply_non_target_drop_guard_mode(
        non_target_rejects,
        policy=policy,
        other_up_resources=explanation.get("other_up_resources") or [],
        other_sweet_resources=sorted(other_sweet_resources),
    )
    p95_ok = latency_guard_ok(candidate.get("p95_ms"), baseline.get("p95_ms"), p95_guard)
    p99_ok = latency_guard_ok(candidate.get("p99_ms"), baseline.get("p99_ms"), p99_guard)
    cand_hybench = candidate.get("hybench") or {}
    best_hybench = current_best.get("hybench") or {}
    cand_hybench_qps = safe_float(cand_hybench.get("xp_qps"), 0.0)
    best_hybench_qps = safe_float(best_hybench.get("xp_qps"), 0.0)
    cand_hybench_tps = safe_float(cand_hybench.get("xp_tps"), 0.0)
    best_hybench_tps = safe_float(best_hybench.get("xp_tps"), 0.0)
    hybench_qps_ok = (
        cand_hybench_qps >= best_hybench_qps * (1.0 - hybench_qps_guard)
        if best_hybench_qps > 0.0 and cand_hybench_qps > 0.0
        else True
    )
    hybench_tps_ok = (
        cand_hybench_tps >= best_hybench_tps * (1.0 - hybench_tps_guard)
        if best_hybench_tps > 0.0 and cand_hybench_tps > 0.0
        else True
    )
    status_ok = candidate.get("status") == "ok"
    none_ceiling = none_resource_ceiling_details(candidate, cfg) if is_none_target(target) else {
        "enabled": False,
        "ok": True,
        "checks": [],
        "rejects": [],
    }
    target_sweet = target_sweet_spot_details(candidate, cfg, target)

    details = {
        "target_resource": target,
        "candidate_target": cand_target,
        "best_target": best_target,
        "target_delta_pct": pct_delta(cand_target, best_target),
        "target_drop_pct": explanation.get("target_drop_pct"),
        "min_target_drop_pct": explanation.get("min_target_drop_pct"),
        "max_target_drop_pct": explanation.get("max_target_drop_pct"),
        "candidate_db_qps": cand_qps,
        "best_db_qps": best_qps,
        "db_qps_delta_pct": pct_delta(cand_qps, best_qps),
        "target_ok": target_ok,
        "resource_explanation_ok": bool(target_ok and other_up_ok),
        "other_up_ok": other_up_ok,
        "other_up_details": explanation.get("other_up_details") or [],
        "other_up_resources": explanation.get("other_up_resources") or [],
        "required_other_up_resources": explanation.get("required_other_up_resources") or [],
        "missing_required_other_up_resources": explanation.get("missing_required_other_up_resources") or [],
        "best_other_up_delta_pct": explanation.get("best_other_up_delta_pct"),
        "db_qps_ok": qps_ok,
        "non_target_ok": not non_target_rejects,
        "non_target_rejects": non_target_rejects,
        "non_target_drop_guard": non_target_drop_guard_details,
        "non_target_guard_waived_sweet_resources": sorted(non_target_guard_waived_sweet_resources),
        "p95_guard_ok": p95_ok,
        "p99_guard_ok": p99_ok,
        "candidate_hybench_xp_qps": cand_hybench_qps,
        "best_hybench_xp_qps": best_hybench_qps,
        "hybench_xp_qps_delta_pct": pct_delta(cand_hybench_qps, best_hybench_qps),
        "hybench_qps_guard_ok": hybench_qps_ok,
        "candidate_hybench_xp_tps": cand_hybench_tps,
        "best_hybench_xp_tps": best_hybench_tps,
        "hybench_xp_tps_delta_pct": pct_delta(cand_hybench_tps, best_hybench_tps),
        "hybench_tps_guard_ok": hybench_tps_ok,
        "status_ok": status_ok,
        "none_resource_ceiling_ok": bool(none_ceiling.get("ok")),
        "none_resource_ceiling": none_ceiling,
        "target_sweet_spot_ok": bool(target_sweet.get("ok")),
        "target_sweet_spot": target_sweet,
        "other_sweet_spot_ok": bool(other_sweet.get("ok")),
        "other_sweet_spot": other_sweet,
    }
    if not status_ok:
        return False, "workload_failed", details
    if not none_ceiling.get("ok"):
        return False, "none_resource_ceiling_failed", details
    if not qps_ok:
        return False, "db_qps_not_higher", details
    if not hybench_qps_ok or not hybench_tps_ok:
        return False, "hybench_qps_tps_guard_failed", details
    if not p95_ok or not p99_ok:
        return False, "latency_guard_failed", details
    if not target_sweet.get("ok"):
        return False, "target_not_in_sweet_spot", details
    if not other_sweet.get("ok"):
        return False, "non_target_sweet_spot_not_reached", details
    if not target_ok:
        drop_pct = safe_float(explanation.get("target_drop_pct"), math.nan)
        if math.isfinite(drop_pct) and drop_pct < min_target_drop * 100.0:
            return False, "target_resource_drop_below_min", details
        if max_target_drop is not None and math.isfinite(drop_pct) and drop_pct > max_target_drop * 100.0:
            return False, "target_resource_drop_above_max", details
        return False, "target_resource_drop_outside_band", details
    if not other_up_ok:
        return False, "resource_direction_not_explainable", details
    if non_target_rejects:
        return False, "non_target_resource_dropped", details
    return True, "accepted_actual_target_drop_and_db_qps_gain", details


def compare_against_baseline_goal(
    candidate: Dict[str, Any],
    baseline: Dict[str, Any],
    *,
    target: str,
    cfg: Dict[str, Any],
) -> Tuple[bool, str, Dict[str, Any]]:
    """Stricter report-time selection against baseline, not against current_best."""
    bpt = cfg.get("binary_plan_tuning") or {}
    _ = bpt
    policy = acceptance_policy_for_target(cfg, target)
    min_target_drop = safe_float(policy.get("min_target_drop_pct"), 0.0) / 100.0
    max_target_drop_pct = safe_float(policy.get("max_target_drop_pct"), math.nan)
    max_target_drop = max_target_drop_pct / 100.0 if math.isfinite(max_target_drop_pct) else None
    min_other_up = safe_float(policy.get("min_other_up_pct"), 0.0) / 100.0
    min_qps_gain = safe_float(policy.get("min_db_qps_gain_pct"), 0.0) / 100.0
    non_target_drop_guard = safe_float(policy.get("non_target_drop_guard_pct"), 20.0) / 100.0
    p95_guard = safe_float(policy.get("p95_guard_regression_pct"), 50.0) / 100.0
    p99_guard = safe_float(policy.get("p99_guard_regression_pct"), 75.0) / 100.0
    hybench_qps_guard = safe_float(policy.get("hybench_qps_guard_drop_pct", 10.0), 10.0) / 100.0
    hybench_tps_guard = safe_float(policy.get("hybench_tps_guard_drop_pct", 10.0), 10.0) / 100.0

    cand_resources = candidate.get("resources") or {}
    baseline_resources = baseline.get("resources") or {}
    cand_target = safe_float(cand_resources.get(target), 0.0)
    baseline_target = safe_float(baseline_resources.get(target), 0.0)
    cand_qps = safe_float(candidate.get("db_qps"), 0.0)
    baseline_qps = safe_float(baseline.get("db_qps"), 0.0)

    explanation = resource_explanation_details(
        cand_resources,
        baseline_resources,
        target=target,
        min_target_drop=min_target_drop,
        max_target_drop=max_target_drop,
        min_other_up=min_other_up,
        cfg=cfg,
    )
    target_ok = bool(explanation.get("target_direction_ok"))
    other_up_ok = bool(explanation.get("other_up_ok"))
    qps_ok = cand_qps >= baseline_qps * (1.0 + min_qps_gain) if baseline_qps > 0 else cand_qps > 0
    other_sweet = bottleneck_other_sweet_spot_details(candidate, cfg, target)
    other_sweet_resources = set(other_sweet.get("passed_resources") or [])
    non_target_rejects: List[str] = []
    non_target_guard_waived_sweet_resources: List[str] = []
    if not is_none_target(target):
        for resource in ("cpu", "memory", "io"):
            if resource == target:
                continue
            cand_value = safe_float(cand_resources.get(resource), 0.0)
            baseline_value = safe_float(baseline_resources.get(resource), 0.0)
            if baseline_value > 0 and cand_value < baseline_value * (1.0 - non_target_drop_guard):
                if resource in other_sweet_resources:
                    non_target_guard_waived_sweet_resources.append(resource)
                    continue
                non_target_rejects.append(
                    f"{resource}_dropped_{(baseline_value - cand_value) / baseline_value * 100.0:.2f}%"
                )
    non_target_rejects, non_target_drop_guard_details = apply_non_target_drop_guard_mode(
        non_target_rejects,
        policy=policy,
        other_up_resources=explanation.get("other_up_resources") or [],
        other_sweet_resources=sorted(other_sweet_resources),
    )
    p95_ok = latency_guard_ok(candidate.get("p95_ms"), baseline.get("p95_ms"), p95_guard)
    p99_ok = latency_guard_ok(candidate.get("p99_ms"), baseline.get("p99_ms"), p99_guard)
    cand_hybench = candidate.get("hybench") or {}
    base_hybench = baseline.get("hybench") or {}
    cand_hybench_qps = safe_float(cand_hybench.get("xp_qps"), 0.0)
    base_hybench_qps = safe_float(base_hybench.get("xp_qps"), 0.0)
    cand_hybench_tps = safe_float(cand_hybench.get("xp_tps"), 0.0)
    base_hybench_tps = safe_float(base_hybench.get("xp_tps"), 0.0)
    hybench_qps_ok = (
        cand_hybench_qps >= base_hybench_qps * (1.0 - hybench_qps_guard)
        if base_hybench_qps > 0.0 and cand_hybench_qps > 0.0
        else True
    )
    hybench_tps_ok = (
        cand_hybench_tps >= base_hybench_tps * (1.0 - hybench_tps_guard)
        if base_hybench_tps > 0.0 and cand_hybench_tps > 0.0
        else True
    )
    status_ok = candidate.get("status") == "ok"
    none_ceiling = none_resource_ceiling_details(candidate, cfg) if is_none_target(target) else {
        "enabled": False,
        "ok": True,
        "checks": [],
        "rejects": [],
    }
    target_sweet = target_sweet_spot_details(candidate, cfg, target)

    details = {
        "target_resource": target,
        "candidate_target": cand_target,
        "baseline_target": baseline_target,
        "target_delta_pct_vs_baseline": pct_delta(cand_target, baseline_target),
        "target_drop_pct_vs_baseline": explanation.get("target_drop_pct"),
        "min_target_drop_pct": explanation.get("min_target_drop_pct"),
        "max_target_drop_pct": explanation.get("max_target_drop_pct"),
        "candidate_db_qps": cand_qps,
        "baseline_db_qps": baseline_qps,
        "db_qps_delta_pct_vs_baseline": pct_delta(cand_qps, baseline_qps),
        "target_ok": target_ok,
        "resource_explanation_ok": bool(target_ok and other_up_ok),
        "other_up_ok": other_up_ok,
        "other_up_details": explanation.get("other_up_details") or [],
        "other_up_resources": explanation.get("other_up_resources") or [],
        "required_other_up_resources": explanation.get("required_other_up_resources") or [],
        "missing_required_other_up_resources": explanation.get("missing_required_other_up_resources") or [],
        "best_other_up_delta_pct": explanation.get("best_other_up_delta_pct"),
        "db_qps_ok": qps_ok,
        "non_target_ok": not non_target_rejects,
        "non_target_rejects": non_target_rejects,
        "non_target_drop_guard": non_target_drop_guard_details,
        "non_target_guard_waived_sweet_resources": sorted(non_target_guard_waived_sweet_resources),
        "p95_guard_ok": p95_ok,
        "p99_guard_ok": p99_ok,
        "candidate_hybench_xp_qps": cand_hybench_qps,
        "baseline_hybench_xp_qps": base_hybench_qps,
        "hybench_xp_qps_delta_pct_vs_baseline": pct_delta(cand_hybench_qps, base_hybench_qps),
        "hybench_qps_guard_ok": hybench_qps_ok,
        "candidate_hybench_xp_tps": cand_hybench_tps,
        "baseline_hybench_xp_tps": base_hybench_tps,
        "hybench_xp_tps_delta_pct_vs_baseline": pct_delta(cand_hybench_tps, base_hybench_tps),
        "hybench_tps_guard_ok": hybench_tps_ok,
        "status_ok": status_ok,
        "none_resource_ceiling_ok": bool(none_ceiling.get("ok")),
        "none_resource_ceiling": none_ceiling,
        "target_sweet_spot_ok": bool(target_sweet.get("ok")),
        "target_sweet_spot": target_sweet,
        "other_sweet_spot_ok": bool(other_sweet.get("ok")),
        "other_sweet_spot": other_sweet,
    }
    if not status_ok:
        return False, "workload_failed", details
    if not none_ceiling.get("ok"):
        return False, "none_resource_ceiling_failed_vs_baseline", details
    if not qps_ok:
        return False, "db_qps_not_higher_than_baseline", details
    if not hybench_qps_ok or not hybench_tps_ok:
        return False, "hybench_qps_tps_guard_failed_vs_baseline", details
    if not p95_ok or not p99_ok:
        return False, "latency_guard_failed_vs_baseline", details
    if not target_sweet.get("ok"):
        return False, "target_not_in_sweet_spot_vs_baseline", details
    if not other_sweet.get("ok"):
        return False, "non_target_sweet_spot_not_reached_vs_baseline", details
    if not target_ok:
        drop_pct = safe_float(explanation.get("target_drop_pct"), math.nan)
        if math.isfinite(drop_pct) and drop_pct < min_target_drop * 100.0:
            return False, "target_resource_drop_below_min_vs_baseline", details
        if max_target_drop is not None and math.isfinite(drop_pct) and drop_pct > max_target_drop * 100.0:
            return False, "target_resource_drop_above_max_vs_baseline", details
        return False, "target_resource_drop_outside_band_vs_baseline", details
    if not other_up_ok:
        return False, "resource_direction_not_explainable_vs_baseline", details
    if non_target_rejects:
        return False, "non_target_resource_dropped_vs_baseline", details
    if not hybench_qps_ok or not hybench_tps_ok:
        return False, "hybench_qps_tps_guard_failed_vs_baseline", details
    if not p95_ok or not p99_ok:
        return False, "latency_guard_failed_vs_baseline", details
    return True, "accepted_baseline_goal", details


def range_anchor_details(
    validation: Dict[str, Any],
    baseline: Dict[str, Any],
    *,
    target: str,
    cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """Real replay metrics used only to choose the next binary-search half."""
    bpt = cfg.get("binary_plan_tuning") or {}
    _ = bpt
    policy = acceptance_policy_for_target(cfg, target)
    min_other_up = safe_float(policy.get("min_other_up_pct"), 0.0) / 100.0
    min_qps_gain = safe_float(policy.get("min_db_qps_gain_pct"), 0.0) / 100.0
    hybench_qps_guard = safe_float(policy.get("hybench_qps_guard_drop_pct", 10.0), 10.0) / 100.0
    hybench_tps_guard = safe_float(policy.get("hybench_tps_guard_drop_pct", 10.0), 10.0) / 100.0
    p95_guard = safe_float(policy.get("p95_guard_regression_pct", 75.0), 75.0) / 100.0
    p99_guard = safe_float(policy.get("p99_guard_regression_pct", 100.0), 100.0) / 100.0

    resources = validation.get("resources") or {}
    baseline_resources = baseline.get("resources") or {}
    target_value = 0.0 if is_none_target(target) else safe_float(resources.get(target), math.inf)
    baseline_target = 0.0 if is_none_target(target) else safe_float(baseline_resources.get(target), math.inf)
    db_qps = safe_float(validation.get("db_qps"), 0.0)
    baseline_db_qps = safe_float(baseline.get("db_qps"), 0.0)
    hybench = validation.get("hybench") or {}
    baseline_hybench = baseline.get("hybench") or {}
    xp_qps = safe_float(hybench.get("xp_qps"), 0.0)
    baseline_xp_qps = safe_float(baseline_hybench.get("xp_qps"), 0.0)
    xp_tps = safe_float(hybench.get("xp_tps"), 0.0)
    baseline_xp_tps = safe_float(baseline_hybench.get("xp_tps"), 0.0)
    p95_ms = safe_float(validation.get("p95_ms"), 0.0)
    baseline_p95_ms = safe_float(baseline.get("p95_ms"), 0.0)
    p99_ms = safe_float(validation.get("p99_ms"), 0.0)
    baseline_p99_ms = safe_float(baseline.get("p99_ms"), 0.0)
    resource_stats = validation.get("resource_stats") or {}

    status_ok = validation.get("status") == "ok"
    monitor_ok = resource_stats.get("resource_monitor_ok") is not False
    target_lower_than_baseline = True if is_none_target(target) else (
        math.isfinite(target_value)
        and math.isfinite(baseline_target)
        and baseline_target > 0.0
        and target_value < baseline_target
    )
    explanation = resource_explanation_details(
        resources,
        baseline_resources,
        target=target,
        min_other_up=min_other_up,
        cfg=cfg,
    )
    none_ceiling = none_resource_ceiling_details(validation, cfg) if is_none_target(target) else {
        "enabled": False,
        "ok": True,
        "checks": [],
        "rejects": [],
    }
    target_sweet = target_sweet_spot_details(validation, cfg, target)
    other_sweet = bottleneck_other_sweet_spot_details(validation, cfg, target)
    db_qps_not_lower = db_qps >= baseline_db_qps * (1.0 + min_qps_gain) if baseline_db_qps > 0.0 else db_qps > 0.0
    xp_qps_guard_ok = (
        xp_qps >= baseline_xp_qps * (1.0 - hybench_qps_guard)
        if baseline_xp_qps > 0.0 and xp_qps > 0.0
        else True
    )
    xp_tps_guard_ok = (
        xp_tps >= baseline_xp_tps * (1.0 - hybench_tps_guard)
        if baseline_xp_tps > 0.0 and xp_tps > 0.0
        else True
    )
    p95_guard_ok = latency_guard_ok(p95_ms, baseline_p95_ms, p95_guard)
    p99_guard_ok = latency_guard_ok(p99_ms, baseline_p99_ms, p99_guard)
    guard_pass_count = sum(
        1
        for item in (db_qps_not_lower, xp_qps_guard_ok, xp_tps_guard_ok, p95_guard_ok, p99_guard_ok)
        if item
    )
    target_sweet_distance = 0.0
    target_sweet_distance_unit = ""
    target_sweet_checks = target_sweet.get("checks") if isinstance(target_sweet.get("checks"), list) else []
    if target_sweet_checks:
        distances: List[Tuple[float, float, str]] = []
        for check in target_sweet_checks:
            if not isinstance(check, dict):
                continue
            value = safe_float(check.get("value"), math.nan)
            floor = safe_float(check.get("floor"), math.nan)
            ceiling = safe_float(check.get("ceiling"), math.nan)
            if not math.isfinite(value):
                continue
            distance = 0.0
            if math.isfinite(floor) and value < floor:
                distance = floor - value
            elif math.isfinite(ceiling) and value > ceiling:
                distance = value - ceiling
            unit = str(check.get("unit") or "")
            if is_none_target(target):
                if unit == "pct":
                    max_distance = safe_float(policy.get("range_anchor_near_miss_max_pct"), 5.0)
                elif unit == "MiB/s":
                    max_distance = safe_float(policy.get("range_anchor_near_miss_max_mib_s"), 10.0)
                else:
                    max_distance = safe_float(policy.get("range_anchor_near_miss_max_distance"), 5.0)
                normalized = distance / max(max_distance, 1e-9)
            else:
                normalized = distance
            distances.append((normalized, distance, unit))
        if distances:
            if is_none_target(target):
                _, target_sweet_distance, target_sweet_distance_unit = min(distances)
            else:
                _, target_sweet_distance, target_sweet_distance_unit = max(distances)
    resource_explanation_ok = bool(explanation.get("target_direction_ok") and explanation.get("other_up_ok"))
    usable_rejects: List[str] = []
    if not status_ok:
        usable_rejects.append("status_not_ok")
    if not monitor_ok:
        usable_rejects.append("resource_monitor_not_ok")
    if not none_ceiling.get("ok"):
        usable_rejects.append("none_resource_ceiling_failed")
    if not target_sweet.get("ok"):
        usable_rejects.append("target_not_in_sweet_spot")
    if not is_none_target(target) and (not math.isfinite(target_value) or target_value <= 0.0):
        usable_rejects.append("target_value_invalid")
    if not target_lower_than_baseline:
        usable_rejects.append("target_not_lower_than_baseline")
    if not resource_explanation_ok:
        usable_rejects.append("resource_direction_not_explainable")
    if not db_qps_not_lower:
        usable_rejects.append("db_qps_guard_failed")
    if not xp_qps_guard_ok:
        usable_rejects.append("hybench_qps_guard_failed")
    if not xp_tps_guard_ok:
        usable_rejects.append("hybench_tps_guard_failed")
    if not p95_guard_ok:
        usable_rejects.append("p95_guard_failed")
    if not p99_guard_ok:
        usable_rejects.append("p99_guard_failed")
    usable = not usable_rejects
    return {
        "usable": usable,
        "target_resource": target,
        "status_ok": status_ok,
        "resource_monitor_ok": monitor_ok,
        "target_lower_than_baseline": target_lower_than_baseline,
        "target_value": target_value,
        "baseline_target_value": baseline_target,
        "target_delta_pct_vs_baseline": pct_delta(target_value, baseline_target) if baseline_target > 0.0 else None,
        "resource_explanation_ok": resource_explanation_ok,
        "usable_rejects": usable_rejects,
        "none_resource_ceiling_ok": bool(none_ceiling.get("ok")),
        "none_resource_ceiling": none_ceiling,
        "target_sweet_spot_ok": bool(target_sweet.get("ok")),
        "target_sweet_spot": target_sweet,
        "target_sweet_spot_distance": target_sweet_distance,
        "target_sweet_spot_distance_unit": target_sweet_distance_unit,
        "other_sweet_spot_ok": bool(other_sweet.get("ok")),
        "other_sweet_spot": other_sweet,
        "other_up_ok": bool(explanation.get("other_up_ok")),
        "other_up_details": explanation.get("other_up_details") or [],
        "other_up_resources": explanation.get("other_up_resources") or [],
        "best_other_up_delta_pct": explanation.get("best_other_up_delta_pct"),
        "db_qps": db_qps,
        "db_qps_delta_pct_vs_baseline": pct_delta(db_qps, baseline_db_qps) if baseline_db_qps > 0.0 else None,
        "db_qps_not_lower": db_qps_not_lower,
        "xp_qps": xp_qps,
        "xp_qps_delta_pct_vs_baseline": pct_delta(xp_qps, baseline_xp_qps) if baseline_xp_qps > 0.0 else None,
        "xp_qps_guard_ok": xp_qps_guard_ok,
        "xp_tps": xp_tps,
        "xp_tps_delta_pct_vs_baseline": pct_delta(xp_tps, baseline_xp_tps) if baseline_xp_tps > 0.0 else None,
        "xp_tps_guard_ok": xp_tps_guard_ok,
        "p95_ms": p95_ms,
        "p95_delta_pct_vs_baseline": pct_delta(p95_ms, baseline_p95_ms) if baseline_p95_ms > 0.0 else None,
        "p95_guard_ok": p95_guard_ok,
        "p99_ms": p99_ms,
        "p99_delta_pct_vs_baseline": pct_delta(p99_ms, baseline_p99_ms) if baseline_p99_ms > 0.0 else None,
        "p99_guard_ok": p99_guard_ok,
        "guard_pass_count": guard_pass_count,
        "label": validation.get("label"),
        "params": validation.get("params") or {},
        "resources": validation.get("resources") or {},
    }


def range_anchor_sort_key(details: Dict[str, Any]) -> Tuple[Any, ...]:
    if is_none_target(str(details.get("target_resource") or "")):
        other_up_ratio = safe_float(details.get("best_other_up_delta_pct"), 0.0) / 100.0
        p95_ms = safe_float(details.get("p95_ms"), math.inf)
        p99_ms = safe_float(details.get("p99_ms"), math.inf)
        return (
            1 if details.get("usable") else 0,
            1 if details.get("db_qps_not_lower") else 0,
            safe_float(details.get("db_qps"), 0.0),
            1 if details.get("resource_explanation_ok") else 0,
            other_up_ratio,
            -p95_ms,
            -p99_ms,
        )
    target_value = safe_float(details.get("target_value"), math.inf)
    baseline_target = safe_float(details.get("baseline_target_value"), math.inf)
    target_drop_ratio = (
        (baseline_target - target_value) / max(abs(baseline_target), 1e-9)
        if math.isfinite(baseline_target) and baseline_target > 0.0
        else 0.0
    )
    other_up_ratio = safe_float(details.get("best_other_up_delta_pct"), 0.0) / 100.0
    p95_ms = safe_float(details.get("p95_ms"), math.inf)
    p99_ms = safe_float(details.get("p99_ms"), math.inf)
    return (
        1 if details.get("usable") else 0,
        1 if details.get("db_qps_not_lower") else 0,
        safe_float(details.get("db_qps"), 0.0),
        1 if details.get("target_lower_than_baseline") else 0,
        1 if details.get("resource_explanation_ok") else 0,
        1 if details.get("other_sweet_spot_ok") else 0,
        target_drop_ratio,
        other_up_ratio,
        -p95_ms,
        -p99_ms,
    )


def range_anchor_near_miss_allowed(details: Dict[str, Any], cfg: Dict[str, Any], target: str) -> bool:
    """Allow a non-accepted near miss to continue narrowing, without treating it as success."""
    if is_none_target(target):
        if details.get("usable") or not details.get("status_ok") or not details.get("resource_monitor_ok"):
            return False
        if not details.get("none_resource_ceiling_ok"):
            return False
        if not details.get("resource_explanation_ok") or not details.get("db_qps_not_lower"):
            return False
        if not details.get("xp_qps_guard_ok") or not details.get("xp_tps_guard_ok"):
            return False
        if not details.get("p95_guard_ok") or not details.get("p99_guard_ok"):
            return False
        policy = acceptance_policy_for_target(cfg, target)
        unit = str(details.get("target_sweet_spot_distance_unit") or "")
        distance = safe_float(details.get("target_sweet_spot_distance"), math.inf)
        if not math.isfinite(distance):
            return False
        if unit == "pct":
            max_distance = safe_float(policy.get("range_anchor_near_miss_max_pct"), 5.0)
        elif unit == "MiB/s":
            max_distance = safe_float(policy.get("range_anchor_near_miss_max_mib_s"), 10.0)
        else:
            max_distance = safe_float(policy.get("range_anchor_near_miss_max_distance"), 5.0)
        return distance <= max_distance
    if details.get("usable") or not details.get("status_ok") or not details.get("resource_monitor_ok"):
        return False
    if not details.get("target_lower_than_baseline"):
        return False
    if not details.get("resource_explanation_ok") or not details.get("db_qps_not_lower"):
        return False
    if not details.get("xp_qps_guard_ok") or not details.get("xp_tps_guard_ok"):
        return False
    if not details.get("p95_guard_ok") or not details.get("p99_guard_ok"):
        return False
    target_value = safe_float(details.get("target_value"), math.inf)
    if not math.isfinite(target_value) or target_value <= 0.0:
        return False
    policy = acceptance_policy_for_target(cfg, target)
    unit = str(details.get("target_sweet_spot_distance_unit") or "")
    distance = safe_float(details.get("target_sweet_spot_distance"), math.inf)
    if not math.isfinite(distance):
        return False
    if unit == "pct":
        max_distance = safe_float(policy.get("range_anchor_near_miss_max_pct"), 5.0)
    elif unit == "MiB/s":
        max_distance = safe_float(policy.get("range_anchor_near_miss_max_mib_s"), 10.0)
    else:
        max_distance = safe_float(policy.get("range_anchor_near_miss_max_distance"), 5.0)
    return distance <= max_distance


def range_anchor_near_miss_sort_key(details: Dict[str, Any]) -> Tuple[Any, ...]:
    distance = safe_float(details.get("target_sweet_spot_distance"), math.inf)
    other_up_ratio = safe_float(details.get("best_other_up_delta_pct"), 0.0) / 100.0
    p95_ms = safe_float(details.get("p95_ms"), math.inf)
    p99_ms = safe_float(details.get("p99_ms"), math.inf)
    return (
        -distance,
        1 if details.get("target_lower_than_baseline") else 0,
        1 if details.get("resource_explanation_ok") else 0,
        1 if details.get("db_qps_not_lower") else 0,
        safe_int(details.get("guard_pass_count"), 0),
        other_up_ratio,
        safe_float(details.get("db_qps"), 0.0),
        -p95_ms,
        -p99_ms,
    )


def select_range_anchor(
    validations: Sequence[Dict[str, Any]],
    baseline: Dict[str, Any],
    *,
    target: str,
    cfg: Dict[str, Any],
    baseline_resolver: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    allow_near_miss: bool = False,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    best_validation: Optional[Dict[str, Any]] = None
    best_details: Optional[Dict[str, Any]] = None
    best_key: Optional[Tuple[Any, ...]] = None
    for validation in validations:
        reference_baseline = baseline_resolver(validation) if baseline_resolver else baseline
        details = range_anchor_details(validation, reference_baseline, target=target, cfg=cfg)
        if not details.get("usable"):
            if not allow_near_miss or not range_anchor_near_miss_allowed(details, cfg, target):
                continue
            key = range_anchor_near_miss_sort_key(details)
        else:
            key = range_anchor_sort_key(details)
        if best_key is None or key > best_key:
            best_key = key
            best_validation = validation
            best_details = details
    return best_validation, best_details


def accepted_best_sort_key(
    validation: Dict[str, Any],
    *,
    target: str,
) -> Tuple[Any, ...]:
    resources = validation.get("resources") or {}
    target_value = safe_float(resources.get(target), math.inf)
    p95_ms = safe_float(validation.get("p95_ms"), math.inf)
    p99_ms = safe_float(validation.get("p99_ms"), math.inf)
    decision = validation.get("decision") if isinstance(validation.get("decision"), dict) else {}
    db_qps = safe_float(validation.get("db_qps"), 0.0)
    explanation_ok = bool(decision.get("resource_explanation_ok", validation.get("resource_explanation_ok")))
    other_up_delta = safe_float(decision.get("best_other_up_delta_pct"), 0.0)
    if is_none_target(target):
        return (
            1 if validation.get("accepted") else 0,
            db_qps,
            1 if explanation_ok else 0,
            other_up_delta,
            -p95_ms,
            -p99_ms,
        )
    return (
        1 if validation.get("accepted") else 0,
        db_qps,
        1 if explanation_ok else 0,
        other_up_delta,
        -target_value,
        -p95_ms,
        -p99_ms,
    )


SMALL_LOOP_TOP1_ANCHOR_POLICY = "small_loop_top1_score_v1"
REAL_SCORE_B_ANCHOR_POLICY = "real_score_b_successful_topk_v2"
REAL_SCORE_C_ANCHOR_POLICY = "real_score_c_no_qps_topk_v1"
NESTED_RESOURCE_SEMANTIC_POLICY = "nested_inner_resource_outer_semantic_v2"


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def real_score_b_details(
    validation: Dict[str, Any],
    baseline: Dict[str, Any],
    *,
    target: str,
    cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """Compute score B and the real successful-candidate gate."""
    policy = acceptance_policy_for_target(cfg, target)
    min_drop = safe_float(policy.get("min_target_drop_pct"), 0.0)
    max_drop = safe_float(policy.get("max_target_drop_pct"), min_drop)
    min_qps_gain = safe_float(policy.get("min_db_qps_gain_pct"), 0.0)
    search_cfg = ((cfg.get("binary_plan_tuning") or {}).get("binary_search") or {})
    score_cfg = search_cfg.get("real_score_b") if isinstance(search_cfg.get("real_score_b"), dict) else {}
    resource_up_scale = max(safe_float(score_cfg.get("resource_up_scale"), 0.10), 1e-12)
    bonus_coefficient = safe_float(score_cfg.get("bonus_coefficient"), 0.02)
    io_up_penalty_coefficient = max(
        0.0,
        safe_float(score_cfg.get("io_up_penalty_coefficient"), 0.01),
    )

    baseline_qps = safe_float(baseline.get("db_qps"), math.nan)
    db_qps = safe_float(validation.get("db_qps"), math.nan)
    qps_gain = (
        (db_qps - baseline_qps) / baseline_qps
        if math.isfinite(db_qps) and math.isfinite(baseline_qps) and baseline_qps > 0.0
        else math.nan
    )
    resources = validation.get("resources") if isinstance(validation.get("resources"), dict) else {}
    baseline_resources = baseline.get("resources") if isinstance(baseline.get("resources"), dict) else {}

    target_sweet_upper: Optional[float] = None
    target_progress = 0.0
    target_drop_ratio: Optional[float] = None
    target_drop_pct: Optional[float] = None
    target_drop_gate = True
    target_metric_valid = True
    if not is_none_target(target):
        target_value = safe_float(resources.get(target), math.nan)
        baseline_target = safe_float(baseline_resources.get(target), math.nan)
        target_metric_valid = bool(
            math.isfinite(target_value)
            and math.isfinite(baseline_target)
            and baseline_target > 0.0
        )
        if target_metric_valid:
            target_drop_ratio = (baseline_target - target_value) / baseline_target
            target_drop_pct = target_drop_ratio * 100.0
            target_sweet_upper = baseline_target * (1.0 - min_drop / 100.0)
            progress_denominator = baseline_target - target_sweet_upper
            target_progress = (
                _clip((baseline_target - target_value) / progress_denominator, 0.0, 1.0)
                if progress_denominator > 0.0
                else 0.0
            )
            target_drop_gate = bool(min_drop <= target_drop_pct <= max_drop)

    resource_stats = validation.get("resource_stats") if isinstance(validation.get("resource_stats"), dict) else {}
    status_ok = str(validation.get("status") or "").lower() == "ok"
    monitor_ok = resource_stats.get("resource_monitor_ok") is not False
    metrics_valid = bool(
        status_ok
        and monitor_ok
        and math.isfinite(db_qps)
        and math.isfinite(qps_gain)
        and target_metric_valid
    )

    bonus_resources = (
        ("cpu", "memory", "io")
        if is_none_target(target)
        else tuple(resource for resource in ("cpu", "memory", "io") if resource != target)
    )
    resource_gain_ratios: Dict[str, Optional[float]] = {}
    bonus_r: Dict[str, Optional[float]] = {}
    resource_up_resources: List[str] = []
    for resource in bonus_resources:
        real_value = safe_float(resources.get(resource), math.nan)
        baseline_value = safe_float(baseline_resources.get(resource), math.nan)
        gain_ratio = (
            (real_value - baseline_value) / baseline_value
            if math.isfinite(real_value) and math.isfinite(baseline_value) and baseline_value > 0.0
            else None
        )
        resource_gain_ratios[resource] = gain_ratio
        bonus = (
            0.0
            if resource == "io" and target != "io" and gain_ratio is not None
            else (
                _clip(max(gain_ratio, 0.0) / resource_up_scale, 0.0, 1.0)
                if gain_ratio is not None
                else None
            )
        )
        bonus_r[resource] = bonus
        if gain_ratio is not None and gain_ratio > 0.0:
            resource_up_resources.append(resource)

    bonus_sum = sum(value for value in bonus_r.values() if value is not None)
    io_gain_ratio = resource_gain_ratios.get("io") if target != "io" else None
    io_up_penalty = (
        _clip(max(float(io_gain_ratio), 0.0) / resource_up_scale, 0.0, 1.0)
        if io_gain_ratio is not None
        else 0.0
    )
    qps_gate = bool(metrics_valid and qps_gain >= min_qps_gain / 100.0)
    resource_gate = bool(resource_up_resources) if is_none_target(target) else target_drop_gate
    successful = bool(metrics_valid and qps_gate and resource_gate)
    score_b = (
        qps_gain
        + target_progress
        + bonus_coefficient * bonus_sum
        - io_up_penalty_coefficient * io_up_penalty
        if metrics_valid
        else None
    )
    return {
        "policy": REAL_SCORE_B_ANCHOR_POLICY,
        "formula": (
            "qps_gain + target_progress + bonus_coefficient * sum(bonus_r) "
            "- io_up_penalty_coefficient * io_up_penalty"
        ),
        "qps_reward_mode": "uncapped_raw_gain",
        "target_reward_mode": "five_to_ten_percent_sweet_spot_plateau",
        "non_target_resource_reward_mode": (
            "cpu_memory_positive_only_with_weak_non_target_io_up_penalty"
        ),
        "valid": metrics_valid,
        "successful": successful,
        "feasible": successful,
        "status_ok": status_ok,
        "resource_monitor_ok": monitor_ok,
        "target_resource": target,
        "min_target_drop_pct": None if is_none_target(target) else min_drop,
        "max_target_drop_pct": None if is_none_target(target) else max_drop,
        "target_sweet_upper": target_sweet_upper,
        "target_progress": target_progress,
        "target_drop_ratio": target_drop_ratio,
        "target_drop_pct": target_drop_pct,
        "target_drop_gate": target_drop_gate if not is_none_target(target) else None,
        "min_db_qps_gain_pct": min_qps_gain,
        "db_qps": db_qps,
        "qps_gain": qps_gain,
        "db_qps_gain_pct": qps_gain * 100.0 if math.isfinite(qps_gain) else None,
        "qps_gate": qps_gate,
        "resource_gate": resource_gate,
        "resource_gain_ratios": resource_gain_ratios,
        "resource_up_resources": resource_up_resources,
        "resource_up_scale": resource_up_scale,
        "bonus_r": bonus_r,
        "bonus_sum": bonus_sum,
        "bonus_coefficient": bonus_coefficient,
        "io_gain_ratio": io_gain_ratio,
        "io_up_penalty": io_up_penalty,
        "io_up_penalty_coefficient": io_up_penalty_coefficient,
        "score_b": score_b,
        "p95_ms": safe_float(validation.get("p95_ms"), math.inf),
        "p99_ms": safe_float(validation.get("p99_ms"), math.inf),
        "candidate_index": safe_int(validation.get("candidate_index"), 0),
        "label": validation.get("label"),
        "params": validation.get("params") or {},
        "resources": resources,
    }


def real_score_b_sort_key(details: Dict[str, Any]) -> Tuple[Any, ...]:
    """Rank successful real candidates by score B and deterministic tie-breaks."""
    return (
        1 if details.get("successful") else 0,
        safe_float(details.get("score_b"), -math.inf),
        safe_float(details.get("qps_gain"), -math.inf),
        safe_float(details.get("target_progress"), -math.inf),
        -safe_float(details.get("p95_ms"), math.inf),
        -safe_float(details.get("p99_ms"), math.inf),
        -safe_int(details.get("candidate_index"), 0),
    )


def select_real_score_b_anchor(
    validations: Sequence[Dict[str, Any]],
    baseline: Dict[str, Any],
    *,
    target: str,
    cfg: Dict[str, Any],
    baseline_resolver: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Select argmax(score_B) from current-round successful candidates."""
    best_validation: Optional[Dict[str, Any]] = None
    best_details: Optional[Dict[str, Any]] = None
    best_key: Optional[Tuple[Any, ...]] = None
    for validation in validations:
        reference = baseline_resolver(validation) if baseline_resolver else baseline
        details = real_score_b_details(validation, reference, target=target, cfg=cfg)
        if not details.get("successful"):
            continue
        key = real_score_b_sort_key(details)
        if best_key is None or key > best_key:
            best_key = key
            best_validation = validation
            best_details = details
    return (
        copy.deepcopy(best_validation) if best_validation is not None else None,
        copy.deepcopy(best_details) if best_details is not None else None,
    )


def real_score_c_details(
    validation: Dict[str, Any],
    baseline: Dict[str, Any],
    *,
    target: str,
    cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """Compute C's real-only resource score without a QPS score or gate."""
    components = real_score_b_details(validation, baseline, target=target, cfg=cfg)
    search_cfg = ((cfg.get("binary_plan_tuning") or {}).get("binary_search") or {})
    score_cfg = search_cfg.get("real_score_c") if isinstance(search_cfg.get("real_score_c"), dict) else {}
    resource_up_scale = max(safe_float(score_cfg.get("resource_up_scale"), 0.10), 1e-12)
    bonus_coefficient = safe_float(score_cfg.get("bonus_coefficient"), 0.02)
    io_up_penalty_coefficient = max(
        0.0,
        safe_float(score_cfg.get("io_up_penalty_coefficient"), 0.01),
    )

    status_ok = bool(components.get("status_ok"))
    monitor_ok = bool(components.get("resource_monitor_ok"))
    target_metric_valid = bool(
        is_none_target(target) or components.get("target_drop_ratio") is not None
    )
    resource_gains = components.get("resource_gain_ratios") or {}
    bonus_r = {
        resource: (
            0.0
            if resource == "io" and target != "io"
            else _clip(max(safe_float(gain_ratio), 0.0) / resource_up_scale, 0.0, 1.0)
            if gain_ratio is not None
            else None
        )
        for resource, gain_ratio in resource_gains.items()
    }
    bonus_sum = sum(value for value in bonus_r.values() if value is not None)
    io_gain_ratio = resource_gains.get("io") if target != "io" else None
    io_up_penalty = (
        _clip(max(safe_float(io_gain_ratio), 0.0) / resource_up_scale, 0.0, 1.0)
        if io_gain_ratio is not None
        else 0.0
    )
    bonus_metric_valid = bool(
        not is_none_target(target)
        or any(value is not None for value in resource_gains.values())
    )
    metrics_valid = bool(status_ok and monitor_ok and target_metric_valid and bonus_metric_valid)
    resource_gate = bool(
        components.get("resource_up_resources")
        if is_none_target(target)
        else components.get("target_drop_gate")
    )
    successful = bool(metrics_valid and resource_gate)
    score_c = (
        safe_float(components.get("target_progress"), 0.0)
        + bonus_coefficient * bonus_sum
        - io_up_penalty_coefficient * io_up_penalty
        if metrics_valid
        else None
    )
    return {
        **components,
        "policy": REAL_SCORE_C_ANCHOR_POLICY,
        "formula": (
            "target_progress + bonus_coefficient * sum(bonus_r) "
            "- io_up_penalty_coefficient * io_up_penalty"
        ),
        "qps_reward_mode": "not_considered",
        "valid": metrics_valid,
        "successful": successful,
        "feasible": successful,
        "qps_considered": False,
        "qps_gate": None,
        "resource_gate": resource_gate,
        "resource_up_scale": resource_up_scale,
        "bonus_r": bonus_r,
        "bonus_sum": bonus_sum,
        "bonus_coefficient": bonus_coefficient,
        "io_gain_ratio": io_gain_ratio,
        "io_up_penalty": io_up_penalty,
        "io_up_penalty_coefficient": io_up_penalty_coefficient,
        "score_c": score_c,
    }


def real_score_c_sort_key(details: Dict[str, Any]) -> Tuple[Any, ...]:
    """Rank C candidates without any QPS-based component or tie-break."""
    return (
        1 if details.get("successful") else 0,
        safe_float(details.get("score_c"), -math.inf),
        safe_float(details.get("target_progress"), -math.inf),
        safe_float(details.get("bonus_sum"), -math.inf),
        -safe_float(details.get("p95_ms"), math.inf),
        -safe_float(details.get("p99_ms"), math.inf),
        -safe_int(details.get("candidate_index"), 0),
    )


def select_real_score_c_anchor(
    validations: Sequence[Dict[str, Any]],
    baseline: Dict[str, Any],
    *,
    target: str,
    cfg: Dict[str, Any],
    baseline_resolver: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Select C's real-only winner, preferring resource-gate successes.

    When no replay reaches the resource gate, the highest-scoring valid real
    replay remains the anchor. Small-loop scores never decide C's range.
    """
    best_validation: Optional[Dict[str, Any]] = None
    best_details: Optional[Dict[str, Any]] = None
    best_key: Optional[Tuple[Any, ...]] = None
    for validation in validations:
        reference = baseline_resolver(validation) if baseline_resolver else baseline
        details = real_score_c_details(validation, reference, target=target, cfg=cfg)
        if not details.get("valid"):
            continue
        key = real_score_c_sort_key(details)
        if best_key is None or key > best_key:
            best_key = key
            best_validation = validation
            best_details = details
    return (
        copy.deepcopy(best_validation) if best_validation is not None else None,
        copy.deepcopy(best_details) if best_details is not None else None,
    )


def inner_resource_score_details(
    validation: Dict[str, Any],
    baseline: Dict[str, Any],
    *,
    target: str,
    cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """Inner-loop score computed only from real resource measurements."""
    details = real_score_c_details(validation, baseline, target=target, cfg=cfg)
    return {
        **details,
        "policy": NESTED_RESOURCE_SEMANTIC_POLICY,
        "role": "inner_loop_real_resource_ranking",
        "qps_considered": False,
        "score": details.get("score_c"),
    }


def inner_resource_score_sort_key(details: Dict[str, Any]) -> Tuple[Any, ...]:
    """Rank inner candidates without QPS and without an acceptance gate."""
    return (
        safe_float(details.get("score"), -math.inf),
        safe_float(details.get("target_progress"), -math.inf),
        safe_float(details.get("bonus_sum"), -math.inf),
        -safe_float(details.get("p95_ms"), math.inf),
        -safe_float(details.get("p99_ms"), math.inf),
        -safe_int(details.get("candidate_index"), 0),
    )


def outer_semantic_acceptance_details(
    candidate: Dict[str, Any],
    reference: Dict[str, Any],
    baseline: Dict[str, Any],
    *,
    target: str,
    cfg: Dict[str, Any],
    consecutive_missed_outer_rounds: int = 0,
) -> Dict[str, Any]:
    """Outer-loop directional decision using QPS and real resource semantics."""
    search_cfg = ((cfg.get("binary_plan_tuning") or {}).get("binary_search") or {})
    nested_cfg = search_cfg.get("nested_loop") if isinstance(search_cfg.get("nested_loop"), dict) else {}
    outer_cfg = nested_cfg.get("outer_acceptance") if isinstance(nested_cfg.get("outer_acceptance"), dict) else {}
    acceptance_policy = acceptance_policy_for_target(cfg, target)
    static_min_qps_gain_ratio = safe_float(
        outer_cfg.get("min_db_qps_gain_pct"),
        safe_float(acceptance_policy.get("min_db_qps_gain_pct"), 0.1),
    ) / 100.0
    algorithm_cfg = cfg.get("algorithm_c") if isinstance(cfg.get("algorithm_c"), dict) else {}
    qps_noise_cfg = (
        algorithm_cfg.get("qps_noise_gate")
        if isinstance(algorithm_cfg.get("qps_noise_gate"), dict)
        else {}
    )
    qps_noise_required = qps_noise_cfg.get("enabled") is True
    qps_noise_model = (
        candidate.get("qps_noise_model")
        if isinstance(candidate.get("qps_noise_model"), dict)
        else {}
    )
    qps_noise_ready = qps_noise_model.get("ready") is True
    qps_reference_mode = str(
        outer_cfg.get("qps_reference_mode") or "last_accepted"
    ).strip().lower()
    if qps_reference_mode not in {"last_accepted", "canonical_baseline"}:
        qps_reference_mode = "invalid"
    qps_threshold_mode = str(
        outer_cfg.get("qps_threshold_mode") or "max_static_and_noise"
    ).strip().lower()
    if qps_threshold_mode not in {"max_static_and_noise", "static_floor"}:
        qps_threshold_mode = "invalid"
    noise_min_qps_gain_pct = (
        safe_float(qps_noise_model.get("effective_min_gain_pct"), math.inf)
        if qps_noise_ready
        else math.inf
    )
    effective_min_qps_gain_pct = static_min_qps_gain_ratio * 100.0
    if (qps_threshold_mode == "max_static_and_noise" and qps_noise_ready
            and qps_noise_cfg.get("enabled") is not False):
        effective_min_qps_gain_pct = max(
            effective_min_qps_gain_pct,
            noise_min_qps_gain_pct,
        )
    if qps_noise_required and not qps_noise_ready:
        effective_min_qps_gain_pct = math.inf
    min_qps_gain_ratio = effective_min_qps_gain_pct / 100.0
    min_target_drop_ratio = safe_float(
        outer_cfg.get("min_target_drop_pct"),
        safe_float(acceptance_policy.get("min_target_drop_pct"), 0.1),
    ) / 100.0
    min_other_up_ratio = safe_float(
        outer_cfg.get("min_other_up_pct"),
        safe_float(acceptance_policy.get("min_other_up_pct"), 0.0),
    ) / 100.0
    tier_patience = max(
        1, safe_int(outer_cfg.get("acceptance_tier_downgrade_after_rounds"), 2)
    )
    preferred_tier = "strict"
    required_tier = outer_acceptance_required_tier()

    status_ok = str(candidate.get("status") or "").lower() == "ok"
    stats = candidate.get("resource_stats") if isinstance(candidate.get("resource_stats"), dict) else {}
    monitor_ok = stats.get("resource_monitor_ok") is not False
    candidate_qps = safe_float(candidate.get("db_qps"), math.nan)
    reference_qps = safe_float(reference.get("db_qps"), math.nan)
    baseline_qps = safe_float(baseline.get("db_qps"), math.nan)
    qps_gain_vs_reference = (
        (candidate_qps - reference_qps) / reference_qps
        if math.isfinite(candidate_qps) and math.isfinite(reference_qps) and reference_qps > 0.0
        else math.nan
    )
    qps_gain_vs_baseline = (
        (candidate_qps - baseline_qps) / baseline_qps
        if math.isfinite(candidate_qps) and math.isfinite(baseline_qps) and baseline_qps > 0.0
        else math.nan
    )
    qps_gate_gain = (
        qps_gain_vs_baseline
        if qps_reference_mode == "canonical_baseline"
        else qps_gain_vs_reference
    )
    qps_gate_reference = (
        baseline if qps_reference_mode == "canonical_baseline" else reference
    )
    reference_matches_baseline = bool(
        reference is baseline
        or (
            reference.get("label") == baseline.get("label")
            and math.isfinite(reference_qps)
            and math.isfinite(baseline_qps)
            and math.isclose(
                reference_qps,
                baseline_qps,
                rel_tol=0.0,
                abs_tol=1e-12,
            )
        )
    )
    if qps_reference_mode == "canonical_baseline":
        qps_gate_reference_role = "canonical_baseline"
    elif reference_matches_baseline:
        qps_gate_reference_role = "canonical_baseline_first_round"
    else:
        qps_gate_reference_role = "previous_accepted_round"
    qps_up = bool(
        qps_reference_mode != "invalid"
        and qps_threshold_mode != "invalid"
        and math.isfinite(qps_gate_gain)
        and qps_gate_gain > min_qps_gain_ratio
    )

    candidate_resources = candidate.get("resources") if isinstance(candidate.get("resources"), dict) else {}
    reference_resources = reference.get("resources") if isinstance(reference.get("resources"), dict) else {}
    baseline_resources = baseline.get("resources") if isinstance(baseline.get("resources"), dict) else {}
    target_drop_vs_reference: Optional[float] = None
    target_drop_vs_baseline: Optional[float] = None
    target_down = True
    if not is_none_target(target):
        candidate_target = safe_float(candidate_resources.get(target), math.nan)
        reference_target = safe_float(reference_resources.get(target), math.nan)
        baseline_target = safe_float(baseline_resources.get(target), math.nan)
        if math.isfinite(candidate_target) and math.isfinite(reference_target) and reference_target > 0.0:
            target_drop_vs_reference = (reference_target - candidate_target) / reference_target
        if math.isfinite(candidate_target) and math.isfinite(baseline_target) and baseline_target > 0.0:
            target_drop_vs_baseline = (baseline_target - candidate_target) / baseline_target
        target_down = bool(
            target_drop_vs_reference is not None
            and target_drop_vs_reference > min_target_drop_ratio
        )

    other_resources = (
        ("cpu", "memory", "io")
        if is_none_target(target)
        else tuple(resource for resource in ("cpu", "memory", "io") if resource != target)
    )
    other_gain_vs_reference: Dict[str, Optional[float]] = {}
    other_gain_vs_baseline: Dict[str, Optional[float]] = {}
    for resource in other_resources:
        candidate_value = safe_float(candidate_resources.get(resource), math.nan)
        reference_value = safe_float(reference_resources.get(resource), math.nan)
        baseline_value = safe_float(baseline_resources.get(resource), math.nan)
        other_gain_vs_reference[resource] = (
            (candidate_value - reference_value) / reference_value
            if math.isfinite(candidate_value) and math.isfinite(reference_value) and reference_value > 0.0
            else None
        )
        other_gain_vs_baseline[resource] = (
            (candidate_value - baseline_value) / baseline_value
            if math.isfinite(candidate_value) and math.isfinite(baseline_value) and baseline_value > 0.0
            else None
        )
    other_up_vs_reference = sorted(
        resource
        for resource, gain in other_gain_vs_reference.items()
        if gain is not None and gain > max(0.0, min_other_up_ratio)
    )
    other_up_vs_baseline = sorted(
        resource for resource, gain in other_gain_vs_baseline.items() if gain is not None and gain > 0.0
    )
    utilization_payload = (
        candidate.get("actual_utilization_pct")
        if isinstance(candidate.get("actual_utilization_pct"), dict)
        else {}
    )
    resource_capacity = (
        acceptance_policy.get("resource_capacity")
        if isinstance(acceptance_policy.get("resource_capacity"), dict)
        else {}
    )
    capacity_keys = {
        "cpu": "cpu_cores",
        "memory": "memory_gib",
        "io": "io_mib_s",
    }
    non_target_threshold_pct = safe_float(
        outer_cfg.get("non_target_bottleneck_threshold_pct"), 80.0
    )
    non_target_utilization_pct: Dict[str, Optional[float]] = {}
    for resource in other_resources:
        explicit_utilization = safe_float(utilization_payload.get(resource), math.nan)
        if math.isfinite(explicit_utilization):
            non_target_utilization_pct[resource] = explicit_utilization
            continue
        resource_value = safe_float(candidate_resources.get(resource), math.nan)
        capacity_value = safe_float(
            resource_capacity.get(capacity_keys[resource]), math.nan
        )
        non_target_utilization_pct[resource] = (
            resource_value / capacity_value * 100.0
            if math.isfinite(resource_value)
            and math.isfinite(capacity_value)
            and capacity_value > 0.0
            else None
        )
    non_target_safety_evaluable = all(
        value is not None for value in non_target_utilization_pct.values()
    )
    non_target_bottleneck_resources = sorted(
        resource
        for resource, utilization_pct in non_target_utilization_pct.items()
        if utilization_pct is not None
        and utilization_pct >= non_target_threshold_pct
    )
    non_target_safe = bool(
        non_target_safety_evaluable and not non_target_bottleneck_resources
    )

    if qps_up and target_down and other_up_vs_reference:
        achieved_tier: Optional[str] = "strict"
    elif qps_up and target_down:
        achieved_tier = "secondary"
    elif qps_up:
        achieved_tier = "fallback"
    else:
        achieved_tier = None
    tier_passed = achieved_tier is not None
    tier_eligible = bool(
        status_ok
        and monitor_ok
        and qps_up
        and tier_passed
        and non_target_safe
    )
    # All three levels are alternatives in the same Top-K batch.  Selection
    # happens after every candidate is evaluated and prefers strict, then
    # secondary, then fallback.  The miss-derived tier remains diagnostic
    # preference only and can never block an otherwise safe QPS-up point.
    accepted_shrink = tier_eligible
    accepted_escape = False
    accepted = accepted_shrink
    rejects: List[str] = []
    if not status_ok:
        rejects.append("real_validation_status_not_ok")
    if not monitor_ok:
        rejects.append("real_resource_monitor_not_ok")
    if not qps_up:
        rejects.append(
            "db_qps_not_up_vs_canonical_baseline"
            if qps_reference_mode == "canonical_baseline"
            else "db_qps_not_up_vs_last_accepted"
        )
    if qps_reference_mode == "invalid":
        rejects.append("invalid_qps_reference_mode")
    if qps_threshold_mode == "invalid":
        rejects.append("invalid_qps_threshold_mode")
    if qps_noise_required and not qps_noise_ready:
        rejects.append("qps_noise_model_not_ready")
    if not non_target_safety_evaluable:
        rejects.append("non_target_bottleneck_safety_not_evaluable")
    elif not non_target_safe:
        rejects.append("non_target_resource_became_bottleneck")
    return {
        "policy": NESTED_RESOURCE_SEMANTIC_POLICY,
        "role": "outer_loop_semantic_acceptance",
        "accepted": accepted,
        "accepted_shrink": accepted_shrink,
        "accepted_escape": accepted_escape,
        "tier_eligible": tier_eligible,
        "formal_state_mutation_allowed": accepted_shrink,
        "rejects": rejects,
        "reference_label": reference.get("label"),
        "baseline_label": baseline.get("label"),
        "db_qps": candidate_qps,
        "reference_db_qps": reference_qps,
        "baseline_db_qps": baseline_qps,
        "qps_gain_vs_reference": qps_gain_vs_reference,
        "qps_gain_vs_baseline": qps_gain_vs_baseline,
        "qps_gate_gain": qps_gate_gain,
        "qps_gate_reference_label": qps_gate_reference.get("label"),
        "qps_gate_reference_db_qps": safe_float(
            qps_gate_reference.get("db_qps"), math.nan
        ),
        "qps_gate_reference_role": qps_gate_reference_role,
        "qps_progress_contract": (
            "candidate_qps_strictly_above_previous_accepted_round_v1"
            if qps_reference_mode == "last_accepted"
            else "legacy_canonical_baseline_reference"
        ),
        "qps_reference_mode": qps_reference_mode,
        "qps_threshold_mode": qps_threshold_mode,
        "qps_up": qps_up,
        "acceptance_tier": achieved_tier,
        "required_acceptance_tier": required_tier,
        "preferred_acceptance_tier": preferred_tier,
        "acceptance_selection_policy": (
            "same_batch_strict_then_secondary_then_fallback"
        ),
        "acceptance_tier_passed": tier_passed,
        "consecutive_missed_outer_rounds_before_decision": consecutive_missed_outer_rounds,
        "acceptance_tier_downgrade_after_rounds": tier_patience,
        "acceptance_tier_downgrade_active": False,
        "min_db_qps_gain_pct": effective_min_qps_gain_pct,
        "static_min_db_qps_gain_pct": static_min_qps_gain_ratio * 100.0,
        "qps_noise_min_db_qps_gain_pct": noise_min_qps_gain_pct,
        "qps_noise_gate_required": qps_noise_required,
        "qps_noise_model_ready": qps_noise_ready,
        "qps_noise_model_id": qps_noise_model.get("model_id"),
        "qps_noise_comparison_sigma_log": qps_noise_model.get("comparison_sigma_log"),
        "target_resource": target,
        "target_drop_vs_reference": target_drop_vs_reference,
        "target_drop_vs_baseline": target_drop_vs_baseline,
        "target_down": target_down if not is_none_target(target) else None,
        "min_target_drop_pct": min_target_drop_ratio * 100.0,
        "other_gain_vs_reference": other_gain_vs_reference,
        "other_gain_vs_baseline": other_gain_vs_baseline,
        "other_up_vs_reference": other_up_vs_reference,
        "other_up_vs_baseline": other_up_vs_baseline,
        "min_other_up_pct": min_other_up_ratio * 100.0,
        "other_resource_up_required": False,
        "other_resource_up_preferred": True,
        "other_resource_preference_met": bool(other_up_vs_reference),
        "non_target_bottleneck_threshold_pct": non_target_threshold_pct,
        "non_target_utilization_pct": non_target_utilization_pct,
        "non_target_safety_evaluable": non_target_safety_evaluable,
        "non_target_bottleneck_resources": non_target_bottleneck_resources,
        "non_target_safe": non_target_safe,
    }


def select_inner_best_for_outer_acceptance(
    validations: Sequence[Dict[str, Any]],
    reference: Dict[str, Any],
    baseline: Dict[str, Any],
    *,
    target: str,
    cfg: Dict[str, Any],
    consecutive_missed_outer_rounds: int = 0,
    comparison_resolver: Optional[
        Callable[
            [Dict[str, Any]],
            Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]],
        ]
    ] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """Evaluate all valid Top-K replays and select one by tier then resource score."""
    ranked: List[
        Tuple[
            Dict[str, Any],
            Dict[str, Any],
            Dict[str, Any],
            Dict[str, Any],
            Dict[str, Any],
        ]
    ] = []
    for validation in validations:
        if comparison_resolver is not None:
            candidate_reference, candidate_baseline, comparison_metadata = (
                comparison_resolver(validation)
            )
        else:
            candidate_reference = reference
            candidate_baseline = baseline
            comparison_metadata = {
                "mode": "single_baseline",
                "valid": True,
                "reference_label": reference.get("label"),
                "candidate_baseline_label": baseline.get("label"),
            }
        details = inner_resource_score_details(
            validation,
            candidate_baseline,
            target=target,
            cfg=cfg,
        )
        if details.get("valid"):
            ranked.append(
                (
                    validation,
                    details,
                    candidate_reference,
                    candidate_baseline,
                    comparison_metadata,
                )
            )
    ranked.sort(key=lambda item: inner_resource_score_sort_key(item[1]), reverse=True)
    if not ranked:
        return None, None, []
    evaluated: List[
        Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]
    ] = []
    eligible: List[Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]] = []
    tier_rank = {"fallback": 1, "secondary": 2, "strict": 3}
    for inner_rank, (
        validation,
        score_details,
        candidate_reference,
        candidate_baseline,
        comparison_metadata,
    ) in enumerate(ranked, start=1):
        decision = outer_semantic_acceptance_details(
            validation,
            candidate_reference,
            candidate_baseline,
            target=target,
            cfg=cfg,
            consecutive_missed_outer_rounds=consecutive_missed_outer_rounds,
        )
        if comparison_metadata.get("valid") is False:
            decision["accepted"] = False
            decision.setdefault("rejects", []).append(
                "same_worker_reference_resolution_failed"
            )
        decision.update(
            {
                "inner_real_score_rank": inner_rank,
                "inner_real_score": score_details.get("score"),
                "inner_score_details": score_details,
                "candidate_label": validation.get("label"),
                "candidate_params": validation.get("params") or {},
                "comparison_reference": copy.deepcopy(comparison_metadata),
                "acceptance_eligible": bool(decision.get("accepted")),
                "acceptance_selection_policy": (
                    "same_batch_strict_then_secondary_then_fallback_then_inner_resource_score"
                ),
            }
        )
        evaluated.append((validation, score_details, decision))
        if decision.get("acceptance_eligible"):
            eligible.append((validation, score_details, decision))

    selected: Optional[Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]] = None
    if eligible:
        selected = max(
            eligible,
            key=lambda item: (
                tier_rank.get(str(item[2].get("acceptance_tier") or ""), 0),
                inner_resource_score_sort_key(item[1]),
            ),
        )
    decisions: List[Dict[str, Any]] = []
    for validation, score_details, decision in evaluated:
        selected_here = bool(
            selected is not None
            and validation.get("label") == selected[0].get("label")
        )
        decision["selected_for_acceptance"] = selected_here
        if decision.get("acceptance_eligible") and not selected_here:
            decision["accepted"] = False
            decision.setdefault("rejects", []).append(
                "higher_priority_outer_candidate_selected"
            )
        decisions.append(decision)
    if selected is None:
        return None, ranked[0][1], decisions

    validation, score_details, decision = selected
    accepted = copy.deepcopy(validation)
    accepted["accepted"] = True
    accepted["accept_reject_reason"] = "accepted_by_outer_semantics"
    accepted["outer_semantic_decision"] = decision
    accepted["inner_resource_score"] = score_details
    return accepted, score_details, decisions


def final_convergence_goal_details(
    confirmation: Dict[str, Any],
    baseline: Dict[str, Any],
    *,
    target: str,
    cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """Evaluate the post-convergence physical goal from an independent replay."""
    search_cfg = ((cfg.get("binary_plan_tuning") or {}).get("binary_search") or {})
    nested_cfg = search_cfg.get("nested_loop") if isinstance(search_cfg.get("nested_loop"), dict) else {}
    final_cfg = nested_cfg.get("final_validation") if isinstance(nested_cfg.get("final_validation"), dict) else {}
    min_qps_gain_ratio = safe_float(final_cfg.get("min_db_qps_gain_pct"), 15.0) / 100.0
    min_target_drop_ratio = safe_float(final_cfg.get("min_target_drop_pct"), 5.0) / 100.0
    max_target_drop_ratio = safe_float(final_cfg.get("max_target_drop_pct"), 10.0) / 100.0

    confirmation_qps = safe_float(confirmation.get("db_qps"), math.nan)
    baseline_qps = safe_float(baseline.get("db_qps"), math.nan)
    qps_gain = (
        (confirmation_qps - baseline_qps) / baseline_qps
        if math.isfinite(confirmation_qps) and math.isfinite(baseline_qps) and baseline_qps > 0.0
        else math.nan
    )
    qps_goal = bool(math.isfinite(qps_gain) and qps_gain >= min_qps_gain_ratio)
    resources = confirmation.get("resources") if isinstance(confirmation.get("resources"), dict) else {}
    baseline_resources = baseline.get("resources") if isinstance(baseline.get("resources"), dict) else {}
    utilization = (
        confirmation.get("actual_utilization_pct")
        if isinstance(confirmation.get("actual_utilization_pct"), dict)
        else ((confirmation.get("vm_replay") or {}).get("actual_utilization_pct") or {})
    )
    scoring = confirmation.get("runtime_scoring") if isinstance(confirmation.get("runtime_scoring"), dict) else {}
    overload_threshold_pct = safe_float(scoring.get("overload_threshold"), 0.80) * 100.0
    target_ranges = scoring.get("resource_target_ranges") if isinstance(scoring.get("resource_target_ranges"), dict) else {}
    target_range = target_ranges.get(target) if isinstance(target_ranges.get(target), dict) else {}

    target_drop_ratio: Optional[float] = None
    target_utilization_pct: Optional[float] = None
    target_down = True
    target_drop_in_range = True
    target_not_bottleneck = True
    target_in_sweet_spot: Optional[bool] = None
    if not is_none_target(target):
        target_value = safe_float(resources.get(target), math.nan)
        baseline_target = safe_float(baseline_resources.get(target), math.nan)
        target_drop_ratio = (
            (baseline_target - target_value) / baseline_target
            if math.isfinite(target_value) and math.isfinite(baseline_target) and baseline_target > 0.0
            else None
        )
        target_down = bool(target_drop_ratio is not None and target_drop_ratio > 0.0)
        target_drop_in_range = bool(
            target_drop_ratio is not None
            and min_target_drop_ratio <= target_drop_ratio <= max_target_drop_ratio
        )
        util_value = safe_float(utilization.get(target), math.nan)
        target_utilization_pct = util_value if math.isfinite(util_value) else None
        target_not_bottleneck = bool(
            target_utilization_pct is not None
            and target_utilization_pct <= overload_threshold_pct
        )
        sweet_min = safe_float(target_range.get("min_pct"), math.nan)
        sweet_max = safe_float(target_range.get("max_pct"), math.nan)
        target_in_sweet_spot = bool(
            target_utilization_pct is not None
            and math.isfinite(sweet_min)
            and math.isfinite(sweet_max)
            and sweet_min <= target_utilization_pct <= sweet_max
        )

    other_resources = (
        ("cpu", "memory", "io")
        if is_none_target(target)
        else tuple(resource for resource in ("cpu", "memory", "io") if resource != target)
    )
    other_gain_ratios: Dict[str, Optional[float]] = {}
    for resource in other_resources:
        real_value = safe_float(resources.get(resource), math.nan)
        baseline_value = safe_float(baseline_resources.get(resource), math.nan)
        other_gain_ratios[resource] = (
            (real_value - baseline_value) / baseline_value
            if math.isfinite(real_value) and math.isfinite(baseline_value) and baseline_value > 0.0
            else None
        )
    other_up_resources = sorted(
        resource for resource, gain in other_gain_ratios.items() if gain is not None and gain > 0.0
    )
    other_up_goal = bool(other_up_resources)
    other_up_required = is_none_target(target)
    status_ok = str(confirmation.get("status") or "").lower() == "ok"
    stats = confirmation.get("resource_stats") if isinstance(confirmation.get("resource_stats"), dict) else {}
    monitor_ok = stats.get("resource_monitor_ok") is not False
    latency_ok = all(
        math.isfinite(safe_float(confirmation.get(name), math.nan))
        and safe_float(confirmation.get(name), 0.0) > 0.0
        for name in ("p90_ms", "p95_ms", "p99_ms")
    )
    goal_reached = bool(
        status_ok
        and monitor_ok
        and latency_ok
        and qps_goal
        and target_down
        and target_drop_in_range
        and target_not_bottleneck
        and (other_up_goal or not other_up_required)
    )
    return {
        "policy": NESTED_RESOURCE_SEMANTIC_POLICY,
        "role": "post_convergence_independent_physical_validation",
        "goal_reached": goal_reached,
        "status_ok": status_ok,
        "resource_monitor_ok": monitor_ok,
        "latency_ok": latency_ok,
        "db_qps": confirmation_qps,
        "baseline_db_qps": baseline_qps,
        "db_qps_gain_ratio": qps_gain if math.isfinite(qps_gain) else None,
        "db_qps_gain_pct": qps_gain * 100.0 if math.isfinite(qps_gain) else None,
        "min_db_qps_gain_pct": min_qps_gain_ratio * 100.0,
        "qps_goal_reached": qps_goal,
        "target_resource": target,
        "target_drop_ratio": target_drop_ratio,
        "target_drop_pct": target_drop_ratio * 100.0 if target_drop_ratio is not None else None,
        "target_down": target_down if not is_none_target(target) else None,
        "target_drop_in_range": target_drop_in_range if not is_none_target(target) else None,
        "min_target_drop_pct": min_target_drop_ratio * 100.0 if not is_none_target(target) else None,
        "max_target_drop_pct": max_target_drop_ratio * 100.0 if not is_none_target(target) else None,
        "target_utilization_pct": target_utilization_pct,
        "overload_threshold_pct": overload_threshold_pct if not is_none_target(target) else None,
        "target_not_bottleneck": target_not_bottleneck if not is_none_target(target) else None,
        "target_sweet_range_pct": target_range if not is_none_target(target) else None,
        "target_in_sweet_spot": target_in_sweet_spot,
        "sweet_spot_preferred_not_required": True,
        "other_gain_ratios": other_gain_ratios,
        "other_up_resources": other_up_resources,
        "any_other_resource_up": other_up_goal,
        "other_resource_up_required": other_up_required,
        "other_resource_up_preferred": not other_up_required,
        "resources": resources,
        "actual_utilization_pct": utilization,
        "db_xact_tps": confirmation.get("db_xact_tps"),
        "p90_ms": confirmation.get("p90_ms"),
        "p95_ms": confirmation.get("p95_ms"),
        "p99_ms": confirmation.get("p99_ms"),
    }


def select_small_loop_top1_anchor(
    topk_candidates: Sequence[Tuple[int, Dict[str, Any], Dict[str, Any]]],
    validations: Sequence[Dict[str, Any]],
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Choose Top1 by the small-loop score; real metrics are evidence only."""
    ranked_validations = sorted(
        (row for row in validations if safe_int(row.get("topk_rank"), 0) > 0),
        key=lambda row: safe_int(row.get("topk_rank"), 10**9),
    )
    validation_top1 = ranked_validations[0] if ranked_validations else None
    candidate_top1 = topk_candidates[0] if topk_candidates else None
    if validation_top1 is not None and (
        candidate_top1 is None
        or safe_int(validation_top1.get("topk_rank"), 10**9)
        <= safe_int(candidate_top1[1].get("topk_rank"), 10**9)
    ):
        validation_raw_idx = safe_int(
            validation_top1.get("raw_candidate_index"),
            safe_int(validation_top1.get("candidate_index"), 0),
        )
        matching_candidate = next(
            (
                item
                for item in topk_candidates
                if safe_int(item[1].get("raw_candidate_index"), item[0]) == validation_raw_idx
            ),
            None,
        )
        if matching_candidate is not None:
            raw_idx, candidate, probe_changes = matching_candidate
        else:
            raw_idx = validation_raw_idx
            candidate = validation_top1
            probe_changes = (
                validation_top1.get("plan_input_changes")
                if isinstance(validation_top1.get("plan_input_changes"), dict)
                else {}
            )
    elif candidate_top1 is not None:
        raw_idx, candidate, probe_changes = candidate_top1
    else:
        return None, None
    candidate_idx = safe_int(candidate.get("raw_candidate_index"), raw_idx)
    validation = next(
        (
            row
            for row in validations
            if safe_int(row.get("raw_candidate_index"), safe_int(row.get("candidate_index"), 0))
            == candidate_idx
        ),
        None,
    )
    anchor = copy.deepcopy(validation if validation is not None else candidate)
    anchor.setdefault("params", copy.deepcopy(candidate.get("params") or {}))
    anchor.setdefault("mask", candidate.get("mask"))
    anchor.setdefault("raw_candidate_index", candidate_idx)
    anchor.setdefault("topk_rank", 1)
    details = {
        "policy": SMALL_LOOP_TOP1_ANCHOR_POLICY,
        "selection_source": "small_loop_plan_estimator_only",
        "real_metrics_used_for_anchor_selection": False,
        "small_loop_rank": 1,
        "raw_candidate_index": candidate_idx,
        "mask": candidate.get("mask"),
        "params": copy.deepcopy(candidate.get("params") or {}),
        "small_loop_score": list(probe_changes.get("topk_rank_key") or []),
        "estimated_resources": copy.deepcopy(probe_changes.get("candidate_resource_avg") or {}),
        "real_replay_attempted": validation is not None,
        "real_replay_status": validation.get("status") if validation is not None else None,
        "real_replay_label": validation.get("label") if validation is not None else None,
        "real_replay_db_qps": validation.get("db_qps") if validation is not None else None,
        "real_replay_resources": copy.deepcopy(validation.get("resources") or {}) if validation is not None else {},
    }
    return anchor, details


def select_best_accepted(
    current_best: Dict[str, Any],
    accepted_candidates: Sequence[Dict[str, Any]],
    *,
    target: str,
) -> Dict[str, Any]:
    best = current_best
    best_key = accepted_best_sort_key(best, target=target)
    for candidate in accepted_candidates:
        key = accepted_best_sort_key(candidate, target=target)
        if key > best_key:
            best = candidate
            best_key = key
    return copy.deepcopy(best)


def select_round_best_accepted(
    accepted_candidates: Sequence[Dict[str, Any]],
    *,
    target: str,
) -> Optional[Dict[str, Any]]:
    best: Optional[Dict[str, Any]] = None
    best_key: Optional[Tuple[Any, ...]] = None
    for candidate in accepted_candidates:
        key = accepted_best_sort_key(candidate, target=target)
        if best_key is None or key > best_key:
            best = candidate
            best_key = key
    return copy.deepcopy(best) if best is not None else None


def baseline_goal_best_sort_key(
    validation: Dict[str, Any],
    *,
    target: str,
    baseline: Dict[str, Any],
    cfg: Dict[str, Any],
) -> Tuple[Any, ...]:
    accepted, _reason, details = compare_against_baseline_goal(
        validation,
        baseline,
        target=target,
        cfg=cfg,
    )
    resources = validation.get("resources") or {}
    target_value = safe_float(resources.get(target), math.inf)
    baseline_resources = baseline.get("resources") or {}
    baseline_target = safe_float(baseline_resources.get(target), math.inf)
    target_drop_ratio = (
        (baseline_target - target_value) / max(abs(baseline_target), 1e-9)
        if math.isfinite(baseline_target) and baseline_target > 0.0
        else 0.0
    )
    hybench = validation.get("hybench") or {}
    xp_qps = safe_float(hybench.get("xp_qps"), 0.0)
    xp_tps = safe_float(hybench.get("xp_tps"), 0.0)
    p95_ms = safe_float(validation.get("p95_ms"), math.inf)
    p99_ms = safe_float(validation.get("p99_ms"), math.inf)
    return (
        1 if accepted else 0,
        target_drop_ratio,
        safe_float(validation.get("db_qps"), 0.0),
        safe_float(details.get("best_other_up_delta_pct"), 0.0),
        xp_qps,
        xp_tps,
        -p95_ms,
        -p99_ms,
    )


def select_best_baseline_goal(
    candidates: Sequence[Dict[str, Any]],
    baseline: Dict[str, Any],
    *,
    target: str,
    cfg: Dict[str, Any],
) -> Tuple[Optional[Dict[str, Any]], Dict[str, int], int]:
    best: Optional[Dict[str, Any]] = None
    best_key: Optional[Tuple[Any, ...]] = None
    reject_counts: Dict[str, int] = {}
    accepted_count = 0
    for candidate in candidates:
        accepted, reason, _details = compare_against_baseline_goal(
            candidate,
            baseline,
            target=target,
            cfg=cfg,
        )
        if not accepted:
            reject_counts[reason] = reject_counts.get(reason, 0) + 1
            continue
        accepted_count += 1
        key = baseline_goal_best_sort_key(
            candidate,
            target=target,
            baseline=baseline,
            cfg=cfg,
        )
        if best_key is None or key > best_key:
            best = copy.deepcopy(candidate)
            best_key = key
    return best, reject_counts, accepted_count


def latency_guard_ok(candidate: Any, baseline: Any, guard: float) -> bool:
    cand = safe_float(candidate, 0.0)
    base = safe_float(baseline, 0.0)
    if cand <= 0.0 or base <= 0.0:
        return True
    return cand <= base * (1.0 + guard)


def pct_delta(value: float, base: float) -> Optional[float]:
    if base == 0:
        return None
    return (value - base) / base * 100.0


def update_ranges_around_best(
    ranges: Dict[str, Tuple[float, float]],
    active: Sequence[str],
    best_params: Dict[str, float],
    *,
    tie_break: str = "low",
) -> Dict[str, Tuple[float, float]]:
    updated = dict(ranges)
    for name in active:
        low, high = ranges[name]
        mid = interval_midpoint(low, high)
        value = safe_float(best_params.get(name), mid)
        updated[name] = split_interval_for_value(low, high, value, tie_break=tie_break)
    return updated


def update_nested_outer_ranges(
    ranges: Dict[str, Tuple[float, float]],
    active: Sequence[str],
    accepted_candidate: Optional[Dict[str, Any]],
) -> Tuple[Dict[str, Tuple[float, float]], str]:
    """Outer loop owns interval updates; rejection must leave them unchanged."""
    if accepted_candidate is None:
        return dict(ranges), "keep_interval_and_continue_inner_loop"
    return (
        update_ranges_around_best(
            ranges,
            active,
            accepted_candidate.get("params") or {},
            tie_break="low",
        ),
        "shrink_around_accepted_candidate",
    )


def expand_ranges_around_point(
    ranges: Dict[str, Tuple[float, float]],
    bounds: Dict[str, Tuple[float, float]],
    active: Sequence[str],
    point_params: Dict[str, float],
    *,
    factor: float = 2.0,
) -> Dict[str, Tuple[float, float]]:
    """Backtrack from a dead search half by widening around a known useful point."""
    if factor <= 1.0:
        return dict(ranges)
    updated = dict(ranges)
    for name in active:
        low, high = ranges[name]
        bound_low, bound_high = bounds.get(name, (low, high))
        if bound_high <= bound_low or high <= low:
            updated[name] = (low, high)
            continue
        center = safe_float(point_params.get(name), interval_midpoint(low, high))
        if not math.isfinite(center):
            center = interval_midpoint(low, high)
        new_low, new_high = expand_interval_around_point(
            low,
            high,
            bound_low,
            bound_high,
            center,
            factor,
        )
        if new_high <= new_low:
            updated[name] = (low, high)
        else:
            updated[name] = (new_low, new_high)
    return updated


def all_widths_below_epsilon(
    ranges: Dict[str, Tuple[float, float]],
    active: Sequence[str],
    epsilon_cfg: Dict[str, Any],
) -> bool:
    for name in active:
        low, high = ranges[name]
        epsilon = safe_float(epsilon_cfg.get(name), safe_float(epsilon_cfg.get("default"), 1e-6))
        if (high - low) > epsilon:
            return False
    return True


def required_rounds_for_epsilon(
    ranges: Dict[str, Tuple[float, float]],
    active: Sequence[str],
    epsilon_cfg: Dict[str, Any],
) -> Tuple[int, Dict[str, int]]:
    rounds_by_param: Dict[str, int] = {}
    for name in active:
        low, high = ranges[name]
        width = max(0.0, high - low)
        epsilon = safe_float(epsilon_cfg.get(name), safe_float(epsilon_cfg.get("default"), 1e-6))
        if epsilon <= 0.0 or width <= epsilon:
            rounds_by_param[name] = 0
            continue
        rounds_by_param[name] = max(0, int(math.ceil(math.log(width / epsilon, 2))))
    return (max(rounds_by_param.values()) if rounds_by_param else 0), rounds_by_param


def _error_bound_candidates(error_bound: Any) -> List[Dict[str, Any]]:
    if not isinstance(error_bound, dict):
        return []
    raw = error_bound.get("candidates")
    if isinstance(raw, list) and raw:
        return [item for item in raw if isinstance(item, dict)]
    if error_bound.get("value") not in (None, ""):
        return [
            {
                "source": str(error_bound.get("source") or "selected_error_bound"),
                "value": safe_float(error_bound.get("value"), 0.0),
                "unit": error_bound.get("unit"),
                "detail": error_bound,
            }
        ]
    return []


def _resource_bound_to_probe_unit(resource: str, value: float, learned: Dict[str, Any], result: Dict[str, Any]) -> float:
    if value <= 0.0:
        return 0.0
    if resource == "cpu":
        planning = result.get("planning_breakdown") if isinstance(result.get("planning_breakdown"), dict) else {}
        window_s = safe_float(
            learned.get("prediction_window_seconds"),
            safe_float(planning.get("prediction_window_seconds"), safe_float(planning.get("window_seconds"), 10.0)),
        )
        return value / max(window_s, 1e-9)
    if resource == "memory":
        return value / 1024.0
    if resource == "io":
        planning = result.get("planning_breakdown") if isinstance(result.get("planning_breakdown"), dict) else {}
        concurrency = result.get("concurrency_breakdown") if isinstance(result.get("concurrency_breakdown"), dict) else {}
        window_s = safe_float(
            learned.get("prediction_window_seconds"),
            safe_float(
                learned.get("feature_window_seconds"),
                safe_float(planning.get("window_seconds"), safe_float(concurrency.get("window_seconds"), 10.0)),
            ),
        )
        return value / max(window_s, 1e-9)
    return value


def _prediction_before_error_in_probe_unit(resource: str, learned: Dict[str, Any], result: Dict[str, Any]) -> Optional[float]:
    if not learned:
        return None
    if resource == "cpu":
        value = learned.get("prediction_before_error_bound")
    elif resource == "memory":
        value = learned.get("prediction_before_error_bound")
    else:
        value = learned.get("prediction_before_error_bound")
    if value in (None, ""):
        return None
    parsed = safe_float(value, math.nan)
    if not math.isfinite(parsed):
        return None
    if resource == "cpu":
        planning = result.get("planning_breakdown") if isinstance(result.get("planning_breakdown"), dict) else {}
        window_s = safe_float(
            learned.get("prediction_window_seconds"),
            safe_float(planning.get("prediction_window_seconds"), safe_float(planning.get("window_seconds"), 10.0)),
        )
        return parsed / max(window_s, 1e-9)
    if resource == "memory":
        return parsed / 1024.0
    if resource == "io":
        planning = result.get("planning_breakdown") if isinstance(result.get("planning_breakdown"), dict) else {}
        concurrency = result.get("concurrency_breakdown") if isinstance(result.get("concurrency_breakdown"), dict) else {}
        window_s = safe_float(
            learned.get("prediction_window_seconds"),
            safe_float(
                learned.get("feature_window_seconds"),
                safe_float(planning.get("window_seconds"), safe_float(concurrency.get("window_seconds"), 10.0)),
            ),
        )
        return parsed / max(window_s, 1e-9)
    return parsed


def summarize_resource_error_profile(resource_breakdown: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    profile: Dict[str, Dict[str, Any]] = {}
    for resource in ("cpu", "memory", "io"):
        result = resource_breakdown.get(resource) if isinstance(resource_breakdown.get(resource), dict) else {}
        calibration = result.get("calibration_breakdown") if isinstance(result.get("calibration_breakdown"), dict) else {}
        learned = calibration.get("learned_model") if isinstance(calibration.get("learned_model"), dict) else {}
        candidates = _error_bound_candidates(learned.get("error_bound"))
        model_abs_bound = 0.0
        baseline_abs_bound = 0.0
        for item in candidates:
            source = str(item.get("source") or "")
            detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
            value = safe_float(item.get("value", detail.get("value")), 0.0)
            converted = _resource_bound_to_probe_unit(resource, value, learned, result)
            if source == "baseline_error_bound":
                baseline_abs_bound = max(baseline_abs_bound, converted)
            else:
                model_abs_bound = max(model_abs_bound, converted)
        metrics = learned.get("metrics") if isinstance(learned.get("metrics"), dict) else {}
        test_metrics = metrics.get("test") if isinstance(metrics.get("test"), dict) else {}
        relative_p95 = safe_float(test_metrics.get("relative_error_p95"), 0.0)
        predicted = safe_float(result.get("value"), 0.0)
        prediction_before = _prediction_before_error_in_probe_unit(resource, learned, result)
        total_bound = max(model_abs_bound, baseline_abs_bound)
        profile[resource] = {
            "available": bool(learned),
            "formula_name": result.get("formula_name"),
            "unit": result.get("normalized_unit") or result.get("unit"),
            "predicted_value": predicted,
            "prediction_before_error_bound": prediction_before,
            "model_abs_bound": model_abs_bound,
            "baseline_abs_bound": baseline_abs_bound,
            "total_abs_bound": total_bound,
            "relative_p95": relative_p95,
            "error_bound": learned.get("error_bound") if isinstance(learned.get("error_bound"), dict) else {},
        }
    return profile


def replay_pruning_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    bpt = cfg.get("binary_plan_tuning") if isinstance(cfg.get("binary_plan_tuning"), dict) else {}
    search_cfg = bpt.get("binary_search") if isinstance(bpt.get("binary_search"), dict) else {}
    pruning_cfg = (
        search_cfg.get("replay_candidate_pruning")
        if isinstance(search_cfg.get("replay_candidate_pruning"), dict)
        else {}
    )
    return pruning_cfg


def replay_error_bound_artifact_path(cfg: Dict[str, Any], out_dir: Path) -> Path:
    pruning_cfg = replay_pruning_config(cfg)
    configured = str(pruning_cfg.get("error_bound_artifact") or "resource_error_bounds_hybenchxp.json")
    path = Path(configured)
    return path if path.is_absolute() else out_dir / path


def replay_error_bound_artifact_usable(artifact: Dict[str, Any], cfg: Dict[str, Any]) -> bool:
    if not isinstance(artifact, dict):
        return False
    artifact_workload = str(artifact.get("workload") or "").strip().lower()
    if artifact_workload and artifact_workload != workload_name(cfg).strip().lower():
        return False
    resources = artifact.get("resources") if isinstance(artifact.get("resources"), dict) else {}
    for resource in ("cpu", "memory", "io"):
        entry = resources.get(resource) if isinstance(resources.get(resource), dict) else {}
        bound = entry.get("error_bound") if isinstance(entry.get("error_bound"), dict) else {}
        value = safe_float(bound.get("value"), math.nan)
        if not entry.get("available") or not math.isfinite(value):
            return False
    return True


def _resource_error_bound_report_lines(artifact: Dict[str, Any], out_dir: Path) -> List[str]:
    resources = artifact.get("resources") if isinstance(artifact.get("resources"), dict) else {}
    lines = [
        f"# Resource Error Bounds: {artifact.get('workload') or 'unknown'}",
        "",
        f"- generated_at: `{artifact.get('generated_at') or ''}`",
        f"- run_dir: `{out_dir}`",
        f"- source_logs: `{len(artifact.get('source_logs') or [])}`",
        f"- sample_rows: `{safe_int(artifact.get('sample_row_count'), 0)}`",
        f"- quantile/safety: `{artifact.get('error_quantile')}` / `{artifact.get('safety_factor')}`",
        "",
        "| resource | available | samples | model | error_bound | all_p95 | test_p95 | actual_range | raw_prediction_range |",
        "|---|---:|---:|---|---:|---:|---:|---|---|",
    ]
    for resource, entry in resources.items():
        if not isinstance(entry, dict):
            continue
        model = entry.get("model") if isinstance(entry.get("model"), dict) else {}
        metrics = entry.get("metrics") if isinstance(entry.get("metrics"), dict) else {}
        all_metrics = metrics.get("all") if isinstance(metrics.get("all"), dict) else {}
        test_metrics = metrics.get("test") if isinstance(metrics.get("test"), dict) else {}
        error_bound = entry.get("error_bound") if isinstance(entry.get("error_bound"), dict) else {}
        lines.append(
            "| {resource} | {available} | {samples} | slope={slope:.6g}, intercept={intercept:.6g} | {bound:.6g} {unit} | {all_p95:.6g} | {test_p95:.6g} | {actual_min:.6g}..{actual_max:.6g} | {raw_min:.6g}..{raw_max:.6g} |".format(
                resource=resource,
                available=str(bool(entry.get("available"))).lower(),
                samples=safe_int(entry.get("sample_count"), 0),
                slope=safe_float(model.get("slope"), 0.0),
                intercept=safe_float(model.get("intercept"), 0.0),
                bound=safe_float(error_bound.get("value"), 0.0),
                unit=entry.get("unit") or "",
                all_p95=safe_float(all_metrics.get("abs_error_p95"), 0.0),
                test_p95=safe_float(test_metrics.get("abs_error_p95"), 0.0),
                actual_min=safe_float(entry.get("actual_min"), 0.0),
                actual_max=safe_float(entry.get("actual_max"), 0.0),
                raw_min=safe_float(entry.get("raw_prediction_min"), 0.0),
                raw_max=safe_float(entry.get("raw_prediction_max"), 0.0),
            )
        )
    return lines


def build_replay_error_bound_artifact(
    cfg: Dict[str, Any],
    out_dir: Path,
    *,
    target_filter: str = "all",
    trigger: str = "auto",
) -> Path:
    pruning_cfg = replay_pruning_config(cfg)
    quantile = min(0.999, max(0.50, safe_float(pruning_cfg.get("calibration_error_quantile"), 0.95)))
    safety_factor = max(1.0, safe_float(pruning_cfg.get("calibration_safety_factor"), 1.1))
    min_samples = max(2, safe_int(pruning_cfg.get("min_samples_per_resource"), 20))
    rows, source_logs = _collect_error_bound_training_rows(out_dir, cfg, target_filter)
    resources = {
        resource: _fit_resource_error_entry(
            rows,
            resource,
            quantile=quantile,
            safety_factor=safety_factor,
            min_samples=min_samples,
        )
        for resource in ("cpu", "memory", "io")
    }
    artifact_path = replay_error_bound_artifact_path(cfg, out_dir)
    artifact = {
        "workload": workload_name(cfg),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "run_dir": str(out_dir),
        "source_logs": source_logs,
        "target_filter": target_filter,
        "model_version": "binary_plan_replay_error_bounds_v1",
        "sample_row_count": len(rows),
        "error_quantile": quantile,
        "safety_factor": safety_factor,
        "min_samples_per_resource": min_samples,
        "resources": resources,
        "trigger": trigger,
    }
    write_json(artifact_path, artifact)
    append_jsonl(
        out_dir / f"resource_error_bound_calibration_{workload_log_suffix(cfg)}.jsonl",
        with_workload(
            cfg,
            {
                "event": "resource_error_bound_calibration",
                "artifact_path": str(artifact_path),
                "target_filter": target_filter,
                "sample_row_count": len(rows),
                "resources": resources,
                "trigger": trigger,
            },
        ),
    )
    artifact_path.with_suffix(".md").write_text(
        "\n".join(_resource_error_bound_report_lines(artifact, out_dir)) + "\n",
        encoding="utf-8",
    )
    return artifact_path


def install_replay_error_bound_artifact(cfg: Dict[str, Any], out_dir: Path) -> Optional[Path]:
    pruning_cfg = replay_pruning_config(cfg)
    if not to_bool(pruning_cfg.get("enabled"), True):
        return None
    path = replay_error_bound_artifact_path(cfg, out_dir)
    cfg["_runtime_output_dir"] = str(out_dir)
    cfg["_replay_error_bound_artifact_path"] = str(path)
    if path.exists():
        artifact = read_json(path)
        if replay_error_bound_artifact_usable(artifact, cfg):
            cfg["_replay_error_bound_artifact"] = artifact
            return path
    if not to_bool(pruning_cfg.get("keep_missing_error_bound"), True):
        path = build_replay_error_bound_artifact(
            cfg,
            out_dir,
            target_filter="all",
            trigger="auto_missing_or_unusable_before_binary_search",
        )
        artifact = read_json(path)
        if replay_error_bound_artifact_usable(artifact, cfg):
            cfg["_replay_error_bound_artifact_path"] = str(path)
            cfg["_replay_error_bound_artifact"] = artifact
            return path
        raise RuntimeError(
            "replay pruning requires usable current-task error bounds, "
            f"but auto calibration from {out_dir} did not produce usable cpu/memory/io bounds"
        )
    return None


def _calibration_resource_entry(cfg: Dict[str, Any], resource: str) -> Dict[str, Any]:
    artifact = cfg.get("_replay_error_bound_artifact") if isinstance(cfg.get("_replay_error_bound_artifact"), dict) else {}
    resources = artifact.get("resources") if isinstance(artifact.get("resources"), dict) else {}
    entry = resources.get(resource) if isinstance(resources.get(resource), dict) else {}
    if not entry.get("available"):
        return {}
    return entry


def _calibrated_resource_value(raw_value: Any, entry: Dict[str, Any]) -> Optional[float]:
    if not entry:
        return None
    raw = safe_float(raw_value, math.nan)
    if not math.isfinite(raw):
        return None
    model = entry.get("model") if isinstance(entry.get("model"), dict) else {}
    slope = safe_float(model.get("slope"), 0.0)
    intercept = safe_float(model.get("intercept"), 0.0)
    return max(0.0, intercept + slope * raw)


def apply_replay_error_bounds_to_probe_result(probe_result: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(probe_result, dict):
        return probe_result
    resource_avg = probe_result.get("resource_avg") if isinstance(probe_result.get("resource_avg"), dict) else {}
    if not resource_avg:
        return probe_result
    raw_resource_avg = copy.deepcopy(resource_avg)
    calibrated_avg = dict(resource_avg)
    profile = (
        copy.deepcopy(probe_result.get("resource_error_profile"))
        if isinstance(probe_result.get("resource_error_profile"), dict)
        else {}
    )
    applied: Dict[str, Any] = {}
    for resource in ("cpu", "memory", "io"):
        entry = _calibration_resource_entry(cfg, resource)
        calibrated = _calibrated_resource_value(resource_avg.get(resource), entry)
        if calibrated is None:
            continue
        bound = safe_float(
            ((entry.get("error_bound") or {}) if isinstance(entry.get("error_bound"), dict) else {}).get("value"),
            safe_float(entry.get("abs_error_bound"), 0.0),
        )
        calibrated_avg[resource] = calibrated
        existing = profile.get(resource) if isinstance(profile.get(resource), dict) else {}
        profile[resource] = {
            **existing,
            "available": True,
            "formula_name": "run_calibrated_plan_estimator_v1",
            "unit": entry.get("unit"),
            "predicted_value": calibrated,
            "raw_predicted_value": safe_float(resource_avg.get(resource), 0.0),
            "prediction_before_error_bound": calibrated,
            "model_abs_bound": bound,
            "baseline_abs_bound": 0.0,
            "total_abs_bound": bound,
            "relative_p95": ((entry.get("metrics") or {}).get("all") or {}).get("relative_error_p95"),
            "error_bound": entry.get("error_bound") if isinstance(entry.get("error_bound"), dict) else {},
            "calibration": {
                "source": "current_task_real_replay",
                "artifact_path": cfg.get("_replay_error_bound_artifact_path"),
                "sample_count": entry.get("sample_count"),
                "model": entry.get("model"),
                "metrics": entry.get("metrics"),
            },
        }
        applied[resource] = {
            "raw": safe_float(resource_avg.get(resource), 0.0),
            "calibrated": calibrated,
            "error_bound": bound,
            "sample_count": entry.get("sample_count"),
        }
    if applied:
        probe_result["raw_resource_avg"] = raw_resource_avg
        probe_result["resource_avg"] = calibrated_avg
        probe_result["resource_error_profile"] = profile
        probe_result["resource_error_calibration"] = {
            "artifact_path": cfg.get("_replay_error_bound_artifact_path"),
            "applied": applied,
        }
    return probe_result


def apply_replay_error_bounds_to_probe_changes(changes: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    candidate_avg = changes.get("candidate_resource_avg") if isinstance(changes.get("candidate_resource_avg"), dict) else {}
    if not candidate_avg:
        return changes
    raw_candidate_avg = copy.deepcopy(candidate_avg)
    raw_baseline_avg = (
        copy.deepcopy(changes.get("baseline_resource_avg"))
        if isinstance(changes.get("baseline_resource_avg"), dict)
        else {}
    )
    candidate_probe = {
        "resource_avg": copy.deepcopy(candidate_avg),
        "resource_error_profile": copy.deepcopy(changes.get("candidate_resource_error_profile") or {}),
    }
    apply_replay_error_bounds_to_probe_result(candidate_probe, cfg)
    changes["raw_candidate_resource_avg"] = raw_candidate_avg
    changes["candidate_resource_avg"] = candidate_probe.get("resource_avg") or candidate_avg
    changes["candidate_resource_error_profile"] = candidate_probe.get("resource_error_profile") or {}
    if candidate_probe.get("resource_error_calibration"):
        changes["resource_error_calibration"] = candidate_probe.get("resource_error_calibration")
    if raw_baseline_avg:
        baseline_probe = {"resource_avg": raw_baseline_avg, "resource_error_profile": {}}
        apply_replay_error_bounds_to_probe_result(baseline_probe, cfg)
        changes["raw_baseline_resource_avg"] = raw_baseline_avg
        changes["baseline_resource_avg"] = baseline_probe.get("resource_avg") or raw_baseline_avg
    return changes


def probe_topk_rank_key(
    probe_changes: Dict[str, Any],
    target: str,
    baseline_probe: Dict[str, Any],
    cfg: Optional[Dict[str, Any]] = None,
) -> Tuple[float, float, float, int, float]:
    candidate_avg = probe_changes.get("candidate_resource_avg") or {}
    baseline_avg = probe_changes.get("baseline_resource_avg") or baseline_probe.get("resource_avg") or {}
    profile = probe_changes.get("candidate_resource_error_profile") or {}
    if is_none_target(target):
        best_up_ratio = 0.0
        total_up_ratio = 0.0
        down_penalty = 0.0
        for resource in ("cpu", "memory", "io"):
            info = profile.get(resource) if isinstance(profile.get(resource), dict) else {}
            candidate_value = safe_float(candidate_avg.get(resource), 0.0)
            baseline_value = safe_float(baseline_avg.get(resource), 0.0)
            bound = safe_float(info.get("total_abs_bound"), 0.0)
            optimistic_delta = candidate_value + bound - baseline_value
            ratio = optimistic_delta / max(abs(baseline_value), 1e-9)
            best_up_ratio = max(best_up_ratio, ratio)
            total_up_ratio += max(0.0, ratio)
            if optimistic_delta < 0.0:
                down_penalty += -ratio
        changed = 1 if probe_changes.get("any_changed") else 0
        return (best_up_ratio, total_up_ratio, -down_penalty, changed, 0.0)

    target_info = profile.get(target) if isinstance(profile.get(target), dict) else {}
    candidate_target = safe_float(candidate_avg.get(target), math.inf)
    baseline_target = safe_float(baseline_avg.get(target), 0.0)
    target_bound = safe_float(target_info.get("total_abs_bound"), 0.0)
    optimistic_target = max(0.0, candidate_target - target_bound)
    target_drop = baseline_target - optimistic_target
    target_drop_ratio = target_drop / max(abs(baseline_target), 1e-9)
    other_up = 0.0
    other_down_penalty = 0.0
    for resource in explanation_other_resources(target, cfg):
        info = profile.get(resource) if isinstance(profile.get(resource), dict) else {}
        candidate_value = safe_float(candidate_avg.get(resource), 0.0)
        baseline_value = safe_float(baseline_avg.get(resource), 0.0)
        bound = safe_float(info.get("total_abs_bound"), 0.0)
        optimistic_up = candidate_value + bound - baseline_value
        other_up = max(other_up, optimistic_up / max(abs(baseline_value), 1e-9))
        if candidate_value + bound < baseline_value:
            other_down_penalty += (baseline_value - candidate_value - bound) / max(abs(baseline_value), 1e-9)
    changed = 1 if probe_changes.get("any_changed") else 0
    return (target_drop_ratio, other_up, -other_down_penalty, changed, -candidate_target)


def replay_pruning_status_from_bounds(
    probe_changes: Dict[str, Any],
    *,
    target: str,
    baseline_probe: Dict[str, Any],
    best_probe: Dict[str, Any],
    cfg: Dict[str, Any],
) -> Dict[str, Any]:
    bpt = cfg.get("binary_plan_tuning") or {}
    search_cfg = bpt.get("binary_search") if isinstance(bpt.get("binary_search"), dict) else {}
    pruning_cfg = search_cfg.get("replay_candidate_pruning") if isinstance(search_cfg.get("replay_candidate_pruning"), dict) else {}
    if not to_bool(pruning_cfg.get("enabled"), True):
        return {"enabled": False, "pruned": False, "reason": "disabled"}

    candidate_avg = probe_changes.get("candidate_resource_avg") or {}
    baseline_avg = baseline_probe.get("resource_avg") or {}
    best_avg = best_probe.get("resource_avg") or baseline_avg
    profile = probe_changes.get("candidate_resource_error_profile") or {}
    missing = [
        resource
        for resource in ("cpu", "memory", "io")
        if not isinstance(profile.get(resource), dict) or not profile.get(resource, {}).get("available")
    ]
    if missing and to_bool(pruning_cfg.get("keep_missing_error_bound"), True):
        return {
            "enabled": True,
            "pruned": False,
            "reason": "missing_error_bound_keep_for_real_replay",
            "missing_resources": missing,
            "bound_info": profile,
        }

    if is_none_target(target):
        optimistic_resource_up: Dict[str, float] = {}
        for resource in ("cpu", "memory", "io"):
            info = profile.get(resource) if isinstance(profile.get(resource), dict) else {}
            candidate_value = safe_float(candidate_avg.get(resource), 0.0)
            baseline_value = safe_float(baseline_avg.get(resource), 0.0)
            bound = safe_float(info.get("total_abs_bound"), 0.0)
            optimistic_resource_up[resource] = candidate_value + bound - baseline_value
        direction_possible = any(value > 0.0 for value in optimistic_resource_up.values())
        return {
            "enabled": True,
            "pruned": not direction_possible,
            "reason": (
                "optimistic_none_target_has_no_resource_up_potential"
                if not direction_possible
                else "none_target_kept_for_real_replay"
            ),
            "target_resource": target,
            "candidate_resource_avg": candidate_avg,
            "baseline_resource_avg": baseline_avg,
            "best_resource_avg": best_avg,
            "optimistic_other_up": optimistic_resource_up,
            "direction_possible": direction_possible,
            "cannot_beat_best": False,
            "bound_info": profile,
        }

    target_info = profile.get(target) if isinstance(profile.get(target), dict) else {}
    if not target_info:
        return {
            "enabled": True,
            "pruned": False,
            "reason": "missing_target_error_bound_keep_for_real_replay",
            "bound_info": profile,
        }
    candidate_target = safe_float(candidate_avg.get(target), math.inf)
    baseline_target = safe_float(baseline_avg.get(target), math.inf)
    best_target = safe_float(best_avg.get(target), baseline_target)
    target_bound = safe_float(target_info.get("total_abs_bound"), 0.0)
    optimistic_target = max(0.0, candidate_target - target_bound)
    optimistic_target_improvement = baseline_target - optimistic_target
    best_target_improvement = baseline_target - best_target

    optimistic_other_up: Dict[str, float] = {}
    for resource in explanation_other_resources(target, cfg):
        info = profile.get(resource) if isinstance(profile.get(resource), dict) else {}
        candidate_value = safe_float(candidate_avg.get(resource), 0.0)
        baseline_value = safe_float(baseline_avg.get(resource), 0.0)
        bound = safe_float(info.get("total_abs_bound"), 0.0)
        optimistic_other_up[resource] = candidate_value + bound - baseline_value
    best_other_up = max(optimistic_other_up.values()) if optimistic_other_up else float("-inf")
    direction_possible = bool(optimistic_target_improvement > 0.0 and math.isfinite(best_other_up) and best_other_up > 0.0)
    margin = safe_float(pruning_cfg.get("resource_improvement_margin"), 0.0)
    cannot_beat_best = bool(
        math.isfinite(best_target_improvement)
        and math.isfinite(optimistic_target_improvement)
        and optimistic_target_improvement <= best_target_improvement + margin
    )
    pruned = cannot_beat_best
    reason = (
        "optimistic_bottleneck_improvement_not_above_best"
        if cannot_beat_best
        else "optimistic_candidate_has_resource_potential"
    )
    return {
        "enabled": True,
        "pruned": pruned,
        "reason": reason,
        "target_resource": target,
        "candidate_resource_avg": candidate_avg,
        "baseline_resource_avg": baseline_avg,
        "best_resource_avg": best_avg,
        "optimistic_target_value": optimistic_target,
        "optimistic_target_improvement": optimistic_target_improvement,
        "best_target_improvement": best_target_improvement,
        "optimistic_other_up": optimistic_other_up,
        "direction_possible": direction_possible,
        "cannot_beat_best": cannot_beat_best,
        "resource_improvement_margin": margin,
        "bound_info": profile,
    }


def candidate_plan_probe_task(
    cfg: Dict[str, Any],
    queries: Sequence[Dict[str, Any]],
    baseline_probe: Dict[str, Any],
    candidate: Dict[str, Any],
) -> Dict[str, Any]:
    started = time.perf_counter()
    probe = PlanFeatureProbe(cfg)
    candidate_probe = probe.probe(queries, candidate["params"])
    changes = summarize_probe_changes(baseline_probe, candidate_probe)
    candidate_coverage_audit = plan_probe_coverage_audit(cfg, candidate_probe)
    candidate_coverage_required_for_base = bool(
        candidate_coverage_audit.get("enabled")
        and candidate_coverage_audit.get("block_physical_base_topk")
    )
    candidate_coverage_ok = bool(
        not candidate_coverage_required_for_base
        or candidate_coverage_audit.get("passed")
    )
    changes["status"] = "ok" if candidate_coverage_ok else "failed"
    if not candidate_coverage_ok:
        changes["reason"] = "candidate_plan_probe_coverage_incomplete"
    changes["candidate_plan_coverage_gate"] = candidate_coverage_audit
    changes["elapsed_s"] = time.perf_counter() - started
    changes["candidate_resource_avg"] = candidate_probe.get("resource_avg") or {}
    changes["baseline_resource_avg"] = baseline_probe.get("resource_avg") or {}
    changes["candidate_resource_error_profile"] = candidate_probe.get("resource_error_profile") or {}
    changes["baseline_plan_fingerprint"] = plan_signature_set_fingerprint(
        baseline_probe.get("plan_signatures") or {}
    )
    changes["candidate_plan_fingerprint"] = plan_signature_set_fingerprint(
        candidate_probe.get("plan_signatures") or {}
    )
    apply_replay_error_bounds_to_probe_changes(changes, cfg)
    changes["topk_rank_key"] = list(
        probe_topk_rank_key(changes, str(candidate.get("target") or ""), baseline_probe, cfg)
    )
    return changes


def plan_signature_set_fingerprint(plan_signatures: Dict[str, str]) -> str:
    """Return one stable fingerprint for the workload-wide normalized plan set."""
    if not isinstance(plan_signatures, dict) or not plan_signatures:
        return ""
    normalized = [
        (str(query_key), str(signature))
        for query_key, signature in sorted(plan_signatures.items(), key=lambda item: str(item[0]))
    ]
    return stable_hash(json.dumps(normalized, ensure_ascii=False, separators=(",", ":")))


def plan_change_replay_expansion_state(
    cfg: Dict[str, Any],
    consecutive_missed_outer_rounds: int,
) -> Dict[str, Any]:
    """Describe whether a prior fallback-tier miss arms an expanded replay round."""
    search_cfg = ((cfg.get("binary_plan_tuning") or {}).get("binary_search") or {})
    nested_cfg = search_cfg.get("nested_loop") if isinstance(search_cfg.get("nested_loop"), dict) else {}
    outer_cfg = (
        nested_cfg.get("outer_acceptance")
        if isinstance(nested_cfg.get("outer_acceptance"), dict)
        else {}
    )
    expansion_cfg = (
        nested_cfg.get("fallback_plan_change_replay_expansion")
        if isinstance(nested_cfg.get("fallback_plan_change_replay_expansion"), dict)
        else {}
    )
    tier_patience = max(
        1, safe_int(outer_cfg.get("acceptance_tier_downgrade_after_rounds"), 2)
    )
    missed_rounds = max(0, safe_int(consecutive_missed_outer_rounds, 0))
    fallback_start_miss_count = 2 * tier_patience
    completed_fallback_miss_rounds = max(0, missed_rounds - fallback_start_miss_count)
    trigger_after = max(
        1, safe_int(expansion_cfg.get("trigger_after_fallback_miss_rounds"), 1)
    )
    enabled = to_bool(expansion_cfg.get("enabled"), False)
    max_extra = max(0, safe_int(expansion_cfg.get("max_extra_candidates_per_round"), 5))
    active = bool(
        enabled
        and max_extra > 0
        and completed_fallback_miss_rounds >= trigger_after
    )
    return {
        "enabled": enabled,
        "active": active,
        "required_acceptance_tier": acceptance_tier_for_miss_count(
            missed_rounds,
            downgrade_after_rounds=tier_patience,
        ),
        "consecutive_missed_outer_rounds_before_round": missed_rounds,
        "acceptance_tier_downgrade_after_rounds": tier_patience,
        "fallback_start_miss_count": fallback_start_miss_count,
        "completed_fallback_miss_rounds": completed_fallback_miss_rounds,
        "trigger_after_fallback_miss_rounds": trigger_after,
        "max_extra_candidates_per_round": max_extra,
        "plan_tree_change_required": True,
        "unique_plan_fingerprint_required": True,
        "trigger_reason": (
            "prior_fallback_tier_topk_miss"
            if active
            else "fallback_topk_miss_threshold_not_reached"
        ),
    }


def select_plan_change_replay_expansion_candidates(
    ranked_candidates: Sequence[Tuple[int, Dict[str, Any], Dict[str, Any]]],
    *,
    max_extra_candidates: int,
    excluded_plan_fingerprints: Optional[Set[str]] = None,
) -> Tuple[List[Tuple[int, Dict[str, Any], Dict[str, Any]]], Dict[str, Any]]:
    """Select bounded, unique, successfully probed plan-tree changes for extra replay."""
    max_extra = max(0, safe_int(max_extra_candidates, 0))
    seen = {
        str(fingerprint)
        for fingerprint in (excluded_plan_fingerprints or set())
        if str(fingerprint)
    }
    selected: List[Tuple[int, Dict[str, Any], Dict[str, Any]]] = []
    exclusion_counts: Dict[str, int] = {}
    eligible_plan_changed_count = 0
    unique_eligible_count = 0
    selected_rows: List[Dict[str, Any]] = []

    for raw_idx, candidate, probe_changes in ranked_candidates:
        reason = ""
        if str(probe_changes.get("status") or "").lower() != "ok":
            reason = "plan_probe_not_ok"
        else:
            coverage = (
                probe_changes.get("candidate_plan_coverage_gate")
                if isinstance(probe_changes.get("candidate_plan_coverage_gate"), dict)
                else {}
            )
            if coverage.get("enabled") and coverage.get("passed") is not True:
                reason = "plan_probe_workload_coverage_incomplete"
            plan = probe_changes.get("plan") if isinstance(probe_changes.get("plan"), dict) else {}
            if not reason and plan.get("changed") is not True:
                reason = "plan_tree_unchanged"
            elif not reason:
                eligible_plan_changed_count += 1
                fingerprint = str(probe_changes.get("candidate_plan_fingerprint") or "")
                if not fingerprint:
                    reason = "missing_candidate_plan_fingerprint"
                elif fingerprint in seen:
                    reason = "duplicate_plan_fingerprint"
                else:
                    unique_eligible_count += 1
                    seen.add(fingerprint)
                    if len(selected) >= max_extra:
                        reason = "extra_replay_cap_reached"
                    else:
                        selected.append((raw_idx, candidate, probe_changes))
                        selected_rows.append(
                            {
                                "raw_candidate_index": safe_int(
                                    candidate.get("raw_candidate_index"), raw_idx
                                ),
                                "mask": candidate.get("mask"),
                                "candidate_plan_fingerprint": fingerprint,
                                "plan_changed_count": safe_int(plan.get("changed_count"), 0),
                            }
                        )
        if reason:
            exclusion_counts[reason] = exclusion_counts.get(reason, 0) + 1

    return selected, {
        "candidate_pool_count": len(ranked_candidates),
        "eligible_plan_changed_count": eligible_plan_changed_count,
        "unique_eligible_count": unique_eligible_count,
        "selected_extra_count": len(selected),
        "max_extra_candidates_per_round": max_extra,
        "plan_tree_change_required": True,
        "unique_plan_fingerprint_required": True,
        "excluded_plan_fingerprint_count": len(excluded_plan_fingerprints or set()),
        "exclusion_counts": exclusion_counts,
        "selected_candidates": selected_rows,
    }


def select_unique_changed_plan_candidates(
    ranked_candidates: Sequence[Tuple[int, Dict[str, Any], Dict[str, Any]]],
    *,
    baseline_plan_fingerprint: str,
    excluded_plan_fingerprints: Optional[Set[str]] = None,
) -> Tuple[
    List[Tuple[int, Dict[str, Any], Dict[str, Any]]],
    List[Tuple[int, int, Dict[str, Any], Dict[str, Any], str]],
    Dict[str, Any],
]:
    """Keep one replay representative per new, non-baseline plan behavior."""
    previously_replayed = {
        str(fingerprint)
        for fingerprint in (excluded_plan_fingerprints or set())
        if str(fingerprint)
    }
    selected_fingerprints: Set[str] = set()
    eligible: List[Tuple[int, Dict[str, Any], Dict[str, Any]]] = []
    rejected: List[Tuple[int, int, Dict[str, Any], Dict[str, Any], str]] = []
    reason_counts: Dict[str, int] = {}
    for behavior_rank, (raw_idx, candidate, probe_changes) in enumerate(
        ranked_candidates,
        start=1,
    ):
        reason = ""
        fingerprint = str(probe_changes.get("candidate_plan_fingerprint") or "")
        plan = probe_changes.get("plan") if isinstance(probe_changes.get("plan"), dict) else {}
        if str(probe_changes.get("status") or "").lower() != "ok":
            reason = "plan_probe_not_ok"
        elif not fingerprint:
            reason = "missing_candidate_plan_fingerprint"
        elif fingerprint == str(baseline_plan_fingerprint or ""):
            reason = "baseline_plan_behavior"
        elif plan.get("changed") is not True:
            reason = "plan_tree_unchanged"
        elif fingerprint in previously_replayed:
            reason = "plan_fingerprint_already_replayed_in_interval"
        elif fingerprint in selected_fingerprints:
            reason = "duplicate_behavior_cluster_in_round"
        else:
            selected_fingerprints.add(fingerprint)
            eligible.append((raw_idx, candidate, probe_changes))
        if reason:
            rejected.append(
                (behavior_rank, raw_idx, candidate, probe_changes, reason)
            )
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    return eligible, rejected, {
        "enabled": True,
        "candidate_count": len(ranked_candidates),
        "eligible_unique_changed_behavior_count": len(eligible),
        "rejected_count": len(rejected),
        "rejection_reason_counts": reason_counts,
        "baseline_plan_fingerprint": baseline_plan_fingerprint,
        "previously_replayed_plan_fingerprint_count": len(previously_replayed),
        "plan_probe_success_required": True,
        "plan_tree_change_required": True,
        "baseline_behavior_forbidden": True,
        "one_replay_per_behavior_cluster": True,
    }


def select_distinct_base_topk_candidates(
    ranked_candidates: Sequence[Tuple[int, Dict[str, Any], Dict[str, Any]]],
    *,
    excluded_parameter_signatures: Optional[Set[str]] = None,
) -> Tuple[
    List[Tuple[int, Dict[str, Any], Dict[str, Any]]],
    List[Tuple[int, int, Dict[str, Any], Dict[str, Any], str]],
    Dict[str, Any],
]:
    """Deduplicate base Top-K only by exact candidate coordinates.

    Equal normalized plan trees are deliberately retained: cost parameters and
    workload controls can change QPS/resources without changing plan structure.
    Plan-tree uniqueness belongs only to the fallback expansion pool.
    """
    excluded = {
        str(signature)
        for signature in (excluded_parameter_signatures or set())
        if str(signature)
    }
    selected_signatures: Set[str] = set()
    eligible: List[Tuple[int, Dict[str, Any], Dict[str, Any]]] = []
    rejected: List[Tuple[int, int, Dict[str, Any], Dict[str, Any], str]] = []
    reason_counts: Dict[str, int] = {}
    retained_duplicate_plan_fingerprints = 0
    selected_plan_fingerprints: Set[str] = set()
    for rank, (raw_idx, candidate, probe_changes) in enumerate(
        ranked_candidates,
        start=1,
    ):
        reason = ""
        signature = str(candidate.get("signature") or "")
        if not signature:
            params = candidate.get("params") if isinstance(candidate.get("params"), dict) else {}
            signature = json.dumps(params, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if str(probe_changes.get("status") or "").lower() != "ok":
            reason = "plan_probe_not_ok"
        elif not signature:
            reason = "missing_parameter_signature"
        elif signature in excluded:
            reason = "parameter_vector_already_replayed_in_interval"
        elif signature in selected_signatures:
            reason = "duplicate_parameter_vector_in_round"
        else:
            selected_signatures.add(signature)
            fingerprint = str(probe_changes.get("candidate_plan_fingerprint") or "")
            if fingerprint and fingerprint in selected_plan_fingerprints:
                retained_duplicate_plan_fingerprints += 1
            if fingerprint:
                selected_plan_fingerprints.add(fingerprint)
            eligible.append((raw_idx, candidate, probe_changes))
        if reason:
            rejected.append((rank, raw_idx, candidate, probe_changes, reason))
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    return eligible, rejected, {
        "enabled": True,
        "candidate_count": len(ranked_candidates),
        "eligible_distinct_parameter_count": len(eligible),
        "rejected_count": len(rejected),
        "rejection_reason_counts": reason_counts,
        "dedupe_key": "exact_parameter_vector",
        "same_plan_different_parameters_retained": True,
        "retained_duplicate_plan_fingerprint_count": retained_duplicate_plan_fingerprints,
        "plan_tree_uniqueness_applies_only_to_fallback_expansion": True,
    }


def partition_operator_rule_rejected_candidates(
    ranked_candidates: Sequence[Tuple[int, Dict[str, Any], Dict[str, Any]]],
    operator_rule_gate: Dict[str, Any],
) -> Tuple[
    List[Tuple[int, Dict[str, Any], Dict[str, Any]]],
    List[Tuple[int, int, Dict[str, Any], Dict[str, Any], Dict[str, Any]]],
]:
    """Apply the frozen hard gate before Top-K so rejected points are backfilled."""
    eligible: List[Tuple[int, Dict[str, Any], Dict[str, Any]]] = []
    rejected: List[
        Tuple[int, int, Dict[str, Any], Dict[str, Any], Dict[str, Any]]
    ] = []
    for pre_gate_rank, (raw_idx, candidate, probe_changes) in enumerate(
        ranked_candidates,
        start=1,
    ):
        rule_decision = evaluate_rule_gate(probe_changes, operator_rule_gate)
        probe_changes["operator_resource_rule_gate"] = rule_decision
        candidate["operator_rule_pre_gate_rank"] = pre_gate_rank
        if rule_decision.get("rejected"):
            rejected.append(
                (pre_gate_rank, raw_idx, candidate, probe_changes, rule_decision)
            )
        else:
            eligible.append((raw_idx, candidate, probe_changes))
    return eligible, rejected


def failed_probe_changes(error: Exception) -> Dict[str, Any]:
    return {
        "status": "failed",
        "error": str(error),
        "any_changed": True,
        "plan": {"changed": True, "error": str(error)},
        "features": {
            "cpu": {"changed": True, "error": str(error)},
            "memory": {"changed": True, "error": str(error)},
            "io": {"changed": True, "error": str(error)},
        },
        "operator_changes": {"changed": False, "changed_count": 0, "changes": [], "transition_key": ""},
    }


def compact_probe_audit(probe_changes: Dict[str, Any]) -> Dict[str, Any]:
    plan = probe_changes.get("plan") if isinstance(probe_changes.get("plan"), dict) else {}
    features = probe_changes.get("features") if isinstance(probe_changes.get("features"), dict) else {}
    feature_summary: Dict[str, Any] = {}
    for resource in ("cpu", "memory", "io"):
        detail = features.get(resource) if isinstance(features.get(resource), dict) else {}
        feature_summary[resource] = {
            "changed": bool(detail.get("changed")),
            "changed_count": safe_int(detail.get("changed_count"), 0),
        }
    pruning = (
        probe_changes.get("replay_pruning_status")
        if isinstance(probe_changes.get("replay_pruning_status"), dict)
        else {}
    )
    operator_changes = (
        probe_changes.get("operator_changes")
        if isinstance(probe_changes.get("operator_changes"), dict)
        else {}
    )
    operator_rule_gate = (
        probe_changes.get("operator_resource_rule_gate")
        if isinstance(probe_changes.get("operator_resource_rule_gate"), dict)
        else {}
    )
    return {
        "status": probe_changes.get("status"),
        "any_changed": bool(probe_changes.get("any_changed")),
        "plan_changed": bool(plan.get("changed")),
        "plan_changed_count": safe_int(plan.get("changed_count"), 0),
        "baseline_plan_fingerprint": probe_changes.get("baseline_plan_fingerprint"),
        "candidate_plan_fingerprint": probe_changes.get("candidate_plan_fingerprint"),
        "feature_changed": feature_summary,
        "operator_transition_key": operator_changes.get("transition_key"),
        "operator_changed_count": safe_int(operator_changes.get("changed_count"), 0),
        "operator_rule_rejected": bool(operator_rule_gate.get("rejected")),
        "operator_rule_id": operator_rule_gate.get("matched_rule_id"),
        "estimated_resource_avg": probe_changes.get("candidate_resource_avg") or {},
        "raw_estimated_resource_avg": probe_changes.get("raw_candidate_resource_avg") or {},
        "topk_rank_key": probe_changes.get("topk_rank_key") or [],
        "replay_pruning_reason": pruning.get("reason"),
        "replay_pruning_pruned": pruning.get("pruned"),
    }


def compact_validation_audit(validation: Dict[str, Any]) -> Dict[str, Any]:
    hybench = validation.get("hybench") if isinstance(validation.get("hybench"), dict) else {}
    worker = validation.get("worker") if isinstance(validation.get("worker"), dict) else {}
    return {
        "label": validation.get("label"),
        "status": validation.get("status"),
        "accepted": bool(validation.get("accepted")),
        "accept_reject_reason": validation.get("accept_reject_reason")
        or validation.get("reject_reason")
        or validation.get("accept_reason"),
        "resources": validation.get("resources") or {},
        "resource_samples": validation.get("resource_samples") or {},
        "db_qps": validation.get("db_qps"),
        "db_xact_tps": validation.get("db_xact_tps"),
        "hybench_xp_qps": hybench.get("xp_qps"),
        "hybench_xp_tps": hybench.get("xp_tps"),
        "p95_ms": validation.get("p95_ms"),
        "p99_ms": validation.get("p99_ms"),
        "elapsed_s": validation.get("elapsed_s"),
        "worker": {
            "name": worker.get("name"),
            "container": worker.get("container"),
            "port": worker.get("port"),
            "disk_root": worker.get("disk_root"),
        } if worker else {},
    }


def build_round_search_points_audit(
    *,
    cfg: Dict[str, Any],
    target: str,
    round_index: int,
    active: Sequence[str],
    base_params: Dict[str, float],
    ranges_payload: Dict[str, Any],
    raw_candidates: Optional[Sequence[Dict[str, Any]]] = None,
    ranked_candidates: Optional[Sequence[Tuple[int, Dict[str, Any], Dict[str, Any]]]] = None,
    topk_candidates: Optional[Sequence[Tuple[int, Dict[str, Any], Dict[str, Any]]]] = None,
    replay_pruned: Optional[Sequence[Dict[str, Any]]] = None,
    validations: Optional[Sequence[Dict[str, Any]]] = None,
    topk_limit: Optional[int] = None,
    note: str = "",
) -> Dict[str, Any]:
    normalized_ranges = normalize_ranges_payload(ranges_payload, active, param_ranges(cfg))
    points: Dict[int, Dict[str, Any]] = {}
    source_candidates: Sequence[Dict[str, Any]]
    if raw_candidates is not None:
        source_candidates = raw_candidates
    else:
        generated: List[Dict[str, Any]] = []
        for raw_idx, (mask, values) in enumerate(candidate_mask_values(active, normalized_ranges), start=1):
            params = dict(base_params)
            params.update(values)
            generated.append(
                {
                    "round": round_index,
                    "mask": mask,
                    "params": params,
                    "signature": param_signature(params),
                    "target": target,
                    "raw_candidate_index": raw_idx,
                    "ranges": {name: list(normalized_ranges[name]) for name in active},
                }
            )
        source_candidates = generated

    for fallback_idx, candidate in enumerate(source_candidates, start=1):
        raw_idx = safe_int(candidate.get("raw_candidate_index"), fallback_idx)
        points[raw_idx] = {
            "raw_candidate_index": raw_idx,
            "candidate_index": raw_idx,
            "mask": candidate.get("mask"),
            "params": candidate.get("params") or {},
            "signature": candidate.get("signature"),
            "state": "not_selected_by_candidate_cap",
            "selected_by_candidate_cap": False,
            "error_bound_pruned_before_replay": False,
            "real_replay_attempted": False,
            "actual_resource_recorded": False,
            "accepted": None,
            "accept_reject_reason": "",
        }

    for rank, (raw_idx, candidate, probe_changes) in enumerate(ranked_candidates or [], start=1):
        candidate_idx = safe_int(candidate.get("raw_candidate_index"), raw_idx)
        point = points.setdefault(
            candidate_idx,
            {
                "raw_candidate_index": candidate_idx,
                "candidate_index": candidate_idx,
                "mask": candidate.get("mask"),
                "params": candidate.get("params") or {},
                "signature": candidate.get("signature"),
            },
        )
        point["probe_rank"] = rank
        point["plan_probe"] = compact_probe_audit(probe_changes)

    for topk_idx, (raw_idx, candidate, probe_changes) in enumerate(topk_candidates or [], start=1):
        candidate_idx = safe_int(candidate.get("raw_candidate_index"), raw_idx)
        point = points.setdefault(
            candidate_idx,
            {
                "raw_candidate_index": candidate_idx,
                "candidate_index": candidate_idx,
                "mask": candidate.get("mask"),
                "params": candidate.get("params") or {},
                "signature": candidate.get("signature"),
            },
        )
        point.update(
            {
                "topk_rank": safe_int(candidate.get("topk_rank"), topk_idx),
                "selected_by_candidate_cap": True,
                "state": "selected_for_replay_or_error_bound_check",
                "plan_probe": compact_probe_audit(probe_changes),
            }
        )

    for record in replay_pruned or []:
        candidate_idx = safe_int(record.get("raw_candidate_index"), safe_int(record.get("candidate_index"), 0))
        if candidate_idx <= 0:
            continue
        point = points.setdefault(candidate_idx, {"raw_candidate_index": candidate_idx, "candidate_index": candidate_idx})
        pruning_status = (
            record.get("replay_pruning_status")
            if isinstance(record.get("replay_pruning_status"), dict)
            else {}
        )
        point.update(
            {
                "mask": record.get("mask", point.get("mask")),
                "params": record.get("params", point.get("params") or {}),
                "topk_rank": record.get("topk_rank", point.get("topk_rank")),
                "selected_by_candidate_cap": True,
                "error_bound_pruned_before_replay": True,
                "state": "error_bound_pruned_before_replay",
                "accept_reject_reason": pruning_status.get("reason") or "replay_pruned",
                "plan_probe": compact_probe_audit(
                    record.get("plan_input_changes") if isinstance(record.get("plan_input_changes"), dict) else {}
                ),
            }
        )

    for validation in validations or []:
        candidate_idx = safe_int(validation.get("raw_candidate_index"), safe_int(validation.get("candidate_index"), 0))
        if candidate_idx <= 0:
            continue
        point = points.setdefault(candidate_idx, {"raw_candidate_index": candidate_idx, "candidate_index": candidate_idx})
        point.update(
            {
                "mask": validation.get("mask", point.get("mask")),
                "params": validation.get("params", point.get("params") or {}),
                "topk_rank": validation.get("topk_rank", point.get("topk_rank")),
                "selected_by_candidate_cap": True,
                "real_replay_attempted": True,
                "actual_resource_recorded": True,
                "state": "real_validation",
                "accepted": bool(validation.get("accepted")),
                "accept_reject_reason": validation.get("accept_reject_reason")
                or validation.get("reject_reason")
                or validation.get("accept_reason")
                or "",
                "actual_validation": compact_validation_audit(validation),
            }
        )
        if isinstance(validation.get("plan_input_changes"), dict):
            point["plan_probe"] = compact_probe_audit(validation.get("plan_input_changes") or {})

    ordered = [points[idx] for idx in sorted(points)]
    state_counts: Dict[str, int] = {}
    for point in ordered:
        state = str(point.get("state") or "unknown")
        state_counts[state] = state_counts.get(state, 0) + 1
    return with_workload(
        cfg,
        {
            "event": "round_search_points_audit",
            "target": target,
            "round": round_index,
            "active_params": list(active),
            "candidate_count": len(ordered),
            "raw_search_point_count": len(ordered),
            "topk_limit": topk_limit,
            "state_counts": state_counts,
            "note": note,
            "points": ordered,
        },
    )


def build_round_validation_audit(
    *,
    cfg: Dict[str, Any],
    target: str,
    round_index: int,
    validations: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    points = [
        {
            "candidate_index": safe_int(row.get("candidate_index"), 0),
            "raw_candidate_index": safe_int(row.get("raw_candidate_index"), safe_int(row.get("candidate_index"), 0)),
            "topk_rank": row.get("topk_rank"),
            "mask": row.get("mask"),
            **compact_validation_audit(row),
        }
        for row in validations
    ]
    reject_counts: Dict[str, int] = {}
    accepted_count = 0
    for point in points:
        if point.get("accepted"):
            accepted_count += 1
            continue
        reason = str(point.get("accept_reject_reason") or "unknown")
        reject_counts[reason] = reject_counts.get(reason, 0) + 1
    return with_workload(
        cfg,
        {
            "event": "round_real_validation_audit",
            "target": target,
            "round": round_index,
            "validation_count": len(points),
            "accepted_count": accepted_count,
            "reject_reasons": reject_counts,
            "points": points,
        },
    )


def normalize_ranges_payload(
    payload: Any,
    active: Sequence[str],
    fallback_ranges: Dict[str, Tuple[float, float]],
) -> Dict[str, Tuple[float, float]]:
    ranges = dict(fallback_ranges)
    if not isinstance(payload, dict):
        return ranges
    for name in active:
        raw = payload.get(name)
        if isinstance(raw, (list, tuple)) and len(raw) >= 2:
            ranges[name] = (safe_float(raw[0], ranges[name][0]), safe_float(raw[1], ranges[name][1]))
        elif isinstance(raw, dict):
            ranges[name] = (
                safe_float(raw.get("min_value", raw.get("low")), ranges[name][0]),
                safe_float(raw.get("max_value", raw.get("high")), ranges[name][1]),
            )
    return ranges


def probe_from_validation_changes(
    validation: Dict[str, Any],
    fallback_probe: Dict[str, Any],
) -> Dict[str, Any]:
    changes = validation.get("plan_input_changes") if isinstance(validation.get("plan_input_changes"), dict) else {}
    candidate_avg = changes.get("candidate_resource_avg") if isinstance(changes.get("candidate_resource_avg"), dict) else {}
    candidate_profile = (
        changes.get("candidate_resource_error_profile")
        if isinstance(changes.get("candidate_resource_error_profile"), dict)
        else {}
    )
    if candidate_avg:
        return {
            "resource_avg": copy.deepcopy(candidate_avg),
            "resource_error_profile": copy.deepcopy(candidate_profile),
        }
    resources = validation.get("resources") if isinstance(validation.get("resources"), dict) else {}
    if resources:
        return {
            "resource_avg": {name: safe_float(resources.get(name), 0.0) for name in ("cpu", "memory", "io")},
            "resource_error_profile": {},
        }
    return copy.deepcopy(fallback_probe)


def replay_existing_stage_state(
    *,
    cfg: Dict[str, Any],
    target: str,
    log_path: Path,
    base_params: Dict[str, float],
    initial_ranges: Dict[str, Tuple[float, float]],
    active: Sequence[str],
    baseline_probe: Dict[str, Any],
) -> Dict[str, Any]:
    rows = load_jsonl(log_path)
    baseline_validation: Optional[Dict[str, Any]] = None
    baseline_worker_validations: List[Dict[str, Any]] = []
    round_summaries: List[Dict[str, Any]] = []
    completed_validations: List[Dict[str, Any]] = []
    retryable_failed_validation_count = 0
    replayed_validations_by_round: Dict[int, List[Dict[str, Any]]] = {}
    replayed_pruned_by_round: Dict[int, List[Dict[str, Any]]] = {}
    replayed_operator_rule_rejected_by_round: Dict[int, List[Dict[str, Any]]] = {}
    outer_accepted_by_key: Dict[Tuple[int, str], Dict[str, Any]] = {}
    seen_signatures: Set[str] = {param_signature(base_params)}
    previous_stage_stops: List[str] = []
    previous_stage_summaries = 0
    applied_resume_override_ids: Set[str] = set()
    pending_resume_override: Optional[Dict[str, Any]] = None

    for row in rows:
        event = row.get("event")
        if event == "parallel_baseline_validation_start" and row.get("baseline_refresh_id"):
            baseline_worker_validations = []
        elif event == "baseline_validation":
            baseline_validation = copy.deepcopy(row)
            if row.get("baseline_refresh_id"):
                completed_validations = []
                replayed_validations_by_round = {}
                replayed_pruned_by_round = {}
                replayed_operator_rule_rejected_by_round = {}
        elif event == "baseline_worker_validation":
            baseline_worker_validations.append(copy.deepcopy(row))
        elif event == "round_summary":
            round_summaries.append(copy.deepcopy(row))
            if (
                pending_resume_override
                and safe_int(row.get("round"), 0)
                >= safe_int(pending_resume_override.get("resume_next_round"), 0)
            ):
                pending_resume_override = None
        elif event == "candidate_validation":
            status = str(row.get("status") or "").strip().lower()
            reason = str(
                row.get("accept_reject_reason")
                or row.get("reject_reason")
                or row.get("accept_reason")
                or ""
            ).strip().lower()
            error_text = str(row.get("error") or "").lower()
            retryable_infra_failure = (
                status not in {"", "ok"}
                and reason == "workload_failed"
                and (
                    "connection refused" in error_text
                    or "could not connect to server" in error_text
                    or "is the server running" in error_text
                    or "i/o operation on closed file" in error_text
                    or "closed file" in error_text
                    or "broken pipe" in error_text
                    or (
                        "timed out" in error_text
                        and ("create database" in error_text or "drop database" in error_text)
                    )
                )
            )
            if retryable_infra_failure:
                retryable_failed_validation_count += 1
                continue
            completed_validations.append(copy.deepcopy(row))
            params = row.get("params") if isinstance(row.get("params"), dict) else {}
            if params:
                seen_signatures.add(param_signature(params))
        elif event == "candidate_replay_pruned":
            pruned_row = copy.deepcopy(row)
            round_index = safe_int(pruned_row.get("round"), 0)
            if round_index > 0:
                replayed_pruned_by_round.setdefault(round_index, []).append(pruned_row)
            params = row.get("params") if isinstance(row.get("params"), dict) else {}
            if params:
                seen_signatures.add(param_signature(params))
        elif event == "operator_resource_rule_reject":
            rejected_row = copy.deepcopy(row)
            round_index = safe_int(rejected_row.get("round"), 0)
            if round_index > 0:
                replayed_operator_rule_rejected_by_round.setdefault(round_index, []).append(
                    rejected_row
                )
            params = row.get("params") if isinstance(row.get("params"), dict) else {}
            if params:
                seen_signatures.add(param_signature(params))
        elif event == "outer_accepted_candidate":
            key = (safe_int(row.get("round"), 0), str(row.get("label") or ""))
            outer_accepted_by_key[key] = copy.deepcopy(row)
        elif event == "stage_stop" and row.get("reason"):
            previous_stage_stops.append(str(row.get("reason")))
        elif event == "stage_summary":
            previous_stage_summaries += 1
        elif event == "stage_resume":
            applied_override = row.get("resume_range_override")
            if isinstance(applied_override, dict) and applied_override.get("id"):
                applied_resume_override_ids.add(str(applied_override["id"]))
                pending_resume_override = {
                    "override": copy.deepcopy(applied_override),
                    "resume_next_round": safe_int(row.get("resume_next_round"), 0),
                    "resume_restored_ranges": copy.deepcopy(row.get("resume_restored_ranges") or {}),
                }

    if baseline_validation is None:
        raise RuntimeError(f"--resume requested but baseline_validation was not found in {log_path}")
    if baseline_validation.get("status") != "ok":
        raise RuntimeError(
            f"--resume requested but existing baseline failed for target={target}: "
            f"{baseline_validation.get('error') or baseline_validation.get('runtime_errors')}"
        )

    current_best = copy.deepcopy(baseline_validation)
    current_best_probe = copy.deepcopy(baseline_probe)
    baseline_by_worker = baseline_map_from_validations(baseline_worker_validations)
    worker_policy = worker_baseline_policy(cfg, target, validation_workers(cfg, target))
    use_same_worker_baseline = bool(
        worker_policy.get("enabled")
        and worker_policy.get("compare_mode") == "same_worker"
        and baseline_by_worker
    )
    replay_accept_count = 0
    for validation in completed_validations:
        replay_validation = copy.deepcopy(validation)
        outer_accepted = outer_accepted_by_key.get(
            (safe_int(validation.get("round"), 0), str(validation.get("label") or ""))
        )
        if outer_accepted is not None:
            replay_validation.update(
                {
                    "accepted": True,
                    "accept_reject_reason": "accepted_by_outer_semantics",
                    "accept_reason": "accepted_by_outer_semantics",
                    "reject_reason": "",
                    "outer_semantic_decision": outer_accepted.get("outer_semantic_decision") or {},
                }
            )
        if "accepted" in replay_validation:
            accepted = bool(replay_validation.get("accepted"))
            reason = str(
                replay_validation.get("accept_reject_reason")
                or replay_validation.get("accept_reason")
                or replay_validation.get("reject_reason")
                or ("accepted" if accepted else "rejected")
            )
            decision = (
                replay_validation.get("decision")
                if isinstance(replay_validation.get("decision"), dict)
                else {}
            )
        else:
            comparison_best = current_best
            comparison_baseline = baseline_validation
            comparison_key = validation_worker_identity(validation)
            comparison_matched = False
            if use_same_worker_baseline:
                comparison_best, comparison_key, comparison_matched = resolve_worker_baseline(
                    validation,
                    baseline_validation,
                    baseline_by_worker,
                )
                comparison_baseline = comparison_best
            accepted, reason, decision = compare_actual(
                validation,
                comparison_best,
                comparison_baseline,
                target=target,
                cfg=cfg,
            )
            if use_same_worker_baseline:
                decision["comparison_baseline_mode"] = "same_worker"
                decision["comparison_baseline_key"] = comparison_key
                decision["comparison_baseline_matched"] = comparison_matched
                decision["comparison_baseline_label"] = comparison_baseline.get("label")
            replay_validation.update(
                {
                    "accepted": accepted,
                    "accept_reject_reason": reason,
                    "accept_reason": reason if accepted else "",
                    "reject_reason": "" if accepted else reason,
                    "decision": decision,
                }
            )
        if accepted:
            replay_accept_count += 1
            current_best = select_best_accepted(current_best, [replay_validation], target=target)
            current_best_probe = probe_from_validation_changes(replay_validation, baseline_probe)
        round_index = safe_int(replay_validation.get("round"), 0)
        if round_index > 0:
            replayed_validations_by_round.setdefault(round_index, []).append(replay_validation)

    restored_ranges = dict(initial_ranges)
    max_completed_round = 0
    no_improvement_rounds = 0
    resume_range_override: Optional[Dict[str, Any]] = None
    if round_summaries:
        last_summary = round_summaries[-1]
        restored_ranges = normalize_ranges_payload(last_summary.get("updated_ranges"), active, initial_ranges)
        max_completed_round = max(safe_int(row.get("round"), 0) for row in round_summaries)
        no_improvement_rounds = safe_int(last_summary.get("no_improvement_rounds"), 0)

        bpt = cfg.get("binary_plan_tuning") if isinstance(cfg.get("binary_plan_tuning"), dict) else {}
        search_cfg = bpt.get("binary_search") if isinstance(bpt.get("binary_search"), dict) else {}
        overrides = (
            search_cfg.get("resume_range_overrides")
            if isinstance(search_cfg.get("resume_range_overrides"), dict)
            else {}
        )
        target_override = overrides.get(target) if isinstance(overrides.get(target), dict) else {}
        apply_after_round = safe_int(target_override.get("apply_after_completed_round"), -1)
        override_id = str(target_override.get("id") or f"{target}_after_round_{apply_after_round}")
        if target_override and apply_after_round == max_completed_round and override_id not in applied_resume_override_ids:
            override_ranges = (
                target_override.get("ranges")
                if isinstance(target_override.get("ranges"), dict)
                else {}
            )
            missing = [name for name in active if name not in override_ranges]
            if missing:
                raise ValueError(
                    f"resume range override for target={target} is missing active params: {', '.join(missing)}"
                )
            restored_ranges = normalize_ranges_payload(override_ranges, active, initial_ranges)
            no_improvement_rounds = 0
            resume_range_override = {
                "id": override_id,
                "reason": str(target_override.get("reason") or "operator_configured_resume_range_override"),
                "apply_after_completed_round": apply_after_round,
                "resume_from_round": max(
                    1,
                    safe_int(target_override.get("resume_from_round"), max_completed_round + 1),
                ),
                "rebaseline": to_bool(target_override.get("rebaseline"), False),
                "clear_existing_round_state": True,
                "ranges": {name: list(restored_ranges[name]) for name in active},
            }

        if resume_range_override is None and pending_resume_override:
            pending_override = pending_resume_override.get("override")
            pending_ranges = pending_resume_override.get("resume_restored_ranges")
            if isinstance(pending_override, dict) and isinstance(pending_ranges, dict):
                restored_ranges = normalize_ranges_payload(pending_ranges, active, initial_ranges)
                resume_range_override = copy.deepcopy(pending_override)
                resume_range_override["resume_from_round"] = max(
                    1,
                    safe_int(pending_resume_override.get("resume_next_round"), max_completed_round + 1),
                )
                resume_range_override["rebaseline"] = False
                resume_range_override["clear_existing_round_state"] = False
                resume_range_override["pending_round_resume"] = True
                resume_range_override["ranges"] = {
                    name: list(restored_ranges[name]) for name in active
                }
                no_improvement_rounds = 0

        if (
            resume_range_override is None
            and previous_stage_stops
            and previous_stage_stops[-1] == "no_range_anchor_after_zero_accepted_round"
            and last_summary.get("range_update_anchor") == "baseline_fallback_after_no_round_anchor"
        ):
            last_round = safe_int(last_summary.get("round"), max_completed_round)
            round_validations = replayed_validations_by_round.get(last_round) or []

            def replay_baseline_resolver(validation: Dict[str, Any]) -> Dict[str, Any]:
                resolved, _, _ = resolve_worker_baseline(
                    validation,
                    baseline_validation,
                    baseline_by_worker,
                )
                return resolved

            near_anchor, near_details = select_range_anchor(
                round_validations,
                baseline_validation,
                target=target,
                cfg=cfg,
                baseline_resolver=replay_baseline_resolver if use_same_worker_baseline else None,
                allow_near_miss=True,
            )
            if near_anchor is not None and near_details is not None:
                anchor_ranges = normalize_ranges_payload(near_anchor.get("ranges"), active, initial_ranges)
                restored_ranges = update_ranges_around_best(
                    anchor_ranges,
                    active,
                    near_anchor.get("params") or base_params,
                    tie_break="low",
                )
                no_improvement_rounds = 0
                resume_range_override = {
                    "id": f"{target}_auto_near_miss_after_round_{last_round}",
                    "reason": "auto_resume_from_near_miss_after_no_range_anchor",
                    "apply_after_completed_round": last_round,
                    "resume_from_round": max_completed_round + 1,
                    "rebaseline": False,
                    "clear_existing_round_state": False,
                    "auto_generated": True,
                    "near_miss_label": near_anchor.get("label"),
                    "near_miss_details": {
                        "target_value": near_details.get("target_value"),
                        "baseline_target_value": near_details.get("baseline_target_value"),
                        "target_sweet_spot_distance": near_details.get("target_sweet_spot_distance"),
                        "target_sweet_spot_distance_unit": near_details.get("target_sweet_spot_distance_unit"),
                        "db_qps": near_details.get("db_qps"),
                        "db_qps_delta_pct_vs_baseline": near_details.get("db_qps_delta_pct_vs_baseline"),
                        "target_delta_pct_vs_baseline": near_details.get("target_delta_pct_vs_baseline"),
                        "other_up_resources": near_details.get("other_up_resources") or [],
                    },
                    "ranges": {name: list(restored_ranges[name]) for name in active},
                }

    return {
        "baseline_validation": baseline_validation,
        "baseline_worker_validations": baseline_worker_validations,
        "baseline_by_worker": baseline_by_worker,
        "worker_baseline_policy": worker_policy,
        "current_best": current_best,
        "current_best_probe": current_best_probe,
        "validated_signatures": seen_signatures,
        "rounds": round_summaries,
        "ranges": restored_ranges,
        "next_round": (
            safe_int(resume_range_override.get("resume_from_round"), max_completed_round + 1)
            if resume_range_override
            else max_completed_round + 1
        ),
        "completed_round_count": len(round_summaries),
        "completed_validation_count": len(completed_validations),
        "retryable_failed_validation_count": retryable_failed_validation_count,
        "replayed_validations_by_round": replayed_validations_by_round,
        "replayed_pruned_by_round": replayed_pruned_by_round,
        "replayed_operator_rule_rejected_by_round": replayed_operator_rule_rejected_by_round,
        "no_improvement_rounds": no_improvement_rounds,
        "previous_stage_stop_reasons": previous_stage_stops,
        "previous_stage_summary_count": previous_stage_summaries,
        "candidate_replay_accept_count": replay_accept_count,
        "resume_range_override": resume_range_override,
    }


def write_round_handoff_snapshot(
    *,
    out_dir: Path,
    cfg: Dict[str, Any],
    target: str,
    round_summary: Dict[str, Any],
    baseline_validation: Dict[str, Any],
    current_best: Dict[str, Any],
    active: Sequence[str],
    max_rounds: int,
    required_rounds: int,
    required_rounds_by_param: Dict[str, int],
    topk_limit: int,
    log_path: Path,
    stop_only_on_epsilon: bool,
    no_improvement_round_limit: int,
) -> None:
    snapshot_dir = out_dir / "handoff_snapshots"
    round_index = safe_int(round_summary.get("round"), 0)
    prefix = f"{target}_round_{round_index:03d}"
    snapshot = {
        "workload": workload_name(cfg),
        "target": target,
        "round": round_index,
        "run_dir": str(out_dir),
        "log_path": str(log_path),
        "active_params": list(active),
        "max_rounds": max_rounds,
        "required_rounds_for_epsilon": required_rounds,
        "required_rounds_by_param": required_rounds_by_param,
        "stop_policy": {
            "stop_only_on_epsilon": stop_only_on_epsilon,
            "no_improvement_round_limit": no_improvement_round_limit,
            "no_improvement_stop_enabled": bool(
                (not stop_only_on_epsilon) and no_improvement_round_limit > 0
            ),
        },
        "candidate_policy": {
            "full_round_search_points": round_summary.get("full_round_search_points"),
            "raw_search_point_count": round_summary.get("raw_search_point_count"),
            "topk_limit": topk_limit,
            "topk_candidate_count": round_summary.get("topk_candidate_count"),
            "replay_pruned_count": round_summary.get("replay_pruned_count"),
            "validation_count": round_summary.get("validation_count"),
        },
        "parallelism": {
            "plan_probe_parallelism": round_summary.get("plan_probe_parallelism"),
            "validation_parallelism": round_summary.get("validation_parallelism"),
            "validation_batch_count": round_summary.get("validation_batch_count"),
        },
        "updated_ranges": round_summary.get("updated_ranges"),
        "best_candidate": round_summary.get("best_candidate"),
        "baseline": summarize_validation(baseline_validation, target),
        "current_best": summarize_validation(current_best, target),
        "range_update_anchor": round_summary.get("range_update_anchor"),
        "range_anchor_label": round_summary.get("range_anchor_label"),
        "reject_reasons": round_summary.get("reject_reasons"),
        "replay_pruning_reason_counts": round_summary.get("replay_pruning_reason_counts"),
        "round_elapsed_s": round_summary.get("round_elapsed_s"),
        "validation_elapsed_s": round_summary.get("validation_elapsed_s"),
    }
    write_json(snapshot_dir / f"{prefix}_snapshot.json", snapshot)

    lines = [
        f"# Handoff Snapshot: {target} round {round_index}",
        "",
        f"- workload: `{workload_name(cfg)}`",
        f"- run_dir: `{out_dir}`",
        f"- log_path: `{log_path}`",
        f"- active_params: `{', '.join(active)}`",
        f"- max_rounds/effective: `{max_rounds}`; required_for_epsilon: `{required_rounds}`",
        f"- raw_search_points: `{round_summary.get('raw_search_point_count')}`; topk_limit: `{topk_limit}`; validated: `{round_summary.get('validation_count')}`",
        f"- validation_parallelism/batches: `{round_summary.get('validation_parallelism')}` / `{round_summary.get('validation_batch_count')}`",
        f"- stop_policy: `epsilon_only={stop_only_on_epsilon}`, `no_improvement_limit={no_improvement_round_limit}`",
        f"- range_anchor: `{round_summary.get('range_anchor_label')}` via `{round_summary.get('range_update_anchor')}`",
        f"- stop_reason_in_round: `{round_summary.get('stop_reason') or ''}`",
        "",
        "## Next Agent Checklist",
        "",
        "- Do not kill a running binary-search stage unless the user explicitly asks.",
        "- Treat the stage as complete only when the latest stage_stop reason is `epsilon_converged` or all active ranges are within epsilon.",
        f"- Keep the per-round real replay candidate cap at `{topk_limit}` for this run; do not revert to top20.",
        "- This rebuild uses full real HyBench XP replay for all selected binary points; do not enable estimator-only pruning unless the user explicitly changes the policy.",
        "- CPU, Memory, none, and IO stages restart from the same initial_params and initial_ranges; do not inherit params across targets.",
        "- Run target stages serially in CPU, Memory, none, IO order for this experiment.",
        "- IO acceptance requires IO down and Memory up relative to the same-worker baseline.",
        "- IO validation parallelism stays capped at 2 workers bound to /disk1 and /disk2.",
    ]
    (snapshot_dir / f"{prefix}_handoff.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _finite_number(value: Any) -> Optional[float]:
    parsed = safe_float(value, math.nan)
    if math.isfinite(parsed):
        return parsed
    return None


def _linear_fit(xs: Sequence[float], ys: Sequence[float]) -> Dict[str, float]:
    if not xs or not ys or len(xs) != len(ys):
        return {"slope": 0.0, "intercept": 0.0}
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    variance = sum((x - mean_x) ** 2 for x in xs)
    if variance <= 1e-18:
        return {"slope": 0.0, "intercept": mean_y}
    covariance = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    slope = covariance / variance
    intercept = mean_y - slope * mean_x
    return {"slope": slope, "intercept": intercept}


def _linear_predict(model: Dict[str, float], raw_value: float) -> float:
    return max(0.0, safe_float(model.get("intercept"), 0.0) + safe_float(model.get("slope"), 0.0) * raw_value)


def _residual_metrics(xs: Sequence[float], ys: Sequence[float], model: Dict[str, float]) -> Dict[str, Any]:
    if not xs:
        return {"count": 0}
    predictions = [_linear_predict(model, x) for x in xs]
    residuals = [y - pred for y, pred in zip(ys, predictions)]
    abs_residuals = [abs(value) for value in residuals]
    relative_errors = [
        abs_residual / max(abs(actual), 1e-9)
        for abs_residual, actual in zip(abs_residuals, ys)
    ]
    return {
        "count": len(xs),
        "abs_error_p50": percentile(abs_residuals, 0.50),
        "abs_error_p90": percentile(abs_residuals, 0.90),
        "abs_error_p95": percentile(abs_residuals, 0.95),
        "abs_error_p99": percentile(abs_residuals, 0.99),
        "abs_error_max": max(abs_residuals) if abs_residuals else None,
        "relative_error_p50": percentile(relative_errors, 0.50),
        "relative_error_p90": percentile(relative_errors, 0.90),
        "relative_error_p95": percentile(relative_errors, 0.95),
        "prediction_min": min(predictions) if predictions else None,
        "prediction_max": max(predictions) if predictions else None,
        "actual_min": min(ys) if ys else None,
        "actual_max": max(ys) if ys else None,
    }


def _collect_error_bound_training_rows(out_dir: Path, cfg: Dict[str, Any], target_filter: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    suffix = workload_log_suffix(cfg)
    targets = list(stage_targets()) if target_filter == "all" else [target_filter]
    rows: List[Dict[str, Any]] = []
    source_logs: List[str] = []
    for target in targets:
        log_path = out_dir / f"{target}_binary_search_{suffix}.jsonl"
        if not log_path.exists():
            continue
        source_logs.append(str(log_path))
        for record in load_jsonl(log_path):
            if record.get("event") != "candidate_validation":
                continue
            if record.get("status") != "ok":
                continue
            changes = record.get("plan_input_changes") if isinstance(record.get("plan_input_changes"), dict) else {}
            raw_pred = (
                changes.get("raw_candidate_resource_avg")
                if isinstance(changes.get("raw_candidate_resource_avg"), dict)
                else changes.get("candidate_resource_avg")
            )
            actual = record.get("resources") if isinstance(record.get("resources"), dict) else {}
            if not isinstance(raw_pred, dict) or not actual:
                continue
            row = {
                "stage_target": target,
                "round": record.get("round"),
                "candidate_index": record.get("candidate_index"),
                "raw_prediction": {
                    resource: _finite_number(raw_pred.get(resource))
                    for resource in ("cpu", "memory", "io")
                },
                "actual": {
                    resource: _finite_number(actual.get(resource))
                    for resource in ("cpu", "memory", "io")
                },
            }
            if any(row["raw_prediction"].get(resource) is not None for resource in ("cpu", "memory", "io")):
                rows.append(row)
    return rows, source_logs


def _fit_resource_error_entry(
    rows: Sequence[Dict[str, Any]],
    resource: str,
    *,
    quantile: float,
    safety_factor: float,
    min_samples: int,
) -> Dict[str, Any]:
    samples: List[Tuple[float, float]] = []
    for row in rows:
        raw = row.get("raw_prediction") if isinstance(row.get("raw_prediction"), dict) else {}
        actual = row.get("actual") if isinstance(row.get("actual"), dict) else {}
        x = raw.get(resource)
        y = actual.get(resource)
        if isinstance(x, (int, float)) and isinstance(y, (int, float)) and math.isfinite(x) and math.isfinite(y):
            samples.append((float(x), float(y)))
    unit_by_resource = {"cpu": "cores", "memory": "GB", "io": "MB/s"}
    if len(samples) < min_samples:
        return {
            "available": False,
            "resource": resource,
            "unit": unit_by_resource.get(resource),
            "sample_count": len(samples),
            "min_samples": min_samples,
            "reason": "insufficient_current_task_samples",
        }
    train: List[Tuple[float, float]] = []
    test: List[Tuple[float, float]] = []
    for idx, sample in enumerate(samples):
        if idx % 5 == 0:
            test.append(sample)
        else:
            train.append(sample)
    if len(train) < max(2, min_samples // 2):
        train = list(samples)
        test = []
    train_x = [item[0] for item in train]
    train_y = [item[1] for item in train]
    model = _linear_fit(train_x, train_y)
    all_x = [item[0] for item in samples]
    all_y = [item[1] for item in samples]
    train_metrics = _residual_metrics(train_x, train_y, model)
    test_metrics = _residual_metrics([item[0] for item in test], [item[1] for item in test], model) if test else {"count": 0}
    all_metrics = _residual_metrics(all_x, all_y, model)
    all_residuals = [abs(y - _linear_predict(model, x)) for x, y in samples]
    test_residuals = [abs(y - _linear_predict(model, x)) for x, y in test]
    selected_pool = test_residuals if len(test_residuals) >= max(5, min_samples // 5) else all_residuals
    selected_bound = safe_float(percentile(selected_pool, quantile), 0.0) * max(1.0, safety_factor)
    return {
        "available": True,
        "resource": resource,
        "unit": unit_by_resource.get(resource),
        "sample_count": len(samples),
        "train_count": len(train),
        "test_count": len(test),
        "model": {
            "type": "linear_raw_plan_prediction_to_actual_replay_resource",
            "slope": model["slope"],
            "intercept": model["intercept"],
            "clip_min": 0.0,
        },
        "metrics": {
            "train": train_metrics,
            "test": test_metrics,
            "all": all_metrics,
        },
        "error_bound": {
            "value": selected_bound,
            "unit": unit_by_resource.get(resource),
            "quantile": quantile,
            "safety_factor": safety_factor,
            "source": "current_task_retrained_replay_residual_bound",
            "residual_pool": "test" if selected_pool is test_residuals else "all",
        },
        "raw_prediction_min": min(all_x),
        "raw_prediction_max": max(all_x),
        "actual_min": min(all_y),
        "actual_max": max(all_y),
    }


def calibrate_error_bounds(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    out_dir = output_dir_from_args(cfg, args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    pruning_cfg = replay_pruning_config(cfg)
    quantile = min(0.999, max(0.50, safe_float(getattr(args, "quantile", 0.0), 0.0) or safe_float(pruning_cfg.get("calibration_error_quantile"), 0.95)))
    safety_factor = max(1.0, safe_float(getattr(args, "safety_factor", 0.0), 0.0) or safe_float(pruning_cfg.get("calibration_safety_factor"), 1.1))
    min_samples = max(2, safe_int(pruning_cfg.get("min_samples_per_resource"), 20))
    target_filter = str(getattr(args, "target", "all") or "all").strip().lower()
    rows, source_logs = _collect_error_bound_training_rows(out_dir, cfg, target_filter)
    resources = {
        resource: _fit_resource_error_entry(
            rows,
            resource,
            quantile=quantile,
            safety_factor=safety_factor,
            min_samples=min_samples,
        )
        for resource in ("cpu", "memory", "io")
    }
    artifact_path = replay_error_bound_artifact_path(cfg, out_dir)
    artifact = {
        "workload": workload_name(cfg),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "run_dir": str(out_dir),
        "source_logs": source_logs,
        "target_filter": target_filter,
        "model_version": "binary_plan_replay_error_bounds_v1",
        "sample_row_count": len(rows),
        "error_quantile": quantile,
        "safety_factor": safety_factor,
        "min_samples_per_resource": min_samples,
        "resources": resources,
    }
    write_json(artifact_path, artifact)
    log_path = out_dir / f"resource_error_bound_calibration_{workload_log_suffix(cfg)}.jsonl"
    append_jsonl(
        log_path,
        with_workload(
            cfg,
            {
                "event": "resource_error_bound_calibration",
                "artifact_path": str(artifact_path),
                "target_filter": target_filter,
                "sample_row_count": len(rows),
                "resources": resources,
            },
        ),
    )
    md_path = artifact_path.with_suffix(".md")
    lines = [
        f"# Resource Error Bounds: {workload_name(cfg)}",
        "",
        f"- generated_at: `{artifact['generated_at']}`",
        f"- run_dir: `{out_dir}`",
        f"- source_logs: `{len(source_logs)}`",
        f"- sample_rows: `{len(rows)}`",
        f"- quantile/safety: `{quantile}` / `{safety_factor}`",
        "",
        "| resource | available | samples | model | error_bound | all_p95 | test_p95 | actual_range | raw_prediction_range |",
        "|---|---:|---:|---|---:|---:|---:|---|---|",
    ]
    for resource, entry in resources.items():
        model = entry.get("model") if isinstance(entry.get("model"), dict) else {}
        metrics = entry.get("metrics") if isinstance(entry.get("metrics"), dict) else {}
        all_metrics = metrics.get("all") if isinstance(metrics.get("all"), dict) else {}
        test_metrics = metrics.get("test") if isinstance(metrics.get("test"), dict) else {}
        error_bound = entry.get("error_bound") if isinstance(entry.get("error_bound"), dict) else {}
        lines.append(
            "| {resource} | {available} | {samples} | slope={slope:.6g}, intercept={intercept:.6g} | {bound:.6g} {unit} | {all_p95:.6g} | {test_p95:.6g} | {actual_min:.6g}..{actual_max:.6g} | {raw_min:.6g}..{raw_max:.6g} |".format(
                resource=resource,
                available=str(bool(entry.get("available"))).lower(),
                samples=safe_int(entry.get("sample_count"), 0),
                slope=safe_float(model.get("slope"), 0.0),
                intercept=safe_float(model.get("intercept"), 0.0),
                bound=safe_float(error_bound.get("value"), 0.0),
                unit=entry.get("unit") or "",
                all_p95=safe_float(all_metrics.get("abs_error_p95"), 0.0),
                test_p95=safe_float(test_metrics.get("abs_error_p95"), 0.0),
                actual_min=safe_float(entry.get("actual_min"), 0.0),
                actual_max=safe_float(entry.get("actual_max"), 0.0),
                raw_min=safe_float(entry.get("raw_prediction_min"), 0.0),
                raw_max=safe_float(entry.get("raw_prediction_max"), 0.0),
            )
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    repo_report = getattr(args, "repo_report", "") or ""
    if repo_report:
        repo_path = Path(repo_report)
        if not repo_path.is_absolute():
            repo_path = ROOT / repo_path
        repo_path.parent.mkdir(parents=True, exist_ok=True)
        repo_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"resource error bounds wrote {artifact_path}", flush=True)
    print(f"resource error bound calibration log wrote {log_path}", flush=True)
    return 0


def binary_search(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    target = str(args.target).strip().lower()
    if target not in set(stage_targets()):
        raise ValueError("--target must be cpu, memory, io, or none")
    method_identity_error = binary_tuner_method_identity_error(cfg)
    if method_identity_error:
        print(method_identity_error, file=sys.stderr, flush=True)
        return 5
    lock_error = exclusive_vm_disk_lock_error(cfg, target)
    if lock_error:
        print(lock_error, file=sys.stderr, flush=True)
        return 4
    out_dir = output_dir_from_args(cfg, args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    error_bound_artifact_path = install_replay_error_bound_artifact(cfg, out_dir)
    suffix = workload_log_suffix(cfg)
    log_path = out_dir / f"{target}_binary_search_{suffix}.jsonl"
    resume_requested = to_bool(getattr(args, "resume", False), False)
    baseline_only = to_bool(getattr(args, "baseline_only", False), False)
    # The launcher always supplies --resume so that a systemd restart can pick up
    # an interrupted run. A new output directory has nothing to replay and must
    # first take the normal baseline-collection path.
    resume_log_rows = load_jsonl(log_path) if resume_requested else []
    resume_has_baseline = any(row.get("event") == "baseline_validation" for row in resume_log_rows)
    resume_existing_stage = bool(resume_requested and resume_has_baseline)
    if not resume_requested:
        log_path.write_text("", encoding="utf-8")

    external_stop_counts: Dict[str, int] = dict(_EARLY_EXTERNAL_STOP_COUNTS)
    _EARLY_EXTERNAL_STOP_COUNTS.clear()

    def _continue_on_external_stop(signum: int, _frame: Any) -> None:
        signal_name = _signal_name(signum)
        external_stop_counts[signal_name] = external_stop_counts.get(signal_name, 0) + 1

    def flush_external_stop_summary(phase: str) -> None:
        if not external_stop_counts:
            return
        append_jsonl(
            log_path,
            with_workload(
                cfg,
                {
                    "event": "external_stop_signal_summary",
                    "target": target,
                    "pid": os.getpid(),
                    "phase": phase,
                    "signals": dict(external_stop_counts),
                    "detail": "external TERM/HUP signals were ignored and counted outside the signal handler",
                },
            ),
        )
        external_stop_counts.clear()

    for stop_signal in (signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(stop_signal, _continue_on_external_stop)
        except Exception:
            pass

    stage_started = time.perf_counter()
    bpt = cfg.get("binary_plan_tuning") or {}
    search_cfg = bpt.get("binary_search") or {}
    configured_max_rounds = safe_int(search_cfg.get("max_rounds"), 6)
    configured_max_candidates = safe_int(search_cfg.get("max_candidates_per_round"), 0)
    accepted_shrink_target = max(0, safe_int(search_cfg.get("accepted_shrink_target"), 0))
    full_bruteforce = to_bool(search_cfg.get("full_bruteforce"), False)
    stop_only_on_epsilon = to_bool(search_cfg.get("stop_only_on_epsilon"), full_bruteforce)
    disable_candidate_reduction = full_bruteforce or to_bool(search_cfg.get("disable_candidate_reduction"), False)
    no_improvement_round_limit = safe_int(search_cfg.get("no_improvement_round_limit"), 0)
    zero_accepted_backtrack = to_bool(search_cfg.get("zero_accepted_backtrack"), True)
    zero_accepted_backtrack_factor = max(1.0, safe_float(search_cfg.get("zero_accepted_backtrack_factor"), 2.0))
    range_anchor_policy = str(search_cfg.get("range_anchor_policy") or "legacy_global_best_backtrack")
    experiment_strategy = str(search_cfg.get("experiment_strategy") or "legacy").upper()
    epsilon_cfg = search_cfg.get("epsilon") if isinstance(search_cfg.get("epsilon"), dict) else {"default": 1e-6}

    base_params = initial_params(cfg)
    ranges = param_ranges(cfg)
    initial_search_ranges = dict(ranges)
    active = load_active_params(out_dir, args.active_params)
    active = [name for name in active if name in COST_PARAMS]
    required_rounds, required_rounds_by_param = required_rounds_for_epsilon(ranges, active, epsilon_cfg)
    requested_max_rounds = safe_int(args.max_rounds, 0)
    max_rounds = requested_max_rounds if requested_max_rounds > 0 else configured_max_rounds
    if max_rounds <= 0:
        max_rounds = required_rounds if stop_only_on_epsilon else 6
    if stop_only_on_epsilon and requested_max_rounds <= 0:
        max_rounds = max(max_rounds, required_rounds)
    max_rounds = max(1, max_rounds)
    max_candidates = (
        safe_int(args.max_candidates, 0)
        if safe_int(args.max_candidates, 0) > 0
        else configured_max_candidates
    )
    if disable_candidate_reduction:
        max_candidates = 0
    if range_anchor_policy == SMALL_LOOP_TOP1_ANCHOR_POLICY and max_candidates != 1:
        raise ValueError(
            f"{SMALL_LOOP_TOP1_ANCHOR_POLICY} requires max_candidates_per_round=1; got {max_candidates}"
        )
    if range_anchor_policy in (
        REAL_SCORE_B_ANCHOR_POLICY,
        REAL_SCORE_C_ANCHOR_POLICY,
        NESTED_RESOURCE_SEMANTIC_POLICY,
    ) and max_candidates <= 1:
        raise ValueError(
            f"{range_anchor_policy} requires TopK real replay with K>1; got {max_candidates}"
        )
    full_round_search_points = 2 ** len(active) if active else 0
    probe = PlanFeatureProbe(cfg)
    queries_path = out_dir / "screening_workload_queries.json"
    if queries_path.exists():
        query_payload = read_json(queries_path)
        queries = query_payload.get("queries") or []
        sampling_meta = query_payload.get("sampling") or {}
    else:
        queries, sampling_meta = workload_queries_with_sampling(cfg, probe)
        write_json(queries_path, {"queries": queries, "sampling": sampling_meta})
    baseline_probe = probe.probe(queries, base_params)
    apply_replay_error_bounds_to_probe_result(baseline_probe, cfg)
    plan_coverage_audit = plan_probe_coverage_audit(cfg, baseline_probe)
    append_jsonl(
        log_path,
        with_workload(
            cfg,
            {
                "event": "plan_probe_coverage_gate",
                "target": target,
                **plan_coverage_audit,
            },
        ),
    )
    if (
        plan_coverage_audit.get("enabled")
        and plan_coverage_audit.get("block_physical_base_topk")
        and not plan_coverage_audit.get("passed")
    ):
        append_jsonl(
            log_path,
            with_workload(
                cfg,
                {
                    "event": "stage_stop",
                    "target": target,
                    "reason": "plan_probe_workload_coverage_incomplete",
                    "physical_workload_invocations": 0,
                    "valid_replay_count": 0,
                    "plan_probe_coverage_gate": plan_coverage_audit,
                },
            ),
        )
        print(
            "plan probe workload coverage incomplete; refusing physical baseline "
            f"or Top-K replay: {json.dumps(plan_coverage_audit['checks'], sort_keys=True)}",
            file=sys.stderr,
            flush=True,
        )
        return 6
    workers = validation_workers(cfg, target)
    baseline_policy = worker_baseline_policy(cfg, target, workers)
    validation_cfg = bpt.get("validation") if isinstance(bpt.get("validation"), dict) else {}
    shared_disk_restore_barrier = to_bool(validation_cfg.get("shared_disk_restore_barrier"), False)
    configured_probe_workers = safe_int(search_cfg.get("plan_probe_workers"), 4)
    run_plan_probe_for_full_bruteforce = to_bool(search_cfg.get("run_plan_probe_for_full_bruteforce"), False)
    skip_plan_probe_for_full_replay = bool(disable_candidate_reduction and not run_plan_probe_for_full_bruteforce)
    plan_probe_workers = max(
        1,
        min(max(1, configured_probe_workers), max(1, 2 ** max(len(active), 1))),
    )
    if skip_plan_probe_for_full_replay:
        plan_probe_workers = 0

    operator_rule_cfg = (
        search_cfg.get("operator_resource_reject_rules")
        if isinstance(search_cfg.get("operator_resource_reject_rules"), dict)
        else {}
    )
    workload_cfg = bpt.get("workload") if isinstance(bpt.get("workload"), dict) else {}
    plan_sql_value = workload_cfg.get("plan_sql_file")
    plan_sql_path = resolve_path(plan_sql_value) if plan_sql_value else None
    operator_rule_gate = load_rule_gate(
        operator_rule_cfg,
        workload=workload_name(cfg),
        target=target,
        plan_sql_path=plan_sql_path,
    )

    stage_metadata = {
        "target": target,
        "active_params": active,
        "initial_params": base_params,
        "initial_ranges": {name: list(ranges[name]) for name in active},
        "configured_max_rounds": configured_max_rounds,
        "requested_max_rounds": requested_max_rounds,
        "max_rounds": max_rounds,
        "required_rounds_for_epsilon": required_rounds,
        "required_rounds_by_param": required_rounds_by_param,
        "max_candidates_per_round": max_candidates,
        "accepted_shrink_target": accepted_shrink_target,
        "candidate_topk_limit": max_candidates,
        "full_round_search_points": full_round_search_points,
        "candidate_selection_policy": (
            "probe_all_binary_points_rank_by_small_loop_then_real_replay_top1"
            if range_anchor_policy == SMALL_LOOP_TOP1_ANCHOR_POLICY
            else (
                "probe_all_binary_points_rank_by_small_loop_then_real_replay_topk_score_b"
                if range_anchor_policy == REAL_SCORE_B_ANCHOR_POLICY
                else (
                    "probe_all_binary_points_recall_topk_then_real_score_c_no_qps"
                    if range_anchor_policy == REAL_SCORE_C_ANCHOR_POLICY
                    else (
                        "outer_interval_inner_estimator_topk_real_resource_score_then_outer_semantic_accept"
                        if range_anchor_policy == NESTED_RESOURCE_SEMANTIC_POLICY
                        else (
                            "full_binary_points_real_replay_no_plan_probe_no_pruning"
                            if skip_plan_probe_for_full_replay
                            else "probe_all_binary_points_rank_by_plan_estimator_then_real_replay_topk"
                        )
                    )
                )
            )
        ),
        "experiment_strategy": experiment_strategy,
        "real_score_b": search_cfg.get("real_score_b") or {},
        "real_score_c": search_cfg.get("real_score_c") or {},
        "nested_loop": search_cfg.get("nested_loop") or {},
        "fallback_plan_change_replay_expansion": (
            (search_cfg.get("nested_loop") or {}).get(
                "fallback_plan_change_replay_expansion"
            )
            if isinstance(search_cfg.get("nested_loop"), dict)
            else {}
        ),
        "acceptance_policy": bpt.get("acceptance_policy") or {},
        "replay_pruning_policy": search_cfg.get("replay_candidate_pruning") or {},
        "operator_resource_reject_rules": summarize_rule_gate(operator_rule_gate),
        "replay_error_bound_artifact": str(error_bound_artifact_path) if error_bound_artifact_path else "",
        "replay_error_bound_artifact_loaded": bool(cfg.get("_replay_error_bound_artifact")),
        "full_bruteforce": full_bruteforce,
        "disable_candidate_reduction": disable_candidate_reduction,
        "stop_only_on_epsilon": stop_only_on_epsilon,
        "no_improvement_round_limit": no_improvement_round_limit,
        "no_improvement_stop_enabled": bool((not stop_only_on_epsilon) and no_improvement_round_limit > 0),
        "zero_accepted_backtrack": zero_accepted_backtrack,
        "zero_accepted_backtrack_factor": zero_accepted_backtrack_factor,
        "range_anchor_policy": range_anchor_policy,
        "sampling": sampling_meta,
        "plan_probe_parallelism": plan_probe_workers,
        "run_plan_probe_for_full_bruteforce": run_plan_probe_for_full_bruteforce,
        "skip_plan_probe_for_full_replay": skip_plan_probe_for_full_replay,
        "explain_workers_per_probe": probe.explain_workers,
        "worker_count": len(workers),
        "worker_baseline_policy": baseline_policy,
        "shared_disk_restore_barrier": shared_disk_restore_barrier,
        "workers": [
            {
                "name": worker.get("name"),
                "container": worker.get("container"),
                "host": worker.get("host"),
                "port": worker.get("port"),
                "data_dir": worker.get("data_dir"),
                "disk_root": worker.get("disk_root"),
                "cpuset_cpus": worker.get("cpuset_cpus"),
            }
            for worker in workers
        ],
    }

    def collect_stage_baseline(refresh_id: str = "") -> Tuple[Dict[str, Any], List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
        baseline_rows: List[Dict[str, Any]] = []
        baseline_map: Dict[str, Dict[str, Any]] = {}
        label_tag = f"_{sanitize_label(refresh_id)}" if refresh_id else ""
        if baseline_policy.get("enabled"):
            baseline_started = time.perf_counter()
            append_jsonl(
                log_path,
                with_workload(
                    cfg,
                    {
                        "event": "parallel_baseline_validation_start",
                        "target": target,
                        "baseline_policy": baseline_policy,
                        "worker_count": len(workers),
                        "parallelism": baseline_policy.get("parallelism"),
                        "baseline_refresh_id": refresh_id or None,
                    },
                ),
            )
            prepared_workers = [worker_with_prepared_restore(worker) for worker in workers]
            restore_rows: List[Dict[str, Any]] = []
            append_jsonl(
                log_path,
                with_workload(
                    cfg,
                    {
                        "event": "parallel_worker_restore_start",
                        "target": target,
                        "phase": "baseline",
                        "worker_count": len(workers),
                        "parallelism": 1,
                        "baseline_refresh_id": refresh_id or None,
                    },
                ),
            )
            restore_started = time.perf_counter()
            for slot, worker in enumerate(workers):
                label = f"{target}_baseline{label_tag}_w{slot + 1:02d}_{sanitize_label(worker_identity(worker))}"
                try:
                    row = restore_worker_from_hybench_template(cfg, worker, label=label)
                except Exception as exc:
                    row = {
                        "worker": worker_identity(worker),
                        "status": "failed",
                        "error": str(exc),
                    }
                row.update({"event": "parallel_worker_restore", "target": target, "phase": "baseline", "worker_slot": slot})
                restore_rows.append(row)
                append_jsonl(log_path, with_workload(cfg, row))
            failed_restore_rows = [row for row in restore_rows if row.get("status") == "failed"]
            append_jsonl(
                log_path,
                with_workload(
                    cfg,
                    {
                        "event": "parallel_worker_restore_summary",
                        "target": target,
                        "phase": "baseline",
                        "worker_count": len(workers),
                        "ok_count": len([row for row in restore_rows if row.get("status") == "ok"]),
                        "skipped_count": len([row for row in restore_rows if row.get("status") == "skipped"]),
                        "failed_count": len(failed_restore_rows),
                        "elapsed_s": time.perf_counter() - restore_started,
                    },
                ),
            )
            if failed_restore_rows:
                raise RuntimeError(
                    "parallel worker restore failed for "
                    f"target={target}: {failed_restore_rows[0].get('error')}"
                )

            def run_worker_baseline(slot: int, worker: Dict[str, Any]) -> Dict[str, Any]:
                key = worker_identity(worker)
                label = f"{target}_baseline{label_tag}_w{slot + 1:02d}_{sanitize_label(key)}"
                result = run_real_validation_safe(
                    cfg,
                    base_params,
                    output_dir=out_dir,
                    label=label,
                    worker=worker,
                )
                result.update(
                    {
                        "event": "baseline_worker_validation",
                        "target": target,
                        "baseline_role": "parallel_worker_baseline",
                        "baseline_refresh_id": refresh_id or None,
                        "worker_slot": slot,
                        "worker_key": key,
                        "baseline_policy": baseline_policy,
                    }
                )
                return result

            with ThreadPoolExecutor(max_workers=max(1, safe_int(baseline_policy.get("parallelism"), 1))) as executor:
                future_map = {
                    executor.submit(run_worker_baseline, slot, worker): slot
                    for slot, worker in enumerate(prepared_workers)
                }
                for future in as_completed(future_map):
                    slot = future_map[future]
                    try:
                        worker_result = future.result()
                    except Exception as exc:
                        worker = workers[slot]
                        key = worker_identity(worker)
                        worker_result = failed_validation_result(
                            cfg,
                            base_params,
                            label=f"{target}_baseline{label_tag}_w{slot + 1:02d}_{sanitize_label(key)}",
                            error=exc,
                            started_at=time.time(),
                            worker=worker,
                        )
                        worker_result.update(
                            {
                                "event": "baseline_worker_validation",
                                "target": target,
                                "baseline_role": "parallel_worker_baseline",
                                "baseline_refresh_id": refresh_id or None,
                                "worker_slot": slot,
                                "worker_key": key,
                                "baseline_policy": baseline_policy,
                            }
                        )
                    baseline_rows.append(worker_result)
                    append_jsonl(log_path, with_workload(cfg, worker_result))
            baseline_rows.sort(key=lambda item: safe_int(item.get("worker_slot"), 0))
            baseline_map = baseline_map_from_validations(baseline_rows)
            failed_rows = [row for row in baseline_rows if row.get("status") != "ok"]
            append_jsonl(
                log_path,
                with_workload(
                    cfg,
                    {
                        "event": "parallel_baseline_validation_summary",
                        "target": target,
                        "baseline_policy": baseline_policy,
                        "worker_count": len(workers),
                        "parallelism": baseline_policy.get("parallelism"),
                        "baseline_refresh_id": refresh_id or None,
                        "baseline_count": len(baseline_rows),
                        "ok_count": len(baseline_map),
                        "failed_count": len(failed_rows),
                        "elapsed_s": time.perf_counter() - baseline_started,
                        "worker_keys": sorted(baseline_map),
                    },
                ),
            )
            if failed_rows and baseline_policy.get("required"):
                first_error = failed_rows[0].get("error") or failed_rows[0].get("runtime_errors")
                raise RuntimeError(f"parallel worker baseline failed for target={target}: {first_error}")
            if not baseline_map:
                raise RuntimeError(f"parallel worker baseline produced no usable baseline for target={target}")
            baseline = aggregate_parallel_baseline_validation(baseline_rows)
            baseline["event"] = "baseline_validation"
            baseline["baseline_role"] = "aggregate_from_parallel_worker_baseline"
            baseline["worker_baseline_count"] = len(baseline_map)
            baseline["worker_baseline_keys"] = sorted(baseline_map)
            baseline["baseline_policy"] = baseline_policy
        else:
            baseline = run_real_validation_safe(
                cfg,
                base_params,
                output_dir=out_dir,
                label=f"{target}_baseline{label_tag}",
                worker=workers[0] if workers else None,
            )
            baseline["event"] = "baseline_validation"
            baseline["target"] = target
            baseline["baseline_role"] = "single_baseline"
            baseline["baseline_refresh_id"] = refresh_id or None
        baseline["target"] = target
        if is_none_target(target) and baseline.get("status") == "ok":
            baseline_gate = none_resource_ceiling_details(baseline, cfg)
            baseline["none_baseline_hard_gate"] = baseline_gate
            baseline["none_baseline_hard_gate_ok"] = bool(baseline_gate.get("ok"))
        elif baseline.get("status") == "ok":
            baseline_gate = target_baseline_band_details(baseline, cfg, target)
            baseline["target_baseline_hard_gate"] = baseline_gate
            baseline["target_baseline_hard_gate_ok"] = bool(baseline_gate.get("ok"))
        append_jsonl(log_path, with_workload(cfg, baseline))
        if baseline.get("status") != "ok":
            raise RuntimeError(
                "baseline validation failed for "
                f"target={target}: {baseline.get('error') or baseline.get('runtime_errors')}"
            )
        if is_none_target(target):
            baseline_gate = baseline.get("none_baseline_hard_gate") or none_resource_ceiling_details(baseline, cfg)
            append_jsonl(
                log_path,
                with_workload(
                    cfg,
                    {
                        "event": "none_baseline_hard_gate",
                        "target": target,
                        "baseline_refresh_id": refresh_id or None,
                        "baseline_label": baseline.get("label"),
                        "ok": bool(baseline_gate.get("ok")),
                        "none_resource_ceiling": baseline_gate,
                    },
                ),
            )
            if not baseline_gate.get("ok"):
                append_jsonl(
                    log_path,
                    with_workload(
                        cfg,
                        {
                            "event": "stage_stop",
                            "target": target,
                            "reason": "none_baseline_hard_gate_failed",
                            "baseline_label": baseline.get("label"),
                            "none_resource_ceiling": baseline_gate,
                        },
                    ),
                )
                raise RuntimeError(
                    "none baseline hard gate failed for "
                    f"target={target}: {baseline_gate.get('rejects')}"
                )
        else:
            baseline_gate = baseline.get("target_baseline_hard_gate") or target_baseline_band_details(
                baseline,
                cfg,
                target,
            )
            if baseline_gate.get("enabled"):
                append_jsonl(
                    log_path,
                    with_workload(
                        cfg,
                        {
                            "event": "target_baseline_hard_gate",
                            "target": target,
                            "baseline_refresh_id": refresh_id or None,
                            "baseline_label": baseline.get("label"),
                            "ok": bool(baseline_gate.get("ok")),
                            "target_baseline_band": baseline_gate,
                        },
                    ),
                )
                if not baseline_gate.get("ok"):
                    append_jsonl(
                        log_path,
                        with_workload(
                            cfg,
                            {
                                "event": "stage_stop",
                                "target": target,
                                "reason": "target_baseline_hard_gate_failed",
                                "baseline_label": baseline.get("label"),
                                "target_baseline_band": baseline_gate,
                            },
                        ),
                    )
                    raise RuntimeError(
                        "target baseline hard gate failed for "
                        f"target={target}: {baseline_gate.get('rejects')}"
                    )
        return baseline, baseline_rows, baseline_map

    start_round = 1
    resume_validations_by_round: Dict[int, List[Dict[str, Any]]] = {}
    resume_pruned_by_round: Dict[int, List[Dict[str, Any]]] = {}
    resume_operator_rule_rejected_by_round: Dict[int, List[Dict[str, Any]]] = {}
    if resume_existing_stage:
        resume_state = replay_existing_stage_state(
            cfg=cfg,
            target=target,
            log_path=log_path,
            base_params=base_params,
            initial_ranges=ranges,
            active=active,
            baseline_probe=baseline_probe,
        )
        baseline_validation = resume_state["baseline_validation"]
        baseline_by_worker = resume_state.get("baseline_by_worker") or {}
        baseline_worker_validations = resume_state.get("baseline_worker_validations") or []
        if resume_state.get("worker_baseline_policy"):
            baseline_policy = resume_state.get("worker_baseline_policy") or baseline_policy
        current_best = resume_state["current_best"]
        current_best_probe = resume_state["current_best_probe"]
        validated_signatures = resume_state["validated_signatures"]
        rounds = resume_state["rounds"]
        ranges = resume_state["ranges"]
        resume_validations_by_round = resume_state.get("replayed_validations_by_round") or {}
        resume_pruned_by_round = resume_state.get("replayed_pruned_by_round") or {}
        resume_operator_rule_rejected_by_round = (
            resume_state.get("replayed_operator_rule_rejected_by_round") or {}
        )
        start_round = max(1, safe_int(resume_state.get("next_round"), 1))
        no_improvement_rounds = safe_int(resume_state.get("no_improvement_rounds"), 0)
        resume_range_override = resume_state.get("resume_range_override")
        if (
            isinstance(resume_range_override, dict)
            and resume_range_override.get("clear_existing_round_state")
        ):
            resume_validations_by_round = {
                round_index: rows
                for round_index, rows in resume_validations_by_round.items()
                if round_index < start_round
            }
            resume_pruned_by_round = {
                round_index: rows
                for round_index, rows in resume_pruned_by_round.items()
                if round_index < start_round
            }
            resume_operator_rule_rejected_by_round = {
                round_index: rows
                for round_index, rows in resume_operator_rule_rejected_by_round.items()
                if round_index < start_round
            }
        if isinstance(resume_range_override, dict) and resume_range_override.get("rebaseline"):
            refresh_id = str(resume_range_override.get("id") or f"resume_before_round_{start_round}")
            baseline_validation, baseline_worker_validations, baseline_by_worker = collect_stage_baseline(refresh_id)
            current_best = copy.deepcopy(baseline_validation)
            current_best_probe = copy.deepcopy(baseline_probe)
            no_improvement_rounds = 0
        append_jsonl(
            log_path,
            with_workload(
                cfg,
                {
                    "event": "stage_resume",
                    **stage_metadata,
                    "resume_from_log": str(log_path),
                    "resume_next_round": start_round,
                    "resume_completed_round_count": resume_state.get("completed_round_count"),
                    "resume_completed_validation_count": resume_state.get("completed_validation_count"),
                    "resume_retryable_failed_validation_count": resume_state.get("retryable_failed_validation_count"),
                    "resume_previous_stage_stop_reasons": resume_state.get("previous_stage_stop_reasons"),
                    "resume_previous_stage_summary_count": resume_state.get("previous_stage_summary_count"),
                    "resume_candidate_replay_accept_count": resume_state.get("candidate_replay_accept_count"),
                    "resume_range_override": resume_range_override,
                    "resume_rebaseline_applied": bool(
                        isinstance(resume_range_override, dict) and resume_range_override.get("rebaseline")
                    ),
                    "resume_restored_ranges": {name: list(ranges[name]) for name in active},
                    "resume_restored_best": summarize_validation(current_best, target),
                },
            ),
        )
    else:
        append_jsonl(
            log_path,
            with_workload(
                cfg,
                {
                    "event": "stage_start",
                    **stage_metadata,
                    "resume_requested": resume_requested,
                    "resume_started_fresh": bool(resume_requested and not resume_existing_stage),
                    "preexisting_log_row_count": len(resume_log_rows),
                },
            ),
        )
        baseline_validation, baseline_worker_validations, baseline_by_worker = collect_stage_baseline()
        flush_external_stop_summary("after_stage_baseline")
        current_best = copy.deepcopy(baseline_validation)
        current_best_probe = copy.deepcopy(baseline_probe)
        validated_signatures = {param_signature(base_params)}
        no_improvement_rounds = 0
        rounds = []
    nested_policy_enabled = range_anchor_policy == NESTED_RESOURCE_SEMANTIC_POLICY
    nested_outer_accepted_count = sum(
        1 for row in rounds if row.get("outer_semantic_accepted") is True
    )
    if nested_policy_enabled and accepted_shrink_target > 0 and nested_outer_accepted_count >= accepted_shrink_target:
        append_jsonl(
            log_path,
            with_workload(
                cfg,
                {
                    "event": "stage_stop",
                    "target": target,
                    "reason": "accepted_shrink_target_reached",
                    "outer_accepted_count": nested_outer_accepted_count,
                    "accepted_shrink_target": accepted_shrink_target,
                    "resume_noop": True,
                    "final_ranges": {name: list(ranges[name]) for name in active},
                },
            ),
        )
        print(
            f"target={target} already reached accepted_shrink_target="
            f"{accepted_shrink_target}; nothing to resume",
            flush=True,
        )
        return 0
    nested_last_accepted_attempt = max(
        (
            safe_int(row.get("round"), 0)
            for row in rounds
            if row.get("outer_semantic_accepted") is True
        ),
        default=0,
    )
    nested_outer_consecutive_misses = sum(
        1
        for row in rounds
        if safe_int(row.get("round"), 0) > nested_last_accepted_attempt
        and row.get("outer_semantic_accepted") is not True
    )
    nested_plan_change_expansion_armed = bool(
        rounds
        and rounds[-1].get(
            "fallback_plan_change_replay_expansion_next_round_armed"
        )
    )
    nested_interval_real_signatures: Set[str] = set()
    nested_interval_real_plan_fingerprints: Set[str] = set()
    if nested_policy_enabled and resume_requested:
        for row in load_jsonl(log_path):
            if (
                row.get("event") in {"candidate_validation", "operator_resource_rule_reject"}
                and safe_int(row.get("round"), 0) > nested_last_accepted_attempt
            ):
                if (
                    row.get("event") == "candidate_validation"
                    and str(row.get("status") or "").lower() != "ok"
                ):
                    continue
                nested_interval_real_signatures.add(param_signature(row.get("params") or {}))
                plan_input_changes = (
                    row.get("plan_input_changes")
                    if isinstance(row.get("plan_input_changes"), dict)
                    else {}
                )
                plan_fingerprint = str(
                    plan_input_changes.get("candidate_plan_fingerprint") or ""
                )
                if plan_fingerprint:
                    nested_interval_real_plan_fingerprints.add(plan_fingerprint)
    if baseline_only:
        append_jsonl(
            log_path,
            with_workload(
                cfg,
                {
                    "event": "stage_stop",
                    "target": target,
                    "reason": "baseline_only_complete",
                    "baseline": summarize_validation(baseline_validation, target),
                    "worker_baseline_count": len(baseline_by_worker),
                    "worker_baseline_keys": sorted(baseline_by_worker),
                },
            ),
        )
        return 0
    stop_reason = "max_rounds_reached"
    stage_stop_written = False
    if all_widths_below_epsilon(ranges, active, epsilon_cfg):
        stop_reason = "epsilon_converged"
        max_rounds = min(max_rounds, max(0, start_round - 1))

    for round_index in range(start_round, max_rounds + 1):
        round_started = time.perf_counter()
        plan_change_expansion_state = plan_change_replay_expansion_state(
            cfg,
            nested_outer_consecutive_misses,
        )
        plan_change_expansion_state["miss_threshold_reached"] = bool(
            plan_change_expansion_state.get("active")
        )
        plan_change_expansion_active = bool(
            nested_policy_enabled
            and nested_plan_change_expansion_armed
            and plan_change_expansion_state.get("enabled")
            and safe_int(
                plan_change_expansion_state.get(
                    "max_extra_candidates_per_round"
                ),
                0,
            )
            > 0
        )
        plan_change_expansion_state["active"] = plan_change_expansion_active
        plan_change_expansion_state["armed_by_prior_round_log"] = (
            nested_plan_change_expansion_armed
        )
        plan_change_expansion_state["trigger_reason"] = (
            "prior_logged_fallback_tier_topk_miss"
            if plan_change_expansion_active
            else "no_prior_logged_fallback_tier_topk_miss"
        )
        existing_round_validations = [
            copy.deepcopy(row) for row in resume_validations_by_round.get(round_index, [])
        ]
        existing_round_pruned = [
            copy.deepcopy(row) for row in resume_pruned_by_round.get(round_index, [])
        ]
        existing_round_operator_rule_rejected = [
            copy.deepcopy(row)
            for row in resume_operator_rule_rejected_by_round.get(round_index, [])
        ]
        existing_round_real_count = len(existing_round_validations)
        existing_round_pruned_count = len(existing_round_pruned)
        existing_round_operator_rule_rejected_count = len(
            existing_round_operator_rule_rejected
        )
        existing_round_topk_count = existing_round_real_count + existing_round_pruned_count
        raw_candidates: List[Dict[str, Any]] = []
        for raw_idx, (mask, values) in enumerate(candidate_mask_values(active, ranges), start=1):
            params = dict(base_params)
            params.update(values)
            signature = param_signature(params)
            if (
                signature in nested_interval_real_signatures
                if nested_policy_enabled
                else signature in validated_signatures
            ):
                continue
            raw_candidates.append(
                {
                    "round": round_index,
                    "mask": mask,
                    "params": params,
                    "signature": signature,
                    "target": target,
                    "raw_candidate_index": raw_idx,
                    "ranges": {name: list(ranges[name]) for name in active},
                }
            )
        round_raw_search_point_count = len(raw_candidates) + existing_round_topk_count
        base_round_topk_limit = (
            max_candidates if max_candidates > 0 else round_raw_search_point_count
        )
        expansion_extra_cap = (
            safe_int(
                plan_change_expansion_state.get("max_extra_candidates_per_round"),
                0,
            )
            if plan_change_expansion_active
            else 0
        )
        round_topk_limit = base_round_topk_limit + expansion_extra_cap
        round_topk_remaining_capacity = max(0, round_topk_limit - existing_round_topk_count)
        topk_limit = min(round_topk_remaining_capacity, len(raw_candidates)) if raw_candidates else 0
        cumulative_planned_topk_count = existing_round_topk_count + topk_limit
        degradation = {
            "applied": bool(round_topk_limit and round_raw_search_point_count > round_topk_limit),
            "reason": "binary_search_topk_replay_limit",
            "strategy": "rank_all_binary_points_by_plan_estimator_then_replay_topk",
            "original_candidate_count": round_raw_search_point_count,
            "remaining_candidate_count": len(raw_candidates),
            "validated_candidate_count": cumulative_planned_topk_count,
            "new_validated_candidate_count": topk_limit,
            "topk_limit": round_topk_limit,
            "base_topk_limit": base_round_topk_limit,
            "plan_change_expansion_extra_cap": expansion_extra_cap,
            "remaining_topk_capacity": round_topk_remaining_capacity,
            "pre_real_validation_label_pruning": False,
        } if round_raw_search_point_count else None
        append_jsonl(
            log_path,
            with_workload(cfg, {
                "event": "round_start",
                "target": target,
                "round": round_index,
                "candidate_count": round_raw_search_point_count,
                "raw_search_point_count": round_raw_search_point_count,
                "remaining_raw_candidate_count": len(raw_candidates),
                "full_round_search_points": full_round_search_points,
                "topk_limit": round_topk_limit,
                "base_topk_limit": base_round_topk_limit,
                "planned_topk_count": cumulative_planned_topk_count,
                "planned_new_topk_count": topk_limit,
                "round_topk_remaining_capacity": round_topk_remaining_capacity,
                "resume_existing_round_validation_count": existing_round_real_count,
                "resume_existing_round_pruned_count": existing_round_pruned_count,
                "resume_existing_round_operator_rule_rejected_count": (
                    existing_round_operator_rule_rejected_count
                ),
                "resume_existing_round_topk_count": existing_round_topk_count,
                "degradation": degradation,
                "fallback_plan_change_replay_expansion": plan_change_expansion_state,
                "operator_resource_rule_gate": summarize_rule_gate(operator_rule_gate),
                "ranges": {name: list(ranges[name]) for name in active},
                "plan_probe_parallelism": (
                    min(plan_probe_workers, max(1, len(raw_candidates)))
                    if raw_candidates and topk_limit > 0 and not skip_plan_probe_for_full_replay
                    else 0
                ),
                "validation_parallelism": None,
                "validation_batch_count": None,
            }),
        )
        if not raw_candidates and not existing_round_validations:
            stop_reason = "candidate_generation_exhausted"
            empty_round_summary = {
                "event": "round_summary",
                "target": target,
                "round": round_index,
                "candidate_count": round_raw_search_point_count,
                "raw_search_point_count": round_raw_search_point_count,
                "remaining_raw_candidate_count": len(raw_candidates),
                "full_round_search_points": full_round_search_points,
                "topk_limit": round_topk_limit,
                "base_topk_limit": base_round_topk_limit,
                "new_topk_limit": topk_limit,
                "topk_candidate_count": 0,
                "new_topk_candidate_count": 0,
                "resume_existing_round_validation_count": existing_round_real_count,
                "resume_existing_round_pruned_count": existing_round_pruned_count,
                "resume_existing_round_topk_count": existing_round_topk_count,
                "round_topk_remaining_capacity": round_topk_remaining_capacity,
                "replay_pruned_count": 0,
                "new_replay_pruned_count": 0,
                "validation_count": 0,
                "new_validation_count": 0,
                "accepted_count": 0,
                "best_candidate": summarize_validation(current_best, target),
                "reject_reasons": {},
                "degradation": degradation,
                "updated_ranges": {name: list(ranges[name]) for name in active},
                "range_update_anchor": "none_no_new_candidate",
                "plan_probe_parallelism": 0,
                "plan_probe_elapsed_s": 0.0,
                "validation_parallelism": 0,
                "validation_batch_count": 0,
                "validation_elapsed_s": 0.0,
                "round_elapsed_s": time.perf_counter() - round_started,
                "validation_throughput_candidates_per_s": None,
                "stop_reason": stop_reason,
            }
            rounds.append(empty_round_summary)
            append_jsonl(
                log_path,
                with_workload(
                    cfg,
                    {
                        "event": "round_stop",
                        "round": round_index,
                        "reason": "candidate_generation_exhausted",
                        "detail": "No unvalidated candidate combinations were generated; this is only expected when active_params is empty or floating-point ranges collapsed.",
                    },
                ),
            )
            append_jsonl(log_path, with_workload(cfg, empty_round_summary))
            write_round_handoff_snapshot(
                out_dir=out_dir,
                cfg=cfg,
                target=target,
                round_summary=empty_round_summary,
                baseline_validation=baseline_validation,
                current_best=current_best,
                active=active,
                max_rounds=max_rounds,
                required_rounds=required_rounds,
                required_rounds_by_param=required_rounds_by_param,
                topk_limit=round_topk_limit,
                log_path=log_path,
                stop_only_on_epsilon=stop_only_on_epsilon,
                no_improvement_round_limit=no_improvement_round_limit,
            )
            append_jsonl(log_path, with_workload(cfg, {"event": "stage_stop", "target": target, "reason": stop_reason}))
            stage_stop_written = True
            break

        probed_candidates: List[Tuple[int, Dict[str, Any], Dict[str, Any]]] = []
        probe_started = time.perf_counter()
        if skip_plan_probe_for_full_replay:
            for idx, candidate in enumerate(raw_candidates, start=1):
                if not nested_policy_enabled:
                    validated_signatures.add(candidate["signature"])
                probe_changes = {
                    "status": "skipped",
                    "reason": "full_bruteforce_real_replay_no_plan_probe",
                    "any_changed": None,
                    "candidate_resource_avg": {},
                    "candidate_resource_error_profile": {},
                    "topk_rank_key": [0, 0, 0, 0, 0],
                }
                probed_candidates.append((idx, candidate, probe_changes))
        elif topk_limit <= 0:
            pass
        elif plan_probe_workers > 1 and len(raw_candidates) > 1:
            with ThreadPoolExecutor(max_workers=min(plan_probe_workers, len(raw_candidates))) as executor:
                future_map = {
                    executor.submit(candidate_plan_probe_task, cfg, queries, baseline_probe, candidate): (idx, candidate)
                    for idx, candidate in enumerate(raw_candidates, start=1)
                }
                for future in as_completed(future_map):
                    idx, candidate = future_map[future]
                    if not nested_policy_enabled:
                        validated_signatures.add(candidate["signature"])
                    try:
                        probe_changes = future.result()
                    except Exception as exc:
                        probe_changes = failed_probe_changes(exc)
                    probe_changes["topk_rank_key"] = list(probe_topk_rank_key(probe_changes, target, baseline_probe, cfg))
                    probed_candidates.append((idx, candidate, probe_changes))
        else:
            for idx, candidate in enumerate(raw_candidates, start=1):
                if not nested_policy_enabled:
                    validated_signatures.add(candidate["signature"])
                try:
                    probe_changes = candidate_plan_probe_task(cfg, queries, baseline_probe, candidate)
                except Exception as exc:
                    probe_changes = failed_probe_changes(exc)
                probe_changes["topk_rank_key"] = list(probe_topk_rank_key(probe_changes, target, baseline_probe, cfg))
                probed_candidates.append((idx, candidate, probe_changes))
        probed_candidates.sort(key=lambda item: item[0])
        plan_probe_elapsed_s = time.perf_counter() - probe_started

        ranked_candidates = sorted(
            probed_candidates,
            key=lambda item: probe_topk_rank_key(item[2], target, baseline_probe, cfg),
            reverse=True,
        )
        replay_eligible_ranked_candidates, rejected_by_operator_rule = (
            partition_operator_rule_rejected_candidates(
                ranked_candidates,
                operator_rule_gate,
            )
        )
        operator_rule_rejected: List[Dict[str, Any]] = []
        operator_rule_reject_reason_counts: Dict[str, int] = {}
        for (
            pre_gate_rank,
            raw_idx,
            candidate,
            probe_changes,
            rule_decision,
        ) in rejected_by_operator_rule:
            if nested_policy_enabled:
                nested_interval_real_signatures.add(candidate["signature"])
            validation_idx = safe_int(candidate.get("raw_candidate_index"), raw_idx)
            rejection = {
                "event": "operator_resource_rule_reject",
                "target": target,
                "round": round_index,
                "candidate_index": validation_idx,
                "raw_candidate_index": validation_idx,
                "pre_gate_rank": pre_gate_rank,
                "mask": candidate.get("mask"),
                "params": candidate.get("params"),
                "ranges": candidate.get("ranges"),
                "accepted": False,
                "accept_reject_reason": rule_decision.get("reason"),
                "reject_reason": rule_decision.get("reason"),
                "used_real_replay": False,
                "physical_workload_invocations": 0,
                "valid_replay_count": 0,
                "operator_resource_rule_gate": rule_decision,
                "plan_input_changes": probe_changes,
                "topk_slot_consumed": False,
                "topk_backfill_required": True,
                "state_updates_applied": {
                    "accepted": False,
                    "incumbent": False,
                    "range": False,
                    "gradient": False,
                },
            }
            operator_rule_rejected.append(rejection)
            reason = str(rule_decision.get("reason") or "unknown")
            operator_rule_reject_reason_counts[reason] = (
                operator_rule_reject_reason_counts.get(reason, 0) + 1
            )
            append_jsonl(log_path, with_workload(cfg, rejection))
        ranked_candidates = replay_eligible_ranked_candidates
        behavior_cluster_rejected: List[Dict[str, Any]] = []
        behavior_cluster_gate_audit: Dict[str, Any] = {"enabled": False}
        if nested_policy_enabled:
            (
                ranked_candidates,
                rejected_by_behavior_cluster,
                behavior_cluster_gate_audit,
            ) = select_distinct_base_topk_candidates(
                ranked_candidates,
                excluded_parameter_signatures=nested_interval_real_signatures,
            )
            deterministic_behavior_rejects = {
                "parameter_vector_already_replayed_in_interval",
                "duplicate_parameter_vector_in_round",
            }
            for (
                behavior_rank,
                raw_idx,
                candidate,
                probe_changes,
                behavior_reason,
            ) in rejected_by_behavior_cluster:
                if behavior_reason in deterministic_behavior_rejects:
                    nested_interval_real_signatures.add(candidate["signature"])
                validation_idx = safe_int(
                    candidate.get("raw_candidate_index"), raw_idx
                )
                rejection = {
                    "event": "base_topk_candidate_reject",
                    "target": target,
                    "round": round_index,
                    "candidate_index": validation_idx,
                    "raw_candidate_index": validation_idx,
                    "behavior_gate_rank": behavior_rank,
                    "mask": candidate.get("mask"),
                    "params": candidate.get("params"),
                    "accepted": False,
                    "accept_reject_reason": behavior_reason,
                    "reject_reason": behavior_reason,
                    "used_real_replay": False,
                    "physical_workload_invocations": 0,
                    "valid_replay_count": 0,
                    "candidate_plan_fingerprint": probe_changes.get("candidate_plan_fingerprint"),
                    "plan_input_changes": probe_changes,
                    "topk_slot_consumed": False,
                    "topk_backfill_required": True,
                    "exploration_dedup_state_updated": (
                        behavior_reason in deterministic_behavior_rejects
                    ),
                    "state_updates_applied": {
                        "accepted": False,
                        "incumbent": False,
                        "range": False,
                        "gradient": False,
                    },
                }
                behavior_cluster_rejected.append(rejection)
                append_jsonl(log_path, with_workload(cfg, rejection))
        new_base_topk_capacity = max(
            0, base_round_topk_limit - existing_round_topk_count
        )
        base_topk_candidate_count = min(
            new_base_topk_capacity,
            len(ranked_candidates),
            topk_limit,
        )
        base_topk_candidates = ranked_candidates[:base_topk_candidate_count]
        operator_rule_backfill_count = sum(
            1
            for _, candidate, _ in base_topk_candidates
            if safe_int(candidate.get("operator_rule_pre_gate_rank"), 0)
            > new_base_topk_capacity
        )
        topk_candidates = list(base_topk_candidates)
        behavior_cluster_gate_audit.update(
            {
                "base_topk_limit": base_round_topk_limit,
                "selected_base_topk_count": len(base_topk_candidates),
                "base_topk_parameter_deficit": max(
                    0,
                    new_base_topk_capacity - len(base_topk_candidates),
                ),
                "deficit_policy": "replay_fewer_candidates_never_duplicate_parameter_vectors_or_pad",
            }
        )
        append_jsonl(
            log_path,
            with_workload(
                cfg,
                {
                    "event": "base_topk_parameter_dedupe_summary",
                    "target": target,
                    "round": round_index,
                    **behavior_cluster_gate_audit,
                },
            ),
        )
        plan_change_expansion_audit: Dict[str, Any] = {
            **plan_change_expansion_state,
            "triggered": plan_change_expansion_active,
            "base_topk_limit": base_round_topk_limit,
            "existing_round_topk_count": existing_round_topk_count,
            "new_base_topk_candidate_count": len(base_topk_candidates),
            "effective_replay_cap": round_topk_limit,
            "selected_extra_count": 0,
            "operator_rule_reject_cannot_be_bypassed": True,
        }
        if plan_change_expansion_active:
            fingerprints_excluded_from_extra = set(
                nested_interval_real_plan_fingerprints
            )
            for _, _, probe_changes in base_topk_candidates:
                plan_fingerprint = str(
                    probe_changes.get("candidate_plan_fingerprint") or ""
                )
                if plan_fingerprint:
                    fingerprints_excluded_from_extra.add(plan_fingerprint)
            extra_remaining_capacity = max(
                0,
                round_topk_limit
                - existing_round_topk_count
                - len(base_topk_candidates),
            )
            expansion_pool = ranked_candidates[len(base_topk_candidates):]
            extra_candidates, extra_selection_audit = (
                select_plan_change_replay_expansion_candidates(
                    expansion_pool,
                    max_extra_candidates=extra_remaining_capacity,
                    excluded_plan_fingerprints=fingerprints_excluded_from_extra,
                )
            )
            for _, candidate, probe_changes in extra_candidates:
                candidate["fallback_plan_change_replay_expansion"] = True
                candidate["replay_expansion_reason"] = (
                    "prior_fallback_tier_topk_miss"
                )
                candidate["candidate_plan_fingerprint"] = probe_changes.get(
                    "candidate_plan_fingerprint"
                )
            topk_candidates.extend(extra_candidates)
            plan_change_expansion_audit.update(extra_selection_audit)
            plan_change_expansion_audit["selected_extra_count"] = len(
                extra_candidates
            )
            append_jsonl(
                log_path,
                with_workload(
                    cfg,
                    {
                        "event": "fallback_plan_change_replay_expansion",
                        "target": target,
                        "round": round_index,
                        **plan_change_expansion_audit,
                    },
                ),
            )
        prepared_candidates: List[Tuple[int, Dict[str, Any], Dict[str, Any], Optional[Dict[str, Any]]]] = []
        replay_pruned: List[Dict[str, Any]] = []
        pruning_reason_counts: Dict[str, int] = {}
        forced_admit: Optional[Tuple[int, Dict[str, Any], Dict[str, Any], Optional[Dict[str, Any]], Dict[str, Any]]] = None
        for topk_idx, (raw_idx, candidate, probe_changes) in enumerate(
            topk_candidates,
            start=existing_round_topk_count + 1,
        ):
            candidate["topk_rank"] = topk_idx
            candidate["base_topk_candidate"] = not bool(
                candidate.get("fallback_plan_change_replay_expansion")
            )
            validation_idx = safe_int(candidate.get("raw_candidate_index"), raw_idx)
            pruning_status = replay_pruning_status_from_bounds(
                probe_changes,
                target=target,
                baseline_probe=baseline_probe,
                best_probe=current_best_probe,
                cfg=cfg,
            )
            if candidate.get("fallback_plan_change_replay_expansion"):
                pruning_status = {
                    **pruning_status,
                    "pruned": False,
                    "reason": "plan_change_expansion_bypasses_estimator_pruning",
                    "original_prune_reason": pruning_status.get("reason"),
                }
            probe_changes["replay_pruning_status"] = pruning_status
            worker_slot = (topk_idx - 1) % len(workers) if workers else 0
            candidate["validation_worker_slot"] = worker_slot
            worker = workers[worker_slot] if workers else None
            if pruning_status.get("pruned"):
                pruned_record = {
                    "event": "candidate_replay_pruned",
                    "target": target,
                    "round": round_index,
                    "candidate_index": validation_idx,
                    "topk_rank": topk_idx,
                    "raw_candidate_index": validation_idx,
                    "mask": candidate.get("mask"),
                    "params": candidate.get("params"),
                    "replay_pruning_status": pruning_status,
                    "plan_input_changes": probe_changes,
                }
                replay_pruned.append(pruned_record)
                reason = str(pruning_status.get("reason") or "unknown")
                pruning_reason_counts[reason] = pruning_reason_counts.get(reason, 0) + 1
                if forced_admit is None:
                    forced_status = copy.deepcopy(pruning_status)
                    forced_status.update(
                        {
                            "pruned": False,
                            "reason": "forced_admit_best_topk_to_preserve_binary_progress",
                            "original_prune_reason": pruning_status.get("reason"),
                        }
                    )
                    forced_probe = copy.deepcopy(probe_changes)
                    forced_probe["replay_pruning_status"] = forced_status
                    forced_admit = (topk_idx, candidate, forced_probe, worker, forced_status)
                continue
            prepared_candidates.append((validation_idx, candidate, probe_changes, worker))
        if not prepared_candidates and forced_admit is not None:
            topk_idx, candidate, probe_changes, worker, forced_status = forced_admit
            validation_idx = safe_int(candidate.get("raw_candidate_index"), topk_idx)
            prepared_candidates.append((validation_idx, candidate, probe_changes, worker))
            append_jsonl(
                log_path,
                with_workload(
                    cfg,
                    {
                        "event": "candidate_replay_prune_override",
                        "target": target,
                        "round": round_index,
                        "candidate_index": validation_idx,
                        "topk_rank": topk_idx,
                        "raw_candidate_index": validation_idx,
                        "mask": candidate.get("mask"),
                        "reason": forced_status.get("reason"),
                        "original_prune_reason": forced_status.get("original_prune_reason"),
                    },
                ),
            )
        for record in replay_pruned:
            append_jsonl(log_path, with_workload(cfg, record))

        existing_pruning_reason_counts: Dict[str, int] = {}
        for record in existing_round_pruned:
            pruning_status = record.get("replay_pruning_status")
            if not isinstance(pruning_status, dict):
                pruning_status = {}
            reason = str(pruning_status.get("reason") or "unknown")
            existing_pruning_reason_counts[reason] = existing_pruning_reason_counts.get(reason, 0) + 1
        cumulative_pruning_reason_counts = dict(existing_pruning_reason_counts)
        for reason, count in pruning_reason_counts.items():
            cumulative_pruning_reason_counts[reason] = cumulative_pruning_reason_counts.get(reason, 0) + count
        cumulative_topk_candidate_count = existing_round_topk_count + len(topk_candidates)
        cumulative_replay_pruned_count = existing_round_pruned_count + len(replay_pruned)
        cumulative_validation_count_after_pruning = existing_round_real_count + len(prepared_candidates)
        adaptive_parallelism_cfg = same_ssd_adaptive_config(cfg, target=target, workers=workers)
        adaptive_parallelism_decision: Optional[Dict[str, Any]] = None
        if to_bool(adaptive_parallelism_cfg.get("enabled"), False) and workers:
            previous_round = rounds[-1] if rounds else {}
            previous_ssd = previous_round.get("ssd_utilization") or {}
            previous_decision = previous_round.get("same_ssd_adaptive_parallelism") or {}
            current_parallelism = safe_int(
                previous_decision.get("desired"),
                initial_validation_parallelism(cfg, target, len(workers)),
            )
            adaptive_parallelism_decision = choose_same_ssd_parallelism(
                current=current_parallelism,
                available_workers=len(workers),
                pending_candidates=len(prepared_candidates),
                mean_util_pct=(previous_ssd.get("mean_util_pct") if previous_ssd.get("valid") else None),
                peak_util_pct=(previous_ssd.get("peak_util_pct") if previous_ssd.get("valid") else None),
                preferred=safe_int(adaptive_parallelism_cfg.get("preferred"), 8),
                stable_cap=safe_int(adaptive_parallelism_cfg.get("stable_cap"), 12),
                allow_stable_cap=to_bool(adaptive_parallelism_cfg.get("allow_stable_cap"), False),
                daily_mean_limit_pct=safe_float(adaptive_parallelism_cfg.get("daily_mean_limit_pct"), 75.0),
                daily_peak_limit_pct=safe_float(adaptive_parallelism_cfg.get("daily_peak_limit_pct"), 90.0),
                warning_mean_pct=safe_float(adaptive_parallelism_cfg.get("warning_mean_pct"), 85.0),
                warning_peak_pct=safe_float(adaptive_parallelism_cfg.get("warning_peak_pct"), 95.0),
            )
            validation_parallelism = safe_int(adaptive_parallelism_decision.get("effective"), 0)
        else:
            validation_parallelism = min(len(workers), len(prepared_candidates)) if workers else (1 if prepared_candidates else 0)
        validation_parallelism = max(0, validation_parallelism)
        validation_batch_count = (
            int(math.ceil(len(prepared_candidates) / max(1, validation_parallelism))) if prepared_candidates else 0
        )
        append_jsonl(
            log_path,
            with_workload(cfg, {
                "event": "round_plan_probe_summary",
                "target": target,
                "round": round_index,
                "candidate_count": round_raw_search_point_count,
                "raw_search_point_count": round_raw_search_point_count,
                "remaining_raw_candidate_count": len(raw_candidates),
                "topk_limit": round_topk_limit,
                "base_topk_limit": base_round_topk_limit,
                "new_topk_limit": topk_limit,
                "topk_candidate_count": cumulative_topk_candidate_count,
                "new_topk_candidate_count": len(topk_candidates),
                "resume_existing_round_validation_count": existing_round_real_count,
                "resume_existing_round_pruned_count": existing_round_pruned_count,
                "resume_existing_round_topk_count": existing_round_topk_count,
                "round_topk_remaining_capacity": round_topk_remaining_capacity,
                "replay_pruned_count": cumulative_replay_pruned_count,
                "new_replay_pruned_count": len(replay_pruned),
                "operator_rule_evaluated_count": len(probed_candidates),
                "operator_rule_eligible_count": len(ranked_candidates),
                "operator_rule_rejected_count": (
                    existing_round_operator_rule_rejected_count
                    + len(operator_rule_rejected)
                ),
                "new_operator_rule_rejected_count": len(operator_rule_rejected),
                "operator_rule_reject_reason_counts": operator_rule_reject_reason_counts,
                "operator_rule_topk_backfill_count": operator_rule_backfill_count,
                "operator_rule_rejects_consume_topk_slots": False,
                "operator_rule_rejects_consume_physical_replay_slots": False,
                "behavior_cluster_gate": behavior_cluster_gate_audit,
                "behavior_cluster_rejected_count": len(behavior_cluster_rejected),
                "behavior_cluster_rejects_consume_topk_slots": False,
                "behavior_cluster_rejects_consume_physical_replay_slots": False,
                "replay_pruning_reason_counts": cumulative_pruning_reason_counts,
                "new_replay_pruning_reason_counts": pruning_reason_counts,
                "validation_count_after_pruning": cumulative_validation_count_after_pruning,
                "new_validation_count_after_pruning": len(prepared_candidates),
                "same_ssd_adaptive_parallelism": adaptive_parallelism_decision,
                "fallback_plan_change_replay_expansion": plan_change_expansion_audit,
                "plan_probe_parallelism": min(plan_probe_workers, len(raw_candidates)) if topk_limit > 0 else 0,
                "elapsed_s": plan_probe_elapsed_s,
            }),
        )

        accepted_this_round: List[Dict[str, Any]] = [
            copy.deepcopy(row) for row in existing_round_validations if row.get("accepted")
        ]
        reject_reasons: Dict[str, int] = {}
        for row in existing_round_validations:
            if row.get("accepted"):
                continue
            reason = str(row.get("reject_reason") or row.get("accept_reject_reason") or "unknown")
            reject_reasons[reason] = reject_reasons.get(reason, 0) + 1
        round_validations: List[Dict[str, Any]] = [copy.deepcopy(row) for row in existing_round_validations]
        validation_started = time.perf_counter()
        validation_completed_count = 0
        previous_best_key = accepted_best_sort_key(current_best, target=target)
        use_same_worker_baseline = bool(
            baseline_policy.get("enabled")
            and baseline_policy.get("compare_mode") == "same_worker"
            and baseline_by_worker
        )

        def comparison_references(validation: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any], str, bool]:
            if use_same_worker_baseline:
                worker_baseline, worker_key, matched = resolve_worker_baseline(
                    validation,
                    baseline_validation,
                    baseline_by_worker,
                )
                return worker_baseline, worker_baseline, worker_key, matched
            return current_best, baseline_validation, "", False

        def outer_comparison_references(
            validation: Dict[str, Any],
        ) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
            if use_same_worker_baseline:
                return resolve_same_worker_outer_references(
                    validation,
                    current_best,
                    baseline_validation,
                    baseline_by_worker,
                )
            return (
                current_best,
                baseline_validation,
                {
                    "mode": "single_baseline",
                    "valid": True,
                    "reference_label": current_best.get("label"),
                    "candidate_baseline_label": baseline_validation.get("label"),
                },
            )

        def baseline_for_range_anchor(validation: Dict[str, Any]) -> Dict[str, Any]:
            if use_same_worker_baseline:
                worker_baseline, _, _ = resolve_worker_baseline(validation, baseline_validation, baseline_by_worker)
                return worker_baseline
            return baseline_validation

        def record_validation(
            idx: int,
            candidate: Dict[str, Any],
            probe_changes: Dict[str, Any],
            validation: Dict[str, Any],
        ) -> None:
            nonlocal validation_completed_count
            comparison_best, comparison_baseline, comparison_key, comparison_matched = comparison_references(validation)
            if nested_policy_enabled:
                accepted = False
                reason = "pending_outer_semantic_decision"
                decision = {
                    "policy": NESTED_RESOURCE_SEMANTIC_POLICY,
                    "role": "inner_loop_candidate_evidence_only",
                    "accepted": False,
                    "detail": "outer loop evaluates only candidates offered in inner resource-score order",
                }
                if str(validation.get("status") or "").lower() == "ok":
                    nested_interval_real_signatures.add(candidate["signature"])
                    candidate_plan_fingerprint = str(
                        probe_changes.get("candidate_plan_fingerprint") or ""
                    )
                    if candidate_plan_fingerprint:
                        nested_interval_real_plan_fingerprints.add(
                            candidate_plan_fingerprint
                        )
            else:
                accepted, reason, decision = compare_actual(
                    validation,
                    comparison_best,
                    comparison_baseline,
                    target=target,
                    cfg=cfg,
                )
            score_b_payload = real_score_b_details(
                validation,
                comparison_baseline,
                target=target,
                cfg=cfg,
            )
            score_b_payload["used_for_range_selection"] = (
                range_anchor_policy == REAL_SCORE_B_ANCHOR_POLICY
            )
            score_c_payload = real_score_c_details(
                validation,
                comparison_baseline,
                target=target,
                cfg=cfg,
            )
            score_c_payload["used_for_range_selection"] = (
                range_anchor_policy == REAL_SCORE_C_ANCHOR_POLICY
            )
            inner_score_payload = inner_resource_score_details(
                validation,
                comparison_baseline,
                target=target,
                cfg=cfg,
            )
            inner_score_payload["used_for_inner_best_selection"] = nested_policy_enabled
            if use_same_worker_baseline:
                decision["comparison_baseline_mode"] = "same_worker"
                decision["comparison_baseline_key"] = comparison_key
                decision["comparison_baseline_matched"] = comparison_matched
                decision["comparison_baseline_label"] = comparison_baseline.get("label")
            validation.update(
                {
                    "event": "candidate_validation",
                    "target": target,
                    "round": round_index,
                    "candidate_index": idx,
                    "topk_rank": candidate.get("topk_rank"),
                    "raw_candidate_index": candidate.get("raw_candidate_index", idx),
                    "mask": candidate["mask"],
                    "ranges": candidate["ranges"],
                    "plan_input_changes": probe_changes,
                    "fallback_plan_change_replay_expansion": bool(
                        candidate.get("fallback_plan_change_replay_expansion")
                    ),
                    "replay_expansion_reason": candidate.get(
                        "replay_expansion_reason"
                    ),
                    "plan_tree_changed": bool(
                        (probe_changes.get("plan") or {}).get("changed")
                    ),
                    "candidate_plan_fingerprint": probe_changes.get(
                        "candidate_plan_fingerprint"
                    ),
                    "small_loop_score": list(probe_changes.get("topk_rank_key") or []),
                    "real_score_b": score_b_payload,
                    "real_score_c": score_c_payload,
                    "inner_resource_score": inner_score_payload,
                    "accepted": accepted,
                    "accept_reject_reason": reason,
                    "accept_reason": reason if accepted else "",
                    "reject_reason": "" if accepted else reason,
                    "decision": decision,
                    "comparison_baseline_mode": "same_worker" if use_same_worker_baseline else "single_baseline",
                    "comparison_baseline_key": comparison_key,
                    "comparison_baseline_matched": comparison_matched,
                    "comparison_baseline_label": comparison_baseline.get("label"),
                    "validation_parallelism": validation_parallelism,
                    "validation_batch_count": validation_batch_count,
                }
            )
            append_jsonl(log_path, with_workload(cfg, validation))
            round_validations.append(validation)
            validation_completed_count += 1
            if accepted:
                accepted_this_round.append(validation)
            else:
                reject_reasons[reason] = reject_reasons.get(reason, 0) + 1

        append_jsonl(
            log_path,
            with_workload(cfg, {
                "event": "round_parallel_validation_start",
                "target": target,
                "round": round_index,
                "worker_count": len(workers),
                "validation_count": existing_round_real_count + len(prepared_candidates),
                "new_validation_count": len(prepared_candidates),
                "resume_existing_round_validation_count": existing_round_real_count,
                "validation_parallelism": validation_parallelism,
                "validation_batch_count": validation_batch_count,
                "worker_baseline_policy": baseline_policy,
                "shared_disk_restore_barrier": shared_disk_restore_barrier,
                "comparison_baseline_mode": "same_worker" if use_same_worker_baseline else "single_baseline",
                "worker_baseline_count": len(baseline_by_worker),
                "same_ssd_adaptive_parallelism": adaptive_parallelism_decision,
                "fallback_plan_change_replay_expansion": plan_change_expansion_audit,
            }),
        )
        if workers and len(workers) > 1 and len(prepared_candidates) > 1:
            worker_buckets: Dict[int, List[Tuple[int, Dict[str, Any], Dict[str, Any], Dict[str, Any]]]] = {
                idx: [] for idx in range(len(workers))
            }
            for idx, candidate, probe_changes, worker in prepared_candidates:
                worker_idx = safe_int(candidate.get("validation_worker_slot"), (idx - 1) % len(workers))
                worker_idx = max(0, min(worker_idx, len(workers) - 1))
                worker_buckets[worker_idx].append((idx, candidate, probe_changes, worker or workers[worker_idx]))

            if shared_disk_restore_barrier:
                with ThreadPoolExecutor(max_workers=validation_parallelism) as barrier_executor:
                    while any(worker_buckets.values()):
                        restored_batch: List[
                            Tuple[int, int, Dict[str, Any], Dict[str, Any], Dict[str, Any], str]
                        ] = []
                        for worker_idx in sorted(worker_buckets):
                            if not worker_buckets[worker_idx]:
                                continue
                            item_idx, item_candidate, item_probe_changes, item_worker = worker_buckets[worker_idx].pop(0)
                            label_idx = safe_int(item_candidate.get("raw_candidate_index"), item_idx)
                            item_label = f"{target}_r{round_index:02d}_c{label_idx:03d}_{item_candidate['mask']}"
                            restore_started = time.perf_counter()
                            append_jsonl(
                                log_path,
                                with_workload(
                                    cfg,
                                    {
                                        "event": "parallel_worker_restore_start",
                                        "target": target,
                                        "phase": "candidate",
                                        "round": round_index,
                                        "candidate_index": item_idx,
                                        "label": item_label,
                                        "worker_slot": worker_idx,
                                        "worker_key": worker_identity(item_worker),
                                        "restore_barrier": True,
                                    },
                                ),
                            )
                            try:
                                restore_row = restore_worker_from_hybench_template(cfg, item_worker, label=item_label)
                            except Exception as exc:
                                restore_row = {
                                    "worker": worker_identity(item_worker),
                                    "status": "failed",
                                    "error": str(exc),
                                }
                            restore_row.update(
                                {
                                    "event": "parallel_worker_restore",
                                    "target": target,
                                    "phase": "candidate",
                                    "round": round_index,
                                    "candidate_index": item_idx,
                                    "label": item_label,
                                    "worker_slot": worker_idx,
                                    "elapsed_s": restore_row.get(
                                        "elapsed_s", time.perf_counter() - restore_started
                                    ),
                                    "restore_barrier": True,
                                }
                            )
                            append_jsonl(log_path, with_workload(cfg, restore_row))
                            if restore_row.get("status") == "failed":
                                item_validation = failed_validation_result(
                                    cfg,
                                    item_candidate["params"],
                                    label=item_label,
                                    error=restore_row.get("error") or "worker restore failed",
                                    started_at=time.time(),
                                    worker=item_worker,
                                )
                                record_validation(item_idx, item_candidate, item_probe_changes, item_validation)
                                continue
                            restored_batch.append(
                                (
                                    worker_idx,
                                    item_idx,
                                    item_candidate,
                                    item_probe_changes,
                                    worker_with_prepared_restore(item_worker),
                                    item_label,
                                )
                            )

                        barrier_future_map: Dict[
                            Any, Tuple[int, int, Dict[str, Any], Dict[str, Any], Dict[str, Any], str]
                        ] = {}
                        for restored_item in restored_batch:
                            worker_idx, item_idx, item_candidate, item_probe_changes, item_worker, item_label = restored_item
                            future = barrier_executor.submit(
                                run_real_validation_safe,
                                cfg,
                                item_candidate["params"],
                                output_dir=out_dir,
                                label=item_label,
                                worker=item_worker,
                            )
                            barrier_future_map[future] = restored_item
                        for future in as_completed(barrier_future_map):
                            (
                                worker_idx,
                                item_idx,
                                item_candidate,
                                item_probe_changes,
                                item_worker,
                                item_label,
                            ) = barrier_future_map[future]
                            try:
                                item_validation = future.result()
                            except Exception as exc:
                                item_validation = failed_validation_result(
                                    cfg,
                                    item_candidate["params"],
                                    label=item_label,
                                    error=exc,
                                    started_at=time.time(),
                                    worker=item_worker,
                                )
                            record_validation(item_idx, item_candidate, item_probe_changes, item_validation)

            with ThreadPoolExecutor(max_workers=validation_parallelism) as executor:
                pending: Set[Any] = set()
                future_map: Dict[Any, Tuple[int, int, Dict[str, Any], Dict[str, Any], Dict[str, Any]]] = {}

                def submit_next(worker_idx: int) -> None:
                    if not worker_buckets.get(worker_idx):
                        return
                    item_idx, item_candidate, item_probe_changes, item_worker = worker_buckets[worker_idx].pop(0)
                    label_idx = safe_int(item_candidate.get("raw_candidate_index"), item_idx)
                    item_label = f"{target}_r{round_index:02d}_c{label_idx:03d}_{item_candidate['mask']}"
                    restore_started = time.perf_counter()
                    append_jsonl(
                        log_path,
                        with_workload(
                            cfg,
                            {
                                "event": "parallel_worker_restore_start",
                                "target": target,
                                "phase": "candidate",
                                "round": round_index,
                                "candidate_index": item_idx,
                                "label": item_label,
                                "worker_slot": worker_idx,
                                "worker_key": worker_identity(item_worker),
                            },
                        ),
                    )
                    try:
                        restore_row = restore_worker_from_hybench_template(cfg, item_worker, label=item_label)
                    except Exception as exc:
                        restore_row = {
                            "worker": worker_identity(item_worker),
                            "status": "failed",
                            "error": str(exc),
                        }
                    restore_row.update(
                        {
                            "event": "parallel_worker_restore",
                            "target": target,
                            "phase": "candidate",
                            "round": round_index,
                            "candidate_index": item_idx,
                            "label": item_label,
                            "worker_slot": worker_idx,
                            "elapsed_s": restore_row.get("elapsed_s", time.perf_counter() - restore_started),
                        }
                    )
                    append_jsonl(log_path, with_workload(cfg, restore_row))
                    if restore_row.get("status") == "failed":
                        item_validation = failed_validation_result(
                            cfg,
                            item_candidate["params"],
                            label=item_label,
                            error=restore_row.get("error") or "worker restore failed",
                            started_at=time.time(),
                            worker=item_worker,
                        )
                        record_validation(item_idx, item_candidate, item_probe_changes, item_validation)
                        submit_next(worker_idx)
                        return
                    item_worker = worker_with_prepared_restore(item_worker)
                    future = executor.submit(
                        run_real_validation_safe,
                        cfg,
                        item_candidate["params"],
                        output_dir=out_dir,
                        label=item_label,
                        worker=item_worker,
                    )
                    pending.add(future)
                    future_map[future] = (worker_idx, item_idx, item_candidate, item_probe_changes, item_worker)

                for worker_idx, bucket_items in worker_buckets.items():
                    if bucket_items:
                        submit_next(worker_idx)
                while pending:
                    future = next(as_completed(pending))
                    pending.remove(future)
                    worker_idx, item_idx, item_candidate, item_probe_changes, item_worker = future_map.pop(future)
                    try:
                        item_validation = future.result()
                    except Exception as exc:
                        label_idx = safe_int(item_candidate.get("raw_candidate_index"), item_idx)
                        label = f"{target}_r{round_index:02d}_c{label_idx:03d}_{item_candidate['mask']}"
                        item_validation = failed_validation_result(
                            cfg,
                            item_candidate["params"],
                            label=label,
                            error=exc,
                            started_at=time.time(),
                            worker=item_worker,
                        )
                    record_validation(item_idx, item_candidate, item_probe_changes, item_validation)
                    submit_next(worker_idx)
        else:
            for idx, candidate, probe_changes, worker in prepared_candidates:
                label_idx = safe_int(candidate.get("raw_candidate_index"), idx)
                validation = run_real_validation_safe(
                    cfg,
                    candidate["params"],
                    output_dir=out_dir,
                    label=f"{target}_r{round_index:02d}_c{label_idx:03d}_{candidate['mask']}",
                    worker=worker,
                )
                record_validation(idx, candidate, probe_changes, validation)

        validation_elapsed_s = time.perf_counter() - validation_started
        round_elapsed_s = time.perf_counter() - round_started
        round_ssd_utilization = ssd_utilization_from_validations(round_validations)
        append_jsonl(
            log_path,
            build_round_search_points_audit(
                cfg=cfg,
                target=target,
                round_index=round_index,
                active=active,
                base_params=base_params,
                ranges_payload={name: list(ranges[name]) for name in active},
                raw_candidates=raw_candidates,
                ranked_candidates=ranked_candidates,
                topk_candidates=topk_candidates,
                replay_pruned=[*existing_round_pruned, *replay_pruned],
                validations=round_validations,
                topk_limit=round_topk_limit,
                note=(
                    "All binary search points are listed. plan_probe is estimator-only; "
                    "actual_validation is present only for HyBench XP real replay attempts."
                ),
            ),
        )
        append_jsonl(
            log_path,
            build_round_validation_audit(
                cfg=cfg,
                target=target,
                round_index=round_index,
                validations=round_validations,
            ),
        )

        round_score_b_successful_count = 0
        round_score_c_successful_count = 0
        round_score_c_valid_count = 0
        for validation in round_validations:
            details = (
                validation.get("real_score_b")
                if isinstance(validation.get("real_score_b"), dict)
                else real_score_b_details(
                    validation,
                    baseline_for_range_anchor(validation),
                    target=target,
                    cfg=cfg,
                )
            )
            if details.get("successful"):
                round_score_b_successful_count += 1
            score_c_details = (
                validation.get("real_score_c")
                if isinstance(validation.get("real_score_c"), dict)
                else real_score_c_details(
                    validation,
                    baseline_for_range_anchor(validation),
                    target=target,
                    cfg=cfg,
                )
            )
            if score_c_details.get("valid"):
                round_score_c_valid_count += 1
            if score_c_details.get("successful"):
                round_score_c_successful_count += 1

        nested_outer_decisions: List[Dict[str, Any]] = []
        nested_inner_best_score: Optional[Dict[str, Any]] = None
        nested_accepted_candidate: Optional[Dict[str, Any]] = None
        plan_change_expansion_next_round_armed = False
        plan_change_expansion_next_round_state: Dict[str, Any] = {}
        if nested_policy_enabled:
            outer_reference = copy.deepcopy(current_best)
            (
                nested_accepted_candidate,
                nested_inner_best_score,
                nested_outer_decisions,
            ) = select_inner_best_for_outer_acceptance(
                round_validations,
                outer_reference,
                baseline_validation,
                target=target,
                cfg=cfg,
                consecutive_missed_outer_rounds=(
                    nested_outer_consecutive_misses
                ),
                comparison_resolver=(
                    outer_comparison_references
                    if use_same_worker_baseline
                    else None
                ),
            )
            accepted_this_round = []
            reject_reasons = {}
            for decision in nested_outer_decisions:
                resolved_validation = next(
                    (
                        validation
                        for validation in round_validations
                        if validation.get("label") == decision.get("candidate_label")
                    ),
                    None,
                )
                if resolved_validation is not None:
                    final_reason = (
                        "accepted_by_outer_semantics"
                        if decision.get("accepted")
                        else ";".join(decision.get("rejects") or ["outer_semantic_rejected"])
                    )
                    resolved_validation.update(
                        {
                            "accepted": bool(decision.get("accepted")),
                            "accept_reject_reason": final_reason,
                            "accept_reason": final_reason if decision.get("accepted") else "",
                            "reject_reason": "" if decision.get("accepted") else final_reason,
                            "outer_semantic_decision": decision,
                        }
                    )
                append_jsonl(
                    log_path,
                    with_workload(
                        cfg,
                        {
                            "event": "outer_candidate_decision",
                            "target": target,
                            "round": round_index,
                            **decision,
                        },
                    ),
                )
                append_jsonl(
                    log_path,
                    with_workload(
                        cfg,
                        {
                            "event": "candidate_outer_resolution",
                            "target": target,
                            "round": round_index,
                            "candidate_label": decision.get("candidate_label"),
                            "accepted": bool(decision.get("accepted")),
                            "acceptance_eligible": bool(
                                decision.get("acceptance_eligible")
                            ),
                            "selected_for_acceptance": bool(
                                decision.get("selected_for_acceptance")
                            ),
                            "rejects": decision.get("rejects") or [],
                            "state_updates_applied": {
                                "accepted": bool(decision.get("accepted")),
                                "incumbent": bool(decision.get("accepted")),
                                "range": bool(decision.get("accepted")),
                                "gradient": False,
                            },
                            "outer_semantic_decision": decision,
                        },
                    ),
                )
                if not decision.get("accepted"):
                    for reject in decision.get("rejects") or ["outer_semantic_rejected"]:
                        reject_reasons[str(reject)] = reject_reasons.get(str(reject), 0) + 1
            if nested_accepted_candidate is not None:
                accepted_this_round = [nested_accepted_candidate]
                nested_outer_accepted_count += 1
                nested_outer_consecutive_misses = 0
                for validation in round_validations:
                    if validation.get("label") == nested_accepted_candidate.get("label"):
                        validation.update(
                            {
                                "accepted": True,
                                "accept_reject_reason": "accepted_by_outer_semantics",
                                "accept_reason": "accepted_by_outer_semantics",
                                "reject_reason": "",
                                "outer_semantic_decision": nested_accepted_candidate.get(
                                    "outer_semantic_decision"
                                ),
                            }
                        )
                        break
                append_jsonl(
                    log_path,
                    with_workload(
                        cfg,
                        {
                            **nested_accepted_candidate,
                            "event": "outer_accepted_candidate",
                            "target": target,
                            "round": round_index,
                            "outer_accepted_count": nested_outer_accepted_count,
                        },
                    ),
                )
            misses_after_decision = (
                0
                if nested_accepted_candidate is not None
                else nested_outer_consecutive_misses + 1
            )
            plan_change_expansion_next_round_state = (
                plan_change_replay_expansion_state(
                    cfg,
                    misses_after_decision,
                )
            )
            base_topk_physical_slot_count = sum(
                1
                for validation in round_validations
                if 0 < safe_int(validation.get("topk_rank"), 0)
                <= base_round_topk_limit
            )
            base_topk_real_replay_complete = bool(
                base_round_topk_limit > 0
                and base_topk_physical_slot_count >= base_round_topk_limit
            )
            plan_change_expansion_next_round_state.update(
                {
                    "base_topk_limit": base_round_topk_limit,
                    "base_topk_physical_slot_count": base_topk_physical_slot_count,
                    "base_topk_real_replay_complete": base_topk_real_replay_complete,
                }
            )
            plan_change_expansion_next_round_armed = bool(
                nested_accepted_candidate is None
                and plan_change_expansion_next_round_state.get("active")
                and base_topk_real_replay_complete
            )
            if plan_change_expansion_next_round_armed:
                append_jsonl(
                    log_path,
                    with_workload(
                        cfg,
                        {
                            "event": "fallback_plan_change_replay_expansion_armed",
                            "target": target,
                            "round": round_index,
                            "next_round": round_index + 1,
                            "action": (
                                "continue_expansion_after_expanded_round_miss"
                                if plan_change_expansion_active
                                else "arm_after_fallback_tier_base_topk_miss"
                            ),
                            "base_topk_limit": base_round_topk_limit,
                            "real_replay_count_this_round": len(round_validations),
                            "selected_extra_count_this_round": safe_int(
                                plan_change_expansion_audit.get(
                                    "selected_extra_count"
                                ),
                                0,
                            ),
                            "outer_accepted": False,
                            **plan_change_expansion_next_round_state,
                        },
                    ),
                )
            nested_plan_change_expansion_armed = (
                plan_change_expansion_next_round_armed
            )
            append_jsonl(
                log_path,
                with_workload(
                    cfg,
                    {
                        "event": "inner_outer_handoff_summary",
                        "target": target,
                        "round": round_index,
                        "outer_reference_label": outer_reference.get("label"),
                        "real_topk_count": len(round_validations),
                        "outer_decision_count": len(nested_outer_decisions),
                        "accepted": nested_accepted_candidate is not None,
                        "consecutive_missed_outer_rounds_after_decision": (
                            nested_outer_consecutive_misses
                            if nested_accepted_candidate is not None
                            else nested_outer_consecutive_misses + 1
                        ),
                        "accepted_label": (
                            nested_accepted_candidate.get("label")
                            if nested_accepted_candidate is not None
                            else None
                        ),
                        "inner_best_score": nested_inner_best_score,
                        "interval_action": (
                            "shrink_around_accepted_candidate"
                            if nested_accepted_candidate is not None
                            else "keep_interval_and_continue_inner_loop"
                        ),
                        "fallback_plan_change_replay_expansion_this_round": (
                            plan_change_expansion_audit
                        ),
                        "fallback_plan_change_replay_expansion_next_round_armed": (
                            plan_change_expansion_next_round_armed
                        ),
                        "fallback_plan_change_replay_expansion_next_round_state": (
                            plan_change_expansion_next_round_state
                        ),
                    },
                ),
            )
            if nested_accepted_candidate is None:
                nested_outer_consecutive_misses += 1

        range_anchor: Optional[Dict[str, Any]]
        range_anchor_details_payload: Optional[Dict[str, Any]]
        nested_keep_interval = False
        no_improved_accepted_force_backtrack = False
        discarded_no_improved_search_anchor: Optional[Dict[str, Any]] = None
        if accepted_this_round:
            if nested_policy_enabled:
                round_best_accepted = nested_accepted_candidate
                current_best = copy.deepcopy(nested_accepted_candidate)
                best_improved = True
            else:
                round_best_accepted = select_round_best_accepted(accepted_this_round, target=target)
                next_best = select_best_accepted(current_best, accepted_this_round, target=target)
                best_improved = accepted_best_sort_key(next_best, target=target) > previous_best_key
                current_best = next_best
            current_best_probe = probe_from_validation_changes(current_best, baseline_probe)
            if best_improved or round_best_accepted is None:
                range_anchor = current_best
                range_update_anchor = "accepted_best"
            else:
                discarded_no_improved_search_anchor = summarize_validation(round_best_accepted, target)
                range_anchor = copy.deepcopy(current_best)
                range_update_anchor = "current_best_backtrack_after_no_improved_accepted_round"
                no_improved_accepted_force_backtrack = True
            range_anchor_baseline = baseline_for_range_anchor(range_anchor)
            range_anchor_details_payload = range_anchor_details(
                range_anchor,
                range_anchor_baseline,
                target=target,
                cfg=cfg,
            )
            if no_improved_accepted_force_backtrack:
                range_anchor_details_payload["fallback_reason"] = (
                    "no_improved_accepted_round_backtrack_to_current_best"
                )
            if use_same_worker_baseline:
                _, anchor_key, anchor_matched = resolve_worker_baseline(
                    range_anchor,
                    baseline_validation,
                    baseline_by_worker,
                )
                range_anchor_details_payload["comparison_baseline_mode"] = "same_worker"
                range_anchor_details_payload["comparison_baseline_key"] = anchor_key
                range_anchor_details_payload["comparison_baseline_matched"] = anchor_matched
                range_anchor_details_payload["comparison_baseline_label"] = range_anchor_baseline.get("label")
        else:
            range_update_anchor = "real_metric_anchor_for_search_only"
            range_anchor, range_anchor_details_payload = select_range_anchor(
                round_validations,
                baseline_validation,
                target=target,
                cfg=cfg,
                baseline_resolver=baseline_for_range_anchor if use_same_worker_baseline else None,
            )
            if range_anchor is None:
                range_anchor, range_anchor_details_payload = select_range_anchor(
                    round_validations,
                    baseline_validation,
                    target=target,
                    cfg=cfg,
                    baseline_resolver=baseline_for_range_anchor if use_same_worker_baseline else None,
                    allow_near_miss=True,
                )
                if range_anchor is not None and range_anchor_details_payload is not None:
                    range_anchor_details_payload["near_miss_anchor"] = True
                    range_anchor_details_payload["near_miss_reason"] = (
                        "continue_search_from_guarded_candidate_near_target_sweet_spot"
                    )
                    range_update_anchor = "near_miss_metric_anchor_for_search_only"
            if use_same_worker_baseline and range_anchor is not None and range_anchor_details_payload is not None:
                range_anchor_baseline = baseline_for_range_anchor(range_anchor)
                _, anchor_key, anchor_matched = resolve_worker_baseline(
                    range_anchor,
                    baseline_validation,
                    baseline_by_worker,
                )
                range_anchor_details_payload["comparison_baseline_mode"] = "same_worker"
                range_anchor_details_payload["comparison_baseline_key"] = anchor_key
                range_anchor_details_payload["comparison_baseline_matched"] = anchor_matched
                range_anchor_details_payload["comparison_baseline_label"] = range_anchor_baseline.get("label")
        zero_accepted_force_backtrack = False
        if not accepted_this_round and zero_accepted_backtrack and current_best.get("accepted"):
            discarded_search_anchor = summarize_validation(range_anchor, target) if range_anchor else None
            range_anchor = copy.deepcopy(current_best)
            range_anchor_baseline = baseline_for_range_anchor(range_anchor)
            range_anchor_details_payload = range_anchor_details(
                range_anchor,
                range_anchor_baseline,
                target=target,
                cfg=cfg,
            )
            if use_same_worker_baseline:
                _, anchor_key, anchor_matched = resolve_worker_baseline(
                    range_anchor,
                    baseline_validation,
                    baseline_by_worker,
                )
                range_anchor_details_payload["comparison_baseline_mode"] = "same_worker"
                range_anchor_details_payload["comparison_baseline_key"] = anchor_key
                range_anchor_details_payload["comparison_baseline_matched"] = anchor_matched
                range_anchor_details_payload["comparison_baseline_label"] = range_anchor_baseline.get("label")
            range_anchor_details_payload["fallback_reason"] = "zero_accepted_round_backtrack_to_current_best"
            range_anchor_details_payload["fallback_from"] = "current_best_accepted"
            range_anchor_details_payload["discarded_search_anchor"] = discarded_search_anchor
            range_update_anchor = "current_best_backtrack_after_zero_accepted_round"
            zero_accepted_force_backtrack = True
        if range_anchor_policy == SMALL_LOOP_TOP1_ANCHOR_POLICY:
            range_anchor, range_anchor_details_payload = select_small_loop_top1_anchor(
                topk_candidates,
                round_validations,
            )
            no_improved_accepted_force_backtrack = False
            zero_accepted_force_backtrack = False
            if range_anchor is not None:
                range_update_anchor = "small_loop_top1"
            else:
                range_anchor = copy.deepcopy(baseline_validation)
                range_anchor_details_payload = {
                    "policy": SMALL_LOOP_TOP1_ANCHOR_POLICY,
                    "selection_source": "small_loop_plan_estimator_only",
                    "real_metrics_used_for_anchor_selection": False,
                    "fallback_reason": "small_loop_top1_missing",
                    "fallback_from": "fixed_baseline",
                    "label": range_anchor.get("label"),
                    "params": range_anchor.get("params") or base_params,
                }
                range_update_anchor = "small_loop_top1_baseline_fallback"
        elif range_anchor_policy == REAL_SCORE_B_ANCHOR_POLICY:
            range_anchor, range_anchor_details_payload = select_real_score_b_anchor(
                round_validations,
                baseline_validation,
                target=target,
                cfg=cfg,
                baseline_resolver=baseline_for_range_anchor if use_same_worker_baseline else None,
            )
            no_improved_accepted_force_backtrack = False
            zero_accepted_force_backtrack = False
            if range_anchor is not None:
                range_update_anchor = "real_score_b_successful_argmax"
            else:
                fallback_anchor, fallback_details = select_small_loop_top1_anchor(
                    topk_candidates,
                    round_validations,
                )
                range_anchor = fallback_anchor or copy.deepcopy(baseline_validation)
                range_anchor_details_payload = {
                    "policy": REAL_SCORE_B_ANCHOR_POLICY,
                    "successful": False,
                    "fallback_reason": "no_successful_real_candidate_in_round",
                    "fallback_from": "small_loop_top1",
                    "small_loop_top1_details": fallback_details,
                    "label": range_anchor.get("label"),
                    "params": range_anchor.get("params") or base_params,
                    "resources": range_anchor.get("resources") or {},
                }
                range_update_anchor = "real_score_b_no_success_fallback_small_loop_top1"
        elif range_anchor_policy == REAL_SCORE_C_ANCHOR_POLICY:
            range_anchor, range_anchor_details_payload = select_real_score_c_anchor(
                round_validations,
                baseline_validation,
                target=target,
                cfg=cfg,
                baseline_resolver=baseline_for_range_anchor if use_same_worker_baseline else None,
            )
            no_improved_accepted_force_backtrack = False
            zero_accepted_force_backtrack = False
            if range_anchor is None or range_anchor_details_payload is None:
                raise RuntimeError(
                    "real score C requires at least one valid real TopK replay; "
                    "small-loop fallback is forbidden"
                )
            if range_anchor_details_payload.get("successful"):
                range_update_anchor = "real_score_c_successful_argmax"
            else:
                range_anchor_details_payload["fallback_reason"] = (
                    "no_resource_gate_success_in_round"
                )
                range_anchor_details_payload["fallback_from"] = (
                    "valid_real_score_c_argmax"
                )
                range_update_anchor = "real_score_c_no_success_fallback_valid_argmax"
        elif range_anchor_policy == NESTED_RESOURCE_SEMANTIC_POLICY:
            no_improved_accepted_force_backtrack = False
            zero_accepted_force_backtrack = False
            if nested_accepted_candidate is not None:
                range_anchor = copy.deepcopy(nested_accepted_candidate)
                range_anchor_details_payload = {
                    "policy": NESTED_RESOURCE_SEMANTIC_POLICY,
                    "selection_source": "inner_real_resource_score_then_outer_semantic_acceptance",
                    "inner_resource_score": nested_inner_best_score,
                    "outer_semantic_decision": nested_accepted_candidate.get(
                        "outer_semantic_decision"
                    ),
                    "label": range_anchor.get("label"),
                    "params": range_anchor.get("params") or {},
                    "resources": range_anchor.get("resources") or {},
                    "db_qps": range_anchor.get("db_qps"),
                    "accepted": True,
                }
                range_update_anchor = "outer_accepted_inner_best"
            else:
                range_anchor = copy.deepcopy(current_best)
                range_anchor_details_payload = {
                    "policy": NESTED_RESOURCE_SEMANTIC_POLICY,
                    "selection_source": "outer_rejected_all_presented_inner_candidates",
                    "outer_decisions": nested_outer_decisions,
                    "label": range_anchor.get("label"),
                    "params": range_anchor.get("params") or base_params,
                    "resources": range_anchor.get("resources") or {},
                    "db_qps": range_anchor.get("db_qps"),
                    "accepted": False,
                    "detail": "candidate interval is unchanged; next small-loop attempt excludes already replayed TopK",
                }
                range_update_anchor = "outer_rejected_keep_interval"
                nested_keep_interval = True
        range_anchor_stop_reason: Optional[str] = None
        range_update_tie_break = "low"
        range_update_strategy = "shrink_around_anchor"
        range_update_previous_ranges = {name: list(ranges[name]) for name in active}
        if no_improved_accepted_force_backtrack:
            range_update_strategy = (
                "no_improved_accepted_shrink_around_current_best"
                if stop_only_on_epsilon
                else "no_improved_accepted_backtrack_expand_around_current_best"
            )
        if zero_accepted_force_backtrack:
            range_update_strategy = (
                "zero_accepted_shrink_around_current_best"
                if stop_only_on_epsilon
                else "zero_accepted_backtrack_expand_around_current_best"
            )
        if nested_keep_interval:
            range_update_strategy = "keep_interval_and_continue_inner_loop"
        if range_anchor is None:
            if current_best.get("accepted"):
                range_anchor = copy.deepcopy(current_best)
                range_anchor_baseline = baseline_for_range_anchor(range_anchor)
                range_anchor_details_payload = range_anchor_details(
                    range_anchor,
                    range_anchor_baseline,
                    target=target,
                    cfg=cfg,
                )
                if use_same_worker_baseline:
                    _, anchor_key, anchor_matched = resolve_worker_baseline(
                        range_anchor,
                        baseline_validation,
                        baseline_by_worker,
                    )
                    range_anchor_details_payload["comparison_baseline_mode"] = "same_worker"
                    range_anchor_details_payload["comparison_baseline_key"] = anchor_key
                    range_anchor_details_payload["comparison_baseline_matched"] = anchor_matched
                    range_anchor_details_payload["comparison_baseline_label"] = range_anchor_baseline.get("label")
                range_anchor_details_payload["fallback_reason"] = "no_usable_round_anchor_after_zero_accepted_round"
                range_anchor_details_payload["fallback_from"] = "current_best_accepted"
                range_update_anchor = "current_best_fallback_after_no_round_anchor"
                if zero_accepted_backtrack:
                    range_update_strategy = (
                        "zero_accepted_shrink_around_current_best"
                        if stop_only_on_epsilon
                        else "zero_accepted_backtrack_expand_around_current_best"
                    )
                else:
                    range_update_tie_break = "high" if is_none_target(target) else "low"
            else:
                range_anchor = copy.deepcopy(current_best)
                range_anchor_details_payload = {
                    "usable": False,
                    "fallback_reason": "no_usable_round_anchor_after_zero_accepted_round",
                    "fallback_from": "current_best_baseline",
                    "status": range_anchor.get("status"),
                    "label": range_anchor.get("label"),
                }
                range_update_anchor = "baseline_fallback_after_no_round_anchor"
                if stop_only_on_epsilon:
                    range_update_strategy = "baseline_fallback_shrink_until_epsilon"
                else:
                    range_anchor_stop_reason = "no_range_anchor_after_zero_accepted_round"
        best_improved = (
            nested_accepted_candidate is not None
            if nested_policy_enabled
            else accepted_best_sort_key(current_best, target=target) > previous_best_key
        )
        if best_improved:
            no_improvement_rounds = 0
        else:
            no_improvement_rounds += 1
        if nested_policy_enabled:
            ranges, _ = update_nested_outer_ranges(
                ranges,
                active,
                nested_accepted_candidate,
            )
        elif range_update_strategy in {
            "zero_accepted_backtrack_expand_around_current_best",
            "no_improved_accepted_backtrack_expand_around_current_best",
        }:
            ranges = expand_ranges_around_point(
                ranges,
                initial_search_ranges,
                active,
                range_anchor.get("params") or base_params,
                factor=zero_accepted_backtrack_factor,
            )
        else:
            ranges = update_ranges_around_best(
                ranges,
                active,
                range_anchor.get("params") or base_params,
                tie_break=range_update_tie_break,
            )
        if nested_policy_enabled and nested_accepted_candidate is not None:
            nested_interval_real_signatures.clear()
            nested_interval_real_plan_fingerprints.clear()

        round_summary = {
            "event": "round_summary",
            "target": target,
            "round": round_index,
            "candidate_count": round_raw_search_point_count,
            "raw_search_point_count": round_raw_search_point_count,
            "remaining_raw_candidate_count": len(raw_candidates),
            "full_round_search_points": full_round_search_points,
            "topk_limit": round_topk_limit,
            "base_topk_limit": base_round_topk_limit,
            "new_topk_limit": topk_limit,
            "topk_candidate_count": cumulative_topk_candidate_count,
            "new_topk_candidate_count": len(topk_candidates),
            "resume_existing_round_validation_count": existing_round_real_count,
            "resume_existing_round_pruned_count": existing_round_pruned_count,
            "resume_existing_round_operator_rule_rejected_count": (
                existing_round_operator_rule_rejected_count
            ),
            "resume_existing_round_topk_count": existing_round_topk_count,
            "round_topk_remaining_capacity": round_topk_remaining_capacity,
            "replay_pruned_count": cumulative_replay_pruned_count,
            "new_replay_pruned_count": len(replay_pruned),
            "operator_rule_evaluated_count": len(probed_candidates),
            "operator_rule_eligible_count": len(ranked_candidates),
            "operator_rule_rejected_count": (
                existing_round_operator_rule_rejected_count
                + len(operator_rule_rejected)
            ),
            "new_operator_rule_rejected_count": len(operator_rule_rejected),
            "operator_rule_reject_reason_counts": operator_rule_reject_reason_counts,
            "operator_rule_topk_backfill_count": operator_rule_backfill_count,
            "operator_rule_rejects_consume_topk_slots": False,
            "operator_rule_rejects_consume_physical_replay_slots": False,
            "behavior_cluster_gate": behavior_cluster_gate_audit,
            "behavior_cluster_rejected_count": len(behavior_cluster_rejected),
            "behavior_cluster_rejects_consume_topk_slots": False,
            "behavior_cluster_rejects_consume_physical_replay_slots": False,
            "replay_pruning_reason_counts": cumulative_pruning_reason_counts,
            "new_replay_pruning_reason_counts": pruning_reason_counts,
            "validation_count": cumulative_validation_count_after_pruning,
            "new_validation_count": len(prepared_candidates),
            "real_validation_count": existing_round_real_count + validation_completed_count,
            "new_real_validation_count": validation_completed_count,
            "accepted_count": len(accepted_this_round),
            "outer_semantic_accepted": (
                nested_accepted_candidate is not None if nested_policy_enabled else None
            ),
            "outer_accepted_count": (
                nested_outer_accepted_count if nested_policy_enabled else None
            ),
            "accepted_shrink_target": (
                accepted_shrink_target if nested_policy_enabled and accepted_shrink_target > 0 else None
            ),
            "inner_real_best_label": (
                nested_outer_decisions[0].get("candidate_label")
                if nested_policy_enabled and nested_outer_decisions
                else None
            ),
            "inner_real_best_score": (
                nested_outer_decisions[0].get("inner_real_score")
                if nested_policy_enabled and nested_outer_decisions
                else None
            ),
            "inner_candidates_presented_to_outer": (
                len(nested_outer_decisions) if nested_policy_enabled else None
            ),
            "outer_semantic_decisions": (
                nested_outer_decisions if nested_policy_enabled else []
            ),
            "fallback_plan_change_replay_expansion": (
                plan_change_expansion_audit if nested_policy_enabled else {}
            ),
            "fallback_plan_change_replay_expansion_next_round_armed": (
                plan_change_expansion_next_round_armed
                if nested_policy_enabled
                else False
            ),
            "fallback_plan_change_replay_expansion_next_round_state": (
                plan_change_expansion_next_round_state
                if nested_policy_enabled
                else {}
            ),
            "interval_action": (
                "keep_interval_and_continue_inner_loop"
                if nested_policy_enabled and nested_keep_interval
                else (
                    "shrink_around_accepted_candidate"
                    if nested_policy_enabled
                    else None
                )
            ),
            "score_b_successful_candidate_count": round_score_b_successful_count,
            "score_b_argmax_label": (
                range_anchor.get("label")
                if range_anchor_policy == REAL_SCORE_B_ANCHOR_POLICY
                and range_update_anchor == "real_score_b_successful_argmax"
                else None
            ),
            "score_c_valid_candidate_count": round_score_c_valid_count,
            "score_c_successful_candidate_count": round_score_c_successful_count,
            "score_c_argmax_label": (
                range_anchor.get("label")
                if range_anchor_policy == REAL_SCORE_C_ANCHOR_POLICY
                else None
            ),
            "best_candidate": summarize_validation(current_best, target),
            "round_final_validation": (
                summarize_validation(round_validations[-1], target, include_params=True)
                if round_validations
                else None
            ),
            "reject_reasons": reject_reasons,
            "degradation": degradation,
            "previous_ranges": range_update_previous_ranges,
            "updated_ranges": {name: list(ranges[name]) for name in active},
            "range_update_anchor": range_update_anchor,
            "range_anchor_policy": range_anchor_policy,
            "experiment_strategy": experiment_strategy,
            "range_update_strategy": range_update_strategy,
            "range_update_tie_break": range_update_tie_break,
            "zero_accepted_backtrack": zero_accepted_backtrack,
            "zero_accepted_backtrack_factor": zero_accepted_backtrack_factor,
            "range_anchor_label": range_anchor.get("label"),
            "range_anchor_mask": range_anchor.get("mask"),
            "range_anchor_accepted": range_anchor.get("accepted"),
            "range_anchor_details": range_anchor_details_payload,
            "range_anchor_point": summarize_validation(range_anchor, target, include_params=True),
            "plan_probe_parallelism": min(plan_probe_workers, len(raw_candidates)) if topk_limit > 0 else 0,
            "skip_plan_probe_for_full_replay": skip_plan_probe_for_full_replay,
            "plan_probe_elapsed_s": plan_probe_elapsed_s,
            "validation_parallelism": validation_parallelism,
            "validation_batch_count": validation_batch_count,
            "same_ssd_adaptive_parallelism": adaptive_parallelism_decision,
            "ssd_utilization": round_ssd_utilization,
            "worker_baseline_policy": baseline_policy,
            "comparison_baseline_mode": "same_worker" if use_same_worker_baseline else "single_baseline",
            "worker_baseline_count": len(baseline_by_worker),
            "validation_elapsed_s": validation_elapsed_s,
            "round_elapsed_s": round_elapsed_s,
            "best_improved": best_improved,
            "no_improvement_rounds": no_improvement_rounds,
            "no_improvement_round_limit": no_improvement_round_limit,
            "stop_only_on_epsilon": stop_only_on_epsilon,
            "no_improvement_stop_enabled": bool((not stop_only_on_epsilon) and no_improvement_round_limit > 0),
            "validation_throughput_candidates_per_s": (
                validation_completed_count / validation_elapsed_s if validation_elapsed_s > 0 else None
            ),
        }
        append_jsonl(log_path, with_workload(cfg, round_summary))
        rounds.append(round_summary)
        write_round_handoff_snapshot(
            out_dir=out_dir,
            cfg=cfg,
            target=target,
            round_summary=round_summary,
            baseline_validation=baseline_validation,
            current_best=current_best,
            active=active,
            max_rounds=max_rounds,
            required_rounds=required_rounds,
            required_rounds_by_param=required_rounds_by_param,
            topk_limit=round_topk_limit,
            log_path=log_path,
            stop_only_on_epsilon=stop_only_on_epsilon,
            no_improvement_round_limit=no_improvement_round_limit,
        )

        if range_anchor_stop_reason:
            stop_reason = range_anchor_stop_reason
            append_jsonl(
                log_path,
                with_workload(
                    cfg,
                    {
                        "event": "stage_stop",
                        "target": target,
                        "reason": stop_reason,
                        "round": round_index,
                        "final_ranges": {name: list(ranges[name]) for name in active},
                    },
                ),
            )
            stage_stop_written = True
            break
        if (
            nested_policy_enabled
            and accepted_shrink_target > 0
            and nested_outer_accepted_count >= accepted_shrink_target
        ):
            stop_reason = "accepted_shrink_target_reached"
            append_jsonl(
                log_path,
                with_workload(
                    cfg,
                    {
                        "event": "stage_stop",
                        "target": target,
                        "reason": stop_reason,
                        "round": round_index,
                        "outer_accepted_count": nested_outer_accepted_count,
                        "accepted_shrink_target": accepted_shrink_target,
                        "final_ranges": {name: list(ranges[name]) for name in active},
                    },
                ),
            )
            stage_stop_written = True
            break
        if (
            all_widths_below_epsilon(ranges, active, epsilon_cfg)
            and not (nested_policy_enabled and accepted_shrink_target > 0)
        ):
            stop_reason = "epsilon_converged"
            append_jsonl(
                log_path,
                with_workload(
                    cfg,
                    {
                        "event": "stage_stop",
                        "target": target,
                        "reason": stop_reason,
                        "converged": True,
                        "round": round_index,
                        "final_ranges": {name: list(ranges[name]) for name in active},
                    },
                ),
            )
            stage_stop_written = True
            break
        if (not stop_only_on_epsilon) and no_improvement_round_limit > 0 and no_improvement_rounds >= no_improvement_round_limit:
            stop_reason = "no_better_candidate_round_limit"
            append_jsonl(
                log_path,
                with_workload(
                    cfg,
                    {
                        "event": "stage_stop",
                        "target": target,
                        "reason": stop_reason,
                        "no_improvement_rounds": no_improvement_rounds,
                        "no_improvement_round_limit": no_improvement_round_limit,
                    },
                ),
            )
            stage_stop_written = True
            break
    if not stage_stop_written:
        append_jsonl(
            log_path,
            with_workload(
                cfg,
                {
                    "event": "stage_stop",
                    "target": target,
                    "reason": stop_reason,
                    "converged": all_widths_below_epsilon(ranges, active, epsilon_cfg),
                    "round": rounds[-1].get("round") if rounds else None,
                    "final_ranges": {name: list(ranges[name]) for name in active},
                },
            ),
        )

    result_key = f"best_for_{target}"
    epsilon_converged = all_widths_below_epsilon(ranges, active, epsilon_cfg)
    accepted_target_reached = bool(
        nested_policy_enabled
        and accepted_shrink_target > 0
        and nested_outer_accepted_count >= accepted_shrink_target
    )
    # A configured accepted-shrink target is the formal outer-loop terminal
    # condition. Ten accepted shrinks must still receive the same final real
    # confirmation as epsilon convergence.
    converged = bool(epsilon_converged or accepted_target_reached)
    baseline_goal_best, baseline_goal_reject_counts, baseline_goal_candidate_count = select_best_baseline_goal(
        [row for row in load_jsonl(log_path) if row.get("event") == "candidate_validation"],
        baseline_validation,
        target=target,
        cfg=cfg,
    )
    stage_log_rows = load_jsonl(log_path)
    observed_validations = [
        row for row in stage_log_rows if row.get("event") == "candidate_validation"
    ]
    final_score_b_baseline_resolver: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None
    if baseline_policy.get("enabled") and baseline_policy.get("compare_mode") == "same_worker" and baseline_by_worker:
        def final_score_b_baseline_resolver(validation: Dict[str, Any]) -> Dict[str, Any]:
            worker_baseline, _, _ = resolve_worker_baseline(
                validation,
                baseline_validation,
                baseline_by_worker,
            )
            return worker_baseline

    global_score_b_best, global_score_b_best_details = select_real_score_b_anchor(
        observed_validations,
        baseline_validation,
        target=target,
        cfg=cfg,
        baseline_resolver=final_score_b_baseline_resolver,
    )
    global_score_c_best, global_score_c_best_details = select_real_score_c_anchor(
        observed_validations,
        baseline_validation,
        target=target,
        cfg=cfg,
        baseline_resolver=final_score_b_baseline_resolver,
    )
    score_c_final_confirmation: Optional[Dict[str, Any]] = None
    if range_anchor_policy == REAL_SCORE_C_ANCHOR_POLICY and global_score_c_best is not None:
        selected_label = str(global_score_c_best.get("label") or "")
        selected_params = dict(global_score_c_best.get("params") or {})
        for row in reversed(stage_log_rows):
            if (
                row.get("event") == "strategy_final_confirmation"
                and row.get("selected_label") == selected_label
                and row.get("status") == "ok"
                and param_signature(row.get("params") or {}) == param_signature(selected_params)
            ):
                score_c_final_confirmation = row
                break
        if score_c_final_confirmation is None:
            confirmation = run_real_validation_safe(
                cfg,
                selected_params,
                output_dir=out_dir,
                label=f"{target}_score_c_final_confirmation",
            )
            confirmation.update(
                {
                    "event": "strategy_final_confirmation",
                    "target": target,
                    "experiment_strategy": experiment_strategy,
                    "range_anchor_policy": range_anchor_policy,
                    "selected_label": selected_label,
                    "selection_changed_by_confirmation": False,
                    "real_score_c": real_score_c_details(
                        confirmation,
                        baseline_validation,
                        target=target,
                        cfg=cfg,
                    ),
                }
            )
            append_jsonl(log_path, with_workload(cfg, confirmation))
            score_c_final_confirmation = confirmation
        if score_c_final_confirmation.get("status") != "ok":
            raise RuntimeError(
                "score C final confirmation failed for "
                f"target={target}: {score_c_final_confirmation.get('error')}"
            )
    nested_final_confirmation: Optional[Dict[str, Any]] = None
    nested_final_goal: Optional[Dict[str, Any]] = None
    if nested_policy_enabled and converged:
        selected_label = str(current_best.get("label") or "")
        selected_params = dict(current_best.get("params") or {})
        for row in reversed(stage_log_rows):
            if (
                row.get("event") == "post_convergence_final_validation"
                and row.get("selected_label") == selected_label
                and row.get("status") == "ok"
                and param_signature(row.get("params") or {}) == param_signature(selected_params)
            ):
                nested_final_confirmation = row
                nested_final_goal = (
                    row.get("final_goal")
                    if isinstance(row.get("final_goal"), dict)
                    else final_convergence_goal_details(
                        row,
                        baseline_validation,
                        target=target,
                        cfg=cfg,
                    )
                )
                break
        if nested_final_confirmation is None:
            confirmation = run_real_validation_safe(
                cfg,
                selected_params,
                output_dir=out_dir,
                label=f"{target}_nested_final_confirmation",
            )
            nested_final_goal = final_convergence_goal_details(
                confirmation,
                baseline_validation,
                target=target,
                cfg=cfg,
            )
            confirmation.update(
                {
                    "event": "post_convergence_final_validation",
                    "target": target,
                    "experiment_strategy": experiment_strategy,
                    "range_anchor_policy": range_anchor_policy,
                    "selected_label": selected_label,
                    "selection_changed_by_confirmation": False,
                    "final_goal": nested_final_goal,
                }
            )
            append_jsonl(log_path, with_workload(cfg, confirmation))
            nested_final_confirmation = confirmation
        if nested_final_confirmation.get("status") != "ok":
            raise RuntimeError(
                "nested-loop post-convergence validation failed for "
                f"target={target}: {nested_final_confirmation.get('error')}"
            )
    final_observed_validation = observed_validations[-1] if observed_validations else None
    final_round_summary = rounds[-1] if rounds else None
    final_output_point = summarize_validation(current_best, target, include_params=True)
    final_observed_point = summarize_optional_validation(
        final_observed_validation,
        target,
        include_params=True,
    )
    final_range_anchor_point = (
        copy.deepcopy(final_round_summary.get("range_anchor_point"))
        if isinstance(final_round_summary, dict)
        else None
    )
    strategy_final_point = (
        summarize_optional_validation(global_score_b_best, target, include_params=True)
        if range_anchor_policy == REAL_SCORE_B_ANCHOR_POLICY and global_score_b_best is not None
        else (
            summarize_optional_validation(global_score_c_best, target, include_params=True)
            if range_anchor_policy == REAL_SCORE_C_ANCHOR_POLICY and global_score_c_best is not None
            else (
                summarize_validation(current_best, target, include_params=True)
                if nested_policy_enabled
                else copy.deepcopy(final_range_anchor_point)
            )
        )
    )
    summary = {
        "event": "stage_summary",
        "workload": workload_name(cfg),
        "target": target,
        result_key: summarize_validation(current_best, target, include_params=True),
        f"policy_best_for_{target}": summarize_validation(current_best, target, include_params=True),
        f"baseline_goal_best_for_{target}": summarize_optional_validation(
            baseline_goal_best,
            target,
            include_params=True,
        ),
        "baseline": summarize_validation(baseline_validation, target, include_params=True),
        "final_output_point": final_output_point,
        "final_observed_point": final_observed_point,
        "final_range_anchor_point": final_range_anchor_point,
        "strategy_final_point": strategy_final_point,
        "global_score_b_best": summarize_optional_validation(
            global_score_b_best,
            target,
            include_params=True,
        ),
        "global_score_b_best_details": global_score_b_best_details,
        "global_score_c_best": summarize_optional_validation(
            global_score_c_best,
            target,
            include_params=True,
        ),
        "global_score_c_best_details": global_score_c_best_details,
        "score_c_final_confirmation": summarize_optional_validation(
            score_c_final_confirmation,
            target,
            include_params=True,
        ),
        "post_convergence_final_validation": summarize_optional_validation(
            nested_final_confirmation,
            target,
            include_params=True,
        ),
        "final_goal": nested_final_goal,
        "active_params": active,
        "final_ranges": {name: list(ranges[name]) for name in active},
        "epsilon": epsilon_cfg,
        "converged": converged,
        "epsilon_converged": epsilon_converged,
        "accepted_target_reached": accepted_target_reached,
        "outer_accepted_count": nested_outer_accepted_count if nested_policy_enabled else None,
        "accepted_shrink_target": accepted_shrink_target if nested_policy_enabled else None,
        "configured_max_rounds": configured_max_rounds,
        "requested_max_rounds": requested_max_rounds,
        "max_rounds": max_rounds,
        "required_rounds_for_epsilon": required_rounds,
        "required_rounds_by_param": required_rounds_by_param,
        "max_candidates_per_round": max_candidates,
        "candidate_topk_limit": max_candidates,
        "full_round_search_points": full_round_search_points,
        "candidate_selection_policy": stage_metadata.get("candidate_selection_policy"),
        "experiment_strategy": experiment_strategy,
        "range_anchor_policy": range_anchor_policy,
        "real_score_b": search_cfg.get("real_score_b") or {},
        "real_score_c": search_cfg.get("real_score_c") or {},
        "nested_loop": search_cfg.get("nested_loop") or {},
        "acceptance_policy": stage_metadata.get("acceptance_policy") or {},
        "replay_pruning_policy": stage_metadata.get("replay_pruning_policy"),
        "stop_only_on_epsilon": stop_only_on_epsilon,
        "no_improvement_round_limit": no_improvement_round_limit,
        "no_improvement_stop_enabled": bool((not stop_only_on_epsilon) and no_improvement_round_limit > 0),
        "resume_requested": resume_requested,
        "rounds": rounds,
        "completed_round_count": len(rounds),
        "validated_candidate_count": len(validated_signatures) - 1,
        "baseline_goal_candidate_count": baseline_goal_candidate_count,
        "baseline_goal_reject_counts": baseline_goal_reject_counts,
        "baseline_goal_replay_policy": "compare_completed_candidates_against_baseline",
        "stop_reason": stop_reason,
        "log_path": str(log_path),
        "stage_elapsed_s": time.perf_counter() - stage_started,
    }
    write_json(out_dir / f"{result_key}.json", summary)
    final_artifact = {
        "event": "final_point_snapshot",
        "workload": workload_name(cfg),
        "target": target,
        "converged": converged,
        "epsilon_converged": epsilon_converged,
        "accepted_target_reached": accepted_target_reached,
        "outer_accepted_count": nested_outer_accepted_count if nested_policy_enabled else None,
        "accepted_shrink_target": accepted_shrink_target if nested_policy_enabled else None,
        "stop_reason": stop_reason,
        "best_point": summary[result_key],
        "final_output_point": final_output_point,
        "final_observed_point": final_observed_point,
        "final_range_anchor_point": final_range_anchor_point,
        "strategy_final_point": strategy_final_point,
        "global_score_b_best": summary["global_score_b_best"],
        "global_score_b_best_details": global_score_b_best_details,
        "global_score_c_best": summary["global_score_c_best"],
        "global_score_c_best_details": global_score_c_best_details,
        "score_c_final_confirmation": summary["score_c_final_confirmation"],
        "post_convergence_final_validation": summary["post_convergence_final_validation"],
        "final_goal": nested_final_goal,
        "final_ranges": summary["final_ranges"],
        "epsilon": epsilon_cfg,
        "completed_round_count": len(rounds),
        "validated_candidate_count": summary["validated_candidate_count"],
        "log_path": str(log_path),
    }
    write_json(out_dir / f"final_for_{target}.json", final_artifact)
    append_jsonl(log_path, with_workload(cfg, final_artifact))
    append_jsonl(log_path, with_workload(cfg, summary))
    print(f"{target} binary search wrote {log_path}", flush=True)
    print(f"{result_key}={json.dumps(summary[result_key], ensure_ascii=False, sort_keys=True)}", flush=True)
    if stop_only_on_epsilon and not converged:
        print(
            f"{target} binary search has not reached epsilon; preserve artifacts and resume",
            flush=True,
        )
        return 3
    return 0


def summarize_validation(validation: Dict[str, Any], target: str, *, include_params: bool = False) -> Dict[str, Any]:
    resources = validation.get("resources") or {}
    summary = {
        "label": validation.get("label"),
        "status": validation.get("status"),
        "target": target,
        "target_resource_value": resources.get(target),
        "resources": {name: resources.get(name) for name in ("cpu", "memory", "io", "io_read", "io_write")},
        "db_qps": validation.get("db_qps"),
        "db_xact_tps": validation.get("db_xact_tps"),
        "pgbench_tps": (validation.get("pgbench") or {}).get("pgbench_tps"),
        "p90_ms": validation.get("p90_ms"),
        "p95_ms": validation.get("p95_ms"),
        "p99_ms": validation.get("p99_ms"),
        "accepted": validation.get("accepted"),
        "accept_reject_reason": validation.get("accept_reject_reason"),
    }
    if include_params:
        summary["params"] = validation.get("params")
        summary["effective_params"] = validation.get("effective_params")
    if validation.get("hybench"):
        summary["hybench"] = validation.get("hybench")
    if validation.get("hybench_run_config"):
        summary["hybench_run_config"] = validation.get("hybench_run_config")
    if validation.get("resource_samples"):
        summary["resource_samples"] = validation.get("resource_samples")
    if validation.get("actual_utilization_pct"):
        summary["actual_utilization_pct"] = validation.get("actual_utilization_pct")
    if validation.get("runtime_capability"):
        summary["runtime_capability"] = validation.get("runtime_capability")
    if validation.get("runtime_scoring"):
        summary["runtime_scoring"] = validation.get("runtime_scoring")
    if validation.get("validation_backend"):
        summary["validation_backend"] = validation.get("validation_backend")
    if validation.get("worker"):
        summary["worker"] = validation.get("worker")
    return summary


def summarize_optional_validation(
    validation: Optional[Dict[str, Any]],
    target: str,
    *,
    include_params: bool = False,
) -> Optional[Dict[str, Any]]:
    if validation is None:
        return None
    return summarize_validation(validation, target, include_params=include_params)


def generate_report(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    out_dir = output_dir_from_args(cfg, args.output_dir)
    suffix = workload_log_suffix(cfg)
    screening = read_json(out_dir / "screening_summary.json") if (out_dir / "screening_summary.json").exists() else {}
    stage_summaries: Dict[str, Any] = {}
    reject_counts: Dict[str, Dict[str, int]] = {}
    round_rows: Dict[str, List[Dict[str, Any]]] = {}
    stop_reasons: Dict[str, str] = {}
    for target in stage_targets():
        path = out_dir / f"best_for_{target}.json"
        if path.exists():
            stage_summaries[target] = read_json(path)
        log_path = out_dir / f"{target}_binary_search_{suffix}.jsonl"
        if not log_path.exists():
            log_path = out_dir / f"{target}_binary_search.jsonl"
        counts: Dict[str, int] = {}
        rows: List[Dict[str, Any]] = []
        for row in load_jsonl(log_path):
            if row.get("event") == "candidate_validation" and not row.get("accepted"):
                reason = str(row.get("accept_reject_reason") or "unknown")
                counts[reason] = counts.get(reason, 0) + 1
            if row.get("event") == "round_summary":
                rows.append(row)
            if row.get("event") == "stage_stop":
                stop_reasons[target] = str(row.get("reason") or "unknown")
        reject_counts[target] = counts
        round_rows[target] = rows

    lines: List[str] = []
    lines.append("# Binary Plan Resource Tuning Report")
    lines.append("")
    lines.append(f"- Generated at: `{time.strftime('%Y-%m-%d %H:%M:%S')}`")
    lines.append(f"- Config: `{cfg.get('_config_path')}`")
    lines.append(f"- Output dir: `{out_dir}`")
    lines.append(f"- Workload: `{workload_name(cfg)}`")
    lines.append(f"- Endpoint log: `{out_dir / f'endpoint_screening_{suffix}.jsonl'}`")
    lines.append("")
    lines.append("## Experiment Environment")
    lines.append("")
    env_cfg = ((cfg.get("binary_plan_tuning") or {}).get("environment") or {})
    validation_cfg = (((cfg.get("binary_plan_tuning") or {}).get("validation") or {}).get("docker_runtime") or {})
    all_validation_cfg = ((cfg.get("binary_plan_tuning") or {}).get("validation") or {})
    workers_cfg = all_validation_cfg.get("docker_workers") or {}
    hybench_cfg = all_validation_cfg.get("hybench_xp") or {}
    lines.append(f"- Runtime: `{env_cfg.get('runtime') or 'docker'}`")
    lines.append(f"- Validation backend: `{'hybench_xp' if to_bool(hybench_cfg.get('enabled'), False) else 'pgbench'}`")
    if to_bool(hybench_cfg.get("enabled"), False):
        lines.append(
            "- HyBench XP config: "
            f"sf=`{hybench_cfg.get('sf')}`, "
            f"xpRunMins=`{hybench_cfg.get('xp_run_mins')}`, "
            f"xtpclient=`{hybench_cfg.get('xtpclient')}`, "
            f"xapclient=`{hybench_cfg.get('xapclient')}`, "
            f"launcher=`{hybench_cfg.get('launcher')}`, "
            f"statement_file=`{hybench_cfg.get('statement_file')}`"
        )
    lines.append(f"- Container: `{env_cfg.get('container') or validation_cfg.get('container') or '(unknown)'}`")
    lines.append(f"- Host port: `{env_cfg.get('host_port') or ((cfg.get('postgres') or {}).get('port'))}`")
    lines.append(f"- Data dir: `{env_cfg.get('data_dir') or '(not configured)'}`")
    lines.append(f"- Log dir: `{env_cfg.get('log_dir') or '(not configured)'}`")
    lines.append(f"- Selected data root: `{env_cfg.get('selected_data_root') or '(not configured)'}`")
    if to_bool(workers_cfg.get("enabled"), False):
        workers = workers_cfg.get("workers") or []
        lines.append(
            "- Worker pool: "
            f"cpu_memory_parallelism={workers_cfg.get('cpu_memory_parallelism')}, "
            f"io_parallelism={workers_cfg.get('io_parallelism')}, "
            f"configured_workers={len(workers)}"
        )
        lines.append("- Worker data dirs: `" + "; ".join(
            str((worker or {}).get("data_dir")) for worker in workers if isinstance(worker, dict)
        ) + "`")
        lines.append("- Worker cpusets: `" + "; ".join(
            f"{(worker or {}).get('name')}={(worker or {}).get('cpuset_cpus')}"
            for worker in workers
            if isinstance(worker, dict)
        ) + "`")
    search_cfg = ((cfg.get("binary_plan_tuning") or {}).get("binary_search") or {})
    pruning_cfg = search_cfg.get("replay_candidate_pruning") if isinstance(search_cfg.get("replay_candidate_pruning"), dict) else {}
    lines.append(
        "- Binary-search policy: "
        f"max_rounds=`{search_cfg.get('max_rounds')}`, "
        f"max_candidates_per_round=`{search_cfg.get('max_candidates_per_round')}`, "
        f"stop_only_on_epsilon=`{search_cfg.get('stop_only_on_epsilon')}`, "
        f"no_improvement_round_limit=`{search_cfg.get('no_improvement_round_limit')}`"
    )
    lines.append(
        "- Replay pruning / error bounds: "
        f"enabled=`{pruning_cfg.get('enabled', False)}`, "
        f"keep_missing_error_bound=`{pruning_cfg.get('keep_missing_error_bound', True)}`, "
        f"provenance=`{pruning_cfg.get('error_bound_provenance', 'not_configured')}`"
    )
    lines.append("")
    lines.append("## Endpoint Screening")
    lines.append("")
    active = screening.get("active_params") or []
    removed = screening.get("removed_params") or []
    sampling = screening.get("sampling") or {}
    lines.append(f"- active_params: `{', '.join(active) if active else '(none)'}`")
    lines.append(f"- removed_params: `{', '.join(removed) if removed else '(none)'}`")
    lines.append(f"- sampled SQL count: `{sampling.get('sampled_count', screening.get('query_count', '(unknown)'))}`")
    lines.append(f"- source SQL count: `{sampling.get('source_count', '(unknown)')}`")
    lines.append(f"- sampling strategy: `{', '.join(str(item) for item in sampling.get('strategy') or [])}`")
    lines.append(f"- endpoint parallelism: `{screening.get('endpoint_parallelism', '(unknown)')}`")
    lines.append("")
    lines.append("| Param | Decision | Reason |")
    lines.append("|---|---:|---|")
    for name in COST_PARAMS:
        item = ((screening.get("param_results") or {}).get(name) or {})
        decision = "active" if item.get("active") else "removed"
        reason = "; ".join(item.get("reasons") or [])
        lines.append(f"| `{name}` | {decision} | {reason} |")
    lines.append("")
    lines.append("## Binary Search Results")
    lines.append("")
    for target in stage_targets():
        summary = stage_summaries.get(target) or {}
        best = summary.get(f"best_for_{target}") or {}
        policy_best = summary.get(f"policy_best_for_{target}") or best
        baseline_goal_best = summary.get(f"baseline_goal_best_for_{target}")
        baseline = summary.get("baseline") or {}
        lines.append(f"### {target.upper()}")
        lines.append("")
        lines.append(f"- Log: `{out_dir / f'{target}_binary_search_{suffix}.jsonl'}`")
        lines.append(f"- Baseline: {format_result_line(baseline)}")
        lines.append(f"- best_for_{target}: {format_result_line(best)}")
        lines.append(f"- policy_best_for_{target}: {format_result_line(policy_best)}")
        lines.append(f"- baseline_goal_best_for_{target}: {format_result_line(baseline_goal_best)}")
        lines.append(f"- best_for_{target}_params: `{json.dumps(best.get('params') or {}, sort_keys=True)}`")
        lines.append(
            f"- baseline_goal_best_for_{target}_params: "
            f"`{json.dumps((baseline_goal_best or {}).get('params') or {}, sort_keys=True)}`"
        )
        lines.append(f"- Final ranges: `{json.dumps(summary.get('final_ranges') or {}, sort_keys=True)}`")
        lines.append(f"- Stop/convergence: `{report_stop_reason(target, summary, round_rows.get(target) or [], stop_reasons, cfg)}`")
        if summary:
            lines.append(
                "- Loop/candidate caps: "
                f"max_rounds=`{summary.get('max_rounds')}`, "
                f"required_rounds_for_epsilon=`{summary.get('required_rounds_for_epsilon')}`, "
                f"full_round_search_points=`{summary.get('full_round_search_points')}`, "
                f"candidate_topk_limit=`{summary.get('candidate_topk_limit')}`, "
                f"stop_only_on_epsilon=`{summary.get('stop_only_on_epsilon')}`"
            )
            lines.append(
                "- Baseline-goal replay summary: "
                f"accepted_count=`{summary.get('baseline_goal_candidate_count')}`, "
                f"policy=`{summary.get('baseline_goal_replay_policy') or 'not_recorded'}`"
            )
        lines.append(f"- Stage elapsed seconds: `{format_float(summary.get('stage_elapsed_s'))}`")
        degradation_text = report_degradation(round_rows.get(target) or [])
        if degradation_text:
            lines.append(f"- Candidate validation budget: {degradation_text}")
        lines.append("")
        lines.append(
            "| Round | Raw Points | TopK | Pruned | Validated | Accepted | Parallelism | Batches | Validation s | Round s | Best Label | Best DB-QPS | Reject Reasons |"
        )
        lines.append("|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---|")
        for row in round_rows.get(target) or []:
            best_row = row.get("best_candidate") or {}
            reject_text = ", ".join(f"{key}={value}" for key, value in (row.get("reject_reasons") or {}).items())
            lines.append(
                "| {round} | {raw_points} | {topk} | {pruned} | {validation_count} | {accepted_count} | {parallelism} | {batches} | {validation_s} | {round_s} | `{label}` | {qps} | {rejects} |".format(
                    round=row.get("round"),
                    raw_points=row.get("raw_search_point_count", row.get("candidate_count")),
                    topk=row.get("topk_candidate_count", row.get("topk_limit", row.get("candidate_count"))),
                    pruned=row.get("replay_pruned_count", 0),
                    validation_count=row.get("validation_count"),
                    accepted_count=row.get("accepted_count"),
                    parallelism=row.get("validation_parallelism"),
                    batches=row.get("validation_batch_count"),
                    validation_s=format_float(row.get("validation_elapsed_s")),
                    round_s=format_float(row.get("round_elapsed_s")),
                    label=best_row.get("label"),
                    qps=format_float(best_row.get("db_qps")),
                    rejects=reject_text or "-",
                )
            )
        if reject_counts.get(target):
            lines.append("")
            lines.append("Reject reason totals: " + ", ".join(
                f"`{key}`={value}" for key, value in sorted(reject_counts[target].items())
            ))
        baseline_goal_reject_counts = summary.get("baseline_goal_reject_counts") or {}
        if baseline_goal_reject_counts:
            lines.append("")
            lines.append("Baseline-goal reject totals: " + ", ".join(
                f"`{key}`={value}" for key, value in sorted(baseline_goal_reject_counts.items())
            ))
        lines.append("")
    report_path = out_dir / "binary_plan_resource_tuning_report.md"
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if args.repo_report:
        repo_report = resolve_path(args.repo_report)
        repo_report.parent.mkdir(parents=True, exist_ok=True)
        repo_report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"report wrote {report_path}", flush=True)
    if args.repo_report:
        print(f"repo report wrote {resolve_path(args.repo_report)}", flush=True)
    return 0


def write_handoff_snapshots_from_logs(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    out_dir = output_dir_from_args(cfg, args.output_dir)
    suffix = workload_log_suffix(cfg)
    bpt = cfg.get("binary_plan_tuning") if isinstance(cfg.get("binary_plan_tuning"), dict) else {}
    search_cfg = bpt.get("binary_search") if isinstance(bpt.get("binary_search"), dict) else {}
    epsilon_cfg = search_cfg.get("epsilon") if isinstance(search_cfg.get("epsilon"), dict) else {"default": 1e-6}
    initial_ranges = param_ranges(cfg)
    configured_active = load_active_params(out_dir, getattr(args, "active_params", ""))
    targets = list(stage_targets()) if str(args.target) == "all" else [str(args.target)]
    wrote = 0

    for target in targets:
        log_path = out_dir / f"{target}_binary_search_{suffix}.jsonl"
        if not log_path.exists():
            continue
        rows = load_jsonl(log_path)
        baseline_validation: Dict[str, Any] = {"label": f"{target}_baseline", "resources": {}}
        active = list(configured_active)
        max_rounds = safe_int(search_cfg.get("max_rounds"), 0)
        required_rounds, required_rounds_by_param = required_rounds_for_epsilon(initial_ranges, active, epsilon_cfg)
        topk_limit = safe_int(search_cfg.get("max_candidates_per_round"), 0)
        stop_only_on_epsilon = to_bool(search_cfg.get("stop_only_on_epsilon"), False)
        no_improvement_round_limit = safe_int(search_cfg.get("no_improvement_round_limit"), 0)
        latest_best: Dict[str, Any] = {"label": f"{target}_unknown", "resources": {}}

        for row in rows:
            event = row.get("event")
            if event == "stage_start" or event == "stage_resume":
                active = [str(name) for name in row.get("active_params") or active]
                max_rounds = safe_int(row.get("max_rounds"), max_rounds)
                required_rounds = safe_int(row.get("required_rounds_for_epsilon"), required_rounds)
                if isinstance(row.get("required_rounds_by_param"), dict):
                    required_rounds_by_param = {
                        str(name): safe_int(value, 0)
                        for name, value in (row.get("required_rounds_by_param") or {}).items()
                    }
                topk_limit = safe_int(row.get("candidate_topk_limit"), safe_int(row.get("max_candidates_per_round"), topk_limit))
                stop_only_on_epsilon = to_bool(row.get("stop_only_on_epsilon"), stop_only_on_epsilon)
                no_improvement_round_limit = safe_int(row.get("no_improvement_round_limit"), no_improvement_round_limit)
            elif event == "baseline_validation":
                baseline_validation = row
                latest_best = row
            elif event == "candidate_validation" and row.get("accepted"):
                latest_best = row
            elif event == "round_summary":
                round_index = safe_int(row.get("round"), 0)
                snapshot_path = out_dir / "handoff_snapshots" / f"{target}_round_{round_index:03d}_snapshot.json"
                if snapshot_path.exists() and not to_bool(getattr(args, "force", False), False):
                    continue
                if isinstance(row.get("best_candidate"), dict):
                    latest_best = row.get("best_candidate") or latest_best
                write_round_handoff_snapshot(
                    out_dir=out_dir,
                    cfg=cfg,
                    target=target,
                    round_summary=row,
                    baseline_validation=baseline_validation,
                    current_best=latest_best,
                    active=active,
                    max_rounds=max_rounds,
                    required_rounds=required_rounds,
                    required_rounds_by_param=required_rounds_by_param,
                    topk_limit=safe_int(row.get("topk_limit"), topk_limit),
                    log_path=log_path,
                    stop_only_on_epsilon=stop_only_on_epsilon,
                    no_improvement_round_limit=no_improvement_round_limit,
                )
                wrote += 1

    print(f"handoff snapshots wrote_or_refreshed={wrote} out_dir={out_dir}", flush=True)
    return 0


def finalize_stage_from_log(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    target = str(args.target).strip().lower()
    if target not in set(stage_targets()):
        raise ValueError("--target must be cpu, memory, io, or none")
    out_dir = output_dir_from_args(cfg, args.output_dir)
    suffix = workload_log_suffix(cfg)
    log_path = out_dir / f"{target}_binary_search_{suffix}.jsonl"
    if not log_path.exists():
        raise FileNotFoundError(str(log_path))

    rows = load_jsonl(log_path)
    baseline: Optional[Dict[str, Any]] = None
    candidates_by_label: Dict[str, Dict[str, Any]] = {}
    candidates_in_order: List[Dict[str, Any]] = []
    round_summaries: List[Dict[str, Any]] = []
    existing_stage_summary = False
    existing_stage_stop = ""
    for row in rows:
        event = row.get("event")
        if event == "baseline_validation":
            baseline = row
        elif event == "candidate_validation" and row.get("label"):
            candidates_by_label[str(row.get("label"))] = row
            candidates_in_order.append(row)
        elif event == "round_summary":
            round_summaries.append(row)
        elif event == "stage_stop" and row.get("reason"):
            existing_stage_stop = str(row.get("reason"))
        elif event == "stage_summary":
            existing_stage_summary = True

    if baseline is None:
        raise RuntimeError(f"baseline_validation not found in {log_path}")

    current_best = copy.deepcopy(baseline)
    replay_accept_count = 0
    for candidate in candidates_in_order:
        accepted, reason, decision = compare_actual(
            candidate,
            current_best,
            baseline,
            target=target,
            cfg=cfg,
        )
        replay_candidate = copy.deepcopy(candidate)
        replay_candidate.update(
            {
                "accepted": accepted,
                "accept_reject_reason": reason,
                "accept_reason": reason if accepted else "",
                "reject_reason": "" if accepted else reason,
                "decision": decision,
            }
        )
        if accepted:
            replay_accept_count += 1
            current_best = select_best_accepted(current_best, [replay_candidate], target=target)

    if round_summaries:
        last_round = round_summaries[-1]
        final_ranges = last_round.get("updated_ranges") or {}
        completed_rounds = len(round_summaries)
    else:
        final_ranges = {}
        completed_rounds = 0
    best_validation = current_best
    baseline_goal_best, baseline_goal_reject_counts, baseline_goal_candidate_count = select_best_baseline_goal(
        candidates_in_order,
        baseline,
        target=target,
        cfg=cfg,
    )

    stop_reason = str(args.reason or existing_stage_stop or "manual_finalize_from_log")
    active = load_active_params(out_dir, getattr(args, "active_params", ""))
    result_key = f"best_for_{target}"
    summary = {
        "event": "stage_summary",
        "workload": workload_name(cfg),
        "target": target,
        result_key: summarize_validation(best_validation, target, include_params=True),
        f"policy_best_for_{target}": summarize_validation(best_validation, target, include_params=True),
        f"baseline_goal_best_for_{target}": summarize_optional_validation(
            baseline_goal_best,
            target,
            include_params=True,
        ),
        "baseline": summarize_validation(baseline, target, include_params=True),
        "active_params": active,
        "final_ranges": final_ranges,
        "acceptance_policy": (cfg.get("binary_plan_tuning") or {}).get("acceptance_policy") or {},
        "rounds": round_summaries,
        "validated_candidate_count": len(candidates_by_label),
        "baseline_goal_candidate_count": baseline_goal_candidate_count,
        "baseline_goal_reject_counts": baseline_goal_reject_counts,
        "baseline_goal_replay_policy": "compare_completed_candidates_against_baseline",
        "stop_reason": stop_reason,
        "log_path": str(log_path),
        "stage_elapsed_s": None,
        "finalized_from_log": True,
        "completed_round_count": completed_rounds,
        "candidate_replay_accept_count": replay_accept_count,
        "candidate_replay_policy": "compare_completed_candidates_against_current_best",
    }
    write_json(out_dir / f"{result_key}.json", summary)
    if not existing_stage_summary or to_bool(getattr(args, "force_append", False), False):
        append_jsonl(
            log_path,
            with_workload(
                cfg,
                {
                    "event": "stage_stop",
                    "target": target,
                    "reason": stop_reason,
                    "finalized_from_log": True,
                },
            ),
        )
        append_jsonl(log_path, with_workload(cfg, summary))
    print(f"finalized {target} stage from {log_path}", flush=True)
    print(f"{result_key}={json.dumps(summary[result_key], ensure_ascii=False, sort_keys=True)}", flush=True)
    return 0


def backfill_round_audit_from_log(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    out_dir = output_dir_from_args(cfg, args.output_dir)
    suffix = workload_log_suffix(cfg)
    base_params = initial_params(cfg)
    fallback_ranges = param_ranges(cfg)
    configured_active = load_active_params(out_dir, getattr(args, "active_params", ""))
    targets = list(stage_targets()) if str(args.target) == "all" else [str(args.target).strip().lower()]
    wrote = 0

    for target in targets:
        log_path = out_dir / f"{target}_binary_search_{suffix}.jsonl"
        if not log_path.exists():
            continue
        rows = load_jsonl(log_path)
        if any(row.get("event") == "round_search_points_audit" for row in rows) and not to_bool(
            getattr(args, "force", False),
            False,
        ):
            continue

        active = list(configured_active)
        ranges_by_round: Dict[int, Dict[str, Any]] = {}
        topk_limit_by_round: Dict[int, int] = {}
        validations_by_round: Dict[int, List[Dict[str, Any]]] = {}
        pruned_by_round: Dict[int, List[Dict[str, Any]]] = {}
        summaries_by_round: Dict[int, Dict[str, Any]] = {}

        for row in rows:
            event = row.get("event")
            if event in {"stage_start", "stage_resume"}:
                active = [str(name) for name in row.get("active_params") or active]
            elif event == "round_start":
                round_index = safe_int(row.get("round"), 0)
                if round_index > 0:
                    ranges_by_round[round_index] = row.get("ranges") or {}
                    topk_limit_by_round[round_index] = safe_int(
                        row.get("topk_limit"),
                        safe_int(row.get("candidate_topk_limit"), 0),
                    )
            elif event == "candidate_validation":
                round_index = safe_int(row.get("round"), 0)
                if round_index > 0:
                    validations_by_round.setdefault(round_index, []).append(row)
            elif event == "candidate_replay_pruned":
                round_index = safe_int(row.get("round"), 0)
                if round_index > 0:
                    pruned_by_round.setdefault(round_index, []).append(row)
            elif event == "round_summary":
                round_index = safe_int(row.get("round"), 0)
                if round_index > 0:
                    summaries_by_round[round_index] = row

        if not active:
            active = [name for name in COST_PARAMS if name in fallback_ranges]
        for round_index in sorted(set(ranges_by_round) | set(summaries_by_round)):
            ranges_payload = ranges_by_round.get(round_index) or {}
            audit = build_round_search_points_audit(
                cfg=cfg,
                target=target,
                round_index=round_index,
                active=active,
                base_params=base_params,
                ranges_payload=ranges_payload,
                replay_pruned=pruned_by_round.get(round_index) or [],
                validations=validations_by_round.get(round_index) or [],
                topk_limit=topk_limit_by_round.get(round_index),
                note=(
                    "Backfilled from existing log without rerunning EXPLAIN or HyBench. "
                    "Non-selected points have no actual resources because they were not replayed in the original run."
                ),
            )
            append_jsonl(log_path, audit)
            validation_audit = build_round_validation_audit(
                cfg=cfg,
                target=target,
                round_index=round_index,
                validations=validations_by_round.get(round_index) or [],
            )
            append_jsonl(log_path, validation_audit)
            wrote += 2

    print(f"backfilled round audit events={wrote} out_dir={out_dir}", flush=True)
    return 0


def replay_best_from_log(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    source_stage = str(args.source_stage).strip().lower()
    eval_target = str(args.target).strip().lower()
    if source_stage not in set(stage_targets()):
        raise ValueError("--source-stage must be cpu, memory, io, or none")
    if eval_target not in {"cpu", "memory", "io", "none", "no_bottleneck", "no-bottleneck"}:
        raise ValueError("--target must be cpu, memory, io, or none")

    out_dir = output_dir_from_args(cfg, args.output_dir)
    suffix = workload_log_suffix(cfg)
    log_path = out_dir / f"{source_stage}_binary_search_{suffix}.jsonl"
    if not log_path.exists():
        raise FileNotFoundError(str(log_path))

    rows = load_jsonl(log_path)
    baseline: Optional[Dict[str, Any]] = None
    candidates: List[Dict[str, Any]] = []
    for row in rows:
        event = row.get("event")
        if event == "baseline_validation":
            baseline = row
        elif event == "candidate_validation" and row.get("label"):
            candidates.append(row)
    if baseline is None:
        raise RuntimeError(f"baseline_validation not found in {log_path}")

    best, reject_counts, accepted_count = select_best_baseline_goal(
        candidates,
        baseline,
        target=eval_target,
        cfg=cfg,
    )
    best_decision: Optional[Dict[str, Any]] = None
    if best is not None:
        _accepted, _reason, best_decision = compare_against_baseline_goal(
            best,
            baseline,
            target=eval_target,
            cfg=cfg,
        )

    safe_target = eval_target.replace("-", "_")
    summary = {
        "event": "replay_best_summary",
        "workload": workload_name(cfg),
        "source_stage": source_stage,
        "evaluation_target": eval_target,
        "log_path": str(log_path),
        "candidate_count": len(candidates),
        "accepted_count": accepted_count,
        "reject_counts": reject_counts,
        "baseline": summarize_validation(baseline, source_stage, include_params=True),
        "best_candidate": summarize_optional_validation(best, eval_target, include_params=True),
        "best_candidate_decision": best_decision,
        "selection_policy": "compare_completed_candidates_against_baseline",
    }
    json_path = out_dir / f"{source_stage}_replay_best_{safe_target}.json"
    write_json(json_path, summary)

    lines: List[str] = []
    lines.append(f"# Replay Best Summary: {source_stage} -> {eval_target}")
    lines.append("")
    lines.append(f"- Generated at: `{time.strftime('%Y-%m-%d %H:%M:%S')}`")
    lines.append(f"- Workload: `{workload_name(cfg)}`")
    lines.append(f"- Source stage: `{source_stage}`")
    lines.append(f"- Evaluation target: `{eval_target}`")
    lines.append(f"- Log: `{log_path}`")
    lines.append(f"- Candidate count: `{len(candidates)}`")
    lines.append(f"- Accepted count: `{accepted_count}`")
    lines.append(f"- Baseline: {format_result_line(summary['baseline'])}")
    lines.append(f"- Best candidate: {format_result_line(summary['best_candidate'])}")
    lines.append(
        f"- Best params: `{json.dumps(((summary.get('best_candidate') or {}).get('params') or {}), sort_keys=True)}`"
    )
    if best_decision:
        lines.append(f"- Best decision: `{json.dumps(best_decision, ensure_ascii=False, sort_keys=True)}`")
    if reject_counts:
        lines.append(
            "- Reject counts: "
            + ", ".join(f"`{key}`={value}" for key, value in sorted(reject_counts.items()))
        )
    md_path = out_dir / f"{source_stage}_replay_best_{safe_target}.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    if args.repo_report:
        repo_report = resolve_path(args.repo_report)
        repo_report.parent.mkdir(parents=True, exist_ok=True)
        repo_report.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"replay-best wrote {json_path}", flush=True)
    print(f"replay-best wrote {md_path}", flush=True)
    if args.repo_report:
        print(f"repo report wrote {resolve_path(args.repo_report)}", flush=True)
    return 0


def validate_params(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    target = str(args.target).strip().lower()
    if target not in set(stage_targets()):
        raise ValueError("--target must be cpu, memory, io, or none")

    if args.params_file:
        raw_params = read_json(resolve_path(args.params_file))
    elif args.params_json:
        raw_params = json.loads(args.params_json)
    else:
        raise ValueError("provide --params-json or --params-file")
    params = normalize_params(raw_params)
    missing = [name for name in COST_PARAMS if name not in params]
    if missing:
        raise ValueError(f"params missing: {', '.join(missing)}")

    out_dir = output_dir_from_args(cfg, args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = workload_log_suffix(cfg)
    log_path = out_dir / f"{target}_single_validation_{suffix}.jsonl"

    workers = validation_workers(cfg, target)
    worker: Optional[Dict[str, Any]] = None
    if workers:
        requested_worker = str(args.worker_name or "").strip()
        if requested_worker:
            for item in workers:
                identities = {str(item.get("name") or ""), str(item.get("container") or "")}
                if requested_worker in identities:
                    worker = item
                    break
            if worker is None:
                raise ValueError(f"--worker-name not found for target={target}: {requested_worker}")
        else:
            worker = workers[0]

    raw_label = str(args.label or f"{target}_single_validation").strip()
    if not raw_label.startswith(f"{target}_"):
        label = f"{target}_{raw_label}"
    else:
        label = raw_label
    append_jsonl(
        log_path,
        with_workload(
            cfg,
            {
                "event": "single_candidate_validation_start",
                "target": target,
                "label": label,
                "params": params,
                "worker": worker,
                "baseline_output_dir": str(args.baseline_output_dir or ""),
            },
        ),
    )

    if worker:
        append_jsonl(
            log_path,
            with_workload(
                cfg,
                {
                    "event": "parallel_worker_restore_start",
                    "target": target,
                    "phase": "single_candidate",
                    "label": label,
                    "worker_key": worker_identity(worker),
                },
            ),
        )
        restore_started = time.perf_counter()
        try:
            restore_row = restore_worker_from_hybench_template(cfg, worker, label=label)
        except Exception as exc:
            restore_row = {"worker": worker_identity(worker), "status": "failed", "error": str(exc)}
        restore_row.update(
            {
                "event": "parallel_worker_restore",
                "target": target,
                "phase": "single_candidate",
                "label": label,
                "elapsed_s": restore_row.get("elapsed_s", time.perf_counter() - restore_started),
            }
        )
        append_jsonl(log_path, with_workload(cfg, restore_row))
        if restore_row.get("status") == "failed":
            raise RuntimeError(f"worker restore failed for {worker_identity(worker)}: {restore_row.get('error')}")
        worker = worker_with_prepared_restore(worker)

    validation = run_real_validation_safe(cfg, params, output_dir=out_dir, label=label, worker=worker)
    validation.update({"event": "single_candidate_validation", "target": target})

    baseline_output_dir = resolve_path(args.baseline_output_dir) if args.baseline_output_dir else out_dir
    baseline_log_path = baseline_output_dir / f"{target}_binary_search_{suffix}.jsonl"
    baseline: Optional[Dict[str, Any]] = None
    same_worker_baseline: Optional[Dict[str, Any]] = None
    if baseline_log_path.exists():
        baseline_rows: List[Dict[str, Any]] = []
        for row in load_jsonl(baseline_log_path):
            if row.get("event") == "baseline_validation":
                baseline = row
            elif row.get("event") == "baseline_worker_validation":
                baseline_rows.append(row)
        baseline_by_worker = baseline_map_from_validations(baseline_rows)
        if baseline_by_worker:
            same_worker_baseline, worker_key, matched = resolve_worker_baseline(
                validation,
                baseline or {},
                baseline_by_worker,
            )
            validation["comparison_baseline_key"] = worker_key
            validation["comparison_baseline_matched"] = matched
            validation["comparison_baseline_label"] = same_worker_baseline.get("label")
            validation["comparison_baseline_mode"] = "same_worker"

    decision_baseline = same_worker_baseline or baseline
    if decision_baseline:
        accepted, reason, decision = compare_against_baseline_goal(
            validation,
            decision_baseline,
            target=target,
            cfg=cfg,
        )
        validation.update(
            {
                "accepted": accepted,
                "accept_reject_reason": reason,
                "accept_reason": reason if accepted else "",
                "reject_reason": "" if accepted else reason,
                "decision": decision,
            }
        )
    else:
        validation.update(
            {
                "accepted": False,
                "accept_reject_reason": "baseline_not_found",
                "reject_reason": "baseline_not_found",
                "decision": {},
            }
        )

    append_jsonl(log_path, with_workload(cfg, validation))
    summary = {
        "event": "single_candidate_validation_summary",
        "workload": workload_name(cfg),
        "target": target,
        "label": label,
        "log_path": str(log_path),
        "baseline_log_path": str(baseline_log_path),
        "validation": summarize_validation(validation, target, include_params=True),
        "decision": validation.get("decision") or {},
        "accepted": bool(validation.get("accepted")),
        "accept_reject_reason": validation.get("accept_reject_reason"),
    }
    json_path = out_dir / f"{sanitize_label(label)}_summary.json"
    write_json(json_path, summary)

    lines = [
        f"# Single Candidate Validation: {label}",
        "",
        f"- Generated at: `{time.strftime('%Y-%m-%d %H:%M:%S')}`",
        f"- Target: `{target}`",
        f"- Log: `{log_path}`",
        f"- Baseline log: `{baseline_log_path}`",
        f"- Accepted: `{summary['accepted']}`",
        f"- Reason: `{summary['accept_reject_reason']}`",
        f"- Validation: {format_result_line(summary['validation'])}",
        f"- Params: `{json.dumps(params, sort_keys=True)}`",
        f"- Decision: `{json.dumps(summary['decision'], ensure_ascii=False, sort_keys=True)}`",
    ]
    md_path = out_dir / f"{sanitize_label(label)}_summary.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if args.repo_report:
        repo_report = resolve_path(args.repo_report)
        repo_report.parent.mkdir(parents=True, exist_ok=True)
        repo_report.write_text("\n".join(lines) + "\n", encoding="utf-8")

    append_jsonl(log_path, with_workload(cfg, summary))
    print(f"validate-params wrote {json_path}", flush=True)
    print(f"validate-params wrote {md_path}", flush=True)
    if args.repo_report:
        print(f"repo report wrote {resolve_path(args.repo_report)}", flush=True)
    return 0


def validate_params_parallel(args: argparse.Namespace) -> int:
    """Validate one fixed parameter set concurrently on every target worker."""
    cfg = load_config(args.config)
    target = str(args.target).strip().lower()
    if target not in set(stage_targets()):
        raise ValueError("--target must be cpu, memory, io, or none")

    if args.params_file:
        raw_params = read_json(resolve_path(args.params_file))
    elif args.params_json:
        raw_params = json.loads(args.params_json)
    else:
        raise ValueError("provide --params-json or --params-file")
    params = normalize_params(raw_params)
    missing = [name for name in COST_PARAMS if name not in params]
    if missing:
        raise ValueError(f"params missing: {', '.join(missing)}")

    workers = validation_workers(cfg, target)
    if len(workers) < 2:
        raise RuntimeError(f"parallel validation requires at least two workers for target={target}")

    out_dir = output_dir_from_args(cfg, args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = workload_log_suffix(cfg)
    log_path = out_dir / f"{target}_parallel_validation_{suffix}.jsonl"
    label = str(args.label or f"{target}_parallel_validation").strip()

    baseline_output_dir = resolve_path(args.baseline_output_dir) if args.baseline_output_dir else out_dir
    baseline_log_path = baseline_output_dir / f"{target}_binary_search_{suffix}.jsonl"
    if not baseline_log_path.exists():
        raise FileNotFoundError(str(baseline_log_path))
    baseline_rows = [
        row
        for row in load_jsonl(baseline_log_path)
        if row.get("event") == "baseline_worker_validation" and row.get("status") == "ok"
    ]
    baseline_by_worker = baseline_map_from_validations(baseline_rows)
    missing_baselines = [worker_identity(worker) for worker in workers if worker_identity(worker) not in baseline_by_worker]
    if missing_baselines:
        raise RuntimeError(f"same-worker baselines missing: {', '.join(missing_baselines)}")
    aggregate_baseline = aggregate_parallel_baseline_validation(baseline_rows)
    aggregate_baseline.update(
        {
            "event": "baseline_validation",
            "target": target,
            "label": "parallel_baseline_aggregate",
            "baseline_role": "aggregate_from_parallel_worker_baseline",
        }
    )

    policy = worker_baseline_policy(cfg, target, workers)
    requested_parallelism = safe_int(getattr(args, "parallelism", 0), 0)
    parallelism = max(1, min(len(workers), requested_parallelism or safe_int(policy.get("parallelism"), len(workers))))
    append_jsonl(
        log_path,
        with_workload(
            cfg,
            {
                "event": "parallel_candidate_validation_start",
                "target": target,
                "label": label,
                "params": params,
                "worker_count": len(workers),
                "parallelism": parallelism,
                "baseline_output_dir": str(baseline_output_dir),
            },
        ),
    )

    prepared_workers: List[Dict[str, Any]] = []
    restore_rows: List[Dict[str, Any]] = []
    restore_started = time.perf_counter()
    append_jsonl(
        log_path,
        with_workload(
            cfg,
            {
                "event": "parallel_worker_restore_start",
                "target": target,
                "phase": "parallel_fixed_candidate",
                "label": label,
                "worker_count": len(workers),
                "parallelism": 1,
            },
        ),
    )
    for slot, worker in enumerate(workers):
        worker_label = f"{label}_w{slot + 1:02d}_{sanitize_label(worker_identity(worker))}"
        try:
            row = restore_worker_from_hybench_template(cfg, worker, label=worker_label)
        except Exception as exc:
            row = {"worker": worker_identity(worker), "status": "failed", "error": str(exc)}
        row.update(
            {
                "event": "parallel_worker_restore",
                "target": target,
                "phase": "parallel_fixed_candidate",
                "label": label,
                "worker_slot": slot,
            }
        )
        restore_rows.append(row)
        append_jsonl(log_path, with_workload(cfg, row))
        if row.get("status") != "failed":
            prepared_workers.append(worker_with_prepared_restore(worker))
    failed_restores = [row for row in restore_rows if row.get("status") == "failed"]
    append_jsonl(
        log_path,
        with_workload(
            cfg,
            {
                "event": "parallel_worker_restore_summary",
                "target": target,
                "phase": "parallel_fixed_candidate",
                "label": label,
                "worker_count": len(workers),
                "ok_count": len([row for row in restore_rows if row.get("status") == "ok"]),
                "skipped_count": len([row for row in restore_rows if row.get("status") == "skipped"]),
                "failed_count": len(failed_restores),
                "elapsed_s": time.perf_counter() - restore_started,
            },
        ),
    )
    if failed_restores:
        raise RuntimeError(f"parallel fixed-candidate restore failed: {failed_restores[0].get('error')}")

    def run_worker(slot: int, worker: Dict[str, Any]) -> Dict[str, Any]:
        key = worker_identity(worker)
        worker_label = f"{label}_w{slot + 1:02d}_{sanitize_label(key)}"
        result = run_real_validation_safe(cfg, params, output_dir=out_dir, label=worker_label, worker=worker)
        result.update(
            {
                "event": "parallel_candidate_worker_validation",
                "target": target,
                "parallel_candidate_label": label,
                "worker_slot": slot,
                "worker_key": key,
            }
        )
        same_worker_baseline = baseline_by_worker[key]
        accepted, reason, decision = compare_against_baseline_goal(
            result,
            same_worker_baseline,
            target=target,
            cfg=cfg,
        )
        result.update(
            {
                "comparison_baseline_key": key,
                "comparison_baseline_label": same_worker_baseline.get("label"),
                "comparison_baseline_matched": True,
                "comparison_baseline_mode": "same_worker",
                "accepted": accepted,
                "accept_reject_reason": reason,
                "accept_reason": reason if accepted else "",
                "reject_reason": "" if accepted else reason,
                "decision": decision,
            }
        )
        return result

    validations: List[Dict[str, Any]] = []
    validation_started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=parallelism) as executor:
        future_map = {executor.submit(run_worker, slot, worker): slot for slot, worker in enumerate(prepared_workers)}
        for future in as_completed(future_map):
            slot = future_map[future]
            try:
                validation = future.result()
            except Exception as exc:
                worker = prepared_workers[slot]
                validation = failed_validation_result(
                    cfg,
                    params,
                    label=f"{label}_w{slot + 1:02d}_{sanitize_label(worker_identity(worker))}",
                    error=exc,
                    started_at=time.time(),
                    worker=worker,
                )
                validation.update(
                    {
                        "event": "parallel_candidate_worker_validation",
                        "target": target,
                        "parallel_candidate_label": label,
                        "worker_slot": slot,
                        "worker_key": worker_identity(worker),
                        "accepted": False,
                        "accept_reject_reason": "workload_failed",
                    }
                )
            validations.append(validation)
            append_jsonl(log_path, with_workload(cfg, validation))
    validations.sort(key=lambda item: safe_int(item.get("worker_slot"), 0))

    failed_validations = [row for row in validations if row.get("status") != "ok"]
    aggregate = aggregate_parallel_baseline_validation(validations)
    aggregate.update(
        {
            "event": "parallel_candidate_validation",
            "target": target,
            "label": label,
            "params": params,
            "worker_key": "parallel_candidate_aggregate",
            "parallel_candidate_policy": {
                "worker_count": len(workers),
                "parallelism": parallelism,
                "same_params_on_all_workers": True,
                "same_worker_baseline_comparison": True,
                "worker_accepted_count": len([row for row in validations if row.get("accepted")]),
                "worker_failed_count": len(failed_validations),
            },
        }
    )
    if failed_validations:
        accepted = False
        reason = "parallel_worker_validation_failed"
        decision = {"status_ok": False, "failed_workers": [row.get("worker_key") for row in failed_validations]}
    else:
        accepted, reason, decision = compare_against_baseline_goal(
            aggregate,
            aggregate_baseline,
            target=target,
            cfg=cfg,
        )
    aggregate.update(
        {
            "accepted": accepted,
            "accept_reject_reason": reason,
            "accept_reason": reason if accepted else "",
            "reject_reason": "" if accepted else reason,
            "decision": decision,
        }
    )
    append_jsonl(log_path, with_workload(cfg, aggregate))

    summary = {
        "event": "parallel_candidate_validation_summary",
        "workload": workload_name(cfg),
        "target": target,
        "label": label,
        "log_path": str(log_path),
        "baseline_log_path": str(baseline_log_path),
        "worker_count": len(workers),
        "parallelism": parallelism,
        "elapsed_s": time.perf_counter() - validation_started,
        "worker_ok_count": len(validations) - len(failed_validations),
        "worker_failed_count": len(failed_validations),
        "worker_accepted_count": len([row for row in validations if row.get("accepted")]),
        "aggregate_validation": summarize_validation(aggregate, target, include_params=True),
        "decision": decision,
        "accepted": bool(accepted),
        "accept_reject_reason": reason,
    }
    json_path = out_dir / f"{sanitize_label(label)}_parallel_summary.json"
    write_json(json_path, summary)
    lines = [
        f"# Parallel Candidate Validation: {label}",
        "",
        f"- Generated at: `{time.strftime('%Y-%m-%d %H:%M:%S')}`",
        f"- Target: `{target}`",
        f"- Log: `{log_path}`",
        f"- Baseline log: `{baseline_log_path}`",
        f"- Workers: `{summary['worker_ok_count']}/{summary['worker_count']}` ok, "
        f"`{summary['worker_accepted_count']}/{summary['worker_count']}` individually accepted",
        f"- Aggregate accepted: `{summary['accepted']}`",
        f"- Reason: `{reason}`",
        f"- Aggregate: {format_result_line(summary['aggregate_validation'])}",
        f"- Params: `{json.dumps(params, sort_keys=True)}`",
        f"- Decision: `{json.dumps(decision, ensure_ascii=False, sort_keys=True)}`",
    ]
    md_path = out_dir / f"{sanitize_label(label)}_parallel_summary.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if args.repo_report:
        repo_report = resolve_path(args.repo_report)
        repo_report.parent.mkdir(parents=True, exist_ok=True)
        repo_report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    append_jsonl(log_path, with_workload(cfg, summary))
    print(f"validate-params-parallel wrote {json_path}", flush=True)
    print(f"validate-params-parallel wrote {md_path}", flush=True)
    if args.repo_report:
        print(f"repo report wrote {resolve_path(args.repo_report)}", flush=True)
    return 0


def report_stop_reason(
    target: str,
    summary: Dict[str, Any],
    rounds: List[Dict[str, Any]],
    stop_reasons: Dict[str, str],
    cfg: Dict[str, Any],
) -> str:
    if stop_reasons.get(target):
        return stop_reasons[target]
    if summary.get("stop_reason"):
        return str(summary.get("stop_reason"))
    for row in rounds:
        if row.get("stop_reason"):
            return str(row.get("stop_reason"))
    tuning_cfg = cfg.get("binary_plan_tuning") if isinstance(cfg.get("binary_plan_tuning"), dict) else {}
    search_cfg = tuning_cfg.get("binary_search") if isinstance(tuning_cfg.get("binary_search"), dict) else {}
    max_rounds = safe_int(search_cfg.get("max_rounds"), 6)
    if rounds and len(rounds) >= max_rounds:
        return "max_rounds_reached"
    if not rounds and not summary:
        return "not_run"
    return "unknown"


def report_degradation(rounds: List[Dict[str, Any]]) -> str:
    applied = [row.get("degradation") or {} for row in rounds if (row.get("degradation") or {}).get("applied")]
    if not applied:
        return ""
    first = applied[0]
    raw = first.get("original_candidate_count")
    validated = first.get("validated_candidate_count")
    strategy = first.get("strategy") or "unknown"
    if len(applied) == 1:
        return f"degraded 1 round from {raw} to {validated} candidates, strategy=`{strategy}`"
    return f"degraded {len(applied)} rounds from {raw} to {validated} candidates, strategy=`{strategy}`"


def format_float(value: Any) -> str:
    number = safe_float(value, math.nan)
    if not math.isfinite(number):
        return "-"
    return f"{number:.4f}"


def format_result_line(result: Optional[Dict[str, Any]]) -> str:
    if not isinstance(result, dict) or not result:
        return "`(none)`"
    resources = result.get("resources") or {}
    hybench = result.get("hybench") or {}
    run_cfg = result.get("hybench_run_config") or (hybench.get("run_config") if isinstance(hybench, dict) else {}) or {}
    samples = result.get("resource_samples") or {}
    suffix = ""
    if hybench:
        suffix = (
            f", xp_qps={format_float(hybench.get('xp_qps'))}, "
            f"xp_tps={format_float(hybench.get('xp_tps'))}"
        )
    if run_cfg:
        suffix += (
            f", xpRunMins={run_cfg.get('xpRunMins')}, "
            f"xtpclient={run_cfg.get('xtpclient')}, "
            f"xapclient={run_cfg.get('xapclient')}"
        )
    if samples:
        suffix += (
            f", samples(cpu/mem/io)="
            f"{samples.get('cpu_cores')}/{samples.get('memory_bytes')}/{samples.get('io_bps')}"
        )
    return (
        f"`{result.get('label')}` "
        f"cpu={format_float(resources.get('cpu'))}, "
        f"memory_gb={format_float(resources.get('memory'))}, "
        f"io_mb_s={format_float(resources.get('io'))}, "
        f"db_qps={format_float(result.get('db_qps'))}, "
        f"p95_ms={format_float(result.get('p95_ms'))}, "
        f"p99_ms={format_float(result.get('p99_ms'))}"
        f"{suffix}"
    )


def validate_config(args: argparse.Namespace) -> int:
    """Validate static tuning config without starting PostgreSQL or HyBench work."""
    cfg = load_config(args.config)
    target = str(args.target).strip().lower()
    if target not in set(stage_targets()):
        raise ValueError("--target must be cpu, memory, io, or none")
    out_dir = output_dir_from_args(cfg, args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = workload_log_suffix(cfg)
    bpt = cfg.get("binary_plan_tuning") if isinstance(cfg.get("binary_plan_tuning"), dict) else {}
    search_cfg = bpt.get("binary_search") if isinstance(bpt.get("binary_search"), dict) else {}
    validation_cfg = bpt.get("validation") if isinstance(bpt.get("validation"), dict) else {}
    docker_cfg = validation_cfg.get("docker_runtime") if isinstance(validation_cfg.get("docker_runtime"), dict) else {}
    workers_cfg = validation_cfg.get("docker_workers") if isinstance(validation_cfg.get("docker_workers"), dict) else {}
    hybench_base_cfg = validation_cfg.get("hybench_xp") if isinstance(validation_cfg.get("hybench_xp"), dict) else {}

    label = f"{target}_config_validation"
    hybench_cfg = hybench_xp_config_for_label(hybench_base_cfg, label)
    hybench_dir = Path(str(hybench_cfg.get("hybench_dir") or "/home/anonymous/HyBench-2024-master"))
    statement_file: Optional[Path] = None
    statement_meta: Dict[str, Any] = {}
    errors: List[str] = []
    warnings: List[str] = []
    operator_rule_gate_static: Dict[str, Any] = {}
    try:
        operator_rule_cfg = (
            search_cfg.get("operator_resource_reject_rules")
            if isinstance(search_cfg.get("operator_resource_reject_rules"), dict)
            else {}
        )
        workload_cfg = bpt.get("workload") if isinstance(bpt.get("workload"), dict) else {}
        plan_sql_value = workload_cfg.get("plan_sql_file")
        operator_rule_gate_static = summarize_rule_gate(
            load_rule_gate(
                operator_rule_cfg,
                workload=workload_name(cfg),
                target=target,
                plan_sql_path=resolve_path(plan_sql_value) if plan_sql_value else None,
            )
        )
    except Exception as exc:
        operator_rule_gate_static = {
            "enabled": True,
            "active": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
        errors.append(f"operator_resource_rule_gate_invalid: {type(exc).__name__}: {exc}")
    try:
        statement_file, statement_meta = hybench_statement_file_for_config(
            hybench_cfg,
            hybench_dir=hybench_dir,
            output_dir=out_dir / "config_validation",
            label=label,
        )
    except Exception as exc:
        errors.append(f"statement_profile_invalid: {exc}")
    try:
        setup_items = hybench_setup_sql_items(hybench_cfg)
    except Exception as exc:
        setup_items = []
        errors.append(f"setup_sql_invalid: {exc}")

    workers = validation_workers(cfg, target)
    baseline_policy = worker_baseline_policy(cfg, target, workers)
    active = [name for name in load_active_params(out_dir, getattr(args, "active_params", "")) if name in COST_PARAMS]
    policy = acceptance_policy_for_target(cfg, target)
    io_mode = str(docker_cfg.get("io_mode", "cgroup"))
    checkpoint_before = to_bool(docker_cfg.get("checkpoint_before_validation"), is_container_cgroup_io_mode(io_mode))
    checkpoint_after = to_bool(docker_cfg.get("checkpoint_after_validation"), is_container_cgroup_io_mode(io_mode))
    evict_data_file_cache = to_bool(
        docker_cfg.get("evict_data_file_cache_before_validation"),
        is_container_cgroup_io_mode(io_mode),
    )
    vm_replay_cfg = validation_cfg.get("vm_replay") if isinstance(validation_cfg.get("vm_replay"), dict) else {}
    vm_replay_enabled = to_bool(vm_replay_cfg.get("enabled"), False)
    vm_replay_static: Dict[str, Any] = {"enabled": vm_replay_enabled}
    if vm_replay_enabled:
        try:
            shell_dir = ROOT / "shell"
            if str(shell_dir) not in sys.path:
                sys.path.insert(0, str(shell_dir))
            from bonect_parallel_vm_replay import effective_worker_vm_resource  # type: ignore
            from bonect_real_validate_candidates import (  # type: ignore
                load_worker_config,
                scenario_context,
            )

            raw_worker_path = (vm_replay_cfg.get("worker_configs") or {}).get(target)
            if not raw_worker_path:
                raise ValueError(f"vm_replay worker config is missing for target={target}")
            worker_path = resolve_path(raw_worker_path)
            worker = load_worker_config(str(worker_path))
            context = scenario_context(target, worker)
            benchmark = (context.get("config") or {}).get("benchmark") or {}
            workload_cmd = str(benchmark.get("workload_cmd") or "")
            props_match = re.search(r"(?:^|\s)-c\s+(?P<path>[^\s]+)", workload_cmd)
            statement_match = re.search(r"(?:^|\s)-f\s+(?P<path>[^\s]+)", workload_cmd)
            if props_match is None or statement_match is None:
                raise ValueError(f"vm_replay workload command lacks -c/-f inputs: {workload_cmd}")
            source_props = resolve_path(props_match.group("path"))
            source_statement = resolve_path(statement_match.group("path"))
            if not source_props.is_file():
                raise FileNotFoundError(f"vm_replay source props missing: {source_props}")
            if not source_statement.is_file():
                raise FileNotFoundError(f"vm_replay source statement missing: {source_statement}")
            effective_resource = effective_worker_vm_resource(worker, target)
            vm_replay_static.update(
                {
                    "worker_config": str(worker_path),
                    "scenario_config": str(context.get("config_path") or ""),
                    "source_props": str(source_props),
                    "source_statement": str(source_statement),
                    "workload_cmd": workload_cmd,
                    "pg_host": (context.get("pg") or {}).get("host"),
                    "vm_name": effective_resource.get("vm_name") or worker.get("vm_name"),
                    "runtime_import_closure_ok": True,
                }
            )
        except Exception as exc:
            vm_replay_static.update(
                {
                    "runtime_import_closure_ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            errors.append(f"vm_replay_static_validation_failed: {type(exc).__name__}: {exc}")

    if not active:
        errors.append("active_params_empty")
    if target == "none":
        configured_parallelism = safe_int(workers_cfg.get("none_parallelism"), 0)
        if not vm_replay_enabled:
            if configured_parallelism < 8:
                errors.append(f"none_parallelism_less_than_8: {configured_parallelism}")
            if len(workers) < configured_parallelism:
                errors.append(
                    f"none_worker_count_less_than_parallelism: workers={len(workers)} "
                    f"parallelism={configured_parallelism}"
                )
        if not is_container_cgroup_io_mode(io_mode):
            errors.append(f"none_io_mode_not_physical_cgroup: {io_mode}")
        if not checkpoint_before:
            errors.append("none_checkpoint_before_validation_disabled")
        if not checkpoint_after:
            errors.append("none_checkpoint_after_validation_disabled")
        if not evict_data_file_cache:
            errors.append("none_evict_data_file_cache_before_validation_disabled")
        if safe_int(hybench_cfg.get("xtpclient"), 0) != 1 or safe_int(hybench_cfg.get("xapclient"), 0) != 1:
            warnings.append(
                f"none_hybench_clients_not_anchor: xtp={hybench_cfg.get('xtpclient')} xap={hybench_cfg.get('xapclient')}"
            )
        if not setup_items:
            errors.append("none_setup_sql_missing")
        if not statement_meta.get("generated"):
            errors.append("none_statement_profile_not_generated")
        if "AP-6" not in set(statement_meta.get("statement_sql_file_sections") or []):
            errors.append("none_ap6_statement_sql_file_override_missing")
        capacity = policy.get("resource_capacity") if isinstance(policy.get("resource_capacity"), dict) else {}
        if not vm_replay_enabled:
            if safe_float(capacity.get("cpu_cores"), 0.0) <= 0.0:
                errors.append("none_resource_capacity_cpu_cores_missing")
            if safe_float(capacity.get("memory_gib"), 0.0) <= 0.0:
                errors.append("none_resource_capacity_memory_gib_missing")

    result = {
        "event": "binary_plan_config_validation",
        "workload": workload_name(cfg),
        "target": target,
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "config": str(resolve_path(args.config)),
        "output_dir": str(out_dir),
        "active_params": active,
        "full_round_search_points": 2 ** len(active) if active else 0,
        "workers": [
            {
                "name": worker.get("name"),
                "container": worker.get("container"),
                "host": worker.get("host"),
                "port": worker.get("port"),
                "cpuset_cpus": worker.get("cpuset_cpus"),
                "disk_root": worker.get("disk_root"),
            }
            for worker in workers
        ],
        "worker_count": len(workers),
        "worker_baseline_policy": baseline_policy,
        "vm_replay": vm_replay_static,
        "docker_runtime": {
            "io_mode": io_mode,
            "checkpoint_before_validation": checkpoint_before,
            "checkpoint_after_validation": checkpoint_after,
            "evict_data_file_cache_before_validation": evict_data_file_cache,
            "evict_data_file_cache_max_files": docker_cfg.get("evict_data_file_cache_max_files"),
            "evict_data_file_cache_max_bytes": docker_cfg.get("evict_data_file_cache_max_bytes"),
            "memory_mode": docker_cfg.get("memory_mode"),
            "min_stable_samples": docker_cfg.get("min_stable_samples"),
        },
        "hybench_xp": {
            "enabled": to_bool(hybench_base_cfg.get("enabled"), False),
            "xtpclient": hybench_cfg.get("xtpclient"),
            "xapclient": hybench_cfg.get("xapclient"),
            "ap_query_ids": hybench_cfg.get("ap_query_ids"),
            "ap_query_weights": hybench_cfg.get("ap_query_weights"),
            "ap_query_sequence": hybench_cfg.get("ap_query_sequence"),
            "setup_sql_files": hybench_cfg.get("setup_sql_files"),
            "setup_sql_count": len(setup_items),
            "statement_file": str(statement_file) if statement_file else "",
            "statement_meta": statement_meta,
        },
        "resource_capacity": policy.get("resource_capacity") if isinstance(policy.get("resource_capacity"), dict) else {},
        "operator_resource_rule_gate": operator_rule_gate_static,
    }
    out_path = out_dir / f"{target}_config_validation_{suffix}.json"
    write_json(out_path, result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
    print(f"config validation wrote {out_path}", flush=True)
    if errors and to_bool(getattr(args, "strict", False), False):
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Binary plan/resource PostgreSQL cost parameter tuner.")
    parser.add_argument("--config", default="config/binary_plan_tuning_example.json")
    parser.add_argument("--output-dir", default="")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_screen = sub.add_parser("endpoint-screening", help="run min/max endpoint plan and feature screening")
    p_screen.set_defaults(func=endpoint_screening)

    p_stage = sub.add_parser("binary-search", help="run one target stage")
    p_stage.add_argument("--target", required=True, choices=list(stage_targets()))
    p_stage.add_argument("--active-params", default="")
    p_stage.add_argument("--max-rounds", type=int, default=0)
    p_stage.add_argument("--max-candidates", type=int, default=0)
    p_stage.add_argument("--resume", action="store_true", help="append to an existing stage log and resume from latest ranges")
    p_stage.add_argument("--baseline-only", action="store_true", help="run stage baseline and hard gates, then exit")
    p_stage.set_defaults(func=binary_search)

    p_report = sub.add_parser("report", help="generate Markdown report from logs")
    p_report.add_argument("--repo-report", default="")
    p_report.set_defaults(func=generate_report)

    p_handoff = sub.add_parser("handoff-snapshots", help="write missing per-round handoff snapshots from stage logs")
    p_handoff.add_argument("--target", default="all", choices=["all", *stage_targets()])
    p_handoff.add_argument("--active-params", default="")
    p_handoff.add_argument("--force", action="store_true")
    p_handoff.set_defaults(func=write_handoff_snapshots_from_logs)

    p_calibrate = sub.add_parser("calibrate-error-bounds", help="fit current-task replay error bounds for pruning")
    p_calibrate.add_argument("--target", default="all", choices=["all", *stage_targets()])
    p_calibrate.add_argument("--quantile", type=float, default=0.0)
    p_calibrate.add_argument("--safety-factor", type=float, default=0.0)
    p_calibrate.add_argument("--repo-report", default="")
    p_calibrate.set_defaults(func=calibrate_error_bounds)

    p_finalize = sub.add_parser("finalize-stage", help="write a stage summary from an existing stage log")
    p_finalize.add_argument("--target", required=True, choices=list(stage_targets()))
    p_finalize.add_argument("--reason", default="manual_finalize_from_log")
    p_finalize.add_argument("--active-params", default="")
    p_finalize.add_argument("--force-append", action="store_true")
    p_finalize.set_defaults(func=finalize_stage_from_log)

    p_backfill = sub.add_parser("backfill-round-audit", help="append per-round search-point audit events from logs")
    p_backfill.add_argument("--target", default="all", choices=["all", *stage_targets()])
    p_backfill.add_argument("--active-params", default="")
    p_backfill.add_argument("--force", action="store_true")
    p_backfill.set_defaults(func=backfill_round_audit_from_log)

    p_replay = sub.add_parser("replay-best", help="select a baseline-relative best candidate from an existing stage log")
    p_replay.add_argument("--source-stage", required=True, choices=list(stage_targets()))
    p_replay.add_argument("--target", required=True, choices=["cpu", "memory", "io", "none", "no_bottleneck", "no-bottleneck"])
    p_replay.add_argument("--repo-report", default="")
    p_replay.set_defaults(func=replay_best_from_log)

    p_validate_params = sub.add_parser("validate-params", help="run one real validation for explicit cost params")
    p_validate_params.add_argument("--target", required=True, choices=list(stage_targets()))
    p_validate_params.add_argument("--params-json", default="")
    p_validate_params.add_argument("--params-file", default="")
    p_validate_params.add_argument("--label", default="")
    p_validate_params.add_argument("--worker-name", default="")
    p_validate_params.add_argument("--baseline-output-dir", default="")
    p_validate_params.add_argument("--repo-report", default="")
    p_validate_params.set_defaults(func=validate_params)

    p_validate_params_parallel = sub.add_parser(
        "validate-params-parallel",
        help="run one fixed cost parameter set concurrently on every target worker",
    )
    p_validate_params_parallel.add_argument("--target", required=True, choices=list(stage_targets()))
    p_validate_params_parallel.add_argument("--params-json", default="")
    p_validate_params_parallel.add_argument("--params-file", default="")
    p_validate_params_parallel.add_argument("--label", default="")
    p_validate_params_parallel.add_argument("--parallelism", type=int, default=0)
    p_validate_params_parallel.add_argument("--baseline-output-dir", default="")
    p_validate_params_parallel.add_argument("--repo-report", default="")
    p_validate_params_parallel.set_defaults(func=validate_params_parallel)

    p_validate = sub.add_parser("validate-config", help="validate static config without running PostgreSQL or HyBench")
    p_validate.add_argument("--target", required=True, choices=list(stage_targets()))
    p_validate.add_argument("--active-params", default="")
    p_validate.add_argument("--strict", action="store_true")
    p_validate.set_defaults(func=validate_config)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    install_early_external_stop_handler()
    sys.exit(main())

#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


INITIAL_SCORE_RE = re.compile(r"Initial score:\s*([0-9.+-eE]+)")
LINE_RE = re.compile(r"^(?P<label>.+?)\s*:\s*max rt\s*:\s*(?P<max>[0-9.]+)\s*\|\s*min rt\s*:\s*(?P<min>[0-9.]+)\s*\|\s*avg rt\s*:\s*(?P<avg>[0-9.]+)(?P<rest>.*)$")
RT_RE = re.compile(r"(90|95|99)% rt\s*:\s*([0-9.]+)")
AP_QPS_RE = re.compile(r"(?<!XP-)QPS is\s+(?P<qps>[0-9.]+)")
TP_TPS_RE = re.compile(r"(?<!XP-)TPS is\s+(?P<tps>[0-9.]+)")
XP_QPS_RE = re.compile(r"XP-QPS is\s+(?P<qps>[0-9.]+)")
XP_TPS_RE = re.compile(r"XP-TPS is\s+(?P<tps>[0-9.]+)")
AP_PROGRESS_QPS_RE = re.compile(r"Current\s+\d+/\d+\s+time AP QPS is\s+(?P<qps>[0-9.]+)")
TP_PROGRESS_TPS_RE = re.compile(r"Current\s+\d+/\d+\s+time TP TPS is\s+(?P<tps>[0-9.]+)")
TP_CONCURRENCY_RE = re.compile(r"TP Concurrency is\s+(?P<value>\d+)")
AP_CONCURRENCY_RE = re.compile(r"AP Concurrency is\s+(?P<value>\d+)")
TEST_SUMMARY_RE = re.compile(r"Test Summary")
THANK_YOU_RE = re.compile(r"Thank you!")
RESOURCE_ESTIMATION_RE = re.compile(r"Running resource estimation for '(?P<label>[^']+)'")
TEST_START_RE = re.compile(r"Test starts at\s+(?P<value>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
TEST_END_RE = re.compile(r"Test ends at\s+(?P<value>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
UPDATE_RE = re.compile(r"\[Update\]\s+(?P<param>[a-z_]+)\s+\|\s+(?P<before>[0-9.+-eE]+)\s*->")
PARAM_KEYS = [
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
]
BENCHMARK_KEYS = ["ap_qps", "tp_tps", "xp_qps", "xp_tps", "freshness_ms", "htap_score"]
LATENCY_GROUPS = ["tp", "xp_iq", "xp_at", "all"]


@dataclass
class IterationRow:
    iteration: int
    phase: str
    noisy: Optional[bool]
    retry_depth: Optional[int]
    retry_reasons: Optional[str]
    accepted: Optional[bool]
    score: Optional[float]
    candidate_score: Optional[float]
    best_score: Optional[float]
    ap_qps: Optional[float]
    tp_tps: Optional[float]
    xp_qps: Optional[float]
    xp_tps: Optional[float]
    db_qps: Optional[float]
    db_xact_tps: Optional[float]
    db_commit_tps: Optional[float]
    db_rollback_tps: Optional[float]
    db_active_sessions: Optional[float]
    latency_p90: Optional[float]
    latency_p95: Optional[float]
    latency_p99: Optional[float]
    cpu: Optional[float]
    memory: Optional[float]
    io: Optional[float]
    io_read: Optional[float]
    io_write: Optional[float]
    iops_read: Optional[float]
    iops_write: Optional[float]
    seq_page_cost: Optional[float]
    random_page_cost: Optional[float]
    cpu_tuple_cost: Optional[float]
    cpu_index_tuple_cost: Optional[float]
    cpu_operator_cost: Optional[float]
    parallel_setup_cost: Optional[float]
    parallel_tuple_cost: Optional[float]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="HyBench")
    parser.add_argument("log_file", help=" JSONL  history/optimization_history_2026-03-23_151623.jsonl")
    parser.add_argument("--run-log", default="", help=" run_*.log  baseline/iteration  QPS ")
    parser.add_argument("--query-log-csv", default="", help="BenchBase/BenchBench  CSV P90/P95/P99")
    parser.add_argument("--db-monitor-csv", default="", help="monitor_db_view_tps_qps.py --csv  CSV")
    parser.add_argument("--output-csv", default="", help=" CSV ")
    parser.add_argument("--output-json", default="", help=" JSON ")
    parser.add_argument("--limit", type=int, default=0, help=" N 0 ")
    return parser.parse_args()


def to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_local_timestamp(value: Any) -> Optional[float]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return time.mktime(time.strptime(text, "%Y-%m-%d %H:%M:%S"))
    except ValueError:
        return None


def percentile(values: Sequence[float], p: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * p
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def classify_workload_label(label: str) -> Optional[str]:
    lowered = label.lower()
    if lowered.startswith("tp transaction"):
        return "tp"
    if lowered.startswith("interative query") or lowered.startswith("interactive query"):
        return "xp_iq"
    if lowered.startswith("analytical transaction"):
        return "xp_at"
    return None


def classify_querylog_name(name: str) -> Optional[str]:
    lowered = name.lower()
    if lowered.startswith("tp"):
        return "tp"
    if lowered.startswith("at"):
        return "xp_at"
    if lowered.startswith("iq") or lowered.startswith("q"):
        return "xp_iq"
    if "transaction" in lowered and "analytical" not in lowered:
        return "tp"
    if "analytical" in lowered:
        return "xp_at"
    if "query" in lowered:
        return "xp_iq"
    return None


def load_initial_score(log_path: Path) -> Optional[float]:
    with log_path.open("r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("event") != "log":
                continue
            message = str(obj.get("message", ""))
            match = INITIAL_SCORE_RE.search(message)
            if match:
                return to_float(match.group(1))
    return None


def load_final_summary(log_path: Path) -> Dict[str, Any]:
    summary: Dict[str, Any] = {}
    with log_path.open("r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("event") == "final_summary":
                summary = obj
    return summary


def iter_iteration_records(log_path: Path) -> Iterable[Dict[str, Any]]:
    with log_path.open("r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj.get("iteration"), int):
                continue
            if "score" not in obj:
                continue
            yield obj


def iter_noise_guard_retry_records(log_path: Path) -> Iterable[Dict[str, Any]]:
    with log_path.open("r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("event") == "noise_guard_retry":
                yield obj


def load_baseline_top_sql(log_path: Path) -> Dict[str, Any]:
    with log_path.open("r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("event") == "baseline_top_sql":
                return obj
    return {}


def load_db_monitor_summaries_from_jsonl(log_path: Path) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
    out: Dict[str, Dict[str, Any]] = {}
    all_summaries: List[Dict[str, Any]] = []
    with log_path.open("r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("event") != "db_view_monitor_summary":
                continue
            all_summaries.append(obj)
            label = obj.get("label")
            if isinstance(label, str) and label:
                out[label] = obj
    return out, all_summaries


def db_metrics_from_summary(summary: Optional[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    return {
        "db_qps": to_float((summary or {}).get("db_qps")),
        "db_xact_tps": to_float((summary or {}).get("db_xact_tps")),
        "db_commit_tps": to_float((summary or {}).get("db_commit_tps")),
        "db_rollback_tps": to_float((summary or {}).get("db_rollback_tps")),
        "db_active_sessions": to_float((summary or {}).get("db_active_sessions")),
    }


def stat_avg(stats: Dict[str, Any], key: str) -> Optional[float]:
    value = stats.get(key)
    if isinstance(value, dict):
        return to_float(value.get("avg"))
    return None


def normalize_runtime_resource(avg: Any, stats: Any) -> Dict[str, Optional[float]]:
    """Convert runtime monitor bytes into the CSV unit: memory=GiB, IO=MiB/s."""
    avg_dict = avg if isinstance(avg, dict) else {}
    stats_dict = stats if isinstance(stats, dict) else {}
    memory_bytes = to_float(avg_dict.get("memory"))
    io_bytes = to_float(avg_dict.get("io"))
    io_read_bytes = stat_avg(stats_dict, "io_read")
    io_write_bytes = stat_avg(stats_dict, "io_write")
    return {
        "cpu": to_float(avg_dict.get("cpu")),
        "memory": (memory_bytes / (1024 ** 3)) if memory_bytes is not None else None,
        "io": (io_bytes / (1024 ** 2)) if io_bytes is not None else None,
        "io_read": (io_read_bytes / (1024 ** 2)) if io_read_bytes is not None else None,
        "io_write": (io_write_bytes / (1024 ** 2)) if io_write_bytes is not None else None,
        "iops_read": stat_avg(stats_dict, "iops_read"),
        "iops_write": stat_avg(stats_dict, "iops_write"),
    }


def infer_initial_params(iteration_records: List[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    """Infer baseline params from first iteration candidate params and update logs."""
    if not iteration_records:
        return {}
    first = min(iteration_records, key=lambda item: int(item.get("iteration") or 0))
    params = {
        key: to_float((first.get("candidate_params") or first.get("params") or {}).get(key))
        for key in PARAM_KEYS
    }
    for line in first.get("logs") or []:
        match = UPDATE_RE.search(str(line))
        if not match:
            continue
        param = match.group("param")
        if param in params:
            params[param] = to_float(match.group("before"))
    return params


def find_db_monitor_summary_by_window(
    summaries: List[Dict[str, Any]],
    start_ts: Optional[float],
    end_ts: Optional[float],
) -> Optional[Dict[str, Any]]:
    if start_ts is None or end_ts is None or end_ts <= start_ts:
        return None
    best_summary: Optional[Dict[str, Any]] = None
    best_overlap = 0.0
    for summary in summaries:
        window = summary.get("window")
        if not isinstance(window, list) or len(window) != 2:
            continue
        summary_start = to_float(window[0])
        summary_end = to_float(window[1])
        if summary_start is None or summary_end is None:
            continue
        overlap = max(0.0, min(end_ts, summary_end) - max(start_ts, summary_start))
        if overlap > best_overlap:
            best_overlap = overlap
            best_summary = summary
    return best_summary


def summarize_workload_output(workload_output: Any) -> Dict[str, Optional[float]]:
    grouped: Dict[str, Dict[str, List[float]]] = {
        name: {"90": [], "95": [], "99": []} for name in LATENCY_GROUPS
    }
    if isinstance(workload_output, str):
        for raw_line in workload_output.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            match = LINE_RE.match(line)
            if not match:
                continue
            label = match.group("label").strip()
            group = classify_workload_label(label)
            if not group:
                # Some HyBench outputs only expose AP Query lines instead of TP/XP labels.
                # Those lines should still contribute to the overall latency percentiles.
                if not label.lower().startswith("ap query"):
                    continue
            for percentile_name, value in RT_RE.findall(line):
                if percentile_name not in {"90", "95", "99"}:
                    continue
                try:
                    number = float(value)
                except ValueError:
                    continue
                grouped["all"][percentile_name].append(number)
                if group:
                    grouped[group][percentile_name].append(number)

    out: Dict[str, Optional[float]] = {}
    for group in LATENCY_GROUPS:
        for percentile_name in ["90", "95", "99"]:
            values = grouped[group][percentile_name]
            out[f"{group}_p{percentile_name}_avg"] = (sum(values) / len(values)) if values else None
            out[f"{group}_p{percentile_name}_max"] = max(values) if values else None
    return out


def extract_benchmark_metrics(workload_output: Any) -> Dict[str, Optional[float]]:
    metrics = {
        "ap_qps": None,
        "tp_tps": None,
        "xp_qps": None,
        "xp_tps": None,
    }
    if not isinstance(workload_output, str):
        return metrics

    xp_qps_match = XP_QPS_RE.search(workload_output)
    if xp_qps_match:
        metrics["xp_qps"] = to_float(xp_qps_match.group("qps"))

    xp_tps_match = XP_TPS_RE.search(workload_output)
    if xp_tps_match:
        metrics["xp_tps"] = to_float(xp_tps_match.group("tps"))

    ap_concurrency = None
    tp_concurrency = None
    ap_match = AP_CONCURRENCY_RE.search(workload_output)
    tp_match = TP_CONCURRENCY_RE.search(workload_output)
    if ap_match:
        ap_concurrency = int(ap_match.group("value"))
    if tp_match:
        tp_concurrency = int(tp_match.group("value"))

    summary_match = AP_QPS_RE.search(workload_output)
    if summary_match:
        qps_value = to_float(summary_match.group("qps"))
        if qps_value is not None:
            if ap_concurrency and ap_concurrency > 0:
                metrics["ap_qps"] = qps_value
            else:
                metrics["xp_qps"] = qps_value

    summary_tps_match = TP_TPS_RE.search(workload_output)
    if summary_tps_match:
        tps_value = to_float(summary_tps_match.group("tps"))
        if tps_value is not None:
            if tp_concurrency and tp_concurrency > 0:
                metrics["tp_tps"] = tps_value
            else:
                metrics["xp_tps"] = tps_value

    progress_values: List[float] = []
    for match in AP_PROGRESS_QPS_RE.finditer(workload_output):
        value = to_float(match.group("qps"))
        if value is not None:
            progress_values.append(value)
    if progress_values:
        metrics["ap_qps"] = progress_values[-1]

    tp_progress_values: List[float] = []
    for match in TP_PROGRESS_TPS_RE.finditer(workload_output):
        value = to_float(match.group("tps"))
        if value is not None:
            tp_progress_values.append(value)
    if tp_progress_values:
        metrics["tp_tps"] = tp_progress_values[-1]
    return metrics


def merge_benchmark_metrics(
    base: Dict[str, Optional[float]],
    extra: Any,
) -> Dict[str, Optional[float]]:
    """Fill missing benchmark metrics from a parsed benchmark dictionary."""
    out = dict(base)
    if not isinstance(extra, dict):
        return out
    key_aliases = {
        "ap_qps": ["ap_qps"],
        "tp_tps": ["tp_tps"],
        "xp_qps": ["xp_qps", "qps"],
        "xp_tps": ["xp_tps", "tps"],
    }
    for target_key, aliases in key_aliases.items():
        if out.get(target_key) is not None:
            continue
        for alias in aliases:
            value = to_float(extra.get(alias))
            if value is not None:
                out[target_key] = value
                break
    return out


def merge_latency_metrics(
    base: Dict[str, Optional[float]],
    extra: Any,
) -> Dict[str, Optional[float]]:
    """Fill missing latency metrics from parsed HyBench latency dictionaries."""
    out = dict(base)
    if not isinstance(extra, dict):
        return out
    aliases = {
        "all_p90_avg": ["all_p90_avg", "latency_p90", "p90"],
        "all_p95_avg": ["all_p95_avg", "latency_p95", "p95"],
        "all_p99_avg": ["all_p99_avg", "latency_p99", "p99"],
    }
    for target_key, source_keys in aliases.items():
        if out.get(target_key) is not None:
            continue
        for source_key in source_keys:
            value = to_float(extra.get(source_key))
            if value is not None:
                out[target_key] = value
                break
    return out


def infer_run_log_path(log_path: Path) -> Optional[Path]:
    match = re.match(r".*_(\d{4})-(\d{2})-(\d{2})_(\d{6})\.jsonl$", log_path.name)
    if not match:
        return None
    yyyy, mm, dd, hhmmss = match.groups()
    candidate = log_path.with_name(f"run_{yyyy}{mm}{dd}_{hhmmss}.log")
    return candidate if candidate.exists() else None


def load_run_log_summaries(run_log_path: Optional[Path]) -> List[Dict[str, Any]]:
    if not run_log_path or not run_log_path.exists():
        return []

    summaries: List[Dict[str, Any]] = []
    collecting = False
    current_lines: List[str] = []
    pending_label: Optional[str] = None
    current_label: Optional[str] = None
    with run_log_path.open("r", encoding="utf-8", errors="ignore") as fp:
        for raw_line in fp:
            line = raw_line.rstrip("\n")
            label_match = RESOURCE_ESTIMATION_RE.search(line)
            if label_match:
                pending_label = label_match.group("label")
            if TEST_SUMMARY_RE.search(line):
                collecting = True
                current_lines = [line]
                current_label = pending_label or ("baseline" if not summaries else None)
                pending_label = None
                continue
            if not collecting:
                continue
            current_lines.append(line)
            if THANK_YOU_RE.search(line):
                block = "\n".join(current_lines)
                start_match = TEST_START_RE.search(block)
                end_match = TEST_END_RE.search(block)
                summaries.append(
                    {
                        "benchmark_metrics": extract_benchmark_metrics(block),
                        "latency": summarize_workload_output(block),
                        "label": current_label,
                        "start_ts": parse_local_timestamp(start_match.group("value")) if start_match else None,
                        "end_ts": parse_local_timestamp(end_match.group("value")) if end_match else None,
                    }
                )
                collecting = False
                current_lines = []
                current_label = None
    return summaries


def index_run_log_summaries(run_log_summaries: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for summary in run_log_summaries:
        label = summary.get("label")
        if isinstance(label, str) and label:
            out[label] = summary
    return out


def find_run_log_summary_before(
    run_log_summaries: List[Dict[str, Any]],
    label: Optional[str],
    timestamp_value: Optional[float],
) -> Dict[str, Any]:
    if timestamp_value is None:
        return {}
    candidates = [
        summary for summary in run_log_summaries
        if to_float(summary.get("end_ts")) is not None
        and float(summary["end_ts"]) <= timestamp_value
    ]
    if label:
        exact_candidates = [summary for summary in candidates if summary.get("label") == label]
        if exact_candidates:
            candidates = exact_candidates
    if not candidates:
        return {}
    return max(candidates, key=lambda item: float(item.get("end_ts") or 0.0))


def load_db_monitor_rows(path: Optional[Path]) -> List[Dict[str, Any]]:
    if not path or not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as fp:
        reader = csv.DictReader(fp)
        for row in reader:
            ts = parse_local_timestamp(row.get("time"))
            if ts is None:
                continue
            rows.append(
                {
                    "ts": ts,
                    "qps_statements": to_float(row.get("qps_statements")),
                    "xact_tps": to_float(row.get("xact_tps")),
                    "commit_tps": to_float(row.get("commit_tps")),
                    "rollback_tps": to_float(row.get("rollback_tps")),
                    "active_sessions": to_float(row.get("active_sessions")),
                }
            )
    return rows


def average_db_monitor_for_window(
    rows: List[Dict[str, Any]],
    start_ts: Optional[float],
    end_ts: Optional[float],
) -> Dict[str, Optional[float]]:
    out = {
        "db_qps": None,
        "db_xact_tps": None,
        "db_commit_tps": None,
        "db_rollback_tps": None,
        "db_active_sessions": None,
    }
    if not rows or start_ts is None or end_ts is None or end_ts <= start_ts:
        return out

    selected = [row for row in rows if start_ts <= float(row["ts"]) <= end_ts]
    if not selected:
        return out

    source_keys = {
        "db_qps": "qps_statements",
        "db_xact_tps": "xact_tps",
        "db_commit_tps": "commit_tps",
        "db_rollback_tps": "rollback_tps",
        "db_active_sessions": "active_sessions",
    }
    for out_key, source_key in source_keys.items():
        values = [to_float(row.get(source_key)) for row in selected]
        clean_values = [value for value in values if value is not None]
        if clean_values:
            out[out_key] = sum(clean_values) / len(clean_values)
    return out


def detect_querylog_columns(fieldnames: Sequence[str]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    lowered = {name.lower(): name for name in fieldnames}
    latency_candidates = [
        "latency", "latency_ms", "latency (ms)", "latency_ms_total", "response_time_ms", "rt", "duration_ms"
    ]
    name_candidates = [
        "name", "transaction", "transactiontype", "transaction_type", "type", "stmt", "statement"
    ]
    iter_candidates = ["iteration", "iter", "round"]

    latency_col = next((lowered[c] for c in latency_candidates if c in lowered), None)
    name_col = next((lowered[c] for c in name_candidates if c in lowered), None)
    iter_col = next((lowered[c] for c in iter_candidates if c in lowered), None)
    return latency_col, name_col, iter_col


def load_querylog_percentiles(query_log_csv: Path) -> Dict[int, Dict[str, Optional[float]]]:
    with query_log_csv.open("r", encoding="utf-8") as fp:
        reader = csv.DictReader(fp)
        if not reader.fieldnames:
            return {}
        latency_col, name_col, iter_col = detect_querylog_columns(reader.fieldnames)
        if not latency_col:
            return {}

        per_iteration: Dict[int, Dict[str, List[float]]] = {}
        synthetic_iteration = 1
        for row in reader:
            latency_value = to_float(row.get(latency_col))
            if latency_value is None:
                continue
            if iter_col:
                iteration_value = int(float(row.get(iter_col) or 0) or 0)
                if iteration_value <= 0:
                    iteration_value = synthetic_iteration
            else:
                iteration_value = synthetic_iteration
            synthetic_iteration = max(synthetic_iteration, iteration_value)

            bucket = per_iteration.setdefault(
                iteration_value,
                {
                    "all": [],
                    "tp": [],
                    "xp_iq": [],
                    "xp_at": [],
                },
            )
            bucket["all"].append(latency_value)
            if name_col:
                group = classify_querylog_name(str(row.get(name_col) or ""))
                if group:
                    bucket[group].append(latency_value)

    out: Dict[int, Dict[str, Optional[float]]] = {}
    for iteration_value, bucket in per_iteration.items():
        summary: Dict[str, Optional[float]] = {}
        for group in ["all", "tp", "xp_iq", "xp_at"]:
            values = bucket[group]
            summary[f"{group}_p90"] = percentile(values, 0.90)
            summary[f"{group}_p95"] = percentile(values, 0.95)
            summary[f"{group}_p99"] = percentile(values, 0.99)
        out[iteration_value] = summary
    return out


def build_rows(
    log_path: Path,
    query_log_csv: Optional[Path],
    run_log_path: Optional[Path],
    db_monitor_csv: Optional[Path],
) -> List[IterationRow]:
    querylog_percentiles = load_querylog_percentiles(query_log_csv) if query_log_csv else {}
    db_monitor_rows = load_db_monitor_rows(db_monitor_csv) if db_monitor_csv else []
    db_monitor_by_label, db_monitor_summaries = load_db_monitor_summaries_from_jsonl(log_path)
    rows: List[IterationRow] = []
    seen_iterations = set()
    final_summary = load_final_summary(log_path)
    run_log_summaries = load_run_log_summaries(run_log_path)
    run_log_by_label = index_run_log_summaries(run_log_summaries)
    iteration_records = list(iter_iteration_records(log_path))
    iteration_record_by_iter: Dict[int, Dict[str, Any]] = {}
    for obj in iteration_records:
        if not isinstance(obj.get("iteration"), int):
            continue
        iteration_number = int(obj["iteration"])
        existing = iteration_record_by_iter.get(iteration_number)
        if existing is None or (
            not existing.get("candidate_params") and obj.get("candidate_params")
        ):
            iteration_record_by_iter[iteration_number] = obj

    baseline_score = to_float(final_summary.get("initial_score")) or load_initial_score(log_path)
    baseline_top_sql = load_baseline_top_sql(log_path)
    baseline_resource = final_summary.get("initial_resource") or {}
    if not baseline_resource:
        baseline_resource = normalize_runtime_resource(
            baseline_top_sql.get("baseline_runtime_resource_avg"),
            baseline_top_sql.get("baseline_runtime_resource_stats"),
        )
    baseline_params = final_summary.get("initial_params") or {}
    if not baseline_params:
        baseline_params = infer_initial_params(iteration_records)
    baseline_workload_output = baseline_top_sql.get("workload_output")
    baseline_latency = summarize_workload_output(baseline_workload_output)
    baseline_benchmark_metrics = extract_benchmark_metrics(baseline_workload_output)
    baseline_benchmark_metrics = merge_benchmark_metrics(
        baseline_benchmark_metrics,
        baseline_top_sql.get("baseline_benchmark"),
    )
    baseline_latency = merge_latency_metrics(
        baseline_latency,
        baseline_top_sql.get("baseline_benchmark"),
    )
    baseline_latency = merge_latency_metrics(
        baseline_latency,
        baseline_top_sql.get("baseline_benchmark_latency"),
    )
    baseline_run_log_summary = (
        run_log_by_label.get("baseline")
        or run_log_by_label.get("p_real (baseline)")
        or (run_log_summaries[0] if run_log_summaries else {})
    )
    if baseline_run_log_summary:
        summary_metrics = baseline_run_log_summary.get("benchmark_metrics") or {}
        for key, value in summary_metrics.items():
            if baseline_benchmark_metrics.get(key) is None:
                baseline_benchmark_metrics[key] = value
        for key in ["all_p90_avg", "all_p95_avg", "all_p99_avg"]:
            if baseline_latency.get(key) is None:
                baseline_latency[key] = (baseline_run_log_summary.get("latency") or {}).get(key)
    baseline_db_metrics = db_metrics_from_summary(db_monitor_by_label.get("baseline"))
    if all(value is None for value in baseline_db_metrics.values()):
        baseline_start = to_float(baseline_run_log_summary.get("start_ts")) if baseline_run_log_summary else None
        baseline_end = to_float(baseline_run_log_summary.get("end_ts")) if baseline_run_log_summary else None
        baseline_db_metrics = db_metrics_from_summary(
            find_db_monitor_summary_by_window(db_monitor_summaries, baseline_start, baseline_end)
        )
        if all(value is None for value in baseline_db_metrics.values()):
            baseline_db_metrics = average_db_monitor_for_window(
                db_monitor_rows,
                baseline_start,
                baseline_end,
            )
    rows.append(
        IterationRow(
            iteration=0,
            phase="baseline",
            noisy=False,
            retry_depth=None,
            retry_reasons=None,
            accepted=True,
            score=baseline_score,
            candidate_score=baseline_score,
            best_score=baseline_score,
            ap_qps=baseline_benchmark_metrics["ap_qps"],
            tp_tps=baseline_benchmark_metrics["tp_tps"],
            xp_qps=baseline_benchmark_metrics["xp_qps"],
            xp_tps=baseline_benchmark_metrics["xp_tps"],
            db_qps=baseline_db_metrics["db_qps"],
            db_xact_tps=baseline_db_metrics["db_xact_tps"],
            db_commit_tps=baseline_db_metrics["db_commit_tps"],
            db_rollback_tps=baseline_db_metrics["db_rollback_tps"],
            db_active_sessions=baseline_db_metrics["db_active_sessions"],
            latency_p90=baseline_latency.get("all_p90_avg"),
            latency_p95=baseline_latency.get("all_p95_avg"),
            latency_p99=baseline_latency.get("all_p99_avg"),
            cpu=to_float(baseline_resource.get("cpu")),
            memory=to_float(baseline_resource.get("memory")),
            io=to_float(baseline_resource.get("io")),
            io_read=to_float(baseline_resource.get("io_read")),
            io_write=to_float(baseline_resource.get("io_write")),
            iops_read=to_float(baseline_resource.get("iops_read")),
            iops_write=to_float(baseline_resource.get("iops_write")),
            seq_page_cost=to_float(baseline_params.get("seq_page_cost")),
            random_page_cost=to_float(baseline_params.get("random_page_cost")),
            cpu_tuple_cost=to_float(baseline_params.get("cpu_tuple_cost")),
            cpu_index_tuple_cost=to_float(baseline_params.get("cpu_index_tuple_cost")),
            cpu_operator_cost=to_float(baseline_params.get("cpu_operator_cost")),
            parallel_setup_cost=to_float(baseline_params.get("parallel_setup_cost")),
            parallel_tuple_cost=to_float(baseline_params.get("parallel_tuple_cost")),
        )
    )

    for noise_obj in iter_noise_guard_retry_records(log_path):
        label = str(noise_obj.get("label") or "")
        iteration_match = re.search(r"iter_(\d+)", label)
        if not iteration_match:
            continue
        iteration = int(iteration_match.group(1))
        timestamp_value = parse_local_timestamp(noise_obj.get("timestamp"))
        run_log_summary = find_run_log_summary_before(run_log_summaries, label, timestamp_value)
        run_log_metrics = run_log_summary.get("benchmark_metrics") or {}
        summary_latency = run_log_summary.get("latency") or {}
        start_ts = to_float(run_log_summary.get("start_ts"))
        end_ts = to_float(run_log_summary.get("end_ts"))
        db_metrics = db_metrics_from_summary(
            find_db_monitor_summary_by_window(db_monitor_summaries, start_ts, end_ts)
        )
        if all(value is None for value in db_metrics.values()):
            db_metrics = average_db_monitor_for_window(db_monitor_rows, start_ts, end_ts)

        raw_resource = noise_obj.get("resource_avg") or {}
        raw_memory = to_float(raw_resource.get("memory"))
        raw_io = to_float(raw_resource.get("io"))
        matching_iteration = iteration_record_by_iter.get(iteration, {})
        params = matching_iteration.get("candidate_params") or matching_iteration.get("params") or {}
        parameter_values = {key: to_float(params.get(key)) for key in PARAM_KEYS}
        rows.append(
            IterationRow(
                iteration=iteration,
                phase="noisy_retry",
                noisy=True,
                retry_depth=int(noise_obj.get("retry_depth") or 0),
                retry_reasons="; ".join(str(item) for item in (noise_obj.get("reasons") or [])) or None,
                accepted=False,
                score=None,
                candidate_score=None,
                best_score=None,
                ap_qps=run_log_metrics.get("ap_qps"),
                tp_tps=run_log_metrics.get("tp_tps"),
                xp_qps=run_log_metrics.get("xp_qps"),
                xp_tps=run_log_metrics.get("xp_tps"),
                db_qps=db_metrics["db_qps"],
                db_xact_tps=db_metrics["db_xact_tps"],
                db_commit_tps=db_metrics["db_commit_tps"],
                db_rollback_tps=db_metrics["db_rollback_tps"],
                db_active_sessions=db_metrics["db_active_sessions"],
                latency_p90=summary_latency.get("all_p90_avg"),
                latency_p95=summary_latency.get("all_p95_avg"),
                latency_p99=summary_latency.get("all_p99_avg"),
                cpu=to_float(raw_resource.get("cpu")),
                memory=(raw_memory / (1024 ** 3)) if raw_memory is not None else None,
                io=(raw_io / (1024 ** 2)) if raw_io is not None else None,
                io_read=None,
                io_write=None,
                iops_read=None,
                iops_write=None,
                seq_page_cost=parameter_values["seq_page_cost"],
                random_page_cost=parameter_values["random_page_cost"],
                cpu_tuple_cost=parameter_values["cpu_tuple_cost"],
                cpu_index_tuple_cost=parameter_values["cpu_index_tuple_cost"],
                cpu_operator_cost=parameter_values["cpu_operator_cost"],
                parallel_setup_cost=parameter_values["parallel_setup_cost"],
                parallel_tuple_cost=parameter_values["parallel_tuple_cost"],
            )
        )

    for obj in iteration_records:
        iteration = int(obj["iteration"])
        if iteration in seen_iterations:
            continue
        seen_iterations.add(iteration)

        benchmark = obj.get("benchmark") or {}
        resource = obj.get("candidate_resource") or obj.get("resource") or {}
        resource_stats = obj.get("candidate_resource_stats") or obj.get("resource_stats") or {}
        params = obj.get("candidate_params") or obj.get("params") or {}

        benchmark_values = {key: to_float(benchmark.get(key)) for key in BENCHMARK_KEYS}
        parameter_values = {key: to_float(params.get(key)) for key in PARAM_KEYS}
        workload_latency = summarize_workload_output(resource_stats.get("workload_output"))
        workload_metrics = extract_benchmark_metrics(resource_stats.get("workload_output"))
        benchmark_latency = resource_stats.get("benchmark_latency") or {}
        latency_p90 = to_float(benchmark_latency.get("all_p90_avg"))
        latency_p95 = to_float(benchmark_latency.get("all_p95_avg"))
        latency_p99 = to_float(benchmark_latency.get("all_p99_avg"))
        ap_qps = benchmark_values["ap_qps"] if benchmark_values["ap_qps"] is not None else workload_metrics["ap_qps"]
        tp_tps = benchmark_values["tp_tps"] if benchmark_values["tp_tps"] is not None else workload_metrics["tp_tps"]
        xp_qps = benchmark_values["xp_qps"] if benchmark_values["xp_qps"] is not None else workload_metrics["xp_qps"]
        xp_tps = benchmark_values["xp_tps"] if benchmark_values["xp_tps"] is not None else workload_metrics["xp_tps"]
        run_log_summary = run_log_by_label.get(f"iter_{iteration}") or {}
        run_log_metrics = run_log_summary.get("benchmark_metrics") or {}
        if ap_qps is None:
            ap_qps = run_log_metrics.get("ap_qps")
        if tp_tps is None:
            tp_tps = run_log_metrics.get("tp_tps")
        if xp_qps is None:
            xp_qps = run_log_metrics.get("xp_qps")
        if xp_tps is None:
            xp_tps = run_log_metrics.get("xp_tps")
        if latency_p90 is None:
            latency_p90 = workload_latency.get("all_p90_avg")
        if latency_p95 is None:
            latency_p95 = workload_latency.get("all_p95_avg")
        if latency_p99 is None:
            latency_p99 = workload_latency.get("all_p99_avg")
        if run_log_summary:
            summary_latency = run_log_summary.get("latency") or {}
            latency_p90 = latency_p90 if latency_p90 is not None else summary_latency.get("all_p90_avg")
            latency_p95 = latency_p95 if latency_p95 is not None else summary_latency.get("all_p95_avg")
            latency_p99 = latency_p99 if latency_p99 is not None else summary_latency.get("all_p99_avg")
        db_metrics = db_metrics_from_summary(db_monitor_by_label.get(f"iter_{iteration}"))
        if all(value is None for value in db_metrics.values()):
            iter_start = to_float(run_log_summary.get("start_ts")) if run_log_summary else None
            iter_end = to_float(run_log_summary.get("end_ts")) if run_log_summary else None
            db_metrics = db_metrics_from_summary(
                find_db_monitor_summary_by_window(db_monitor_summaries, iter_start, iter_end)
            )
            if all(value is None for value in db_metrics.values()):
                db_metrics = average_db_monitor_for_window(
                    db_monitor_rows,
                    iter_start,
                    iter_end,
                )
        row = IterationRow(
            iteration=iteration,
            phase="iteration",
            noisy=bool(((obj.get("candidate_resource_stats") or obj.get("resource_stats") or {}).get("noise_guard") or {}).get("noisy", False)),
            retry_depth=None,
            retry_reasons=None,
            accepted=obj.get("accepted") if isinstance(obj.get("accepted"), bool) else None,
            score=to_float(obj.get("score")),
            candidate_score=to_float(obj.get("candidate_score")),
            best_score=to_float(obj.get("best_score")),
            ap_qps=ap_qps,
            tp_tps=tp_tps,
            xp_qps=xp_qps,
            xp_tps=xp_tps,
            db_qps=db_metrics["db_qps"],
            db_xact_tps=db_metrics["db_xact_tps"],
            db_commit_tps=db_metrics["db_commit_tps"],
            db_rollback_tps=db_metrics["db_rollback_tps"],
            db_active_sessions=db_metrics["db_active_sessions"],
            latency_p90=latency_p90,
            latency_p95=latency_p95,
            latency_p99=latency_p99,
            cpu=to_float(resource.get("cpu")),
            memory=to_float(resource.get("memory")),
            io=to_float(resource.get("io")),
            io_read=to_float(resource.get("io_read")),
            io_write=to_float(resource.get("io_write")),
            iops_read=to_float(resource.get("iops_read")),
            iops_write=to_float(resource.get("iops_write")),
            seq_page_cost=parameter_values["seq_page_cost"],
            random_page_cost=parameter_values["random_page_cost"],
            cpu_tuple_cost=parameter_values["cpu_tuple_cost"],
            cpu_index_tuple_cost=parameter_values["cpu_index_tuple_cost"],
            cpu_operator_cost=parameter_values["cpu_operator_cost"],
            parallel_setup_cost=parameter_values["parallel_setup_cost"],
            parallel_tuple_cost=parameter_values["parallel_tuple_cost"],
        )
        rows.append(row)

    phase_order = {"baseline": 0, "noisy_retry": 1, "iteration": 2}
    rows.sort(key=lambda item: (item.iteration, phase_order.get(item.phase, 99)))
    return rows


def row_to_dict(row: IterationRow) -> Dict[str, Any]:
    return asdict(row)


def format_csv_value(value: Any) -> Any:
    if isinstance(value, float):
        return f"{value:.6f}"
    return value


def select_active_fields(rows: List[IterationRow]) -> List[str]:
    if not rows:
        return []
    all_fields = list(row_to_dict(rows[0]).keys())
    always_keep = {
        "iteration",
        "accepted", "score", "candidate_score", "best_score",
        "ap_qps", "tp_tps", "xp_qps", "xp_tps",
        "db_qps", "db_xact_tps", "db_commit_tps", "db_rollback_tps", "db_active_sessions",
        "latency_p90", "latency_p95", "latency_p99",
        "cpu", "memory", "io", "io_read", "io_write", "iops_read", "iops_write",
    }
    drop_fields = {"phase", "noisy", "retry_depth", "retry_reasons"}
    always_keep.update(PARAM_KEYS)
    active_fields: List[str] = []
    row_dicts = [row_to_dict(row) for row in rows]
    for field in all_fields:
        if field in drop_fields:
            continue
        if field in always_keep:
            active_fields.append(field)
            continue
        if any(row_dict.get(field) is not None for row_dict in row_dicts):
            active_fields.append(field)
    return active_fields


def write_csv(path: Path, rows: List[IterationRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = select_active_fields(rows)
    with path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            row_dict = row_to_dict(row)
            writer.writerow({key: format_csv_value(row_dict.get(key)) for key in fieldnames})


def write_json(path: Path, rows: List[IterationRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = select_active_fields(rows)
    with path.open("w", encoding="utf-8") as fp:
        json.dump(
            [{key: row_to_dict(row).get(key) for key in fieldnames} for row in rows],
            fp,
            ensure_ascii=False,
            indent=2,
        )


def print_summary(rows: List[IterationRow], limit: int) -> None:
    print(f"matched_iterations={len(rows)}", flush=True)
    summary_fields = [
        field for field in [
            "iteration", "phase", "accepted", "score", "candidate_score", "best_score",
            "ap_qps", "tp_tps", "xp_qps", "xp_tps",
            "db_qps", "db_xact_tps", "db_commit_tps", "db_rollback_tps", "db_active_sessions",
            "latency_p90", "latency_p95", "latency_p99",
        ]
        if field in select_active_fields(rows)
    ]
    print(",".join(summary_fields), flush=True)
    shown = rows if limit <= 0 else rows[:limit]
    for row in shown:
        row_dict = row_to_dict(row)
        print(",".join(str(row_dict.get(field)) for field in summary_fields), flush=True)


def main() -> None:
    args = parse_args()
    log_path = Path(args.log_file).resolve()
    if not log_path.exists():
        raise FileNotFoundError(f"log file not found: {log_path}")
    run_log_path = Path(args.run_log).resolve() if args.run_log else infer_run_log_path(log_path)
    if args.run_log and run_log_path and not run_log_path.exists():
        raise FileNotFoundError(f"run log not found: {run_log_path}")
    query_log_csv = Path(args.query_log_csv).resolve() if args.query_log_csv else None
    if query_log_csv and not query_log_csv.exists():
        raise FileNotFoundError(f"query log csv not found: {query_log_csv}")
    db_monitor_csv = Path(args.db_monitor_csv).resolve() if args.db_monitor_csv else None
    if db_monitor_csv and not db_monitor_csv.exists():
        raise FileNotFoundError(f"db monitor csv not found: {db_monitor_csv}")

    rows = build_rows(log_path, query_log_csv, run_log_path, db_monitor_csv)
    print_summary(rows, args.limit)

    if args.output_csv:
        write_csv(Path(args.output_csv).resolve(), rows)
    if args.output_json:
        write_json(Path(args.output_json).resolve(), rows)


if __name__ == "__main__":
    main()

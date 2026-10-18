#!/usr/bin/env python3
"""Strict completion audit for the four-VM HyBenchXP recall search."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple


TARGETS = ("cpu", "memory", "io", "none")
TARGET_HOSTS = {
    "cpu": "192.0.2.11",
    "memory": "192.0.2.12",
    "io": "192.0.2.13",
    "none": "192.0.2.14",
}
SMALL_LOOP_TOP1_ANCHOR_POLICY = "small_loop_top1_score_v1"
REAL_SCORE_B_ANCHOR_POLICY = "real_score_b_successful_topk_v2"
REAL_SCORE_C_ANCHOR_POLICY = "real_score_c_no_qps_topk_v1"
NESTED_RESOURCE_SEMANTIC_POLICY = "nested_inner_resource_outer_semantic_v1"


def load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def load_jsonl(path: Path) -> Tuple[List[Dict[str, Any]], int]:
    rows: List[Dict[str, Any]] = []
    malformed = 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows, 0
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if isinstance(row, dict):
            rows.append(row)
        else:
            malformed += 1
    return rows, malformed


def number(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def verify_sha256_manifest(path: Path) -> Tuple[List[Dict[str, str]], List[str]]:
    """Verify the frozen launch inputs used by all supervisor retries."""
    entries: List[Dict[str, str]] = []
    failures: List[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return entries, [f"snapshot_sha256_manifest_unreadable: {exc}"]
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2 or not re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            failures.append(f"snapshot_sha256_manifest_line_{line_number}_malformed")
            continue
        expected = parts[0].lower()
        artifact_path = Path(parts[1].lstrip("*"))
        try:
            digest = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
        except OSError as exc:
            failures.append(f"snapshot_artifact_unreadable: {artifact_path}: {exc}")
            continue
        passed = digest == expected
        entries.append(
            {
                "path": str(artifact_path),
                "expected_sha256": expected,
                "actual_sha256": digest,
                "passed": str(passed).lower(),
            }
        )
        if not passed:
            failures.append(f"snapshot_sha256_mismatch: {artifact_path}")
    if not entries:
        failures.append("snapshot_sha256_manifest_has_no_valid_entries")
    return entries, failures


def clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def real_score_b_audit_details(
    candidate: Dict[str, Any],
    baseline: Dict[str, Any],
    target: str,
    *,
    bonus_coefficient: float = 0.02,
    resource_up_scale: float = 0.10,
) -> Optional[Dict[str, Any]]:
    """Independently recompute score B and its successful-candidate gate."""
    stats = candidate.get("resource_stats") or {}
    if str(candidate.get("status") or "").lower() != "ok" or stats.get("resource_monitor_ok") is False:
        return None
    baseline_qps = number(baseline.get("db_qps"))
    db_qps = number(candidate.get("db_qps"))
    if baseline_qps in (None, 0.0) or db_qps is None:
        return None
    qps_gain = (db_qps - baseline_qps) / baseline_qps
    qps_gate = qps_gain >= 0.15
    target_drop: Optional[float] = None
    target_sweet_upper: Optional[float] = None
    target_progress = 0.0
    target_drop_gate: Optional[bool] = None
    if target != "none":
        baseline_target = number((baseline.get("resources") or {}).get(target))
        candidate_target = number((candidate.get("resources") or {}).get(target))
        if baseline_target in (None, 0.0) or candidate_target is None:
            return None
        target_drop = (baseline_target - candidate_target) / baseline_target
        target_sweet_upper = baseline_target * 0.95
        target_progress = clip(
            (baseline_target - candidate_target) / (baseline_target - target_sweet_upper),
            0.0,
            1.0,
        )
        target_drop_gate = 0.05 <= target_drop <= 0.10

    candidate_resources = candidate.get("resources") or {}
    baseline_resources = baseline.get("resources") or {}
    bonus_resources = (
        ("cpu", "memory", "io")
        if target == "none"
        else tuple(resource for resource in ("cpu", "memory", "io") if resource != target)
    )
    resource_gain_ratios: Dict[str, Optional[float]] = {}
    bonus_r: Dict[str, Optional[float]] = {}
    resource_up_resources: List[str] = []
    for resource in bonus_resources:
        real_value = number(candidate_resources.get(resource))
        baseline_value = number(baseline_resources.get(resource))
        gain_ratio = (
            (real_value - baseline_value) / baseline_value
            if real_value is not None and baseline_value not in (None, 0.0)
            else None
        )
        resource_gain_ratios[resource] = gain_ratio
        bonus_r[resource] = (
            clip(max(gain_ratio, 0.0) / resource_up_scale, 0.0, 1.0)
            if gain_ratio is not None
            else None
        )
        if gain_ratio is not None and gain_ratio > 0.0:
            resource_up_resources.append(resource)
    bonus_sum = sum(value for value in bonus_r.values() if value is not None)
    resource_gate = bool(resource_up_resources) if target == "none" else bool(target_drop_gate)
    successful = qps_gate and resource_gate
    score_b = qps_gain + target_progress + bonus_coefficient * bonus_sum
    return {
        "successful": successful,
        "feasible": successful,
        "db_qps": db_qps,
        "qps_gain": qps_gain,
        "db_qps_gain_pct": qps_gain * 100.0,
        "qps_gate": qps_gate,
        "target_sweet_upper": target_sweet_upper,
        "target_progress": target_progress,
        "target_drop_ratio": target_drop,
        "target_drop_pct": target_drop * 100.0 if target_drop is not None else math.nan,
        "target_drop_gate": target_drop_gate,
        "resource_gate": resource_gate,
        "resource_gain_ratios": resource_gain_ratios,
        "resource_up_resources": resource_up_resources,
        "bonus_r": bonus_r,
        "bonus_sum": bonus_sum,
        "bonus_coefficient": bonus_coefficient,
        "score_b": score_b,
    }


def real_score_b_audit_key(
    candidate: Dict[str, Any],
    details: Dict[str, Any],
) -> Tuple[Any, ...]:
    return (
        1 if details["successful"] else 0,
        details["score_b"],
        details["qps_gain"],
        details["target_progress"],
        -(number(candidate.get("p95_ms")) or math.inf),
        -(number(candidate.get("p99_ms")) or math.inf),
        -int(candidate.get("candidate_index") or 0),
    )


def real_score_c_audit_details(
    candidate: Dict[str, Any],
    baseline: Dict[str, Any],
    target: str,
    *,
    bonus_coefficient: float = 0.02,
    resource_up_scale: float = 0.10,
) -> Optional[Dict[str, Any]]:
    """Independently recompute C using resources only; QPS is ignored."""
    stats = candidate.get("resource_stats") or {}
    if str(candidate.get("status") or "").lower() != "ok" or stats.get("resource_monitor_ok") is False:
        return None
    candidate_resources = candidate.get("resources") or {}
    baseline_resources = baseline.get("resources") or {}
    target_drop: Optional[float] = None
    target_sweet_upper: Optional[float] = None
    target_progress = 0.0
    target_drop_gate: Optional[bool] = None
    if target != "none":
        baseline_target = number(baseline_resources.get(target))
        candidate_target = number(candidate_resources.get(target))
        if baseline_target in (None, 0.0) or candidate_target is None:
            return None
        target_drop = (baseline_target - candidate_target) / baseline_target
        target_sweet_upper = baseline_target * 0.95
        target_progress = clip(
            (baseline_target - candidate_target) / (baseline_target - target_sweet_upper),
            0.0,
            1.0,
        )
        target_drop_gate = 0.05 <= target_drop <= 0.10

    bonus_resources = (
        ("cpu", "memory", "io")
        if target == "none"
        else tuple(resource for resource in ("cpu", "memory", "io") if resource != target)
    )
    resource_gain_ratios: Dict[str, Optional[float]] = {}
    bonus_r: Dict[str, Optional[float]] = {}
    resource_up_resources: List[str] = []
    for resource in bonus_resources:
        real_value = number(candidate_resources.get(resource))
        baseline_value = number(baseline_resources.get(resource))
        gain_ratio = (
            (real_value - baseline_value) / baseline_value
            if real_value is not None and baseline_value not in (None, 0.0)
            else None
        )
        resource_gain_ratios[resource] = gain_ratio
        bonus_r[resource] = (
            clip(max(gain_ratio, 0.0) / resource_up_scale, 0.0, 1.0)
            if gain_ratio is not None
            else None
        )
        if gain_ratio is not None and gain_ratio > 0.0:
            resource_up_resources.append(resource)
    if target == "none" and not any(value is not None for value in resource_gain_ratios.values()):
        return None
    bonus_sum = sum(value for value in bonus_r.values() if value is not None)
    resource_gate = bool(resource_up_resources) if target == "none" else bool(target_drop_gate)
    successful = resource_gate
    score_c = target_progress + bonus_coefficient * bonus_sum
    return {
        "valid": True,
        "successful": successful,
        "feasible": successful,
        "qps_considered": False,
        "qps_gate": None,
        "target_sweet_upper": target_sweet_upper,
        "target_progress": target_progress,
        "target_drop_ratio": target_drop,
        "target_drop_pct": target_drop * 100.0 if target_drop is not None else math.nan,
        "target_drop_gate": target_drop_gate,
        "resource_gate": resource_gate,
        "resource_gain_ratios": resource_gain_ratios,
        "resource_up_resources": resource_up_resources,
        "bonus_r": bonus_r,
        "bonus_sum": bonus_sum,
        "bonus_coefficient": bonus_coefficient,
        "score_c": score_c,
    }


def real_score_c_audit_key(
    candidate: Dict[str, Any],
    details: Dict[str, Any],
) -> Tuple[Any, ...]:
    return (
        1 if details["successful"] else 0,
        details["score_c"],
        details["target_progress"],
        details["bonus_sum"],
        -(number(candidate.get("p95_ms")) or math.inf),
        -(number(candidate.get("p99_ms")) or math.inf),
        -int(candidate.get("candidate_index") or 0),
    )


def interval_search_width(low: float, high: float) -> Optional[float]:
    """Return interval width in the same space used by the binary tuner."""
    if not math.isfinite(low) or not math.isfinite(high) or high <= low or low < 0.0:
        return None
    if low > 0.0:
        return math.log(high) - math.log(low)
    return high - low


def epsilon_for(epsilon: Any, name: str) -> Optional[float]:
    if isinstance(epsilon, dict):
        return number(epsilon.get(name, epsilon.get("default")))
    return number(epsilon)


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        os.chmod(path, 0o644)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def load_manifest(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        key, separator, value = line.partition("=")
        if separator and key:
            values[key] = value
    return values


def timestamp_key(value: Any) -> Optional[str]:
    """Return the sortable local wall-clock portion of a recorded timestamp."""
    text = str(value or "")
    if len(text) < 19:
        return None
    candidate = text[:19]
    try:
        time.strptime(candidate, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return candidate


def timestamp_epoch(value: Any) -> Optional[float]:
    key = timestamp_key(value)
    if key is None:
        return None
    return time.mktime(time.strptime(key, "%Y-%m-%d %H:%M:%S"))


def stable_samples_pass(row: Dict[str, Any]) -> bool:
    stats = row.get("resource_stats") or {}
    minimum = number(stats.get("min_stable_samples"))
    stable = stats.get("stable_sample_count") or {}
    return bool(
        stats.get("resource_monitor_ok") is True
        and minimum is not None
        and all(number(stable.get(resource)) is not None for resource in ("cpu", "memory", "io"))
        and all(float(stable[resource]) >= minimum for resource in ("cpu", "memory", "io"))
    )


def latency_percentiles_pass(row: Dict[str, Any]) -> bool:
    benchmark = (row.get("hybench") or {}).get("benchmark") or {}
    return all(
        number(benchmark.get(f"latency_p{percentile}")) is not None
        and float(benchmark[f"latency_p{percentile}"]) > 0
        for percentile in (90, 95, 99)
    )


def replay_validation_failures(
    row: Dict[str, Any],
    target: str,
    expected_run_mins: Optional[float],
) -> List[str]:
    """Return hard replay-integrity failures for one baseline or candidate."""
    failures: List[str] = []
    label = str(row.get("label") or "unlabeled")
    prefix = f"{label}:"
    if str(row.get("status") or "").lower() != "ok":
        failures.append(f"{prefix} status_is_not_ok")
    if not stable_samples_pass(row):
        failures.append(f"{prefix} stable_sample_gate_failed")
    if not latency_percentiles_pass(row):
        failures.append(f"{prefix} latency_percentiles_missing_or_nonpositive")

    hybench_run_mins = number(
        ((row.get("hybench") or {}).get("run_config") or {}).get("xpRunMins")
    )
    recovery = ((row.get("vm_replay") or {}).get("recovery") or {})
    recovery_run_mins = number(recovery.get("run_mins"))
    if (
        expected_run_mins is None
        or hybench_run_mins != expected_run_mins
        or recovery_run_mins != expected_run_mins
    ):
        failures.append(
            f"{prefix} run_mins_mismatch hybench={hybench_run_mins} "
            f"recovery={recovery_run_mins} expected={expected_run_mins}"
        )

    run_config = (row.get("hybench") or {}).get("run_config") or {}
    props_path = str(run_config.get("props_path") or "")
    statement_file = str(run_config.get("statement_file") or "")
    if not props_path or not Path(props_path).is_file():
        failures.append(f"{prefix} run_owned_props_missing={props_path!r}")
    if not statement_file or not Path(statement_file).is_file():
        failures.append(f"{prefix} statement_profile_missing={statement_file!r}")
    if target == "none":
        expected_profile = {
            "xtpclient": 1,
            "xapclient": 1,
            "ap_query_ids": "6",
            "ap_query_weights": "100",
            "ap_query_sequence": "6",
            "setup_sql_count": 1,
        }
        for field, expected in expected_profile.items():
            if run_config.get(field) != expected:
                failures.append(
                    f"{prefix} none_profile_{field}={run_config.get(field)!r} expected={expected!r}"
                )
        statement_meta = run_config.get("statement_meta") or {}
        if not (
            statement_meta.get("generated") is True
            and "AP-6" in (statement_meta.get("statement_sql_file_sections") or [])
        ):
            failures.append(f"{prefix} none_AP6_statement_override_not_proven")
        target_override_keys = set(str(key) for key in (run_config.get("target_override_keys") or []))
        required_override_keys = {
            "xtpclient",
            "xapclient",
            "ap_query_ids",
            "ap_query_weights",
            "ap_query_sequence",
            "setup_sql_files",
            "statement_sql_files",
        }
        if not required_override_keys.issubset(target_override_keys):
            failures.append(
                f"{prefix} none_target_override_keys_missing="
                f"{sorted(required_override_keys - target_override_keys)}"
            )

    expected_host = TARGET_HOSTS.get(target)
    restore = recovery.get("snapshot_restore") or {}
    if restore.get("status") != "ok":
        failures.append(f"{prefix} snapshot_restore_not_ok")
    if not expected_host or restore.get("host") != expected_host:
        failures.append(
            f"{prefix} snapshot_restore_host={restore.get('host')} expected={expected_host}"
        )
    if (recovery.get("cache_drop") or {}).get("status") != "ok":
        failures.append(f"{prefix} cache_drop_not_ok")
    measured_host = ((row.get("resource_stats") or {}).get("vm_host") or {}).get("value")
    if not expected_host or measured_host != expected_host:
        failures.append(f"{prefix} measured_vm_host={measured_host} expected={expected_host}")

    workload_exit = (row.get("resource_stats") or {}).get("workload_exit") or {}
    returncode = number(workload_exit.get("returncode"))
    fixed_horizon = workload_exit.get("fixed_horizon_reached") is True
    sigterm_success = workload_exit.get("sigterm_treated_as_success") is True
    fixed_horizon_s = number(workload_exit.get("fixed_horizon_s"))
    clean_exit = returncode == 0.0
    valid_fixed_horizon_exit = bool(
        returncode == -15.0
        and fixed_horizon
        and sigterm_success
        and expected_run_mins is not None
        and fixed_horizon_s is not None
        and fixed_horizon_s >= expected_run_mins * 60.0
    )
    if not clean_exit and not valid_fixed_horizon_exit:
        failures.append(
            f"{prefix} invalid_workload_exit returncode={returncode} "
            f"fixed_horizon={fixed_horizon} sigterm_success={sigterm_success} "
            f"fixed_horizon_s={fixed_horizon_s}"
        )
    if fixed_horizon and (
        expected_run_mins is None
        or fixed_horizon_s is None
        or fixed_horizon_s < expected_run_mins * 60.0
    ):
        failures.append(
            f"{prefix} fixed_horizon_shorter_than_measurement "
            f"fixed_horizon_s={fixed_horizon_s} expected_run_mins={expected_run_mins}"
        )

    if target == "io":
        for throttle_name in ("restore_io_boost", "measurement_io_throttle"):
            throttle = recovery.get(throttle_name) or {}
            if throttle.get("status") != "ok" or throttle.get("verify_matched") is not True:
                failures.append(f"{prefix} {throttle_name}_not_verified")
                continue
            for direction in ("read", "write"):
                requested = number(throttle.get(f"requested_{direction}_bytes_sec"))
                observed = number(throttle.get(f"observed_{direction}_bytes_sec"))
                if requested is None or observed is None or requested != observed:
                    failures.append(
                        f"{prefix} {throttle_name}_{direction}_mismatch "
                        f"requested={requested} observed={observed}"
                    )
    return failures


def warmup_lifecycle_failures(
    controller_text: str,
    validations: List[Dict[str, Any]],
    target: str,
) -> List[str]:
    """Verify required warmup and JDBC drain evidence before measured replay."""
    expected_seconds = {"cpu": 60.0, "memory": 90.0, "io": 60.0}.get(target)
    if expected_seconds is None:
        return []
    failures: List[str] = []
    for validation in validations:
        if str(validation.get("status") or "").lower() != "ok":
            continue
        label = str(validation.get("label") or "")
        if not label:
            failures.append("unlabeled_validation_missing_warmup_lifecycle")
            continue
        start_token = f"[validate] {label} validation warmup starts"
        measured_token = f"[validate] {label} measured workload start"
        done_pattern = re.compile(
            rf"\[validate\] {re.escape(label)} validation warmup done "
            rf"success=True duration_s=([0-9.]+) timed_out=True"
        )
        lifecycle_ok = False
        observed_duration: Optional[float] = None
        for start_match in reversed(list(re.finditer(re.escape(start_token), controller_text))):
            start_pos = start_match.start()
            measured_pos = controller_text.find(measured_token, start_pos)
            if measured_pos < 0:
                continue
            block = controller_text[start_pos:measured_pos]
            done_match = done_pattern.search(block)
            if done_match is None:
                continue
            observed_duration = number(done_match.group(1))
            drain_pos = block.find("validation warmup JDBC sessions drained: count=0")
            if (
                observed_duration is not None
                and observed_duration >= expected_seconds - 1.0
                and drain_pos >= 0
                and done_match.start() < drain_pos
            ):
                lifecycle_ok = True
                break
        if not lifecycle_ok:
            failures.append(
                f"{label}: warmup_lifecycle_missing_or_invalid "
                f"expected_seconds={expected_seconds} observed_duration={observed_duration}"
            )
    return failures


def raw_replay_alignment_failures(
    raw_rows: List[Dict[str, Any]],
    validations: List[Dict[str, Any]],
    target: str,
    expected_run_mins: Optional[float],
) -> List[str]:
    """Prove one ordered raw VM replay row exists for every logged validation."""
    failures: List[str] = []
    expected_labels = [str(row.get("label") or "") for row in validations]
    raw_labels = [str(row.get("binary_plan_label") or "") for row in raw_rows]
    if raw_labels != expected_labels:
        failures.append(
            f"ordered_raw_labels_do_not_match_validations raw_count={len(raw_labels)} "
            f"validation_count={len(expected_labels)}"
        )
    expected_warmup_seconds = {"cpu": 60.0, "memory": 90.0, "io": 60.0}.get(target)
    for validation, raw in zip(validations, raw_rows):
        label = str(validation.get("label") or "unlabeled")
        prefix = f"{label}:"
        if raw.get("binary_plan_label") != label or raw.get("candidate_id") != label:
            failures.append(f"{prefix} raw_identity_mismatch")
        if raw.get("scenario") != target or raw.get("source") != "binary_plan_vm_replay":
            failures.append(
                f"{prefix} raw_provenance_mismatch scenario={raw.get('scenario')} "
                f"source={raw.get('source')}"
            )
        if raw.get("status") != validation.get("status") or raw.get("status") != "ok":
            failures.append(
                f"{prefix} raw_status={raw.get('status')} validation_status={validation.get('status')}"
            )
        if number(raw.get("run_mins_override")) != expected_run_mins:
            failures.append(
                f"{prefix} raw_run_mins={raw.get('run_mins_override')} expected={expected_run_mins}"
            )
        if raw.get("params") != validation.get("params"):
            failures.append(f"{prefix} raw_params_do_not_match_validation")
        recovery = raw.get("binary_plan_recovery") or {}
        if (
            recovery.get("label") != label
            or recovery.get("target") != target
            or number(recovery.get("run_mins")) != expected_run_mins
        ):
            failures.append(f"{prefix} raw_recovery_identity_or_duration_mismatch")

        validation_run_config = (validation.get("hybench") or {}).get("run_config") or {}
        raw_run_config = raw.get("binary_plan_hybench_run_config") or {}
        if raw_run_config != validation_run_config:
            failures.append(f"{prefix} raw_hybench_run_config_mismatch")
        workload_cmd = str(raw.get("workload_cmd") or "")
        props_path = str(raw_run_config.get("props_path") or "")
        statement_file = str(raw_run_config.get("statement_file") or "")
        if not props_path or props_path not in workload_cmd:
            failures.append(f"{prefix} raw_workload_did_not_use_run_owned_props")
        if not statement_file or statement_file not in workload_cmd:
            failures.append(f"{prefix} raw_workload_did_not_use_recorded_statement_profile")
        pre_workload_setup = raw.get("pre_workload_setup") or []
        if target == "none":
            if not (
                len(pre_workload_setup) == 1
                and pre_workload_setup[0].get("status") == "ok"
                and Path(str(pre_workload_setup[0].get("path") or "")).is_file()
            ):
                failures.append(f"{prefix} none_pre_workload_setup_not_proven")
        elif pre_workload_setup:
            failures.append(f"{prefix} unexpected_non_none_pre_workload_setup")

        warmup = raw.get("validation_warmup") or {}
        if expected_warmup_seconds is None:
            if not (
                warmup.get("enabled") is False
                and warmup.get("executed") is False
                and warmup.get("success") is True
            ):
                failures.append(f"{prefix} raw_warmup_should_be_disabled")
            continue
        drain = warmup.get("connection_drain") or {}
        duration = number(warmup.get("duration_s"))
        timeout_seconds = number(warmup.get("timeout_sec"))
        returncode = number(warmup.get("returncode"))
        if not (
            warmup.get("enabled") is True
            and warmup.get("executed") is True
            and warmup.get("success") is True
            and warmup.get("timed_out") is True
            and warmup.get("tag") == label
            and duration is not None
            and duration >= expected_warmup_seconds - 1.0
            and timeout_seconds == expected_warmup_seconds
            and returncode == -15.0
            and drain.get("enabled") is True
            and drain.get("passed") is True
            and number(drain.get("connection_count")) == 0.0
            and number(drain.get("max_allowed")) == 0.0
        ):
            failures.append(
                f"{prefix} raw_warmup_or_connection_drain_invalid "
                f"duration={duration} timeout={timeout_seconds} returncode={returncode}"
            )
    return failures


def audit_param_signature(params: Dict[str, Any]) -> Tuple[Tuple[str, Optional[float]], ...]:
    return tuple(sorted((str(name), number(value)) for name, value in params.items()))


def nested_inner_score_key(
    candidate: Dict[str, Any],
    details: Dict[str, Any],
) -> Tuple[Any, ...]:
    return (
        details["score_c"],
        details["target_progress"],
        details["bonus_sum"],
        -(number(candidate.get("p95_ms")) or math.inf),
        -(number(candidate.get("p99_ms")) or math.inf),
        -int(candidate.get("candidate_index") or 0),
    )


def nested_outer_audit_details(
    candidate: Dict[str, Any],
    reference: Dict[str, Any],
    baseline: Dict[str, Any],
    target: str,
) -> Dict[str, Any]:
    candidate_qps = number(candidate.get("db_qps"))
    reference_qps = number(reference.get("db_qps"))
    baseline_qps = number(baseline.get("db_qps"))
    qps_gain_vs_reference = (
        (candidate_qps - reference_qps) / reference_qps
        if candidate_qps is not None and reference_qps not in (None, 0.0)
        else None
    )
    qps_gain_vs_baseline = (
        (candidate_qps - baseline_qps) / baseline_qps
        if candidate_qps is not None and baseline_qps not in (None, 0.0)
        else None
    )
    qps_up = qps_gain_vs_reference is not None and qps_gain_vs_reference > 0.0
    candidate_resources = candidate.get("resources") or {}
    reference_resources = reference.get("resources") or {}
    baseline_resources = baseline.get("resources") or {}
    target_drop_vs_reference: Optional[float] = None
    target_drop_vs_baseline: Optional[float] = None
    target_down = True
    if target != "none":
        candidate_target = number(candidate_resources.get(target))
        reference_target = number(reference_resources.get(target))
        baseline_target = number(baseline_resources.get(target))
        target_drop_vs_reference = (
            (reference_target - candidate_target) / reference_target
            if candidate_target is not None and reference_target not in (None, 0.0)
            else None
        )
        target_drop_vs_baseline = (
            (baseline_target - candidate_target) / baseline_target
            if candidate_target is not None and baseline_target not in (None, 0.0)
            else None
        )
        target_down = target_drop_vs_reference is not None and target_drop_vs_reference > 0.0
    other_resources = (
        ("cpu", "memory", "io")
        if target == "none"
        else tuple(resource for resource in ("cpu", "memory", "io") if resource != target)
    )
    other_up_vs_reference = []
    other_up_vs_baseline = []
    for resource in other_resources:
        candidate_value = number(candidate_resources.get(resource))
        reference_value = number(reference_resources.get(resource))
        baseline_value = number(baseline_resources.get(resource))
        if candidate_value is not None and reference_value is not None and candidate_value > reference_value:
            other_up_vs_reference.append(resource)
        if candidate_value is not None and baseline_value is not None and candidate_value > baseline_value:
            other_up_vs_baseline.append(resource)
    status_ok = str(candidate.get("status") or "").lower() == "ok"
    monitor_ok = (candidate.get("resource_stats") or {}).get("resource_monitor_ok") is not False
    accepted = bool(status_ok and monitor_ok and qps_up and target_down)
    return {
        "accepted": accepted,
        "qps_up": qps_up,
        "qps_gain_vs_reference": qps_gain_vs_reference,
        "qps_gain_vs_baseline": qps_gain_vs_baseline,
        "target_down": target_down if target != "none" else None,
        "target_drop_vs_reference": target_drop_vs_reference,
        "target_drop_vs_baseline": target_drop_vs_baseline,
        "other_up_vs_reference": sorted(other_up_vs_reference),
        "other_up_vs_baseline": sorted(other_up_vs_baseline),
    }


def nested_final_goal_audit_details(
    confirmation: Dict[str, Any],
    baseline: Dict[str, Any],
    target: str,
) -> Dict[str, Any]:
    db_qps = number(confirmation.get("db_qps"))
    baseline_qps = number(baseline.get("db_qps"))
    qps_gain = (
        (db_qps - baseline_qps) / baseline_qps
        if db_qps is not None and baseline_qps not in (None, 0.0)
        else None
    )
    resources = confirmation.get("resources") or {}
    baseline_resources = baseline.get("resources") or {}
    utilization = confirmation.get("actual_utilization_pct") or {}
    scoring = confirmation.get("runtime_scoring") or {}
    overload_pct = (number(scoring.get("overload_threshold")) or 0.8) * 100.0
    target_ranges = scoring.get("resource_target_ranges") or {}
    target_range = target_ranges.get(target) or {}
    target_drop: Optional[float] = None
    target_util: Optional[float] = None
    target_down = True
    target_drop_in_range = True
    target_not_bottleneck = True
    target_in_sweet: Optional[bool] = None
    if target != "none":
        target_value = number(resources.get(target))
        baseline_target = number(baseline_resources.get(target))
        target_drop = (
            (baseline_target - target_value) / baseline_target
            if target_value is not None and baseline_target not in (None, 0.0)
            else None
        )
        target_down = target_drop is not None and target_drop > 0.0
        target_drop_in_range = target_drop is not None and 0.05 <= target_drop <= 0.10
        target_util = number(utilization.get(target))
        target_not_bottleneck = target_util is not None and target_util <= overload_pct
        sweet_min = number(target_range.get("min_pct"))
        sweet_max = number(target_range.get("max_pct"))
        target_in_sweet = bool(
            target_util is not None
            and sweet_min is not None
            and sweet_max is not None
            and sweet_min <= target_util <= sweet_max
        )
    other_resources = (
        ("cpu", "memory", "io")
        if target == "none"
        else tuple(resource for resource in ("cpu", "memory", "io") if resource != target)
    )
    other_up = sorted(
        resource
        for resource in other_resources
        if number(resources.get(resource)) is not None
        and number(baseline_resources.get(resource)) is not None
        and float(resources[resource]) > float(baseline_resources[resource])
    )
    status_ok = str(confirmation.get("status") or "").lower() == "ok"
    monitor_ok = (confirmation.get("resource_stats") or {}).get("resource_monitor_ok") is not False
    latency_ok = all((number(confirmation.get(name)) or 0.0) > 0.0 for name in ("p90_ms", "p95_ms", "p99_ms"))
    goal_reached = bool(
        status_ok
        and monitor_ok
        and latency_ok
        and qps_gain is not None
        and qps_gain >= 0.15
        and target_down
        and target_drop_in_range
        and target_not_bottleneck
        and (bool(other_up) or target != "none")
    )
    return {
        "goal_reached": goal_reached,
        "db_qps_gain_pct": qps_gain * 100.0 if qps_gain is not None else None,
        "qps_goal_reached": qps_gain is not None and qps_gain >= 0.15,
        "target_drop_pct": target_drop * 100.0 if target_drop is not None else None,
        "target_down": target_down if target != "none" else None,
        "target_drop_in_range": target_drop_in_range if target != "none" else None,
        "min_target_drop_pct": 5.0 if target != "none" else None,
        "max_target_drop_pct": 10.0 if target != "none" else None,
        "target_utilization_pct": target_util,
        "overload_threshold_pct": overload_pct if target != "none" else None,
        "target_not_bottleneck": target_not_bottleneck if target != "none" else None,
        "target_in_sweet_spot": target_in_sweet,
        "other_up_resources": other_up,
        "any_other_resource_up": bool(other_up),
        "other_resource_up_required": target == "none",
        "latency_ok": latency_ok,
    }


def audit_nested_target(
    run_root: Path,
    target: str,
    expected_run_mins: Optional[float],
    *,
    expected_topk: int,
) -> Dict[str, Any]:
    target_dir = run_root / target
    log_path = target_dir / f"{target}_binary_search_hybenchxp.jsonl"
    raw_path = target_dir / f"{target}_vm_replay_raw.jsonl"
    final_path = target_dir / f"final_for_{target}.json"
    best_path = target_dir / f"best_for_{target}.json"
    rolling_path = target_dir / f"best_so_far_for_{target}.json"
    controller_path = target_dir / "controller.log"
    failures: List[str] = []
    for path in (log_path, raw_path, final_path, best_path, rolling_path, controller_path):
        if not path.is_file() or path.stat().st_size <= 0:
            failures.append(f"missing_or_empty_artifact: {path}")
    rows, malformed_log_lines = load_jsonl(log_path)
    raw_rows, malformed_raw_lines = load_jsonl(raw_path)
    if malformed_log_lines:
        failures.append(f"malformed_search_log_lines={malformed_log_lines}")
    if malformed_raw_lines:
        failures.append(f"malformed_raw_log_lines={malformed_raw_lines}")
    starts = [row for row in rows if row.get("event") == "stage_start"]
    baselines = [row for row in rows if row.get("event") == "baseline_validation"]
    candidates = [row for row in rows if row.get("event") == "candidate_validation"]
    rounds = [row for row in rows if row.get("event") == "round_summary"]
    searches = [row for row in rows if row.get("event") == "round_search_points_audit"]
    decisions = [row for row in rows if row.get("event") == "outer_candidate_decision"]
    accepted_events = [row for row in rows if row.get("event") == "outer_accepted_candidate"]
    handoffs = [row for row in rows if row.get("event") == "inner_outer_handoff_summary"]
    confirmations = [row for row in rows if row.get("event") == "post_convergence_final_validation"]
    stops = [row for row in rows if row.get("event") == "stage_stop"]
    final = load_json(final_path) or {}
    best_artifact = load_json(best_path) or {}
    start = starts[-1] if starts else {}
    baseline = next(
        (row for row in reversed(baselines) if str(row.get("status") or "").lower() == "ok"),
        None,
    )
    if len(starts) != 1:
        failures.append(f"stage_start_count={len(starts)}, expected=1")
    if start.get("range_anchor_policy") != NESTED_RESOURCE_SEMANTIC_POLICY:
        failures.append("nested_range_anchor_policy_missing")
    if start.get("experiment_strategy") != "NESTED":
        failures.append("nested_experiment_strategy_missing")
    if expected_topk <= 1 or int(start.get("candidate_topk_limit") or 0) != expected_topk:
        failures.append("nested_topk_invalid")
    nested_cfg = start.get("nested_loop") or {}
    inner_cfg = nested_cfg.get("inner") or {}
    if inner_cfg.get("qps_considered") is not False:
        failures.append("inner_loop_qps_considered_is_not_false")
    if inner_cfg.get("handoff_cardinality") != "exactly_one_best_candidate_per_inner_attempt":
        failures.append("inner_loop_handoff_is_not_exactly_one_best")
    if ((nested_cfg.get("outer_acceptance") or {}).get("shrink_only_when_accepted")) is not True:
        failures.append("outer_shrink_only_when_accepted_is_not_true")
    if baseline is None:
        failures.append("valid_baseline_missing")
        baseline = {}

    by_round = lambda values, round_number: [
        row for row in values if int(row.get("round") or 0) == round_number
    ]
    reference = baseline
    interval_seen: set = set()
    accepted_count = 0
    round_checks: List[Dict[str, Any]] = []
    for summary in rounds:
        round_number = int(summary.get("round") or 0)
        round_candidates = by_round(candidates, round_number)
        round_searches = by_round(searches, round_number)
        round_decisions = by_round(decisions, round_number)
        round_accepted = by_round(accepted_events, round_number)
        round_handoffs = by_round(handoffs, round_number)
        round_failures: List[str] = []
        if len(round_searches) != 1:
            round_failures.append(f"search_audit_count={len(round_searches)}")
            search_points: List[Dict[str, Any]] = []
        else:
            search_points = round_searches[0].get("points") or []
        selected_points = [row for row in search_points if row.get("selected_by_candidate_cap") is True]
        expected_replays = min(expected_topk, len(search_points))
        if len(round_candidates) != expected_replays:
            round_failures.append(
                f"real_replays={len(round_candidates)}, expected={expected_replays}"
            )
        if len(selected_points) != expected_replays:
            round_failures.append(
                f"selected_search_points={len(selected_points)}, expected={expected_replays}"
            )
        if len(search_points) != int(summary.get("raw_search_point_count") or 0):
            round_failures.append("raw_search_point_count_mismatch")
        if any(
            not isinstance((point.get("plan_probe") or {}).get("topk_rank_key"), list)
            for point in search_points
        ):
            round_failures.append("one_or_more_small_loop_estimates_missing")
        search_signatures = {
            audit_param_signature(point.get("params") or {}) for point in search_points
        }
        overlap = search_signatures & interval_seen
        if overlap:
            round_failures.append(f"previously_replayed_candidates_recalled_again={len(overlap)}")
        candidate_signatures = {
            audit_param_signature(candidate.get("params") or {}) for candidate in round_candidates
        }
        selected_signatures = {
            audit_param_signature(point.get("params") or {}) for point in selected_points
        }
        if candidate_signatures != selected_signatures:
            round_failures.append("real_replays_do_not_match_estimated_topk")
        if sorted(int(row.get("topk_rank") or 0) for row in round_candidates) != list(
            range(1, expected_replays + 1)
        ):
            round_failures.append("topk_rank_sequence_invalid")

        ranked: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
        for candidate in round_candidates:
            details = real_score_c_audit_details(candidate, baseline, target)
            if details is None:
                round_failures.append(f"invalid_real_candidate={candidate.get('label')}")
                continue
            logged = candidate.get("inner_resource_score") or {}
            if logged.get("qps_considered") is not False:
                round_failures.append(f"inner_score_considered_qps={candidate.get('label')}")
            if not math.isclose(
                number(logged.get("score")) or math.nan,
                details["score_c"],
                rel_tol=0.0,
                abs_tol=1e-9,
            ):
                round_failures.append(f"inner_score_mismatch={candidate.get('label')}")
            ranked.append((candidate, details))
        ranked.sort(key=lambda item: nested_inner_score_key(item[0], item[1]), reverse=True)
        expected_decisions: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
        expected_accepted: Optional[Dict[str, Any]] = None
        for candidate, _ in ranked[:1]:
            detail = nested_outer_audit_details(candidate, reference, baseline, target)
            expected_decisions.append((candidate, detail))
            if detail["accepted"]:
                expected_accepted = candidate
        if [row.get("candidate_label") for row in round_decisions] != [
            row.get("label") for row, _ in expected_decisions
        ]:
            round_failures.append("outer_decision_order_does_not_follow_inner_resource_score")
        if len(round_decisions) > 1:
            round_failures.append("outer_received_more_than_one_inner_best_in_single_attempt")
        for logged, (candidate, expected) in zip(round_decisions, expected_decisions):
            if logged.get("accepted") is not expected["accepted"]:
                round_failures.append(f"outer_acceptance_mismatch={candidate.get('label')}")
            for field in (
                "qps_gain_vs_reference",
                "qps_gain_vs_baseline",
                "target_drop_vs_reference",
                "target_drop_vs_baseline",
            ):
                logged_value = number(logged.get(field))
                expected_value = number(expected.get(field))
                if logged_value is None and expected_value is None:
                    continue
                if (
                    logged_value is None
                    or expected_value is None
                    or not math.isclose(logged_value, expected_value, rel_tol=0.0, abs_tol=1e-9)
                ):
                    round_failures.append(f"outer_{field}_mismatch={candidate.get('label')}")
        if len(round_handoffs) != 1:
            round_failures.append(f"inner_outer_handoff_count={len(round_handoffs)}")
        previous_ranges = summary.get("previous_ranges") or {}
        updated_ranges = summary.get("updated_ranges") or {}
        if expected_accepted is None:
            if round_accepted:
                round_failures.append("outer_accepted_event_present_for_rejected_round")
            if summary.get("outer_semantic_accepted") is not False:
                round_failures.append("rejected_round_not_marked_outer_rejected")
            if previous_ranges != updated_ranges:
                round_failures.append("outer_rejection_changed_candidate_interval")
            if summary.get("interval_action") != "keep_interval_and_continue_inner_loop":
                round_failures.append("outer_rejection_did_not_continue_inner_loop")
            interval_seen.update(candidate_signatures)
        else:
            if len(round_accepted) != 1 or round_accepted[0].get("label") != expected_accepted.get("label"):
                round_failures.append("outer_accepted_event_mismatch")
            if summary.get("outer_semantic_accepted") is not True:
                round_failures.append("accepted_round_not_marked_outer_accepted")
            if summary.get("range_anchor_label") != expected_accepted.get("label"):
                round_failures.append("accepted_candidate_not_used_as_range_anchor")
            if summary.get("interval_action") != "shrink_around_accepted_candidate":
                round_failures.append("accepted_round_did_not_shrink")
            for name, previous in previous_ranges.items():
                updated = updated_ranges.get(name)
                if not (
                    isinstance(previous, list)
                    and isinstance(updated, list)
                    and len(previous) == 2
                    and len(updated) == 2
                ):
                    round_failures.append(f"range_shape_invalid={name}")
                    continue
                previous_width = interval_search_width(float(previous[0]), float(previous[1]))
                updated_width = interval_search_width(float(updated[0]), float(updated[1]))
                if (
                    previous_width is None
                    or updated_width is None
                    or updated_width > previous_width / 2.0 + max(1e-15, previous_width * 1e-12)
                ):
                    round_failures.append(f"accepted_range_not_halved={name}")
            reference = expected_accepted
            accepted_count += 1
            interval_seen.clear()
        if int(summary.get("outer_accepted_count") or 0) != accepted_count:
            round_failures.append("outer_accepted_count_mismatch")
        if round_failures:
            failures.extend(f"round={round_number}: {item}" for item in round_failures)
        round_checks.append(
            {
                "round": round_number,
                "search_point_count": len(search_points),
                "real_replay_count": len(round_candidates),
                "outer_decision_count": len(round_decisions),
                "accepted_label": expected_accepted.get("label") if expected_accepted else None,
                "passed": not round_failures,
            }
        )

    try:
        controller_text = controller_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        controller_text = ""
    validations = [*baselines, *candidates, *confirmations]
    warmup_failures = warmup_lifecycle_failures(controller_text, validations, target)
    raw_alignment_failures = raw_replay_alignment_failures(
        raw_rows, validations, target, expected_run_mins
    )
    if warmup_failures:
        failures.append("validation_warmup_lifecycle_failed: " + "; ".join(warmup_failures))
    if raw_alignment_failures:
        failures.append("raw_replay_alignment_failed: " + "; ".join(raw_alignment_failures))
    for validation in validations:
        integrity = replay_validation_failures(validation, target, expected_run_mins)
        if integrity:
            failures.append(
                f"replay_integrity_failed={validation.get('label')}: " + "; ".join(integrity)
            )
    if len(raw_rows) != len(validations):
        failures.append(f"raw_replay_row_count={len(raw_rows)}, expected={len(validations)}")
    latest_stop = stops[-1] if stops else {}
    if latest_stop.get("reason") != "epsilon_converged" or latest_stop.get("converged") is not True:
        failures.append("latest_stage_stop_is_not_epsilon_converged")
    if any(row.get("reason") != "epsilon_converged" for row in stops):
        failures.append("non_epsilon_stage_stop_present")
    if final.get("converged") is not True or final.get("stop_reason") != "epsilon_converged":
        failures.append("final_artifact_is_not_epsilon_converged")
    range_checks: Dict[str, Dict[str, Any]] = {}
    final_ranges = final.get("final_ranges") or {}
    epsilon = final.get("epsilon")
    for name, bounds in final_ranges.items():
        width = (
            interval_search_width(float(bounds[0]), float(bounds[1]))
            if isinstance(bounds, list) and len(bounds) == 2
            else None
        )
        threshold = epsilon_for(epsilon, name)
        passed = width is not None and threshold is not None and width <= threshold + 1e-15
        range_checks[name] = {"width": width, "epsilon": threshold, "passed": passed}
    if not range_checks or not all(item["passed"] for item in range_checks.values()):
        failures.append("one_or_more_final_ranges_exceed_epsilon")
    final_goal_details: Optional[Dict[str, Any]] = None
    if len(confirmations) != 1:
        failures.append(f"post_convergence_final_validation_count={len(confirmations)}, expected=1")
    else:
        confirmation = confirmations[0]
        if confirmation.get("selected_label") != reference.get("label"):
            failures.append("final_confirmation_selected_label_mismatch")
        if confirmation.get("params") != reference.get("params"):
            failures.append("final_confirmation_params_mismatch")
        if confirmation.get("selection_changed_by_confirmation") is not False:
            failures.append("final_confirmation_changed_selection")
        if not stable_samples_pass(confirmation):
            failures.append("final_confirmation_stable_samples_failed")
        if not latency_percentiles_pass(confirmation) or (number(confirmation.get("p90_ms")) or 0.0) <= 0.0:
            failures.append("final_confirmation_latency_missing_or_nonpositive")
        final_goal_details = nested_final_goal_audit_details(confirmation, baseline, target)
        logged_goal = confirmation.get("final_goal") or {}
        for field in (
            "goal_reached",
            "qps_goal_reached",
            "target_down",
            "target_drop_in_range",
            "target_not_bottleneck",
            "target_in_sweet_spot",
            "any_other_resource_up",
        ):
            if logged_goal.get(field) is not final_goal_details.get(field):
                failures.append(f"final_goal_{field}_mismatch")
        for field in ("db_qps_gain_pct", "target_drop_pct", "target_utilization_pct"):
            logged_value = number(logged_goal.get(field))
            expected_value = number(final_goal_details.get(field))
            if logged_value is None and expected_value is None:
                continue
            if (
                logged_value is None
                or expected_value is None
                or not math.isclose(logged_value, expected_value, rel_tol=0.0, abs_tol=1e-9)
            ):
                failures.append(f"final_goal_{field}_mismatch")
        if (final.get("final_goal") or {}).get("goal_reached") is not final_goal_details["goal_reached"]:
            failures.append("final_artifact_goal_reached_mismatch")
        final_confirmation_artifact = final.get("post_convergence_final_validation") or {}
        if final_confirmation_artifact.get("label") != confirmation.get("label"):
            failures.append("final_artifact_confirmation_mismatch")
    strategy_point = final.get("strategy_final_point") or {}
    if strategy_point.get("label") != reference.get("label"):
        failures.append("strategy_final_point_does_not_match_last_outer_accepted")
    return {
        "target": target,
        "expected_run_mins": expected_run_mins,
        "complete": not failures,
        "failures": failures,
        "required_rounds": int(start.get("required_rounds_for_epsilon") or 0),
        "completed_rounds": len(rounds),
        "outer_accepted_count": accepted_count,
        "expected_points_per_round": int(start.get("full_round_search_points") or 0),
        "expected_real_replays_per_round": expected_topk,
        "experiment_strategy": "NESTED",
        "range_anchor_policy": NESTED_RESOURCE_SEMANTIC_POLICY,
        "round_completeness": round_checks,
        "round_anchor_selection_failure_count": len(
            [item for item in round_checks if not item["passed"]]
        ),
        "candidate_validation_count": len(candidates),
        "accepted_candidate_count": accepted_count,
        "score_b_successful_candidate_count": 0,
        "candidate_replay_integrity_failure_count": len(raw_alignment_failures),
        "candidate_acceptance_decision_failure_count": 0,
        "candidate_comparison_baseline_failure_count": 0,
        "validation_warmup_lifecycle_failure_count": len(warmup_failures),
        "raw_replay_alignment_failure_count": len(raw_alignment_failures),
        "final_point_consistency_failure_count": len(failures),
        "raw_replay_row_count": len(raw_rows),
        "malformed_search_log_lines": malformed_log_lines,
        "malformed_raw_log_lines": malformed_raw_lines,
        "baseline_reference": baseline,
        "latest_stop_reason": latest_stop.get("reason"),
        "non_epsilon_stage_stop_count": len(
            [row for row in stops if row.get("reason") != "epsilon_converged"]
        ),
        "final_converged": final.get("converged"),
        "range_checks": range_checks,
        "best_accepted_point": reference if reference is not baseline else None,
        "final_output_point": final.get("final_output_point"),
        "final_observed_point": final.get("final_observed_point"),
        "final_range_anchor_point": final.get("final_range_anchor_point"),
        "strategy_final_point": final.get("strategy_final_point"),
        "global_score_b_best": None,
        "global_score_b_best_details": None,
        "post_convergence_final_validation": final.get("post_convergence_final_validation"),
        "final_goal": final_goal_details,
        "artifacts": {
            "search_log": str(log_path),
            "raw_log": str(raw_path),
            "best": str(best_path),
            "final": str(final_path),
        },
    }


def audit_target(
    run_root: Path,
    target: str,
    expected_run_mins: Optional[float],
    *,
    experiment_strategy: str,
    expected_anchor_policy: str,
    expected_topk: int,
) -> Dict[str, Any]:
    if experiment_strategy == "NESTED":
        return audit_nested_target(
            run_root,
            target,
            expected_run_mins,
            expected_topk=expected_topk,
        )
    target_dir = run_root / target
    log_path = target_dir / f"{target}_binary_search_hybenchxp.jsonl"
    raw_path = target_dir / f"{target}_vm_replay_raw.jsonl"
    final_path = target_dir / f"final_for_{target}.json"
    best_path = target_dir / f"best_for_{target}.json"
    rolling_path = target_dir / f"best_so_far_for_{target}.json"
    controller_path = target_dir / "controller.log"
    required_paths = (log_path, raw_path, final_path, best_path, rolling_path, controller_path)
    failures: List[str] = []
    missing = [str(path) for path in required_paths if not path.is_file() or path.stat().st_size <= 0]
    if missing:
        failures.append("missing_or_empty_artifacts: " + ", ".join(missing))

    rows, malformed_log_lines = load_jsonl(log_path)
    raw_rows, malformed_raw_lines = load_jsonl(raw_path)
    try:
        controller_text = controller_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        controller_text = ""
    if malformed_log_lines:
        failures.append(f"malformed_search_log_lines={malformed_log_lines}")
    if malformed_raw_lines:
        failures.append(f"malformed_raw_log_lines={malformed_raw_lines}")

    starts = [row for row in rows if row.get("event") == "stage_start"]
    baselines = [row for row in rows if row.get("event") == "baseline_validation"]
    candidates = [row for row in rows if row.get("event") == "candidate_validation"]
    confirmations = [row for row in rows if row.get("event") == "strategy_final_confirmation"]
    rounds = [row for row in rows if row.get("event") == "round_summary"]
    search_audits = [row for row in rows if row.get("event") == "round_search_points_audit"]
    stops = [row for row in rows if row.get("event") == "stage_stop"]
    snapshots = [row for row in rows if row.get("event") == "final_point_snapshot"]
    summaries = [row for row in rows if row.get("event") == "stage_summary"]
    accepted = [
        row
        for row in candidates
        if row.get("accepted") is True and str(row.get("status") or "").lower() == "ok"
    ]
    validations = [
        *baselines,
        *candidates,
        *(confirmations if experiment_strategy == "C" else []),
    ]
    warmup_failures = warmup_lifecycle_failures(
        controller_text,
        validations,
        target,
    )
    if warmup_failures:
        failures.append(
            "validation_warmup_lifecycle_failed: " + "; ".join(warmup_failures)
        )
    raw_alignment_failures = raw_replay_alignment_failures(
        raw_rows,
        validations,
        target,
        expected_run_mins,
    )
    if raw_alignment_failures:
        failures.append(
            "raw_replay_alignment_failed: " + "; ".join(raw_alignment_failures)
        )
    best_accepted = max(accepted, key=lambda row: number(row.get("db_qps")) or float("-inf"), default=None)
    start = starts[-1] if starts else {}
    latest_stop = stops[-1] if stops else {}
    final = load_json(final_path) or {}
    best_artifact = load_json(best_path) or {}
    rolling = load_json(rolling_path) or {}
    required_rounds = int(start.get("required_rounds_for_epsilon") or 0)
    completed_rounds = len({int(row.get("round") or 0) for row in rounds if row.get("round")})
    expected_points_per_round = int(start.get("full_round_search_points") or 0)
    expected_replays_per_round = expected_topk
    active_params = [str(name) for name in (start.get("active_params") or [])]
    candidate_counts_by_round: Dict[int, int] = {}
    for row in candidates:
        round_number = int(row.get("round") or 0)
        if round_number > 0:
            candidate_counts_by_round[round_number] = candidate_counts_by_round.get(round_number, 0) + 1
    valid_baselines = [row for row in baselines if str(row.get("status") or "").lower() == "ok"]
    baseline_reference = valid_baselines[-1] if valid_baselines else None
    search_audits_by_round: Dict[int, List[Dict[str, Any]]] = {}
    for search_audit in search_audits:
        audit_round = int(search_audit.get("round") or 0)
        if audit_round > 0:
            search_audits_by_round.setdefault(audit_round, []).append(search_audit)
    round_completeness: List[Dict[str, Any]] = []
    round_anchor_selection_failures: List[str] = []
    for row in rounds:
        round_number = int(row.get("round") or 0)
        round_candidates = [
            candidate
            for candidate in candidates
            if int(candidate.get("round") or 0) == round_number
        ]
        logged_candidates = candidate_counts_by_round.get(round_number, 0)
        fields = {
            name: int(row.get(name) or 0)
            for name in (
                "candidate_count",
                "raw_search_point_count",
                "topk_candidate_count",
                "validation_count",
                "real_validation_count",
            )
        }
        previous_ranges = row.get("previous_ranges") or {}
        updated_ranges = row.get("updated_ranges") or {}
        topk_ranks = [int(candidate.get("topk_rank") or 0) for candidate in round_candidates]
        topk_rank_sequence_ok = bool(
            expected_replays_per_round > 0
            and sorted(topk_ranks) == list(range(1, expected_replays_per_round + 1))
        )
        mask_sequence_ok = bool(active_params)
        parameter_values_complete_ok = bool(active_params)
        parameter_signatures = set()
        for candidate in round_candidates:
            candidate_index = int(
                candidate.get("raw_candidate_index") or candidate.get("candidate_index") or 0
            )
            expected_mask = "".join(
                "R"
                if ((candidate_index - 1) >> (len(active_params) - position - 1)) & 1
                else "L"
                for position in range(len(active_params))
            )
            if candidate_index <= 0 or candidate.get("mask") != expected_mask:
                mask_sequence_ok = False
            params = candidate.get("params") or {}
            signature = tuple(number(params.get(name)) for name in active_params)
            if any(value is None for value in signature):
                parameter_values_complete_ok = False
            parameter_signatures.add(signature)
        unique_parameter_combinations_ok = bool(
            expected_replays_per_round > 0
            and parameter_values_complete_ok
            and len(parameter_signatures) == expected_replays_per_round
        )
        round_search_audits = search_audits_by_round.get(round_number, [])
        search_audit = round_search_audits[-1] if round_search_audits else {}
        search_points = search_audit.get("points") if isinstance(search_audit.get("points"), list) else []
        search_raw_indices = sorted(int(point.get("raw_candidate_index") or 0) for point in search_points)
        search_space_complete_ok = bool(
            len(round_search_audits) == 1
            and len(search_points) == expected_points_per_round
            and search_raw_indices == list(range(1, expected_points_per_round + 1))
        )
        selected_search_points = [point for point in search_points if point.get("selected_by_candidate_cap") is True]
        selected_search_topk = sorted(int(point.get("topk_rank") or 0) for point in selected_search_points)
        search_topk_complete_ok = bool(
            len(selected_search_points) == expected_replays_per_round
            and selected_search_topk == list(range(1, expected_replays_per_round + 1))
        )
        small_loop_scored_points = [
            point
            for point in search_points
            if isinstance(point.get("plan_probe"), dict)
            and isinstance((point.get("plan_probe") or {}).get("topk_rank_key"), list)
            and (point.get("plan_probe") or {}).get("topk_rank_key")
        ]
        small_loop_scores_complete_ok = len(small_loop_scored_points) == expected_points_per_round
        small_loop_top1_point = next(
            (point for point in selected_search_points if int(point.get("topk_rank") or 0) == 1),
            None,
        )
        if small_loop_top1_point is not None and small_loop_scores_complete_ok:
            recomputed_small_loop_top1 = max(
                small_loop_scored_points,
                key=lambda point: tuple((point.get("plan_probe") or {}).get("topk_rank_key") or []),
            )
            small_loop_top1_recomputed_ok = (
                recomputed_small_loop_top1.get("raw_candidate_index")
                == small_loop_top1_point.get("raw_candidate_index")
            )
        else:
            small_loop_top1_recomputed_ok = False
        range_shrink_checks: Dict[str, bool] = {}
        for name in active_params:
            previous = previous_ranges.get(name)
            updated = updated_ranges.get(name)
            previous_width: Optional[float] = None
            updated_width: Optional[float] = None
            if isinstance(previous, list) and len(previous) == 2:
                previous_low, previous_high = number(previous[0]), number(previous[1])
                if previous_low is not None and previous_high is not None:
                    previous_width = interval_search_width(previous_low, previous_high)
            if isinstance(updated, list) and len(updated) == 2:
                updated_low, updated_high = number(updated[0]), number(updated[1])
                if updated_low is not None and updated_high is not None:
                    updated_width = interval_search_width(updated_low, updated_high)
            range_shrink_checks[name] = bool(
                previous_width is not None
                and updated_width is not None
                and previous_width > 0
                and updated_width <= previous_width / 2.0 + max(1e-15, previous_width * 1e-12)
            )
        anchor_failures: List[str] = []
        expected_anchor: Optional[Dict[str, Any]] = None
        expected_anchor_details: Optional[Dict[str, Any]] = None
        expected_range_update_anchor = ""
        if baseline_reference is None:
            anchor_failures.append("valid_fixed_baseline_missing")
        elif experiment_strategy == "A":
            expected_anchor = min(
                round_candidates,
                key=lambda candidate: int(candidate.get("topk_rank") or 10**9),
                default=None,
            )
            expected_range_update_anchor = "small_loop_top1"
            if expected_anchor is None or int(expected_anchor.get("topk_rank") or 0) != 1:
                anchor_failures.append("small_loop_top1_real_replay_missing")
        elif experiment_strategy == "B":
            ranked_round_candidates: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
            for candidate in round_candidates:
                details = real_score_b_audit_details(candidate, baseline_reference, target)
                if details is not None:
                    logged_score = candidate.get("real_score_b") or {}
                    for field in (
                        "qps_gain",
                        "target_progress",
                        "target_drop_ratio",
                        "bonus_sum",
                        "score_b",
                    ):
                        expected_value = number(details.get(field))
                        logged_value = number(logged_score.get(field))
                        if expected_value is None and logged_value is None:
                            continue
                        if (
                            expected_value is None
                            or logged_value is None
                            or not math.isclose(expected_value, logged_value, rel_tol=0.0, abs_tol=1e-9)
                        ):
                            anchor_failures.append(
                                f"candidate={candidate.get('label')}: real_score_b.{field}="
                                f"{logged_value}, expected={expected_value}"
                            )
                    for field in ("successful", "qps_gate", "resource_gate", "target_drop_gate"):
                        if logged_score.get(field) is not details.get(field):
                            anchor_failures.append(
                                f"candidate={candidate.get('label')}: real_score_b.{field}="
                                f"{logged_score.get(field)!r}, expected={details.get(field)!r}"
                            )
                if details is not None and details.get("successful"):
                    ranked_round_candidates.append((candidate, details))
            if ranked_round_candidates:
                expected_anchor, expected_anchor_details = max(
                    ranked_round_candidates,
                    key=lambda item: real_score_b_audit_key(item[0], item[1]),
                )
                expected_range_update_anchor = "real_score_b_successful_argmax"
            else:
                expected_anchor = min(
                    round_candidates,
                    key=lambda candidate: int(candidate.get("topk_rank") or 10**9),
                    default=None,
                )
                expected_range_update_anchor = "real_score_b_no_success_fallback_small_loop_top1"
            if int(row.get("score_b_successful_candidate_count") or 0) != len(ranked_round_candidates):
                anchor_failures.append(
                    "score_b_successful_candidate_count="
                    f"{row.get('score_b_successful_candidate_count')!r}, "
                    f"expected={len(ranked_round_candidates)}"
                )
            expected_argmax_label = (
                expected_anchor.get("label") if ranked_round_candidates and expected_anchor else None
            )
            if row.get("score_b_argmax_label") != expected_argmax_label:
                anchor_failures.append(
                    f"score_b_argmax_label={row.get('score_b_argmax_label')!r}, "
                    f"expected={expected_argmax_label!r}"
                )
        elif experiment_strategy == "C":
            ranked_round_candidates: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
            successful_count = 0
            for candidate in round_candidates:
                details = real_score_c_audit_details(candidate, baseline_reference, target)
                if details is None:
                    continue
                ranked_round_candidates.append((candidate, details))
                if details.get("successful"):
                    successful_count += 1
                logged_score = candidate.get("real_score_c") or {}
                for field in ("target_progress", "target_drop_ratio", "bonus_sum", "score_c"):
                    expected_value = number(details.get(field))
                    logged_value = number(logged_score.get(field))
                    if expected_value is None and logged_value is None:
                        continue
                    if (
                        expected_value is None
                        or logged_value is None
                        or not math.isclose(expected_value, logged_value, rel_tol=0.0, abs_tol=1e-9)
                    ):
                        anchor_failures.append(
                            f"candidate={candidate.get('label')}: real_score_c.{field}="
                            f"{logged_value}, expected={expected_value}"
                        )
                for field in ("valid", "successful", "resource_gate", "target_drop_gate"):
                    if logged_score.get(field) is not details.get(field):
                        anchor_failures.append(
                            f"candidate={candidate.get('label')}: real_score_c.{field}="
                            f"{logged_score.get(field)!r}, expected={details.get(field)!r}"
                        )
                if logged_score.get("qps_considered") is not False or logged_score.get("qps_gate") is not None:
                    anchor_failures.append(
                        f"candidate={candidate.get('label')}: real_score_c_considered_qps"
                    )
            if ranked_round_candidates:
                expected_anchor, expected_anchor_details = max(
                    ranked_round_candidates,
                    key=lambda item: real_score_c_audit_key(item[0], item[1]),
                )
                expected_range_update_anchor = (
                    "real_score_c_successful_argmax"
                    if expected_anchor_details.get("successful")
                    else "real_score_c_no_success_fallback_valid_argmax"
                )
            else:
                anchor_failures.append("real_score_c_has_no_valid_real_candidate")
            if int(row.get("score_c_valid_candidate_count") or 0) != len(ranked_round_candidates):
                anchor_failures.append(
                    f"score_c_valid_candidate_count={row.get('score_c_valid_candidate_count')!r}, "
                    f"expected={len(ranked_round_candidates)}"
                )
            if int(row.get("score_c_successful_candidate_count") or 0) != successful_count:
                anchor_failures.append(
                    "score_c_successful_candidate_count="
                    f"{row.get('score_c_successful_candidate_count')!r}, expected={successful_count}"
                )
            expected_argmax_label = expected_anchor.get("label") if expected_anchor else None
            if row.get("score_c_argmax_label") != expected_argmax_label:
                anchor_failures.append(
                    f"score_c_argmax_label={row.get('score_c_argmax_label')!r}, "
                    f"expected={expected_argmax_label!r}"
                )
        else:
            anchor_failures.append(f"unsupported_experiment_strategy={experiment_strategy!r}")
        logged_anchor_point = row.get("range_anchor_point") or {}
        logged_anchor_details = row.get("range_anchor_details") or {}
        expected_anchor_label = expected_anchor.get("label") if expected_anchor else None
        if row.get("range_anchor_policy") != expected_anchor_policy:
            anchor_failures.append(
                f"range_anchor_policy={row.get('range_anchor_policy')!r}"
            )
        if row.get("range_update_anchor") != expected_range_update_anchor:
            anchor_failures.append(
                f"range_update_anchor={row.get('range_update_anchor')!r}, "
                f"expected={expected_range_update_anchor!r}"
            )
        if row.get("zero_accepted_backtrack") is not False:
            anchor_failures.append("zero_accepted_backtrack_is_not_false")
        if expected_anchor is not None:
            if row.get("range_anchor_label") != expected_anchor_label:
                anchor_failures.append(
                    f"range_anchor_label={row.get('range_anchor_label')!r}, "
                    f"expected={expected_anchor_label!r}"
                )
            if logged_anchor_point.get("label") != expected_anchor_label:
                anchor_failures.append(
                    f"range_anchor_point.label={logged_anchor_point.get('label')!r}, "
                    f"expected={expected_anchor_label!r}"
                )
            if logged_anchor_point.get("params") != expected_anchor.get("params"):
                anchor_failures.append("range_anchor_point.params_do_not_match_recomputed_winner")
        if logged_anchor_details.get("policy") != expected_anchor_policy:
            anchor_failures.append(
                f"range_anchor_details.policy={logged_anchor_details.get('policy')!r}"
            )
        if experiment_strategy == "A":
            if logged_anchor_details.get("real_metrics_used_for_anchor_selection") is not False:
                anchor_failures.append("strategy_A_used_real_metrics_for_anchor_selection")
            expected_small_score = (
                (small_loop_top1_point.get("plan_probe") or {}).get("topk_rank_key")
                if small_loop_top1_point is not None
                else None
            )
            if logged_anchor_details.get("small_loop_score") != expected_small_score:
                anchor_failures.append(
                    "strategy_A_logged_small_loop_score_does_not_match_recomputed_top1"
                )
        if experiment_strategy == "B" and expected_anchor_details is not None:
            for field in (
                "db_qps",
                "qps_gain",
                "db_qps_gain_pct",
                "target_progress",
                "target_drop_pct",
                "bonus_sum",
                "score_b",
            ):
                expected_value = number(expected_anchor_details.get(field))
                logged_value = number(logged_anchor_details.get(field))
                if expected_value is None and logged_value is None:
                    continue
                if (
                    expected_value is None
                    or logged_value is None
                    or not math.isclose(expected_value, logged_value, rel_tol=0.0, abs_tol=1e-9)
                ):
                    anchor_failures.append(
                        f"range_anchor_details.{field}={logged_value}, expected={expected_value}"
                    )
            if logged_anchor_details.get("successful") is not expected_anchor_details.get("successful"):
                anchor_failures.append(
                    f"range_anchor_details.successful={logged_anchor_details.get('successful')!r}, "
                    f"expected={expected_anchor_details.get('successful')!r}"
                )
        if experiment_strategy == "C" and expected_anchor_details is not None:
            for field in ("target_progress", "target_drop_pct", "bonus_sum", "score_c"):
                expected_value = number(expected_anchor_details.get(field))
                logged_value = number(logged_anchor_details.get(field))
                if expected_value is None and logged_value is None:
                    continue
                if (
                    expected_value is None
                    or logged_value is None
                    or not math.isclose(expected_value, logged_value, rel_tol=0.0, abs_tol=1e-9)
                ):
                    anchor_failures.append(
                        f"range_anchor_details.{field}={logged_value}, expected={expected_value}"
                    )
            if logged_anchor_details.get("successful") is not expected_anchor_details.get("successful"):
                anchor_failures.append(
                    f"range_anchor_details.successful={logged_anchor_details.get('successful')!r}, "
                    f"expected={expected_anchor_details.get('successful')!r}"
                )
            if logged_anchor_details.get("qps_considered") is not False:
                anchor_failures.append("range_anchor_details_C_considered_qps")
        if anchor_failures:
            round_anchor_selection_failures.extend(
                f"round={round_number}: {failure}" for failure in anchor_failures
            )
        passed = bool(
            round_number > 0
            and expected_points_per_round > 0
            and expected_replays_per_round > 0
            and logged_candidates == expected_replays_per_round
            and fields.get("candidate_count") == expected_points_per_round
            and fields.get("raw_search_point_count") == expected_points_per_round
            and fields.get("topk_candidate_count") == expected_replays_per_round
            and fields.get("validation_count") == expected_replays_per_round
            and fields.get("real_validation_count") == expected_replays_per_round
            and topk_rank_sequence_ok
            and mask_sequence_ok
            and unique_parameter_combinations_ok
            and search_space_complete_ok
            and search_topk_complete_ok
            and small_loop_scores_complete_ok
            and small_loop_top1_recomputed_ok
            and int(row.get("replay_pruned_count") or 0) == 0
            and row.get("stop_only_on_epsilon") is True
            and "expand" not in str(row.get("range_update_strategy") or "")
            and not anchor_failures
            and bool(range_shrink_checks)
            and all(range_shrink_checks.values())
        )
        round_completeness.append(
            {
                "round": round_number,
                "expected_small_loop_points": expected_points_per_round,
                "expected_real_replays": expected_replays_per_round,
                "logged_candidate_validations": logged_candidates,
                "summary_counts": fields,
                "topk_rank_sequence_ok": topk_rank_sequence_ok,
                "mask_sequence_ok": mask_sequence_ok,
                "parameter_values_complete_ok": parameter_values_complete_ok,
                "unique_parameter_combination_count": len(parameter_signatures),
                "unique_parameter_combinations_ok": unique_parameter_combinations_ok,
                "search_space_complete_ok": search_space_complete_ok,
                "search_topk_complete_ok": search_topk_complete_ok,
                "small_loop_scores_complete_ok": small_loop_scores_complete_ok,
                "small_loop_top1_recomputed_ok": small_loop_top1_recomputed_ok,
                "replay_pruned_count": int(row.get("replay_pruned_count") or 0),
                "range_update_strategy": row.get("range_update_strategy"),
                "range_anchor_policy": row.get("range_anchor_policy"),
                "range_anchor_label": row.get("range_anchor_label"),
                "recomputed_range_anchor_label": expected_anchor_label,
                "range_anchor_selection_failures": anchor_failures,
                "range_shrink_checks": range_shrink_checks,
                "passed": passed,
            }
        )

    if not starts:
        failures.append("stage_start_missing")
    elif start.get("stop_only_on_epsilon") is not True:
        failures.append("stage_start_stop_policy_is_not_epsilon_only")
    if starts and start.get("range_anchor_policy") != expected_anchor_policy:
        failures.append(
            f"stage_start_range_anchor_policy={start.get('range_anchor_policy')!r}, "
            f"expected={expected_anchor_policy!r}"
        )
    if starts and str(start.get("experiment_strategy") or "").upper() != experiment_strategy:
        failures.append(
            f"stage_start_experiment_strategy={start.get('experiment_strategy')!r}, "
            f"expected={experiment_strategy!r}"
        )
    if starts and int(start.get("candidate_topk_limit") or 0) != expected_replays_per_round:
        failures.append(
            f"stage_start_candidate_topk_limit={start.get('candidate_topk_limit')!r}, "
            f"expected={expected_replays_per_round}"
        )
    score_b_start_cfg = start.get("real_score_b") if isinstance(start.get("real_score_b"), dict) else {}
    if number(score_b_start_cfg.get("resource_up_scale")) != 0.10:
        failures.append("stage_start_real_score_b_resource_up_scale_is_not_0.10")
    if number(score_b_start_cfg.get("bonus_coefficient")) != 0.02:
        failures.append("stage_start_real_score_b_bonus_coefficient_is_not_plus_0.02")
    score_c_start_cfg = start.get("real_score_c") if isinstance(start.get("real_score_c"), dict) else {}
    if number(score_c_start_cfg.get("resource_up_scale")) != 0.10:
        failures.append("stage_start_real_score_c_resource_up_scale_is_not_0.10")
    if number(score_c_start_cfg.get("bonus_coefficient")) != 0.02:
        failures.append("stage_start_real_score_c_bonus_coefficient_is_not_plus_0.02")
    if score_c_start_cfg.get("qps_considered") is not False:
        failures.append("stage_start_real_score_c_qps_considered_is_not_false")
    if not valid_baselines:
        failures.append("valid_baseline_missing")
    else:
        baseline = valid_baselines[-1]
        if not stable_samples_pass(baseline):
            failures.append("valid_baseline_stable_sample_gate_failed")
        if not latency_percentiles_pass(baseline):
            failures.append("valid_baseline_latency_percentiles_missing_or_nonpositive")
        baseline_run_mins = number(((baseline.get("hybench") or {}).get("run_config") or {}).get("xpRunMins"))
        if expected_run_mins is None or baseline_run_mins != expected_run_mins:
            failures.append(
                f"baseline_run_mins={baseline_run_mins}, expected_run_mins={expected_run_mins}"
            )
        baseline_replay_failures = replay_validation_failures(
            baseline, target, expected_run_mins
        )
        if baseline_replay_failures:
            failures.append(
                "baseline_replay_integrity_failed: " + "; ".join(baseline_replay_failures)
            )
    if not candidates:
        failures.append("candidate_history_missing")
    candidate_replay_failures: List[str] = []
    acceptance_decision_failures: List[str] = []
    comparison_baseline_failures: List[str] = []
    for candidate in candidates:
        candidate_replay_failures.extend(
            replay_validation_failures(candidate, target, expected_run_mins)
        )
        decision = candidate.get("decision") or {}
        qps_gain = number(decision.get("db_qps_delta_pct"))
        target_drop = number(decision.get("target_drop_pct"))
        none_resource_up = any(
            number((candidate.get("resources") or {}).get(resource)) is not None
            and number((baseline_reference or {}).get("resources", {}).get(resource)) not in (None, 0.0)
            and float((candidate.get("resources") or {})[resource])
            > float((baseline_reference or {}).get("resources", {})[resource])
            for resource in ("cpu", "memory", "io")
        )
        should_accept = bool(
            qps_gain is not None
            and qps_gain >= 15.0 - 1e-12
            and (
                (target == "none" and none_resource_up)
                or (
                    target != "none"
                    and
                    target_drop is not None
                    and target_drop >= 5.0 - 1e-12
                    and target_drop <= 10.0 + 1e-12
                )
            )
        )
        actually_accepted = candidate.get("accepted") is True
        if actually_accepted != should_accept:
            acceptance_decision_failures.append(
                f"{candidate.get('label')}: accepted={actually_accepted} "
                f"expected={should_accept} qps_gain={qps_gain} target_drop={target_drop}"
            )
        if baseline_reference is not None:
            label = str(candidate.get("label") or "unlabeled")
            baseline_qps = number(baseline_reference.get("db_qps"))
            candidate_qps = number(candidate.get("db_qps"))
            expected_qps_gain = (
                (candidate_qps - baseline_qps) / baseline_qps * 100.0
                if candidate_qps is not None and baseline_qps not in (None, 0.0)
                else None
            )
            if candidate.get("comparison_baseline_label") != f"{target}_baseline":
                comparison_baseline_failures.append(
                    f"{label}: comparison_baseline_label={candidate.get('comparison_baseline_label')}"
                )
            logged_baseline_qps = number(decision.get("best_db_qps"))
            if (
                baseline_qps is None
                or logged_baseline_qps is None
                or not math.isclose(logged_baseline_qps, baseline_qps, rel_tol=0.0, abs_tol=1e-9)
            ):
                comparison_baseline_failures.append(
                    f"{label}: logged_baseline_qps={logged_baseline_qps} baseline={baseline_qps}"
                )
            if (
                qps_gain is None
                or expected_qps_gain is None
                or not math.isclose(qps_gain, expected_qps_gain, rel_tol=0.0, abs_tol=1e-9)
            ):
                comparison_baseline_failures.append(
                    f"{label}: qps_gain={qps_gain} recomputed={expected_qps_gain}"
                )
            if target != "none":
                baseline_target = number((baseline_reference.get("resources") or {}).get(target))
                candidate_target = number((candidate.get("resources") or {}).get(target))
                expected_target_drop = (
                    (baseline_target - candidate_target) / baseline_target * 100.0
                    if candidate_target is not None and baseline_target not in (None, 0.0)
                    else None
                )
                logged_baseline_target = number(decision.get("best_target"))
                if (
                    baseline_target is None
                    or logged_baseline_target is None
                    or not math.isclose(
                        logged_baseline_target, baseline_target, rel_tol=0.0, abs_tol=1e-9
                    )
                ):
                    comparison_baseline_failures.append(
                        f"{label}: logged_baseline_target={logged_baseline_target} "
                        f"baseline={baseline_target}"
                    )
                if (
                    target_drop is None
                    or expected_target_drop is None
                    or not math.isclose(
                        target_drop, expected_target_drop, rel_tol=0.0, abs_tol=1e-9
                    )
                ):
                    comparison_baseline_failures.append(
                        f"{label}: target_drop={target_drop} recomputed={expected_target_drop}"
                    )
    if candidate_replay_failures:
        failures.append(
            "candidate_replay_integrity_failed: " + "; ".join(candidate_replay_failures)
        )
    if acceptance_decision_failures:
        failures.append(
            "candidate_acceptance_decision_failed: "
            + "; ".join(acceptance_decision_failures)
        )
    if comparison_baseline_failures:
        failures.append(
            "candidate_comparison_baseline_failed: "
            + "; ".join(comparison_baseline_failures)
        )
    # `required_rounds_for_epsilon` is only a launch-time estimate. Positive
    # cost ranges are bisected in log space while the terminal epsilon is an
    # absolute linear width, so the estimate is neither a strict minimum nor
    # an exact stopping round. Completion is proven below from the recorded
    # epsilon stop and every final range's actual width.
    if required_rounds <= 0:
        failures.append("required_rounds_for_epsilon_missing")
    if completed_rounds <= 0:
        failures.append("no_completed_binary_search_rounds")
    if expected_points_per_round <= 0:
        failures.append("full_round_search_points_missing")
    if expected_replays_per_round <= 0:
        failures.append("expected_topk_real_replays_missing")
    if len(round_completeness) != completed_rounds or not all(
        item["passed"] for item in round_completeness
    ):
        failures.append("one_or_more_completed_rounds_fail_small_loop_128_and_topk_real_replay_audit")
    if round_anchor_selection_failures:
        failures.append(
            "per_round_range_anchor_selection_failed: "
            + "; ".join(round_anchor_selection_failures)
        )
    if latest_stop.get("reason") != "epsilon_converged" or latest_stop.get("converged") is not True:
        failures.append("latest_stage_stop_is_not_epsilon_converged")
    non_epsilon_stops = [
        str(row.get("reason") or "missing_reason")
        for row in stops
        if row.get("reason") != "epsilon_converged"
    ]
    if non_epsilon_stops:
        failures.append("non_epsilon_stage_stop_present: " + ", ".join(non_epsilon_stops))
    if final.get("converged") is not True or final.get("stop_reason") != "epsilon_converged":
        failures.append("final_artifact_is_not_epsilon_converged")
    if best_artifact.get("event") != "stage_summary":
        failures.append("best_artifact_is_not_a_stage_summary")
    if not snapshots or not summaries:
        failures.append("final_snapshot_or_stage_summary_missing_from_search_log")
    for field in (
        "best_point",
        "final_output_point",
        "final_observed_point",
        "final_range_anchor_point",
    ):
        point = final.get(field)
        if not isinstance(point, dict) or not point.get("label"):
            failures.append(f"{field}_missing_or_empty")
    if len(raw_rows) < len(validations):
        failures.append(f"raw_replay_rows={len(raw_rows)} below validations={len(validations)}")

    range_checks: Dict[str, Dict[str, Any]] = {}
    final_ranges = final.get("final_ranges") if isinstance(final.get("final_ranges"), dict) else {}
    epsilon = final.get("epsilon")
    for name, bounds in final_ranges.items():
        width: Optional[float] = None
        if isinstance(bounds, list) and len(bounds) == 2:
            low = number(bounds[0])
            high = number(bounds[1])
            if low is not None and high is not None:
                width = high - low
        threshold = epsilon_for(epsilon, name)
        passed = width is not None and threshold is not None and width <= threshold + 1e-15
        range_checks[name] = {"width": width, "epsilon": threshold, "passed": passed}
    if not range_checks or not all(item["passed"] for item in range_checks.values()):
        failures.append("one_or_more_final_ranges_exceed_epsilon")

    if best_accepted is not None:
        if not stable_samples_pass(best_accepted):
            failures.append("best_accepted_point_stable_sample_gate_failed")
        if not latency_percentiles_pass(best_accepted):
            failures.append("best_accepted_point_latency_percentiles_missing_or_nonpositive")
        best_run_mins = number(
            ((best_accepted.get("hybench") or {}).get("run_config") or {}).get("xpRunMins")
        )
        if expected_run_mins is None or best_run_mins != expected_run_mins:
            failures.append(
                f"best_accepted_run_mins={best_run_mins}, expected_run_mins={expected_run_mins}"
            )
        decision = best_accepted.get("decision") or {}
        qps_gain = number(decision.get("db_qps_delta_pct"))
        target_drop = number(decision.get("target_drop_pct"))
        if qps_gain is None or qps_gain < 15.0 - 1e-12:
            failures.append("best_point_db_qps_gain_below_15pct")
        if target != "none" and (
            target_drop is None or target_drop < 5.0 - 1e-12 or target_drop > 10.0 + 1e-12
        ):
            failures.append("best_point_target_drop_outside_5_to_10pct")
        if target == "none" and baseline_reference is not None:
            none_resource_up = any(
                number((best_accepted.get("resources") or {}).get(resource)) is not None
                and number((baseline_reference.get("resources") or {}).get(resource)) not in (None, 0.0)
                and float((best_accepted.get("resources") or {})[resource])
                > float((baseline_reference.get("resources") or {})[resource])
                for resource in ("cpu", "memory", "io")
            )
            if not none_resource_up:
                failures.append("best_point_none_has_no_resource_increase")
        rolling_best = rolling.get("best_accepted_point") or {}
        if rolling_best.get("label") != best_accepted.get("label"):
            failures.append("rolling_best_does_not_match_highest_qps_accepted_point")

    final_point_consistency_failures: List[str] = []
    latest_snapshot = snapshots[-1] if snapshots else {}
    latest_summary = summaries[-1] if summaries else {}
    successful_score_b_candidates: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    if baseline_reference is not None:
        for candidate in candidates:
            details = real_score_b_audit_details(candidate, baseline_reference, target)
            if details is not None and details.get("successful"):
                successful_score_b_candidates.append((candidate, details))
    expected_global_score_b_candidate: Optional[Dict[str, Any]] = None
    expected_global_score_b_details: Optional[Dict[str, Any]] = None
    if successful_score_b_candidates:
        expected_global_score_b_candidate, expected_global_score_b_details = max(
            successful_score_b_candidates,
            key=lambda item: real_score_b_audit_key(item[0], item[1]),
        )
    if experiment_strategy == "B":
        expected_global_label = (
            expected_global_score_b_candidate.get("label")
            if expected_global_score_b_candidate is not None
            else None
        )
        for source_name, source in (
            ("final", final),
            ("best_artifact", best_artifact),
            ("search_log.final_snapshot", latest_snapshot),
            ("search_log.stage_summary", latest_summary),
        ):
            point = source.get("global_score_b_best")
            point = point if isinstance(point, dict) else {}
            point_label = point.get("label")
            if point_label != expected_global_label:
                final_point_consistency_failures.append(
                    f"{source_name}.global_score_b_best.label={point_label!r}, "
                    f"expected={expected_global_label!r}"
                )
        expected_strategy_label = expected_global_label
        if expected_strategy_label is None:
            expected_strategy_label = ((rounds[-1] if rounds else {}).get("range_anchor_point") or {}).get("label")
        for source_name, source in (
            ("final", final),
            ("best_artifact", best_artifact),
            ("search_log.final_snapshot", latest_snapshot),
            ("search_log.stage_summary", latest_summary),
        ):
            point = source.get("strategy_final_point")
            point = point if isinstance(point, dict) else {}
            if point.get("label") != expected_strategy_label:
                final_point_consistency_failures.append(
                    f"{source_name}.strategy_final_point.label={point.get('label')!r}, "
                    f"expected={expected_strategy_label!r}"
                )
        if expected_global_score_b_details is not None:
            logged_details = final.get("global_score_b_best_details") or {}
            for field in ("qps_gain", "target_progress", "bonus_sum", "score_b"):
                expected_value = number(expected_global_score_b_details.get(field))
                logged_value = number(logged_details.get(field))
                if (
                    expected_value is None
                    or logged_value is None
                    or not math.isclose(expected_value, logged_value, rel_tol=0.0, abs_tol=1e-9)
                ):
                    final_point_consistency_failures.append(
                        f"final.global_score_b_best_details.{field}={logged_value}, "
                        f"expected={expected_value}"
                    )
    if experiment_strategy == "C":
        ranked_score_c_candidates: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
        if baseline_reference is not None:
            for candidate in candidates:
                details = real_score_c_audit_details(candidate, baseline_reference, target)
                if details is not None:
                    ranked_score_c_candidates.append((candidate, details))
        expected_global_score_c_candidate: Optional[Dict[str, Any]] = None
        expected_global_score_c_details: Optional[Dict[str, Any]] = None
        if ranked_score_c_candidates:
            expected_global_score_c_candidate, expected_global_score_c_details = max(
                ranked_score_c_candidates,
                key=lambda item: real_score_c_audit_key(item[0], item[1]),
            )
        expected_global_label = (
            expected_global_score_c_candidate.get("label")
            if expected_global_score_c_candidate is not None
            else None
        )
        for source_name, source in (
            ("final", final),
            ("best_artifact", best_artifact),
            ("search_log.final_snapshot", latest_snapshot),
            ("search_log.stage_summary", latest_summary),
        ):
            global_point = source.get("global_score_c_best") or {}
            strategy_point = source.get("strategy_final_point") or {}
            if global_point.get("label") != expected_global_label:
                final_point_consistency_failures.append(
                    f"{source_name}.global_score_c_best.label={global_point.get('label')!r}, "
                    f"expected={expected_global_label!r}"
                )
            if strategy_point.get("label") != expected_global_label:
                final_point_consistency_failures.append(
                    f"{source_name}.strategy_final_point.label={strategy_point.get('label')!r}, "
                    f"expected={expected_global_label!r}"
                )
        if expected_global_score_c_details is not None:
            logged_details = final.get("global_score_c_best_details") or {}
            for field in ("target_progress", "bonus_sum", "score_c"):
                expected_value = number(expected_global_score_c_details.get(field))
                logged_value = number(logged_details.get(field))
                if (
                    expected_value is None
                    or logged_value is None
                    or not math.isclose(expected_value, logged_value, rel_tol=0.0, abs_tol=1e-9)
                ):
                    final_point_consistency_failures.append(
                        f"final.global_score_c_best_details.{field}={logged_value}, "
                        f"expected={expected_value}"
                    )
            if logged_details.get("qps_considered") is not False:
                final_point_consistency_failures.append(
                    "final.global_score_c_best_details_considered_qps"
                )

        if len(confirmations) != 1:
            final_point_consistency_failures.append(
                f"score_c_final_confirmation_count={len(confirmations)}, expected=1"
            )
        else:
            confirmation = confirmations[0]
            if confirmation.get("status") != "ok":
                final_point_consistency_failures.append("score_c_final_confirmation_status_not_ok")
            if confirmation.get("selected_label") != expected_global_label:
                final_point_consistency_failures.append(
                    "score_c_final_confirmation_selected_label_mismatch"
                )
            if confirmation.get("selection_changed_by_confirmation") is not False:
                final_point_consistency_failures.append(
                    "score_c_final_confirmation_changed_selection"
                )
            if expected_global_score_c_candidate is not None and (
                confirmation.get("params") != expected_global_score_c_candidate.get("params")
            ):
                final_point_consistency_failures.append(
                    "score_c_final_confirmation_params_mismatch"
                )
            if not stable_samples_pass(confirmation):
                final_point_consistency_failures.append(
                    "score_c_final_confirmation_stable_samples_failed"
                )
            if not latency_percentiles_pass(confirmation) or (
                number(confirmation.get("p90_ms")) is None
                or number(confirmation.get("p90_ms")) <= 0.0
            ):
                final_point_consistency_failures.append(
                    "score_c_final_confirmation_latency_missing_or_nonpositive"
                )
            if number(confirmation.get("db_qps")) is None or number(confirmation.get("db_xact_tps")) is None:
                final_point_consistency_failures.append(
                    "score_c_final_confirmation_qps_or_tps_missing"
                )
            for resource in ("cpu", "memory", "io"):
                if number((confirmation.get("resources") or {}).get(resource)) is None:
                    final_point_consistency_failures.append(
                        f"score_c_final_confirmation_{resource}_missing"
                    )
            for source_name, source in (("final", final), ("best_artifact", best_artifact)):
                point = source.get("score_c_final_confirmation") or {}
                if point.get("label") != confirmation.get("label"):
                    final_point_consistency_failures.append(
                        f"{source_name}.score_c_final_confirmation_label_mismatch"
                    )
    if best_accepted is not None and final and best_artifact:
        expected_best_label = best_accepted.get("label")
        expected_best_qps = number(best_accepted.get("db_qps"))
        best_point_sources = {
            "final.best_point": final.get("best_point"),
            "final.final_output_point": final.get("final_output_point"),
            f"best_artifact.best_for_{target}": best_artifact.get(f"best_for_{target}"),
            "best_artifact.final_output_point": best_artifact.get("final_output_point"),
            "search_log.final_snapshot.best_point": latest_snapshot.get("best_point"),
            "search_log.final_snapshot.final_output_point": latest_snapshot.get("final_output_point"),
            f"search_log.stage_summary.best_for_{target}": latest_summary.get(f"best_for_{target}"),
            "search_log.stage_summary.final_output_point": latest_summary.get("final_output_point"),
        }
        for source_name, point in best_point_sources.items():
            point = point if isinstance(point, dict) else {}
            point_label = point.get("label")
            point_qps = number(point.get("db_qps"))
            if point_label != expected_best_label:
                final_point_consistency_failures.append(
                    f"{source_name}.label={point_label}, expected={expected_best_label}"
                )
            if (
                expected_best_qps is None
                or point_qps is None
                or not math.isclose(point_qps, expected_best_qps, rel_tol=0.0, abs_tol=1e-9)
            ):
                final_point_consistency_failures.append(
                    f"{source_name}.db_qps={point_qps}, expected={expected_best_qps}"
                )

        expected_observed = candidates[-1] if candidates else {}
        expected_observed_label = expected_observed.get("label")
        for source_name, point in (
            ("final.final_observed_point", final.get("final_observed_point")),
            ("best_artifact.final_observed_point", best_artifact.get("final_observed_point")),
            ("search_log.final_snapshot.final_observed_point", latest_snapshot.get("final_observed_point")),
            ("search_log.stage_summary.final_observed_point", latest_summary.get("final_observed_point")),
        ):
            point = point if isinstance(point, dict) else {}
            if point.get("label") != expected_observed_label:
                final_point_consistency_failures.append(
                    f"{source_name}.label={point.get('label')}, "
                    f"expected_last_candidate={expected_observed_label}"
                )

        final_round = rounds[-1] if rounds else {}
        expected_anchor = final_round.get("range_anchor_point") or {}
        expected_anchor_label = expected_anchor.get("label")
        for source_name, point in (
            ("final.final_range_anchor_point", final.get("final_range_anchor_point")),
            ("best_artifact.final_range_anchor_point", best_artifact.get("final_range_anchor_point")),
            (
                "search_log.final_snapshot.final_range_anchor_point",
                latest_snapshot.get("final_range_anchor_point"),
            ),
            (
                "search_log.stage_summary.final_range_anchor_point",
                latest_summary.get("final_range_anchor_point"),
            ),
        ):
            point = point if isinstance(point, dict) else {}
            if point.get("label") != expected_anchor_label:
                final_point_consistency_failures.append(
                    f"{source_name}.label={point.get('label')}, "
                    f"expected_final_round_anchor={expected_anchor_label}"
                )
        for field in (
            "converged",
            "stop_reason",
            "completed_round_count",
            "validated_candidate_count",
            "final_ranges",
            "epsilon",
        ):
            expected_value = final.get(field)
            for source_name, source in (
                ("best_artifact", best_artifact),
                ("search_log.final_snapshot", latest_snapshot),
                ("search_log.stage_summary", latest_summary),
            ):
                if source.get(field) != expected_value:
                    final_point_consistency_failures.append(
                        f"{source_name}.{field}={source.get(field)!r}, "
                        f"expected_final_artifact={expected_value!r}"
                    )
    if final_point_consistency_failures:
        failures.append(
            "final_point_consistency_failed: "
            + "; ".join(final_point_consistency_failures)
        )

    return {
        "target": target,
        "expected_run_mins": expected_run_mins,
        "complete": not failures,
        "failures": failures,
        "required_rounds": required_rounds,
        "completed_rounds": completed_rounds,
        "expected_points_per_round": expected_points_per_round,
        "expected_real_replays_per_round": expected_replays_per_round,
        "experiment_strategy": experiment_strategy,
        "range_anchor_policy": expected_anchor_policy,
        "round_completeness": round_completeness,
        "round_anchor_selection_failure_count": len(round_anchor_selection_failures),
        "candidate_validation_count": len(candidates),
        "accepted_candidate_count": len(accepted),
        "score_b_successful_candidate_count": len(successful_score_b_candidates),
        "score_c_final_confirmation_count": len(confirmations),
        "candidate_replay_integrity_failure_count": len(candidate_replay_failures),
        "candidate_acceptance_decision_failure_count": len(acceptance_decision_failures),
        "candidate_comparison_baseline_failure_count": len(comparison_baseline_failures),
        "validation_warmup_lifecycle_failure_count": len(warmup_failures),
        "raw_replay_alignment_failure_count": len(raw_alignment_failures),
        "final_point_consistency_failure_count": len(final_point_consistency_failures),
        "raw_replay_row_count": len(raw_rows),
        "malformed_search_log_lines": malformed_log_lines,
        "malformed_raw_log_lines": malformed_raw_lines,
        "baseline_reference": baseline_reference,
        "latest_stop_reason": latest_stop.get("reason"),
        "non_epsilon_stage_stop_count": len(non_epsilon_stops),
        "final_converged": final.get("converged"),
        "range_checks": range_checks,
        "best_accepted_point": best_accepted,
        "final_output_point": final.get("final_output_point"),
        "final_observed_point": final.get("final_observed_point"),
        "final_range_anchor_point": final.get("final_range_anchor_point"),
        "strategy_final_point": final.get("strategy_final_point"),
        "global_score_b_best": final.get("global_score_b_best"),
        "global_score_b_best_details": final.get("global_score_b_best_details"),
        "global_score_c_best": final.get("global_score_c_best"),
        "global_score_c_best_details": final.get("global_score_c_best_details"),
        "score_c_final_confirmation": final.get("score_c_final_confirmation"),
        "artifacts": {
            "search_log": str(log_path),
            "raw_log": str(raw_path),
            "best": str(best_path),
            "final": str(final_path),
        },
    }


def markdown(payload: Dict[str, Any]) -> str:
    lines = [
        "# Four-VM HyBenchXP recall completion audit",
        "",
        f"- Updated: `{payload['updated_at']}`",
        f"- Complete: `{str(payload['complete']).lower()}`",
        f"- Run root: `{payload['run_root']}`",
        f"- Experiment strategy: `{payload.get('experiment_strategy')}`; TopK: `{payload.get('strategy_topk')}`",
        f"- Host disk preflight: `{'passed' if payload['host_preflight']['passed'] else 'failed'}`",
        f"- Host disk runtime guard: `{'passed' if payload['host_disk_guard']['passed'] else 'failed'}`",
        f"- Frozen launch snapshot: `{'passed' if payload['launch_snapshot']['passed'] else 'failed'}`",
        "",
        "| Target | Complete | Attempts | Real replays | Outer accepted | Final goal | Sweet spot | Stop reason | Ranges <= epsilon |",
        "|---|---|---:|---:|---:|---|---|---|---|",
    ]
    for row in payload["scenarios"]:
        ranges_ok = bool(row["range_checks"]) and all(item["passed"] for item in row["range_checks"].values())
        lines.append(
            f"| {row['target']} | {'yes' if row['complete'] else 'no'} | "
            f"{row['completed_rounds']} | {row['candidate_validation_count']} | "
            f"{row['accepted_candidate_count']} | "
            f"{'yes' if (row.get('final_goal') or {}).get('goal_reached') else 'no'} | "
            f"{'yes' if (row.get('final_goal') or {}).get('target_in_sweet_spot') else 'no'} | "
            f"{row['latest_stop_reason'] or '—'} | {'yes' if ranges_ok else 'no'} |"
        )
    if payload["failures"]:
        lines.extend(["", "## Outstanding checks", ""])
        lines.extend(f"- {item}" for item in payload["failures"])
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_root", type=Path)
    args = parser.parse_args()
    run_root = args.run_root.resolve()
    manifest = load_manifest(run_root / "launch_manifest.txt")
    default_run_mins = number(manifest.get("default_vm_run_mins"))
    io_run_mins = number(manifest.get("io_vm_run_mins"))
    experiment_strategy = str(manifest.get("experiment_strategy") or "").upper()
    expected_anchor_policy = str(manifest.get("strategy_policy") or "")
    expected_topk = int(number(manifest.get("strategy_topk")) or 0)
    scenarios = [
        audit_target(
            run_root,
            target,
            io_run_mins if target == "io" else default_run_mins,
            experiment_strategy=experiment_strategy,
            expected_anchor_policy=expected_anchor_policy,
            expected_topk=expected_topk,
        )
        for target in TARGETS
    ]
    invalidation_markers = sorted(str(path) for path in run_root.glob("INVALIDATED*.md"))
    failures = [f"run_invalidated: {path}" for path in invalidation_markers]
    if experiment_strategy == "A":
        if expected_anchor_policy != SMALL_LOOP_TOP1_ANCHOR_POLICY or expected_topk != 1:
            failures.append("strategy_A_manifest_policy_or_topk_invalid")
    elif experiment_strategy == "B":
        if expected_anchor_policy != REAL_SCORE_B_ANCHOR_POLICY or expected_topk <= 1:
            failures.append("strategy_B_manifest_policy_or_topk_invalid")
    elif experiment_strategy == "C":
        if expected_anchor_policy != REAL_SCORE_C_ANCHOR_POLICY or expected_topk <= 1:
            failures.append("strategy_C_manifest_policy_or_topk_invalid")
    elif experiment_strategy == "NESTED":
        if expected_anchor_policy != NESTED_RESOURCE_SEMANTIC_POLICY or expected_topk <= 1:
            failures.append("strategy_NESTED_manifest_policy_or_topk_invalid")
    else:
        failures.append(f"experiment_strategy_invalid: {experiment_strategy!r}")
    snapshot_manifest_value = manifest.get("launch_snapshot_sha256s") or ""
    snapshot_manifest_path = Path(snapshot_manifest_value) if snapshot_manifest_value else Path()
    snapshot_entries, snapshot_failures = (
        verify_sha256_manifest(snapshot_manifest_path)
        if snapshot_manifest_value
        else ([], ["launch_snapshot_sha256s_missing_from_manifest"])
    )
    for field in ("config", "tuner_script", "audit_script", "launch_snapshot_dir"):
        value = manifest.get(field)
        if not value or not Path(value).exists():
            snapshot_failures.append(f"launch_snapshot_manifest_field_missing_or_unreadable: {field}")
    if snapshot_failures:
        failures.append("launch_snapshot_integrity_failed")
        failures.extend(snapshot_failures)
    guard_status = load_json(run_root / "host_disk_isolation_status.json") or {}
    guard_history_path = run_root / "host_disk_isolation_history.jsonl"
    guard_history, malformed_guard_history = load_jsonl(guard_history_path)
    preflight_read = number(manifest.get("preflight_read_mib_s"))
    preflight_write = number(manifest.get("preflight_write_mib_s"))
    preflight_util = number(manifest.get("preflight_util_pct"))
    preflight_max_read = number(manifest.get("preflight_max_read_mib_s"))
    preflight_max_write = number(manifest.get("preflight_max_write_mib_s"))
    preflight_max_util = number(manifest.get("preflight_max_util_pct"))
    host_preflight_passed = bool(
        manifest.get("preflight_passed") == "true"
        and preflight_read is not None
        and preflight_write is not None
        and preflight_util is not None
        and preflight_max_read is not None
        and preflight_max_write is not None
        and preflight_max_util is not None
        and preflight_read <= preflight_max_read
        and preflight_write <= preflight_max_write
        and preflight_util <= preflight_max_util
    )
    if not host_preflight_passed:
        failures.append("host_disk_preflight_missing_or_failed")
    guard_limit = number(manifest.get("host_guard_max_docker_mib_s"))
    qemu_guard_limit = number(manifest.get("host_guard_max_qemu_mib_s"))
    allowed_qemu_guests = sorted(
        value for value in str(manifest.get("host_guard_allowed_qemu_guests") or "").split() if value
    )
    expected_qemu_guests = sorted(
        [
            "bonect-replay-v87-1",
            "bonect-replay-v87-2",
            "bonect-replay-v87-3",
            "bonect-replay-v87-4",
        ]
    )
    history_failures: List[str] = []
    if qemu_guard_limit != 1.0:
        history_failures.append("host_disk_guard_qemu_limit_is_not_1_mib_s")
    if allowed_qemu_guests != expected_qemu_guests:
        history_failures.append(
            f"host_disk_guard_allowed_qemu_guests={allowed_qemu_guests!r}, "
            f"expected={expected_qemu_guests!r}"
        )
    if malformed_guard_history:
        history_failures.append(f"malformed_host_disk_guard_history_lines={malformed_guard_history}")
    if len(guard_history) < 2:
        history_failures.append("host_disk_guard_history_has_fewer_than_two_records")
    if not guard_history or guard_history[0].get("event") != "host_disk_guard_start":
        history_failures.append("host_disk_guard_history_does_not_start_with_start_event")
    if not guard_history or guard_history[-1].get("event") != "host_disk_guard_complete":
        history_failures.append("host_disk_guard_history_does_not_end_with_complete_event")
    for index, row in enumerate(guard_history, start=1):
        if row.get("status") != "ok" or row.get("conflicts"):
            history_failures.append(f"host_disk_guard_history_record_{index}_is_not_clean")
        if str(row.get("device") or "") != str(manifest.get("host_guard_device") or ""):
            history_failures.append(f"host_disk_guard_history_record_{index}_device_mismatch")
        if number(row.get("max_docker_mib_s")) != guard_limit:
            history_failures.append(f"host_disk_guard_history_record_{index}_limit_mismatch")
        if number(row.get("max_qemu_mib_s")) != qemu_guard_limit:
            history_failures.append(f"host_disk_guard_history_record_{index}_qemu_limit_mismatch")
        if sorted(row.get("allowed_qemu_guests") or []) != allowed_qemu_guests:
            history_failures.append(
                f"host_disk_guard_history_record_{index}_allowed_qemu_guests_mismatch"
            )
        for rate in row.get("rates") or []:
            measured = number(rate.get("mib_s")) if isinstance(rate, dict) else None
            if measured is None or guard_limit is None or measured > guard_limit + 1e-12:
                history_failures.append(f"host_disk_guard_history_record_{index}_rate_exceeds_limit")
        for rate in row.get("qemu_rates") or []:
            if not isinstance(rate, dict) or rate.get("allowed") is True:
                continue
            measured = number(rate.get("mib_s"))
            if measured is None or qemu_guard_limit is None or measured > qemu_guard_limit + 1e-12:
                history_failures.append(
                    f"host_disk_guard_history_record_{index}_qemu_rate_exceeds_limit"
                )
    scenario_start_times: List[str] = []
    scenario_start_targets = 0
    for target in TARGETS:
        search_rows, _ = load_jsonl(run_root / target / f"{target}_binary_search_hybenchxp.jsonl")
        target_start_times = [
            timestamp
            for row in search_rows
            if row.get("event") == "stage_start"
            for timestamp in [timestamp_key(row.get("timestamp"))]
            if timestamp is not None
        ]
        if target_start_times:
            scenario_start_targets += 1
            scenario_start_times.extend(target_start_times)
    guard_start_time = timestamp_key(guard_history[0].get("updated_at")) if guard_history else None
    if scenario_start_targets != len(TARGETS):
        history_failures.append(
            f"host_disk_guard_scenario_start_targets={scenario_start_targets}, expected={len(TARGETS)}"
        )
    elif guard_start_time is None or guard_start_time > min(scenario_start_times):
        history_failures.append("host_disk_guard_did_not_start_before_all_scenarios")
    history_epochs = [timestamp_epoch(row.get("updated_at")) for row in guard_history]
    if any(value is None for value in history_epochs):
        history_failures.append("host_disk_guard_history_has_invalid_timestamp")
    else:
        valid_epochs = [float(value) for value in history_epochs if value is not None]
        guard_interval = number(manifest.get("host_guard_interval_sec"))
        max_gap = max(20.0, (guard_interval or 5.0) * 4.0)
        if any(after < before for before, after in zip(valid_epochs, valid_epochs[1:])):
            history_failures.append("host_disk_guard_history_timestamps_are_not_monotonic")
        if any(after - before > max_gap for before, after in zip(valid_epochs, valid_epochs[1:])):
            history_failures.append(f"host_disk_guard_history_gap_exceeds_{max_gap:g}_seconds")
    if not guard_history or guard_history[-1].get("all_converged") is not True:
        history_failures.append("host_disk_guard_history_final_record_is_not_converged")
    host_guard_passed = bool(
        guard_status.get("status") == "ok"
        and guard_status.get("all_converged") is True
        and not guard_status.get("conflicts")
        and str(guard_status.get("device") or "") == str(manifest.get("host_guard_device") or "")
        and number(guard_status.get("max_docker_mib_s"))
        == guard_limit
        and number(guard_status.get("max_qemu_mib_s")) == qemu_guard_limit
        and sorted(guard_status.get("allowed_qemu_guests") or []) == allowed_qemu_guests
        and not history_failures
    )
    if not host_guard_passed:
        failures.append("host_disk_isolation_guard_missing_or_failed")
        failures.extend(history_failures)
    failures.extend(
        f"{row['target']}: {failure}"
        for row in scenarios
        for failure in row["failures"]
    )
    payload = {
        "event": "four_vm_hybenchxp_recall_completion_audit",
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "run_root": str(run_root),
        "experiment_strategy": experiment_strategy,
        "strategy_policy": expected_anchor_policy,
        "strategy_topk": expected_topk,
        "host_preflight": {
            "passed": host_preflight_passed,
            "disk_device": manifest.get("preflight_disk_device"),
            "read_mib_s": preflight_read,
            "write_mib_s": preflight_write,
            "util_pct": preflight_util,
            "max_read_mib_s": preflight_max_read,
            "max_write_mib_s": preflight_max_write,
            "max_util_pct": preflight_max_util,
        },
        "host_disk_guard": {
            "passed": host_guard_passed,
            "max_docker_mib_s": guard_limit,
            "max_qemu_mib_s": qemu_guard_limit,
            "allowed_qemu_guests": allowed_qemu_guests,
            "status": guard_status,
            "history_path": str(guard_history_path),
            "history_record_count": len(guard_history),
            "malformed_history_lines": malformed_guard_history,
            "history_failures": history_failures,
            "guard_start_time": guard_start_time,
            "earliest_scenario_start_time": min(scenario_start_times) if scenario_start_times else None,
        },
        "launch_snapshot": {
            "passed": not snapshot_failures,
            "manifest_path": str(snapshot_manifest_path) if snapshot_manifest_value else None,
            "entries": snapshot_entries,
            "failures": snapshot_failures,
        },
        "complete": not failures and len(scenarios) == len(TARGETS),
        "failures": failures,
        "scenarios": scenarios,
    }
    atomic_write(
        run_root / "convergence_completion_audit.json",
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    atomic_write(run_root / "convergence_completion_audit.md", markdown(payload))
    print(json.dumps({"complete": payload["complete"], "failures": failures}, ensure_ascii=False, indent=2))
    return 0 if payload["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

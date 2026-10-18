"""Classify stable replay payloads with expected transaction aborts."""

from __future__ import annotations

import math
import re
from typing import Any, Dict, Mapping


SQLSTATE_PATTERN = re.compile(r"sql state \[([0-9A-Z]{5})\]", re.IGNORECASE)
EXPECTED_TRANSACTION_ABORT_STATES = frozenset({"40P01", "40001"})


def measurement_integrity_passed(summary: Mapping[str, Any]) -> bool:
    continuity = summary.get("database_counter_continuity") or {}
    cleanup = summary.get("worker_cleanup") or {}
    window_stability = summary.get("window_mean_stability") or {}
    target_resource = summary.get("stability_target_resource")
    target_field = f"{target_resource}_utilization"
    metric_fields = window_stability.get("metric_fields") or []
    workload_returncode = summary.get("workload_returncode")
    controlled_stop = bool(
        workload_returncode == 0
        or (
            summary.get("intentionally_terminated_after_stability") is True
            and workload_returncode in (-15, -9)
        )
    )
    completion_status = bool(
        summary.get("status") == "complete"
        or (
            summary.get("status") == "failed"
            and summary.get("intentionally_terminated_after_stability") is True
            and workload_returncode in (-15, -9)
        )
    )
    return bool(
        completion_status
        and summary.get("stop_reason") == "stable_early_stop"
        and target_resource in {"cpu", "memory", "io"}
        and target_field in metric_fields
        and window_stability.get("stable") is True
        and controlled_stop
        and continuity.get("passed") is True
        and continuity.get("counter_reset_count") == 0
        and cleanup.get("fully_released") is True
        and isinstance(cleanup.get("remaining_pids"), list)
        and not cleanup.get("remaining_pids")
        and cleanup.get("counter_thread_stopped") is True
    )


def classify_transaction_abort_gate(
    summary: Mapping[str, Any],
    native_log_text: str,
    *,
    maximum_abort_rate: float = 0.001,
) -> Dict[str, Any]:
    """Separate stable algorithmic rejection from technical corruption."""

    states = [value.upper() for value in SQLSTATE_PATTERN.findall(native_log_text)]
    marker_count = native_log_text.count("SQLException occurred")
    declared_count = summary.get("native_sql_exception_occurrences")
    if (
        isinstance(declared_count, bool)
        or not isinstance(declared_count, int)
        or declared_count < 0
    ):
        return {
            "valid_replay": False,
            "algorithmic_gate_rejection": False,
            "technical_failure": True,
            "reason": "invalid_declared_sql_exception_count",
            "sql_states": states,
            "sql_exception_marker_count": marker_count,
            "abort_rate": None,
        }
    if declared_count != marker_count or declared_count != len(states):
        return {
            "valid_replay": False,
            "algorithmic_gate_rejection": False,
            "technical_failure": True,
            "reason": "sql_exception_count_log_mismatch",
            "sql_states": states,
            "sql_exception_marker_count": marker_count,
            "abort_rate": None,
        }
    if declared_count == 0:
        clean = measurement_integrity_passed(summary)
        return {
            "valid_replay": clean,
            "algorithmic_gate_rejection": False,
            "technical_failure": not clean,
            "reason": "clean" if clean else "failed_without_sql_exception",
            "sql_states": [],
            "sql_exception_marker_count": marker_count,
            "abort_rate": 0.0,
        }
    if any(state not in EXPECTED_TRANSACTION_ABORT_STATES for state in states):
        return {
            "valid_replay": False,
            "algorithmic_gate_rejection": False,
            "technical_failure": True,
            "reason": "unexpected_sql_state",
            "sql_states": states,
            "sql_exception_marker_count": marker_count,
            "abort_rate": None,
        }
    try:
        native_tps = float(summary["native_tps"])
        seconds = float(summary["effective_measurement_seconds"])
    except (KeyError, TypeError, ValueError):
        native_tps = seconds = math.nan
    transaction_count = native_tps * seconds
    if not math.isfinite(transaction_count) or transaction_count <= 0.0:
        return {
            "valid_replay": False,
            "algorithmic_gate_rejection": False,
            "technical_failure": True,
            "reason": "invalid_transaction_denominator",
            "sql_states": states,
            "sql_exception_marker_count": marker_count,
            "abort_rate": None,
        }
    abort_rate = declared_count / transaction_count
    allowed = bool(
        abort_rate <= maximum_abort_rate
        and measurement_integrity_passed(summary)
        and summary.get("stop_reason") == "stable_early_stop"
    )
    return {
        "valid_replay": allowed,
        "algorithmic_gate_rejection": False,
        "technical_failure": not allowed,
        "reason": (
            "expected_transaction_abort_within_rate"
            if allowed
            else "transaction_abort_rate_or_status_rejected"
        ),
        "sql_states": states,
        "sql_exception_marker_count": marker_count,
        "abort_rate": abort_rate,
    }


__all__ = ["classify_transaction_abort_gate"]

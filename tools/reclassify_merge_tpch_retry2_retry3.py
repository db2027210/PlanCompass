#!/usr/bin/env python3
"""Zero-call TPC-H retry2 rank1 reclassifier and retry3 merger.

The only tolerated retry2 rank1 defect is a timeout while reading the final
pg_stat_statements counter.  DB QPS is outer audit only.  Every workload,
stability, cleanup, PostgreSQL resource, selection, accounting, and baseline
restore contract remains fail closed.
"""

from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import math
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple


RESOURCES: Tuple[str, ...] = ("cpu", "io", "memory")
ALLOWED_PARAMS = {
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
}
BASELINE_PARAMS = {
    "seq_page_cost": 1.0,
    "random_page_cost": 4.0,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
}
CAPABILITY = {
    "cpu": 22.0,
    "io": 36.0,
    "memory": 1_730_000_000 / 1024.0 ** 3,
}
INCUMBENT_SCORE = 4.77702526499285
SELECTION_SHA256 = (
    "8865741a7f2a7840b4a2feef97a355bee9e1fb7b4aab71c73e6558e8faa4e531"
)
CONTROLLER_SHA256 = (
    "5db636e8983b8e743520a870549e744af65e6c40fcb00eab61e66108a091ffb9"
)
FORMAL_STATE_SHA256 = (
    "13829704af08c96e7c63b3b4d9515b747319af7a219af7e463ded5d1eb66fca5"
)
RETRY2_EXPECTED_SHA256 = {
    "summary":
        "d760d63fe6c834a8d0a478da2ccf7c18a3573863f80ed702ef20237d75e0e28b",
    "events":
        "2d52d7eb122a687a78abeea194eede5368f733202cba9d8ff9af392a6e751a76",
    "rank1_workload_log":
        "80ecb71d446e1097f666c9ce1b4df4455c4d0c410c1e0e67b7f9e205fcffd3a8",
    "rank1_candidate_summary":
        "6ebca27ffbabdfd2b9b90dcfad4b390aab0346961df28c54dd6b9af8518a72be",
}
MARKER = "RESOURCE_ADAPTIVE_BENCHMARK_JSON="
AUDIT_FIELDS = (
    "feature_ratio",
    "predicted_resource",
    "utilization",
    "dScore/dU",
    "T",
    "confidence",
    "g_j",
    "eta",
    "delta_param",
    "candidate_score",
)


class EvidenceError(RuntimeError):
    """One immutable input does not satisfy the formal merge contract."""


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_sha(path: Path, expected: str, label: str) -> str:
    if not path.is_file():
        raise EvidenceError(f"{label} is missing: {path}")
    actual = file_sha256(path)
    if actual != expected:
        raise EvidenceError(
            f"{label} SHA mismatch: {actual} != {expected}"
        )
    return actual


def load_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise EvidenceError(f"{path} must contain a JSON object")
    return value


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EvidenceError(
                f"invalid JSONL at {path}:{line_number}"
            ) from exc
        if not isinstance(value, dict):
            raise EvidenceError(
                f"non-object JSONL record at {path}:{line_number}"
            )
        records.append(value)
    return records


def finite(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise EvidenceError(f"{label} is not numeric") from exc
    if not math.isfinite(number):
        raise EvidenceError(f"{label} is not finite")
    return number


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=str(path.parent),
        text=True,
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    atomic_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def band_score(utilization: float, weight: float, target: bool) -> float:
    target_min, target_max, hard = (
        (0.85, 0.90, 0.95) if target else (0.30, 0.50, 0.90)
    )
    if target:
        if utilization < target_min:
            gap = (target_min - utilization) / target_min
            return weight * (1.0 - gap * gap)
        if utilization <= target_max:
            position = (utilization - target_min) / (
                target_max - target_min
            )
            return weight * (1.0 - 0.2 * position)
        span = max(hard - target_max, 0.05)
        gap = (utilization - target_max) / span
        hard_excess = max(0.0, utilization - hard) / span
        return weight * (0.8 - gap * gap - 4.0 * hard_excess)
    if utilization < target_min:
        gap = (target_min - utilization) / target_min
        return weight * 0.8 * (1.0 - gap * gap)
    if utilization <= target_max:
        position = (utilization - target_min) / (
            target_max - target_min
        )
        return weight * (0.8 + 0.2 * position)
    span = max(hard - target_max, 0.05)
    gap = (utilization - target_max) / span
    hard_excess = max(0.0, utilization - hard) / span
    return weight * (1.0 - gap * gap - 4.0 * hard_excess)


def score_resource(
    resource: Mapping[str, Any],
) -> Tuple[float, Dict[str, float]]:
    utilization = {
        name: finite(resource[name], f"resource.{name}") / CAPABILITY[name]
        for name in RESOURCES
    }
    score = sum(
        band_score(utilization[name], weight, name == "memory")
        for name, weight in (
            ("cpu", 1.0),
            ("io", 3.0),
            ("memory", 1.0),
        )
    )
    return score, utilization


def shrink_ranges(
    ranges: Mapping[str, Mapping[str, Any]],
    accepted_params: Mapping[str, Any],
) -> Dict[str, Dict[str, Any]]:
    if set(ranges) != ALLOWED_PARAMS:
        raise EvidenceError("formal range-state parameter set mismatch")
    shrunk: Dict[str, Dict[str, Any]] = {}
    for name, raw in ranges.items():
        minimum = finite(raw["min_value"], f"range.{name}.min")
        maximum = finite(raw["max_value"], f"range.{name}.max")
        accepted = min(
            maximum,
            max(
                minimum,
                finite(accepted_params[name], f"accepted.{name}"),
            ),
        )
        transform = str(raw["transform"])
        if transform == "log":
            if minimum <= 0.0 or maximum <= 0.0:
                raise EvidenceError(f"invalid log range for {name}")
            low = math.log(minimum)
            high = math.log(maximum)
            center = math.log(accepted)
            half_width = (high - low) / 4.0
            new_low = max(
                low,
                min(center - half_width, high - 2.0 * half_width),
            )
            new_high = new_low + 2.0 * half_width
            new_minimum = math.exp(new_low)
            new_maximum = math.exp(new_high)
        elif transform == "linear":
            half_width = (maximum - minimum) / 4.0
            new_minimum = max(
                minimum,
                min(accepted - half_width, maximum - 2.0 * half_width),
            )
            new_maximum = new_minimum + 2.0 * half_width
        else:
            raise EvidenceError(
                f"unsupported range transform for {name}: {transform}"
            )
        shrunk[name] = {
            "min_value": new_minimum,
            "max_value": new_maximum,
            "transform": transform,
        }
    return shrunk


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--selection-sha256", required=True)
    parser.add_argument("--controller", type=Path, required=True)
    parser.add_argument("--controller-sha256", required=True)
    parser.add_argument("--retry2-summary", type=Path, required=True)
    parser.add_argument("--retry2-summary-sha256", required=True)
    parser.add_argument("--retry2-events", type=Path, required=True)
    parser.add_argument("--retry2-events-sha256", required=True)
    parser.add_argument("--rank1-workload-log", type=Path, required=True)
    parser.add_argument("--rank1-workload-log-sha256", required=True)
    parser.add_argument(
        "--rank1-candidate-summary",
        type=Path,
        required=True,
    )
    parser.add_argument("--rank1-candidate-summary-sha256", required=True)
    parser.add_argument("--retry3-summary", type=Path, required=True)
    parser.add_argument("--retry3-summary-sha256", required=True)
    parser.add_argument("--retry3-events", type=Path, required=True)
    parser.add_argument("--retry3-events-sha256", required=True)
    parser.add_argument("--formal-state", type=Path, required=True)
    parser.add_argument("--formal-state-sha256", required=True)
    parser.add_argument("--accepted-counter", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--commit", action="store_true")
    return parser.parse_args()


def validate_selection(
    selection: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    if selection.get("selection_contract") not in {
        "exact_plan_regime_trusted3_unknown_probe2_top5_v1",
        "exact_structural_plan_regime_trusted3_unknown_probe2_top5_v2",
    }:
        raise EvidenceError("selection contract mismatch")
    candidates = selection.get("top5") or []
    if not isinstance(candidates, list) or len(candidates) != 5:
        raise EvidenceError("selection does not contain exactly five points")
    roles = [item.get("selection_role") for item in candidates]
    if roles != ["trusted", "trusted", "trusted", "probe", "probe"]:
        raise EvidenceError(f"selection roles mismatch: {roles}")
    fingerprints = [
        (item.get("three_level_fingerprint") or {}).get("id")
        for item in candidates
    ]
    if (
        any(not isinstance(value, str) for value in fingerprints)
        or len(set(fingerprints)) != 5
    ):
        raise EvidenceError("selection fingerprints are invalid")
    for rank, candidate in enumerate(candidates, start=1):
        params = candidate.get("candidate_params") or {}
        if not isinstance(params, dict) or set(params) != ALLOWED_PARAMS:
            raise EvidenceError(f"rank {rank} parameter set mismatch")
        missing = [
            field
            for field in AUDIT_FIELDS
            if field not in candidate
        ]
        if missing:
            raise EvidenceError(
                f"rank {rank} audit fields missing: {missing}"
            )
    return copy.deepcopy(candidates)


def find_exact_event(
    events: List[Dict[str, Any]],
    event_name: str,
    rank: Optional[int] = None,
) -> Dict[str, Any]:
    matches = [
        event
        for event in events
        if event.get("event") == event_name
        and (rank is None or int(event.get("rank", -1)) == rank)
    ]
    if len(matches) != 1:
        raise EvidenceError(
            f"expected one {event_name} rank={rank}, got {len(matches)}"
        )
    return matches[0]


def assert_candidate_binding(
    record: Mapping[str, Any],
    candidate: Mapping[str, Any],
    rank: int,
) -> None:
    if int(record.get("rank", -1)) != rank:
        raise EvidenceError(f"rank {rank} record rank mismatch")
    if record.get("selection_role") != candidate.get("selection_role"):
        raise EvidenceError(f"rank {rank} role mismatch")
    if record.get("candidate_params") != candidate.get("candidate_params"):
        raise EvidenceError(f"rank {rank} candidate parameters mismatch")
    if (
        (record.get("three_level_fingerprint") or {}).get("id")
        != (candidate.get("three_level_fingerprint") or {}).get("id")
    ):
        raise EvidenceError(f"rank {rank} fingerprint mismatch")
    for field in AUDIT_FIELDS:
        record_field = (
            record.get("predicted_utilization")
            if field == "utilization"
            else record.get(field)
        )
        if record_field is None and field == "utilization":
            record_field = record.get("utilization")
        if field == "utilization":
            expected = candidate.get("utilization")
        else:
            expected = candidate.get(field)
        if record_field != expected:
            raise EvidenceError(
                f"rank {rank} candidate audit field mismatch: {field}"
            )


def parse_baseline_verification(value: Any) -> Dict[str, float]:
    lines = str(value or "").strip().splitlines()
    parsed: Dict[str, float] = {}
    for line in lines:
        if "=" not in line:
            raise EvidenceError("baseline verification contains invalid line")
        name, raw = line.split("=", 1)
        parsed[name.strip()] = finite(raw.strip(), f"baseline.{name}")
    if set(parsed) != ALLOWED_PARAMS:
        raise EvidenceError("baseline verification parameter set mismatch")
    for name, expected in BASELINE_PARAMS.items():
        if not math.isclose(
            parsed[name],
            expected,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise EvidenceError(
                f"baseline restore mismatch for {name}: "
                f"{parsed[name]} != {expected}"
            )
    return parsed


def extract_rank1_log_payload(
    workload_log: Path,
    candidate_summary: Mapping[str, Any],
) -> Dict[str, Any]:
    lines = workload_log.read_text(encoding="utf-8").splitlines()
    markers = [
        index for index, line in enumerate(lines) if MARKER in line
    ]
    if len(markers) != 1:
        raise EvidenceError(
            f"rank1 log must contain one benchmark marker, got {len(markers)}"
        )
    index = markers[0]
    marker = json.loads(lines[index].split(MARKER, 1)[1].strip())
    if not isinstance(marker, dict):
        raise EvidenceError("rank1 benchmark marker is not an object")
    if index + 1 >= len(lines):
        raise EvidenceError("rank1 candidate summary line is missing")
    logged_summary = json.loads(lines[index + 1].strip())
    if logged_summary != candidate_summary:
        raise EvidenceError(
            "rank1 candidate summary does not match workload log"
        )
    required_marker = (
        "benchmark",
        "benchmark_latency",
        "candidate_measurement_protocol",
        "formal_pg_resource",
    )
    if any(name not in marker for name in required_marker):
        raise EvidenceError("rank1 benchmark marker lacks formal fields")
    payload = copy.deepcopy(dict(candidate_summary))
    for name in required_marker:
        payload[name] = copy.deepcopy(marker[name])
    return payload


def validate_qps_only_failure(payload: Mapping[str, Any]) -> Dict[str, Any]:
    if (
        payload.get("status") != "failed"
        or payload.get("stop_reason") != "stable_early_stop"
        or int(payload.get("successful_queries", -1)) != 571
        or int(payload.get("failed_queries", -1)) != 0
    ):
        raise EvidenceError("rank1 workload/query completion contract failed")
    elapsed = finite(
        payload.get("effective_measurement_seconds"),
        "rank1.effective_measurement_seconds",
    )
    if elapsed < 180.0 or elapsed > 181.0:
        raise EvidenceError(f"rank1 effective duration is invalid: {elapsed}")
    per_query = payload.get("per_query") or {}
    if (
        len(per_query) != 22
        or sum(
            int((record or {}).get("ok", -1))
            for record in per_query.values()
        )
        != 571
        or any(
            int((record or {}).get("failed", -1)) != 0
            for record in per_query.values()
        )
    ):
        raise EvidenceError("rank1 per-query completion accounting mismatch")
    protocol = payload.get("candidate_measurement_protocol") or {}
    if (
        protocol.get("stable") is not True
        or protocol.get("stop_reason") != "stable_early_stop"
        or int(protocol.get("complete_window_count", -1)) != 6
        or finite(
            protocol.get("effective_measurement_seconds"),
            "rank1.protocol.effective_measurement_seconds",
        )
        < 180.0
        or int(protocol.get("minimum_measurement_seconds", -1)) != 180
        or int(protocol.get("frozen_base_minimum_seconds", -1)) != 180
        or int(protocol.get("initial_probe_seconds", -1)) != 180
        or int(protocol.get("lane_start_decision_seconds", -1)) != 180
        or int(protocol.get("maximum_measurement_seconds", -1)) != 600
        or protocol.get("throughput_stability_required") is not False
        or not isinstance(protocol.get("stability_contract_sha256"), str)
        or len(protocol.get("stability_contract_sha256")) != 64
    ):
        raise EvidenceError("rank1 candidate measurement protocol mismatch")
    benchmark = payload.get("benchmark") or {}
    if (
        int(benchmark.get("query_count", -1)) != 571
        or not math.isclose(
            finite(benchmark.get("throughput_qps"), "rank1.throughput_qps"),
            finite(payload.get("benchmark_qps"), "rank1.benchmark_qps"),
            rel_tol=0.0,
            abs_tol=1e-12,
        )
        or finite(benchmark.get("db_qps"), "rank1.benchmark.db_qps") != 0.0
    ):
        raise EvidenceError("rank1 benchmark marker accounting mismatch")
    cleanup = payload.get("worker_cleanup") or {}
    if (
        cleanup.get("controller_thread_stopped") is not True
        or cleanup.get("database_backends_fully_released") is not True
        or int(cleanup.get("remaining_benchmark_backend_count", -1)) != 0
    ):
        raise EvidenceError("rank1 cleanup is incomplete")
    assessment = payload.get("measurement_assessment") or {}
    cross = assessment.get("cross_window_resource_stability") or {}
    groups = cross.get("groups") or []
    if (
        assessment.get("stable") is not True
        or int(assessment.get("complete_window_count", -1)) != 6
        or int(assessment.get("consecutive_stable_windows", -1)) != 6
        or finite(assessment.get("elapsed_seconds"), "rank1.elapsed") < 180.0
        or finite(
            assessment.get("aggregate_sample_coverage"),
            "rank1.coverage",
        ) < 0.9
        or cross.get("stable") is not True
        or cross.get("mode")
        != "target_bottleneck_six_consecutive_30s_window_means_common_cv"
        or int(cross.get("required_window_count", -1)) != 6
        or finite(
            cross.get("minimum_elapsed_seconds"),
            "rank1.cross.minimum_elapsed_seconds",
        ) < 180.0
        or len(groups) != 1
        or groups[0].get("stable") is not True
        or groups[0].get("per_window_coverage_passed") is not True
        or finite(
            groups[0].get("cumulative_coverage"),
            "rank1.cross.cumulative_coverage",
        ) < 0.9
        or groups[0].get("stability_metric_fields") != [
            "memory_utilization"
        ]
    ):
        raise EvidenceError("rank1 stability contract failed")
    qps = payload.get("db_qps_counter") or {}
    if (
        qps.get("valid") is not False
        or finite(qps.get("initial_calls"), "rank1.qps.initial_calls") != 0.0
        or qps.get("final_calls") is not None
        or qps.get("initial_error") is not None
        or str(qps.get("final_error") or "").strip()
        != "QueryCanceled: canceling statement due to statement timeout"
        or qps.get("warmup_calls_excluded") is not True
        or qps.get("post_warmup_pg_stat_statements_reset") is not True
        or qps.get("resource_sampling_starts_after_reset") is not True
        or qps.get("window_numbering_starts_after_reset") is not True
        or qps.get("cleanup_calls_excluded") is not True
        or qps.get("fallback_to_workload_completion_qps") is not False
    ):
        raise EvidenceError(
            "rank1 failure is not exclusively the final DB-QPS counter timeout"
        )
    return {
        "ignored_failure": "db_qps_final_counter_statement_timeout",
        "qps_used_for_inner_scoring": False,
        "successful_queries": 571,
        "failed_queries": 0,
        "effective_measurement_seconds": elapsed,
        "stable": True,
        "cleanup_complete": True,
    }


def validate_formal_resource(
    payload: Mapping[str, Any],
) -> Tuple[Dict[str, float], Dict[str, float], float, Dict[str, Any]]:
    formal = payload.get("formal_pg_resource") or {}
    if (
        formal.get("semantics")
        != "pg5_io_total_throughput_utilization_v1"
        or formal.get("io_formula")
        != "(read_bps + write_bps) / (read_cap_bps + write_cap_bps)"
        or formal.get("raw_range_validation")
        != "finite_and_nonnegative_no_upper_clip"
        or int(formal.get("sample_count", -1)) != 180
    ):
        raise EvidenceError("rank1 formal PostgreSQL resource semantics mismatch")
    caps = formal.get("caps") or {}
    expected_caps = {
        "cpu_quota_cores": 22.0,
        "memory_bytes": 1_730_000_000.0,
        "io_total_cap_bps": 37_748_736.0,
        "read_cap_bps": 16_777_216.0,
        "write_cap_bps": 20_971_520.0,
    }
    for name, expected in expected_caps.items():
        observed = finite(caps.get(name), f"rank1.caps.{name}")
        if not math.isclose(
            observed,
            expected,
            rel_tol=0.0,
            abs_tol=1e-5,
        ):
            raise EvidenceError(
                f"rank1 formal resource cap mismatch: {name}"
            )
    aggregate = formal.get("aggregate") or {}
    utilization = {
        name: finite(
            aggregate.get(f"{name}_utilization"),
            f"rank1.utilization.{name}",
        )
        for name in RESOURCES
    }
    if any(value < 0.0 for value in utilization.values()):
        raise EvidenceError("rank1 resource utilization is negative")
    summary_util = payload.get("resource_utilization") or {}
    for name in RESOURCES:
        observed = finite(
            (summary_util.get(name) or {}).get("avg"),
            f"rank1.summary_utilization.{name}",
        )
        if not math.isclose(
            observed,
            utilization[name],
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise EvidenceError(
                f"rank1 marker/summary utilization mismatch: {name}"
            )
    read_bps = finite(aggregate.get("read_bps"), "rank1.read_bps")
    write_bps = finite(aggregate.get("write_bps"), "rank1.write_bps")
    total_bps = finite(aggregate.get("total_bps"), "rank1.total_bps")
    if (
        not math.isclose(
            read_bps + write_bps,
            total_bps,
            rel_tol=0.0,
            abs_tol=1e-6,
        )
        or not math.isclose(
            total_bps / expected_caps["io_total_cap_bps"],
            utilization["io"],
            rel_tol=0.0,
            abs_tol=1e-12,
        )
        or not math.isclose(
            finite(
                aggregate.get("memory_working_set_bytes"),
                "rank1.memory_working_set_bytes",
            )
            / expected_caps["memory_bytes"],
            utilization["memory"],
            rel_tol=0.0,
            abs_tol=1e-12,
        )
    ):
        raise EvidenceError("rank1 aggregate resource arithmetic mismatch")
    resource = {
        name: utilization[name] * CAPABILITY[name]
        for name in RESOURCES
    }
    score, recomputed_utilization = score_resource(resource)
    for name in RESOURCES:
        if not math.isclose(
            recomputed_utilization[name],
            utilization[name],
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise EvidenceError("rank1 resource normalization mismatch")
    return resource, utilization, score, copy.deepcopy(formal)


def build_rank1_result(
    candidate: Mapping[str, Any],
    started: Mapping[str, Any],
    payload: Mapping[str, Any],
    input_binding: Mapping[str, Any],
) -> Dict[str, Any]:
    assert_candidate_binding(started, candidate, 1)
    qps_audit = validate_qps_only_failure(payload)
    resource, utilization, score, formal = validate_formal_resource(payload)
    result = {
        "rank": 1,
        "selection_role": "trusted",
        "candidate_params": copy.deepcopy(candidate["candidate_params"]),
        "feature_ratio": copy.deepcopy(candidate["feature_ratio"]),
        "predicted_resource": copy.deepcopy(
            candidate["predicted_resource"]
        ),
        "predicted_utilization": copy.deepcopy(candidate["utilization"]),
        "utilization": copy.deepcopy(candidate["utilization"]),
        "dScore/dU": copy.deepcopy(candidate["dScore/dU"]),
        "T": copy.deepcopy(candidate["T"]),
        "confidence": copy.deepcopy(candidate["confidence"]),
        "g_j": copy.deepcopy(candidate["g_j"]),
        "eta": candidate["eta"],
        "delta_param": copy.deepcopy(candidate["delta_param"]),
        "candidate_score": candidate["candidate_score"],
        "three_level_fingerprint": copy.deepcopy(
            candidate["three_level_fingerprint"]
        ),
        "actual_resource": resource,
        "actual_utilization": utilization,
        "actual_score": score,
        "technical_failure": False,
        "valid_replay": True,
        "accepted_update_eligible": True,
        "probe_updates_forbidden": False,
        "qps_used_for_inner_scoring": False,
        "qps_outer_audit": {
            "benchmark_qps": payload.get("benchmark_qps"),
            "db_qps": payload.get("db_qps"),
            "db_qps_counter": copy.deepcopy(
                payload.get("db_qps_counter") or {}
            ),
        },
        "formal_pg_resource": formal,
        "zero_call_reclassification": {
            **qps_audit,
            "new_workload_calls": 0,
            "source_binding": copy.deepcopy(dict(input_binding)),
        },
    }
    return result


def validate_result(
    result: Mapping[str, Any],
    candidate: Mapping[str, Any],
    rank: int,
) -> Dict[str, Any]:
    assert_candidate_binding(result, candidate, rank)
    role = candidate["selection_role"]
    if (
        result.get("technical_failure") is not False
        or result.get("valid_replay") is not True
        or result.get("qps_used_for_inner_scoring") is not False
        or result.get("accepted_update_eligible") is not (role == "trusted")
        or result.get("probe_updates_forbidden") is not (role == "probe")
    ):
        raise EvidenceError(f"rank {rank} formal result flags mismatch")
    resource = result.get("actual_resource") or {}
    stored_score = finite(result.get("actual_score"), f"rank{rank}.score")
    score, utilization = score_resource(resource)
    if not math.isclose(
        stored_score,
        score,
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        raise EvidenceError(f"rank {rank} actual score mismatch")
    stored_util = result.get("actual_utilization") or {}
    for name in RESOURCES:
        if not math.isclose(
            finite(stored_util.get(name), f"rank{rank}.utilization.{name}"),
            utilization[name],
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise EvidenceError(
                f"rank {rank} actual utilization mismatch: {name}"
            )
    return copy.deepcopy(dict(result))


def validate_retry2(
    summary: Mapping[str, Any],
    events: List[Dict[str, Any]],
    candidates: List[Dict[str, Any]],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if (
        summary.get("schema_version")
        != "tpch_exact_plan_regime_formal_batch_summary_v1"
        or int(summary.get("accepted_count", -1)) != 1
        or int(summary.get("batch_added_accepted_count", -1)) != 0
        or int(summary.get("physical_call_count", -1)) != 2
        or int(summary.get("valid_replay_count", -1)) != 1
        or int(summary.get("technical_failure_count", -1)) != 1
        or summary.get("baseline_restored") is not True
        or len(summary.get("successful") or []) != 1
        or len(summary.get("technical_failures") or []) != 1
    ):
        raise EvidenceError("retry2 summary accounting mismatch")
    failure = (summary.get("technical_failures") or [])[0]
    if (
        int(failure.get("rank", -1)) != 1
        or failure.get("selection_role") != "trusted"
        or failure.get("error")
        != "TechnicalFailure: workload exited with status 2"
        or failure.get("technical_failure") is not True
    ):
        raise EvidenceError("retry2 rank1 failure signature changed")
    started = find_exact_event(
        events,
        "tpch_formal_real_replay_started",
        1,
    )
    event_failure = find_exact_event(
        events,
        "tpch_formal_real_replay_technical_failure",
        1,
    )
    if (
        event_failure.get("error") != failure.get("error")
        or event_failure.get("technical_failure") is not True
    ):
        raise EvidenceError("retry2 rank1 event/summary failure mismatch")
    rank2 = validate_result(
        (summary.get("successful") or [])[0],
        candidates[1],
        2,
    )
    event_rank2 = find_exact_event(
        events,
        "tpch_formal_real_replay_complete",
        2,
    )
    if {
        key: value
        for key, value in event_rank2.items()
        if key not in {"event", "timestamp_epoch"}
    } != rank2:
        raise EvidenceError("retry2 rank2 event/summary mismatch")
    restored = find_exact_event(
        events,
        "tpch_formal_baseline_params_restored",
    )
    baseline = parse_baseline_verification(restored.get("verification"))
    return started, {
        "rank2": rank2,
        "baseline_verification": baseline,
    }


def validate_retry3(
    summary: Mapping[str, Any],
    events: List[Dict[str, Any]],
    candidates: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, float]]:
    if (
        summary.get("schema_version")
        != "tpch_exact_plan_regime_formal_batch_summary_v1"
        or int(summary.get("accepted_count", -1)) != 1
        or int(summary.get("batch_added_accepted_count", -1)) != 0
        or int(summary.get("physical_call_count", -1)) != 3
        or int(summary.get("valid_replay_count", -1)) != 3
        or int(summary.get("technical_failure_count", -1)) != 0
        or summary.get("baseline_restored") is not True
        or summary.get("selection_sha256") != SELECTION_SHA256
        or summary.get("selection_replayed_ranks") != [3, 4, 5]
        or int(summary.get("maximum_real_replay_invocations", -1)) != 3
        or summary.get("full_selection_replay") is not False
        or summary.get("accepted_decision_deferred") is not True
        or len(summary.get("successful") or []) != 3
        or (summary.get("technical_failures") or []) != []
    ):
        raise EvidenceError("retry3 summary/physical accounting mismatch")
    results: List[Dict[str, Any]] = []
    for expected_rank, raw in zip(
        (3, 4, 5),
        summary.get("successful") or [],
    ):
        result = validate_result(
            raw,
            candidates[expected_rank - 1],
            expected_rank,
        )
        event = find_exact_event(
            events,
            "tpch_formal_real_replay_complete",
            expected_rank,
        )
        if {
            key: value
            for key, value in event.items()
            if key not in {"event", "timestamp_epoch"}
        } != result:
            raise EvidenceError(
                f"retry3 rank {expected_rank} event/summary mismatch"
            )
        results.append(result)
    partial = find_exact_event(
        events,
        "tpch_formal_partial_selection_complete",
    )
    if (
        partial.get("accepted_decision_deferred") is not True
        or partial.get("selection_sha256") != SELECTION_SHA256
        or partial.get("selection_replayed_ranks") != [3, 4, 5]
        or partial.get("incumbent_update_applied") is not False
        or partial.get("range_update_applied") is not False
        or partial.get("gradient_update_applied") is not False
    ):
        raise EvidenceError("retry3 partial-completion event mismatch")
    restored = find_exact_event(
        events,
        "tpch_formal_baseline_params_restored",
    )
    return results, parse_baseline_verification(restored.get("verification"))


def validate_formal_state(state: Mapping[str, Any]) -> Dict[str, Any]:
    if (
        state.get("schema_version")
        != "tpch_exact_plan_regime_formal_state_v1"
        or state.get("status") != "complete"
        or state.get("baseline_restored") is not True
        or int(state.get("accepted_count", -1)) != 1
        or int(state.get("technical_failure_count", -1)) != 0
        or not math.isclose(
            finite(state.get("incumbent_score"), "incumbent_score"),
            INCUMBENT_SCORE,
            rel_tol=0.0,
            abs_tol=1e-12,
        )
        or not isinstance(state.get("incumbent_params"), dict)
        or set(state.get("incumbent_params") or {}) != ALLOWED_PARAMS
        or not isinstance(state.get("incumbent_resource"), dict)
        or set(state.get("incumbent_resource") or {}) != set(RESOURCES)
        or not isinstance(state.get("range_state"), dict)
        or set(state.get("range_state") or {}) != ALLOWED_PARAMS
    ):
        raise EvidenceError("formal accepted1 state is not a clean incumbent")
    score, _ = score_resource(state["incumbent_resource"])
    if not math.isclose(
        score,
        INCUMBENT_SCORE,
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        raise EvidenceError("formal incumbent resource/score mismatch")
    return copy.deepcopy(dict(state))


def update_counter_atomically(
    counter: Path,
    expected_before: int,
    after: int,
    backup: Path,
) -> Dict[str, Any]:
    lock_path = counter.with_name(f"{counter.name}.tpch_merge.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        before_bytes = counter.read_bytes()
        try:
            before = int(before_bytes.decode("utf-8").strip())
        except (UnicodeDecodeError, ValueError) as exc:
            raise EvidenceError("accepted counter is invalid") from exc
        if before != expected_before:
            raise EvidenceError(
                f"accepted counter changed: {before} != {expected_before}"
            )
        atomic_text(backup, before_bytes.decode("utf-8"))
        before_sha = hashlib.sha256(before_bytes).hexdigest()
        atomic_text(counter, f"{after}\n")
        after_sha = file_sha256(counter)
        persisted_after = int(
            counter.read_text(encoding="utf-8").strip()
        )
        if persisted_after != after:
            raise EvidenceError("accepted counter atomic update did not persist")
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
    return {
        "before": before,
        "after": persisted_after,
        "before_sha256": before_sha,
        "after_sha256": after_sha,
        "backup_path": str(backup),
        "backup_sha256": file_sha256(backup),
        "atomic_replace": True,
    }


def main() -> int:
    args = parse_args()
    if args.output.exists():
        raise SystemExit(f"output already exists: {args.output}")
    if args.selection_sha256 != SELECTION_SHA256:
        raise SystemExit("original round27 selection SHA contract mismatch")
    if args.controller_sha256 != CONTROLLER_SHA256:
        raise SystemExit("retry3 continuation controller SHA mismatch")
    if args.formal_state_sha256 != FORMAL_STATE_SHA256:
        raise SystemExit("clean accepted1 formal-state SHA mismatch")
    fixed_inputs = (
        (
            args.selection,
            args.selection_sha256,
            "round27 selection",
        ),
        (
            args.controller,
            args.controller_sha256,
            "retry3 controller",
        ),
        (
            args.retry2_summary,
            args.retry2_summary_sha256,
            "retry2 summary",
        ),
        (
            args.retry2_events,
            args.retry2_events_sha256,
            "retry2 events",
        ),
        (
            args.rank1_workload_log,
            args.rank1_workload_log_sha256,
            "rank1 workload log",
        ),
        (
            args.rank1_candidate_summary,
            args.rank1_candidate_summary_sha256,
            "rank1 candidate summary",
        ),
        (
            args.retry3_summary,
            args.retry3_summary_sha256,
            "retry3 summary",
        ),
        (
            args.retry3_events,
            args.retry3_events_sha256,
            "retry3 events",
        ),
        (
            args.formal_state,
            args.formal_state_sha256,
            "formal accepted1 state",
        ),
    )
    for path, expected, label in fixed_inputs:
        require_sha(path, expected, label)
    expected_retry2_args = {
        "summary": args.retry2_summary_sha256,
        "events": args.retry2_events_sha256,
        "rank1_workload_log": args.rank1_workload_log_sha256,
        "rank1_candidate_summary":
            args.rank1_candidate_summary_sha256,
    }
    if expected_retry2_args != RETRY2_EXPECTED_SHA256:
        raise SystemExit("retry2 immutable evidence SHA contract mismatch")
    if not args.accepted_counter.is_file():
        raise SystemExit("accepted counter is missing")
    if int(args.accepted_counter.read_text(
        encoding="utf-8"
    ).strip()) != 1:
        raise SystemExit("accepted counter must equal 1 before merge")

    selection = load_json(args.selection)
    candidates = validate_selection(selection)
    retry2_summary = load_json(args.retry2_summary)
    retry2_events = load_jsonl(args.retry2_events)
    started_rank1, retry2_valid = validate_retry2(
        retry2_summary,
        retry2_events,
        candidates,
    )
    candidate_summary = load_json(args.rank1_candidate_summary)
    rank1_payload = extract_rank1_log_payload(
        args.rank1_workload_log,
        candidate_summary,
    )
    immutable_binding = {
        "selection_path": str(args.selection),
        "selection_sha256": args.selection_sha256,
        "retry2_summary_path": str(args.retry2_summary),
        "retry2_summary_sha256": args.retry2_summary_sha256,
        "retry2_events_path": str(args.retry2_events),
        "retry2_events_sha256": args.retry2_events_sha256,
        "rank1_workload_log_path": str(args.rank1_workload_log),
        "rank1_workload_log_sha256": args.rank1_workload_log_sha256,
        "rank1_candidate_summary_path": str(
            args.rank1_candidate_summary
        ),
        "rank1_candidate_summary_sha256":
            args.rank1_candidate_summary_sha256,
    }
    rank1_result = build_rank1_result(
        candidates[0],
        started_rank1,
        rank1_payload,
        immutable_binding,
    )
    rank1_result = validate_result(rank1_result, candidates[0], 1)
    rank2_result = retry2_valid["rank2"]

    retry3_summary = load_json(args.retry3_summary)
    retry3_events = load_jsonl(args.retry3_events)
    retry3_results, retry3_baseline = validate_retry3(
        retry3_summary,
        retry3_events,
        candidates,
    )
    formal_state = validate_formal_state(load_json(args.formal_state))
    results = [rank1_result, rank2_result, *retry3_results]
    for result, candidate in zip(results, candidates):
        result.setdefault(
            "utilization",
            copy.deepcopy(candidate["utilization"]),
        )
    if [int(result["rank"]) for result in results] != [1, 2, 3, 4, 5]:
        raise EvidenceError("merged replay ranks are not exactly 1..5")
    roles = [result["selection_role"] for result in results]
    if roles != ["trusted", "trusted", "trusted", "probe", "probe"]:
        raise EvidenceError("merged replay roles are not trusted3+probe2")
    if (
        int(retry2_summary["physical_call_count"])
        + int(retry3_summary["physical_call_count"])
        != 5
    ):
        raise EvidenceError("merged physical-call accounting is not 2+3=5")

    trusted = results[:3]
    best = max(
        trusted,
        key=lambda result: (
            float(result["actual_score"]),
            -int(result["rank"]),
        ),
    )
    strict_improvement = (
        float(best["actual_score"]) > INCUMBENT_SCORE + 1e-12
    )
    accepted_after = 2 if strict_improvement else 1
    merged_state = copy.deepcopy(formal_state)
    merged_state.update({
        "status": "complete",
        "accepted_count": accepted_after,
        "inherited_accepted_count": 1,
        "batch_added_accepted_count": 1 if strict_improvement else 0,
        "physical_call_count": 5,
        "valid_replay_count": 5,
        "technical_failure_count": 0,
        "baseline_restored": True,
        "selection_sha256": args.selection_sha256,
        "qps_used_for_inner_scoring": False,
        "merged_retry2_physical_call_count": 2,
        "merged_retry3_physical_call_count": 3,
        "rank1_zero_call_reclassified": True,
        "best_trusted_rank": int(best["rank"]),
        "best_trusted_score": float(best["actual_score"]),
        "accepted_decision": (
            "strict_improvement" if strict_improvement else "no_improvement"
        ),
        "updated_at_epoch": time.time(),
    })
    if strict_improvement:
        merged_state["incumbent_params"] = copy.deepcopy(
            best["candidate_params"]
        )
        merged_state["incumbent_resource"] = copy.deepcopy(
            best["actual_resource"]
        )
        merged_state["incumbent_score"] = float(best["actual_score"])
        merged_state["range_state"] = shrink_ranges(
            formal_state["range_state"],
            best["candidate_params"],
        )

    input_bindings = {
        label: {
            "path": str(path),
            "sha256": expected,
        }
        for path, expected, label in fixed_inputs
    }
    args.output.mkdir(parents=True)
    rank1_path = args.output / "rank1_reclassified_result.json"
    atomic_json(rank1_path, rank1_result)
    results_path = args.output / "merged_top5_results.json"
    atomic_json(results_path, {
        "schema_version": "tpch_round27_merged_top5_results_v1",
        "selection_sha256": args.selection_sha256,
        "physical_call_count": 5,
        "valid_replay_count": 5,
        "technical_failure_count": 0,
        "trusted_count": 3,
        "probe_count": 2,
        "qps_used_for_inner_scoring": False,
        "results": results,
    })
    merged_state_path = args.output / "formal_state.json"
    atomic_json(merged_state_path, merged_state)
    counter_backup = (
        args.output / "backups/formal_accepted_counter.before.txt"
    )
    atomic_text(
        counter_backup,
        args.accepted_counter.read_text(encoding="utf-8"),
    )
    commit_prepare = {
        "schema_version": "tpch_round27_zero_call_merge_prepare_v1",
        "prepared_at_epoch": time.time(),
        "commit_requested": args.commit,
        "accepted_count_before": 1,
        "accepted_count_after": accepted_after,
        "strict_improvement": strict_improvement,
        "best_trusted_rank": int(best["rank"]),
        "best_trusted_score": float(best["actual_score"]),
        "incumbent_score_before": INCUMBENT_SCORE,
        "probe_results_excluded_from_acceptance": True,
        "gradient_update_applied": False,
        "input_bindings": input_bindings,
        "rank1_reclassified_result_sha256": file_sha256(rank1_path),
        "merged_top5_results_sha256": file_sha256(results_path),
        "formal_state_sha256": file_sha256(merged_state_path),
        "counter_backup_sha256": file_sha256(counter_backup),
        "baseline_restore_verification": {
            "retry2_summary_baseline_restored": True,
            "retry2_events_readback": retry2_valid[
                "baseline_verification"
            ],
            "retry3_summary_baseline_restored": True,
            "retry3_events_readback": retry3_baseline,
            "source_formal_state_baseline_restored": True,
        },
        "physical_call_accounting": {
            "retry2": 2,
            "retry3": 3,
            "total": 5,
            "new_calls_by_this_tool": 0,
        },
        "technical_failure_accounting": {
            "retry2_original": 1,
            "rank1_explained_qps_outer_only": 1,
            "retry3": 0,
            "unexplained_after_reclassification": 0,
        },
    }
    prepare_path = args.output / "commit_prepare.json"
    atomic_json(prepare_path, commit_prepare)

    counter_update: Dict[str, Any] = {
        "mutated": False,
        "reason": (
            "dry_run"
            if not args.commit
            else "no_strict_improvement"
            if not strict_improvement
            else "pending"
        ),
        "before": 1,
        "after": 1,
        "backup_path": str(counter_backup),
        "backup_sha256": file_sha256(counter_backup),
    }
    if args.commit and strict_improvement:
        counter_update = update_counter_atomically(
            args.accepted_counter,
            expected_before=1,
            after=2,
            backup=counter_backup,
        )
        counter_update["mutated"] = True
        counter_update["reason"] = "best_trusted_strictly_improved_incumbent"
    elif args.commit:
        if int(args.accepted_counter.read_text(
            encoding="utf-8"
        ).strip()) != 1:
            raise EvidenceError("accepted counter changed during no-op commit")
        counter_update["reason"] = "no_trusted_strict_improvement"

    final_audit = {
        **commit_prepare,
        "schema_version": "tpch_round27_zero_call_merge_audit_v1",
        "completed_at_epoch": time.time(),
        "counter_update": counter_update,
        "formal_state_authoritative_for_next_resume": bool(args.commit),
        "formal_state_path": str(merged_state_path),
        "formal_state_sha256": file_sha256(merged_state_path),
        "accepted_commit_applied": bool(args.commit and strict_improvement),
        "incumbent_update_applied": bool(
            args.commit and strict_improvement
        ),
        "range_update_applied": bool(args.commit and strict_improvement),
        "probe_update_applied": False,
    }
    audit_path = args.output / "audit.json"
    atomic_json(audit_path, final_audit)
    manifest = {
        "schema_version": "tpch_round27_zero_call_merge_manifest_v1",
        "input_bindings": input_bindings,
        "selection_sha256": args.selection_sha256,
        "rank1_reclassified_result_path": str(rank1_path),
        "rank1_reclassified_result_sha256": file_sha256(rank1_path),
        "merged_top5_results_path": str(results_path),
        "merged_top5_results_sha256": file_sha256(results_path),
        "formal_state_path": str(merged_state_path),
        "formal_state_sha256": file_sha256(merged_state_path),
        "commit_prepare_path": str(prepare_path),
        "commit_prepare_sha256": file_sha256(prepare_path),
        "audit_path": str(audit_path),
        "audit_sha256": file_sha256(audit_path),
        "accepted_count_before": 1,
        "accepted_count_after": accepted_after,
        "commit_requested": args.commit,
        "new_workload_calls": 0,
        "physical_call_count": 5,
        "valid_replay_count": 5,
        "technical_failure_count_after_reclassification": 0,
        "qps_used_for_inner_scoring": False,
    }
    manifest_path = args.output / "manifest.json"
    atomic_json(manifest_path, manifest)
    print(json.dumps({
        "output": str(args.output),
        "manifest_sha256": file_sha256(manifest_path),
        "accepted_count_after": accepted_after,
        "best_trusted_rank": int(best["rank"]),
        "best_trusted_score": float(best["actual_score"]),
        "commit_requested": args.commit,
        "new_workload_calls": 0,
    }, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

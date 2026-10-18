#!/usr/bin/env python3
"""Build an auditable, fail-closed direction-training dataset from PG replays.

Historical optimizer logs contain different workloads, resource limits and
schemas.  They are useful as provenance and a prior, but they must never be
blindly pooled into a TPC-C/TPC-H direction.  This tool extracts only real
candidate replays, keeps their workload/resource scope, and produces two
separate conclusions:

* individual parameter signs, only from predominantly single-parameter
  movements; and
* a *probe-only* reversal for a coherent failed candidate-direction family.

The latter is intentionally not an outer acceptance.  A caller still has to
run the real benchmark and satisfy the shared QPS-up plus bottleneck-down
gate before an interval may shrink.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from replay_direction_confidence import (  # noqa: E402
    DirectionConfidenceConfig,
    ReplayDirectionSample,
    VectorFailureRecoveryConfig,
    calibrate_replay_directions,
    calibrate_vector_failure_recovery,
)


WORKLOAD_TOKENS: Tuple[Tuple[str, str], ...] = (
    ("tpcc_sensitive", "tpcc_sensitive"),
    ("tpcc", "tpcc"),
    ("tpch", "tpch"),
    ("hybench_ap", "hybench_ap"),
    ("hybench_xp", "hybench_xp"),
    ("hyxp", "hybench_xp"),
    ("hybenchxp", "hybench_xp"),
    ("hybench_tp", "hybench_tp"),
    ("job", "job"),
)
RESOURCE_NAMES = ("cpu", "memory", "io")
PARAMETER_SEMANTIC_PRIORS: Mapping[str, Mapping[str, Any]] = {
    "seq_page_cost": {
        "positive_step_plan_tendency": {
            "decrease": ["Seq Scan"],
            "increase": ["Index Scan", "Index Only Scan", "Bitmap Heap Scan"],
        },
        "positive_step_resource_tendency": {"io": "context_dependent"},
        "rationale": "raising sequential-page cost discourages plans dominated by sequential page reads",
    },
    "random_page_cost": {
        "positive_step_plan_tendency": {
            "decrease": ["Index Scan", "Index Only Scan", "Bitmap Index Scan"],
            "increase": ["Seq Scan"],
        },
        "positive_step_resource_tendency": {"io": "context_dependent"},
        "rationale": "raising random-page cost discourages random index access but total IO depends on selectivity",
    },
    "cpu_tuple_cost": {
        "positive_step_plan_tendency": {
            "decrease": ["high-row tuple-processing paths"],
            "increase": ["more selective access paths"],
        },
        "positive_step_resource_tendency": {"cpu": "decrease"},
        "rationale": "raising per-tuple CPU cost favors plans expected to process fewer tuples",
    },
    "cpu_index_tuple_cost": {
        "positive_step_plan_tendency": {
            "decrease": ["Index Scan", "Index Only Scan", "Bitmap Index Scan"],
            "increase": ["Seq Scan"],
        },
        "positive_step_resource_tendency": {"cpu": "context_dependent", "io": "context_dependent"},
        "rationale": "raising index-tuple CPU cost discourages index tuple processing",
    },
    "cpu_operator_cost": {
        "positive_step_plan_tendency": {
            "decrease": ["Sort", "Hash", "Aggregate", "operator-heavy filters"],
            "increase": ["lower operator-count alternatives"],
        },
        "positive_step_resource_tendency": {"cpu": "decrease"},
        "rationale": "raising per-operator CPU cost favors plans with fewer estimated operator evaluations",
    },
    "parallel_setup_cost": {
        "positive_step_plan_tendency": {
            "decrease": ["Gather", "Gather Merge", "parallel workers"],
            "increase": ["serial alternatives"],
        },
        "positive_step_resource_tendency": {"cpu": "decrease"},
        "rationale": "raising parallel startup cost suppresses parallel plans and aggregate CPU concurrency",
    },
    "parallel_tuple_cost": {
        "positive_step_plan_tendency": {
            "decrease": ["Gather", "Gather Merge", "parallel tuple transfer"],
            "increase": ["serial alternatives"],
        },
        "positive_step_resource_tendency": {"cpu": "decrease"},
        "rationale": "raising parallel tuple-transfer cost suppresses tuple-heavy parallel plans",
    },
}


@dataclass(frozen=True)
class ReplayRecord:
    """One valid actual replay relative to its real incumbent."""

    sample_id: str
    source_path: str
    source_line: int
    scope_id: str
    workload: str
    target_resource: str
    scenario: str
    evidence_kind: str
    resource_profile: Mapping[str, float]
    parameter_log_delta: Mapping[str, float]
    changed_parameter_count: int
    parameter_attribution_eligible: bool
    db_qps_delta_pct: float
    target_util_delta_pp: float
    accepted_by_common_outer: bool
    candidate_plan_signature: Optional[str]
    stable_measurement: bool


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_token(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _sample_id(source_path: Path, source_line: int, suffix: Any) -> str:
    """Build a sample ID that remains unique across same-named evidence files."""
    source_token = hashlib.sha256(
        str(source_path.resolve()).encode("utf-8")
    ).hexdigest()[:16]
    return f"{source_path.name}:{source_token}:{source_line}:{suffix}"


def _workload_from_path(path: Path) -> str:
    lowered = str(path).lower()
    for token, workload in WORKLOAD_TOKENS:
        if token in lowered:
            return workload
    return "unknown"


def _normalize_workload(value: Any, path: Path) -> str:
    candidate = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "hybenchxp": "hybench_xp",
        "hyxp": "hybench_xp",
        "hybenchap": "hybench_ap",
        "hybenchtp": "hybench_tp",
    }
    candidate = aliases.get(candidate, candidate)
    known = {item[1] for item in WORKLOAD_TOKENS}
    return candidate if candidate in known else _workload_from_path(path)


def _workload_from_event(event: Mapping[str, Any], path: Path) -> str:
    raw = event.get("workload")
    if isinstance(raw, Mapping):
        raw = raw.get("name") or raw.get("workload") or raw.get("type")
    if not raw:
        raw_result = event.get("raw_result")
        if isinstance(raw_result, Mapping):
            raw = raw_result.get("workload")
    return _normalize_workload(raw, path)


def _scenario_from_event(
    event: Mapping[str, Any],
    path: Path,
    target: Optional[str] = None,
) -> str:
    for source in (event, event.get("raw_result"), event.get("physical_semantics")):
        if isinstance(source, Mapping):
            candidate = str(source.get("scenario") or source.get("target") or "").strip().lower()
            if candidate in (*RESOURCE_NAMES, "none"):
                return candidate
    path_parts = {part.lower() for part in path.parts}
    for candidate in (*RESOURCE_NAMES, "none"):
        if candidate in path_parts:
            return candidate
    return target or "unknown"


def _target_from_event(event: Mapping[str, Any], path: Path) -> Optional[str]:
    for source in (
        event,
        event.get("common_outer_acceptance"),
        event.get("target_direction_details"),
        event.get("primary_target_direction_details"),
        event.get("status_before"),
    ):
        if isinstance(source, Mapping):
            candidate = str(
                source.get("target_resource")
                or source.get("target")
                or source.get("scenario")
                or ""
            ).strip().lower()
            if candidate in RESOURCE_NAMES:
                return candidate
            resources = source.get("target_resources")
            if isinstance(resources, list) and resources:
                candidate = str(resources[0]).strip().lower()
                if candidate in RESOURCE_NAMES:
                    return candidate
        elif isinstance(source, list):
            for item in source:
                if isinstance(item, Mapping):
                    candidate = str(item.get("resource") or "").strip().lower()
                    if candidate in RESOURCE_NAMES:
                        return candidate
    path_parts = {part.lower() for part in path.parts}
    for resource in RESOURCE_NAMES:
        if resource in path_parts:
            return resource
    return None


def _validation_qps(validation: Mapping[str, Any]) -> Optional[float]:
    for key in ("formal_qps", "db_qps", "qps", "xp_qps", "ap_qps", "tp_tps", "native_tps"):
        value = _finite(validation.get(key))
        if value is not None and value > 0.0:
            return value
    benchmark = validation.get("benchmark")
    if isinstance(benchmark, Mapping):
        for key in ("db_qps", "formal_qps", "qps", "xp_qps", "ap_qps", "tp_tps", "native_tps"):
            value = _finite(benchmark.get(key))
            if value is not None and value > 0.0:
                return value
    return None


def _validation_utilization_pct(
    validation: Mapping[str, Any],
    resource: str,
) -> Optional[float]:
    direct = _finite(validation.get(f"{resource}_utilization"))
    if direct is not None:
        return direct * 100.0 if abs(direct) <= 2.0 else direct
    actual = validation.get("actual_utilization_pct")
    if isinstance(actual, Mapping):
        value = _finite(actual.get(resource))
        if value is not None:
            return value
    percent = validation.get("utilization_pct")
    if isinstance(percent, Mapping):
        value = _finite(percent.get(resource))
        if value is not None:
            return value
    normalized = validation.get("resource_utilization")
    if isinstance(normalized, Mapping):
        value = _finite(normalized.get(resource))
        if value is not None:
            return value * 100.0
    resources = validation.get("resources")
    runtime_capability = validation.get("runtime_capability")
    if isinstance(resources, Mapping) and isinstance(runtime_capability, Mapping):
        raw = _finite(resources.get(resource))
        cap = _finite(runtime_capability.get(resource))
        if raw is not None and cap is not None and cap > 0.0:
            return raw / cap * 100.0
    formal = validation.get("resource_raw")
    capability = validation.get("capability")
    if isinstance(formal, Mapping) and isinstance(capability, Mapping):
        raw = _finite(formal.get(resource))
        cap = _finite(capability.get(resource))
        if raw is not None and cap is not None and cap > 0.0:
            return raw / cap * 100.0
    return None


def _validation_is_stable(validation: Mapping[str, Any]) -> Optional[bool]:
    sources: List[Mapping[str, Any]] = [validation]
    stats = validation.get("resource_stats")
    if isinstance(stats, Mapping):
        sources.append(stats)
    for source in sources:
        protocol = source.get("candidate_measurement_protocol")
        if isinstance(protocol, Mapping) and "stable" in protocol:
            return protocol.get("stable") is True
        window = source.get("window_mean_stability")
        if isinstance(window, Mapping) and "stable" in window:
            return window.get("stable") is True
    return None


def _resource_profile(validation: Mapping[str, Any]) -> Dict[str, float]:
    runtime_capability = validation.get("runtime_capability")
    if isinstance(runtime_capability, Mapping):
        profile = {
            key: value
            for key in RESOURCE_NAMES
            if (value := _finite(runtime_capability.get(key))) is not None and value > 0.0
        }
        if profile:
            return profile
    sources: List[Mapping[str, Any]] = [validation]
    stats = validation.get("resource_stats")
    if isinstance(stats, Mapping):
        sources.append(stats)
    for source in sources:
        formal_pg = source.get("formal_pg_resource")
        if not isinstance(formal_pg, Mapping):
            continue
        caps = formal_pg.get("caps")
        if not isinstance(caps, Mapping):
            continue
        raw_profile = {
            "cpu": caps.get("cpu_quota_cores"),
            "memory": caps.get("memory_bytes"),
            "io": caps.get("io_total_cap_bps"),
        }
        profile = {
            key: value
            for key, raw in raw_profile.items()
            if (value := _finite(raw)) is not None and value > 0.0
        }
        if profile:
            return profile
    return {}


def _candidate_plan_signature(validation: Mapping[str, Any]) -> Optional[str]:
    direct = str(validation.get("plan_fingerprint") or "").strip()
    if direct:
        return direct
    summary = validation.get("plan_change_summary")
    if not isinstance(summary, Mapping):
        return None
    hashes = summary.get("changed_plan_detail_files")
    if not isinstance(hashes, list) or not hashes:
        return None
    stable_hashes = []
    for item in hashes:
        if isinstance(item, Mapping):
            value = str(item.get("current_plan_hash") or "").strip()
            if value:
                stable_hashes.append(value)
    if not stable_hashes:
        return None
    return _sha256_token(sorted(stable_hashes))


def _parameter_log_delta(
    incumbent_params: Mapping[str, Any],
    candidate_params: Mapping[str, Any],
) -> Dict[str, float]:
    result: Dict[str, float] = {}
    for parameter, before_raw in incumbent_params.items():
        before = _finite(before_raw)
        after = _finite(candidate_params.get(parameter))
        if before is None or after is None or before <= 0.0 or after <= 0.0:
            continue
        delta = math.log(after / before)
        if abs(delta) > 1e-12:
            result[str(parameter)] = delta
    return result


def _has_invalidated_ancestor(path: Path) -> bool:
    if "failed_attempts" in path.parts:
        return True
    current = path.parent
    for _ in range(8):
        if (current / "INVALIDATED.json").is_file():
            return True
        if current == current.parent:
            break
        current = current.parent
    return False


def _supported_input_name(path: Path) -> bool:
    name = path.name.lower()
    if path.suffix == ".jsonl":
        return "history" in name or name == "fresh_model_calibration.jsonl"
    if path.suffix == ".json":
        return name in {"pair_result.json", "point_results.json"} or name.endswith("_summary.json")
    return False


def _iter_input_files(inputs: Sequence[Path]) -> Iterator[Path]:
    seen = set()
    for item in inputs:
        if item.is_file() and item.suffix in {".json", ".jsonl"}:
            candidates = [item]
        elif item.is_dir():
            candidates = (
                path
                for path in item.rglob("*")
                if path.is_file() and _supported_input_name(path)
            )
        else:
            continue
        for path in candidates:
            resolved = path.resolve()
            if resolved not in seen:
                seen.add(resolved)
                yield resolved


def _event_candidate_records(
    *,
    event: Mapping[str, Any],
    source_path: Path,
    source_line: int,
    workload_filter: Optional[str],
    target_filter: Optional[str],
    context_params: Optional[Mapping[str, Any]] = None,
    context_validation: Optional[Mapping[str, Any]] = None,
) -> List[ReplayRecord]:
    candidates = event.get("candidate_evaluations")
    if not isinstance(candidates, list):
        return []
    incumbent_params = event.get("round_start_params")
    if not isinstance(incumbent_params, Mapping):
        incumbent_params = event.get("baseline_params")
    if not isinstance(incumbent_params, Mapping):
        incumbent_params = context_params
    incumbent_validation = event.get("final_validation")
    if not isinstance(incumbent_validation, Mapping):
        incumbent_validation = event.get("baseline_validation")
    if not isinstance(incumbent_validation, Mapping):
        incumbent_validation = context_validation
    if not isinstance(incumbent_params, Mapping):
        return []
    workload = _workload_from_event(event, source_path)
    if workload_filter and workload != workload_filter:
        return []
    group_target = _target_from_event(event, source_path)
    records: List[ReplayRecord] = []
    for position, candidate in enumerate(candidates, start=1):
        if not isinstance(candidate, Mapping):
            continue
        params = candidate.get("candidate_optimized_params")
        validation = candidate.get("candidate_final_validation")
        common = candidate.get("common_outer_acceptance")
        if not isinstance(params, Mapping) or not isinstance(validation, Mapping):
            continue
        if not isinstance(common, Mapping):
            common = {}
        target = str(common.get("target_resource") or group_target or "").strip().lower()
        if target not in RESOURCE_NAMES or (target_filter and target != target_filter):
            continue
        qps_before = _validation_qps(incumbent_validation) if isinstance(incumbent_validation, Mapping) else None
        qps_after = _validation_qps(validation)
        util_before = (
            _validation_utilization_pct(incumbent_validation, target)
            if isinstance(incumbent_validation, Mapping)
            else None
        )
        util_after = _validation_utilization_pct(validation, target)
        qps_before = (
            _finite(common.get("incumbent_db_qps"))
            or _finite(common.get("db_qps_before"))
            or qps_before
        )
        qps_after = (
            _finite(common.get("candidate_db_qps"))
            or _finite(common.get("db_qps_after"))
            or qps_after
        )
        common_util_before = _finite(
            common.get(
                "incumbent_target_utilization",
                common.get("target_utilization_before"),
            )
        )
        common_util_after = _finite(
            common.get(
                "candidate_target_utilization",
                common.get("target_utilization_after"),
            )
        )
        util_before = common_util_before if common_util_before is not None else util_before
        util_after = common_util_after if common_util_after is not None else util_after
        if None in {qps_before, qps_after, util_before, util_after}:
            continue
        baseline_stable = (
            _validation_is_stable(incumbent_validation)
            if isinstance(incumbent_validation, Mapping)
            else None
        )
        candidate_stable = _validation_is_stable(validation)
        if baseline_stable is False or candidate_stable is False:
            continue
        log_delta = _parameter_log_delta(incumbent_params, params)
        if not log_delta:
            continue
        scope_payload = {
            "source_path": str(source_path),
            "source_line": source_line,
            "round_index": event.get("round_index"),
            "target_resource": target,
            "incumbent_params": incumbent_params,
        }
        sample_id = _sample_id(source_path, source_line, position)
        scenario = _scenario_from_event(common, source_path, target)
        records.append(
            ReplayRecord(
                sample_id=sample_id,
                source_path=str(source_path),
                source_line=source_line,
                scope_id=_sha256_token(scope_payload),
                workload=workload,
                target_resource=target,
                scenario=scenario,
                evidence_kind="optimizer_candidate_replay",
                resource_profile=(
                    _resource_profile(incumbent_validation)
                    if isinstance(incumbent_validation, Mapping)
                    else _resource_profile(validation)
                ),
                parameter_log_delta=log_delta,
                changed_parameter_count=len(log_delta),
                parameter_attribution_eligible=len(log_delta) == 1,
                db_qps_delta_pct=(qps_after / qps_before - 1.0) * 100.0,
                target_util_delta_pp=util_after - util_before,
                accepted_by_common_outer=common.get("accepted") is True,
                candidate_plan_signature=_candidate_plan_signature(validation),
                stable_measurement=(
                    candidate_stable is True and baseline_stable is not False
                ),
            )
        )
    return records


def _event_legacy_final_summary_record(
    *,
    event: Mapping[str, Any],
    source_path: Path,
    source_line: int,
    workload_filter: Optional[str],
    target_filter: Optional[str],
) -> Optional[ReplayRecord]:
    """Extract one legacy ``final_summary`` replay as cross-scope prior data.

    Older runs predate ``candidate_evaluations``.  They have one initial/final
    real measurement, so they are useful to report the broad historical sign
    distribution but never sufficient to emit a same-incumbent direction
    override on their own.
    """
    if event.get("event") != "final_summary":
        return None
    initial_params = event.get("initial_params")
    final_params = event.get("optimized_params")
    if not isinstance(initial_params, Mapping) or not isinstance(final_params, Mapping):
        return None
    workload = _workload_from_event(event, source_path)
    if workload_filter and workload != workload_filter:
        return None
    target = _target_from_event(event, source_path)
    if target not in RESOURCE_NAMES or (target_filter and target != target_filter):
        return None
    qps_before = _finite(event.get("initial_qps"))
    qps_after = _finite(event.get("final_qps"))
    initial_util = event.get("actual_initial_utilization_pct")
    final_util = event.get("actual_final_utilization_pct")
    if not isinstance(initial_util, Mapping) or not isinstance(final_util, Mapping):
        return None
    util_before = _finite(initial_util.get(target))
    util_after = _finite(final_util.get(target))
    if (
        qps_before is None
        or qps_after is None
        or qps_before <= 0.0
        or util_before is None
        or util_after is None
    ):
        return None
    log_delta = _parameter_log_delta(initial_params, final_params)
    if not log_delta:
        return None
    scope_payload = {
        "source_path": str(source_path),
        "source_line": source_line,
        "target_resource": target,
        "incumbent_params": initial_params,
        "legacy_final_summary": True,
    }
    return ReplayRecord(
        sample_id=_sample_id(source_path, source_line, "legacy_final_summary"),
        source_path=str(source_path),
        source_line=source_line,
        scope_id=_sha256_token(scope_payload),
        workload=workload,
        target_resource=target,
        scenario=_scenario_from_event(event, source_path, target),
        evidence_kind="legacy_final_summary",
        resource_profile={},
        parameter_log_delta=log_delta,
        changed_parameter_count=len(log_delta),
        parameter_attribution_eligible=len(log_delta) == 1,
        db_qps_delta_pct=(qps_after / qps_before - 1.0) * 100.0,
        target_util_delta_pp=util_after - util_before,
        accepted_by_common_outer=(qps_after > qps_before and util_after < util_before),
        candidate_plan_signature=None,
        stable_measurement=False,
    )


def _fresh_calibration_record(
    *,
    event: Mapping[str, Any],
    baseline: Mapping[str, Any],
    source_path: Path,
    source_line: int,
    workload_filter: Optional[str],
    target_filter: Optional[str],
) -> Optional[ReplayRecord]:
    if event.get("event") != "fresh_model_calibration_replay" or event.get("status") != "usable":
        return None
    raw_result = event.get("raw_result")
    baseline_raw = baseline.get("raw")
    incumbent_params = baseline.get("params")
    params = event.get("params")
    if not all(isinstance(item, Mapping) for item in (raw_result, baseline_raw, incumbent_params, params)):
        return None
    assert isinstance(raw_result, Mapping)
    assert isinstance(baseline_raw, Mapping)
    assert isinstance(incumbent_params, Mapping)
    assert isinstance(params, Mapping)
    if (
        raw_result.get("status") != "ok"
        or raw_result.get("double_loop_measurement_usable") is not True
        or baseline_raw.get("status") != "ok"
        or baseline_raw.get("double_loop_measurement_usable") is not True
    ):
        return None
    workload = _workload_from_event(raw_result, source_path)
    target = _target_from_event(raw_result, source_path)
    if target not in RESOURCE_NAMES:
        target = str(baseline.get("scenario") or "").strip().lower()
    if target not in RESOURCE_NAMES:
        return None
    if workload_filter and workload != workload_filter:
        return None
    if target_filter and target != target_filter:
        return None
    qps_before = _validation_qps(baseline_raw)
    qps_after = _validation_qps(raw_result)
    util_before = _validation_utilization_pct(baseline_raw, target)
    util_after = _validation_utilization_pct(raw_result, target)
    if None in {qps_before, qps_after, util_before, util_after} or qps_before is None or qps_before <= 0.0:
        return None
    log_delta = _parameter_log_delta(incumbent_params, params)
    if not log_delta:
        return None
    declared_parameter = str(event.get("parameter") or "").strip()
    attribution_eligible = len(log_delta) == 1 and declared_parameter in log_delta
    scenario = _scenario_from_event(baseline, source_path, target)
    profile = _resource_profile(baseline_raw) or _resource_profile(raw_result)
    scope_payload = {
        "calibration_file": str(source_path),
        "fresh_prefix_source": baseline.get("fresh_prefix_source"),
        "workload": workload,
        "target_resource": target,
        "scenario": scenario,
        "resource_profile": profile,
        "incumbent_params": incumbent_params,
    }
    phase = str(event.get("phase") or "fresh_calibration")
    return ReplayRecord(
        sample_id=_sample_id(
            source_path,
            source_line,
            event.get("calibration_id") or source_line,
        ),
        source_path=str(source_path),
        source_line=source_line,
        scope_id=_sha256_token(scope_payload),
        workload=workload,
        target_resource=target,
        scenario=scenario,
        evidence_kind=(
            "fresh_coordinate_calibration"
            if attribution_eligible
            else f"fresh_vector_calibration:{phase}"
        ),
        resource_profile=profile,
        parameter_log_delta=log_delta,
        changed_parameter_count=len(log_delta),
        parameter_attribution_eligible=attribution_eligible,
        db_qps_delta_pct=(qps_after / qps_before - 1.0) * 100.0,
        target_util_delta_pp=util_after - util_before,
        accepted_by_common_outer=False,
        candidate_plan_signature=_candidate_plan_signature(raw_result),
        stable_measurement=True,
    )


def _point_resource_profile(point: Mapping[str, Any]) -> Dict[str, float]:
    profile = _resource_profile(point)
    if profile:
        return profile
    resources = point.get("resource_avg")
    if not isinstance(resources, Mapping):
        resources = point.get("resources")
    if not isinstance(resources, Mapping):
        return {}
    result: Dict[str, float] = {}
    for resource in RESOURCE_NAMES:
        raw = _finite(resources.get(resource))
        util = _validation_utilization_pct(point, resource)
        if raw is not None and util is not None and util > 0.0:
            result[resource] = raw / (util / 100.0)
    return result


def _paired_points_record(
    *,
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    source_path: Path,
    source_line: int,
    workload: str,
    target: str,
    scenario: str,
    evidence_kind: str,
    stable_measurement: bool,
    workload_filter: Optional[str],
    target_filter: Optional[str],
    sample_suffix: str,
) -> Optional[ReplayRecord]:
    if workload_filter and workload != workload_filter:
        return None
    if target not in RESOURCE_NAMES or (target_filter and target != target_filter):
        return None
    incumbent_params = baseline.get("params")
    params = candidate.get("params")
    if not isinstance(incumbent_params, Mapping) or not isinstance(params, Mapping):
        return None
    qps_before = _validation_qps(baseline)
    qps_after = _validation_qps(candidate)
    util_before = _validation_utilization_pct(baseline, target)
    util_after = _validation_utilization_pct(candidate, target)
    if None in {qps_before, qps_after, util_before, util_after} or qps_before is None or qps_before <= 0.0:
        return None
    log_delta = _parameter_log_delta(incumbent_params, params)
    if not log_delta:
        return None
    profile = _point_resource_profile(baseline) or _point_resource_profile(candidate)
    scope_payload = {
        "source_path": str(source_path),
        "workload": workload,
        "target_resource": target,
        "scenario": scenario,
        "resource_profile": profile,
        "incumbent_params": incumbent_params,
        "evidence_kind": evidence_kind,
    }
    return ReplayRecord(
        sample_id=_sample_id(source_path, source_line, sample_suffix),
        source_path=str(source_path),
        source_line=source_line,
        scope_id=_sha256_token(scope_payload),
        workload=workload,
        target_resource=target,
        scenario=scenario,
        evidence_kind=evidence_kind,
        resource_profile=profile,
        parameter_log_delta=log_delta,
        changed_parameter_count=len(log_delta),
        parameter_attribution_eligible=len(log_delta) == 1,
        db_qps_delta_pct=(qps_after / qps_before - 1.0) * 100.0,
        target_util_delta_pp=util_after - util_before,
        accepted_by_common_outer=False,
        candidate_plan_signature=_candidate_plan_signature(candidate),
        stable_measurement=stable_measurement,
    )


def _pair_result_record(
    *,
    document: Mapping[str, Any],
    source_path: Path,
    workload_filter: Optional[str],
    target_filter: Optional[str],
) -> Optional[ReplayRecord]:
    points = document.get("points")
    if not isinstance(points, Mapping):
        return None
    baseline = points.get("baseline")
    candidate = points.get("candidate")
    if not isinstance(baseline, Mapping) or not isinstance(candidate, Mapping):
        return None
    workload = _workload_from_event(document, source_path)
    target = _target_from_event(document, source_path)
    comparison = document.get("comparison")
    if target not in RESOURCE_NAMES and isinstance(comparison, Mapping):
        for resource in RESOURCE_NAMES:
            if f"baseline_{resource}_utilization" in comparison:
                target = resource
                break
    if target not in RESOURCE_NAMES:
        return None
    return _paired_points_record(
        baseline=baseline,
        candidate=candidate,
        source_path=source_path,
        source_line=1,
        workload=workload,
        target=target,
        scenario=_scenario_from_event(document, source_path, target),
        evidence_kind="paired_result_vector",
        stable_measurement=False,
        workload_filter=workload_filter,
        target_filter=target_filter,
        sample_suffix="pair_result",
    )


def _summary_validation(summary_path: Path) -> Optional[Mapping[str, Any]]:
    try:
        document = json.loads(summary_path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(document, Mapping):
        return None
    validation = document.get("validation")
    return validation if isinstance(validation, Mapping) else None


def _point_results_records(
    *,
    document: Mapping[str, Any],
    source_path: Path,
    workload_filter: Optional[str],
    target_filter: Optional[str],
) -> List[ReplayRecord]:
    points = document.get("points")
    if not isinstance(points, list):
        return []
    baseline_entries = [item for item in points if isinstance(item, Mapping) and item.get("counts_as_candidate") is False]
    candidate_entries = [item for item in points if isinstance(item, Mapping) and item.get("counts_as_candidate") is True]
    if not baseline_entries:
        return []
    baseline_entry = baseline_entries[0]
    baseline_path = Path(str(baseline_entry.get("summary_path") or ""))
    baseline = _summary_validation(baseline_path)
    if not isinstance(baseline, Mapping):
        return []
    baseline = dict(baseline)
    attempts = baseline_entry.get("attempts")
    if isinstance(attempts, list) and attempts and isinstance(attempts[-1], Mapping):
        baseline["resource_utilization"] = attempts[-1].get("utilization_ratios")
    workload = _workload_from_event(baseline, source_path)
    target = _target_from_event(baseline, source_path)
    records: List[ReplayRecord] = []
    for index, entry in enumerate(candidate_entries, start=1):
        candidate_path = Path(str(entry.get("summary_path") or ""))
        candidate = _summary_validation(candidate_path)
        if not isinstance(candidate, Mapping):
            continue
        candidate = dict(candidate)
        attempts = entry.get("attempts")
        if isinstance(attempts, list) and attempts and isinstance(attempts[-1], Mapping):
            candidate["resource_utilization"] = attempts[-1].get("utilization_ratios")
        candidate_target = _target_from_event(candidate, source_path) or target
        record = _paired_points_record(
            baseline=baseline,
            candidate=candidate,
            source_path=source_path,
            source_line=1,
            workload=workload,
            target=candidate_target,
            scenario=_scenario_from_event(candidate, source_path, candidate_target),
            evidence_kind="paired_summary_vector",
            stable_measurement=(baseline_entry.get("stable") is True and entry.get("stable") is True),
            workload_filter=workload_filter,
            target_filter=target_filter,
            sample_suffix=f"point_pair_{index}",
        )
        if record is not None:
            records.append(record)
    return records


def extract_replay_records(
    inputs: Sequence[Path],
    *,
    workload_filter: Optional[str] = None,
    target_filter: Optional[str] = None,
) -> Tuple[List[ReplayRecord], Dict[str, int]]:
    """Extract parseable real replay rows from supported history events."""

    records: List[ReplayRecord] = []
    audit = Counter()
    for source_path in _iter_input_files(inputs):
        audit["input_files_seen"] += 1
        if _has_invalidated_ancestor(source_path):
            audit["input_files_rejected_invalidated_or_failed_attempt"] += 1
            continue
        if source_path.suffix == ".json":
            audit["json_files_seen"] += 1
            try:
                document = json.loads(source_path.read_text(encoding="utf-8", errors="replace"))
            except (OSError, json.JSONDecodeError):
                audit["json_files_invalid_or_unreadable"] += 1
                continue
            if not isinstance(document, Mapping):
                audit["json_files_non_object"] += 1
                continue
            extracted_json: List[ReplayRecord] = []
            if source_path.name == "pair_result.json":
                audit["pair_result_files_seen"] += 1
                pair_record = _pair_result_record(
                    document=document,
                    source_path=source_path,
                    workload_filter=workload_filter,
                    target_filter=target_filter,
                )
                if pair_record is not None:
                    extracted_json.append(pair_record)
                    audit["pair_result_rows_extracted"] += 1
            elif source_path.name == "point_results.json":
                audit["point_results_files_seen"] += 1
                point_records = _point_results_records(
                    document=document,
                    source_path=source_path,
                    workload_filter=workload_filter,
                    target_filter=target_filter,
                )
                extracted_json.extend(point_records)
                audit["paired_summary_rows_extracted"] += len(point_records)
            elif document.get("event") == "final_summary":
                record = _event_legacy_final_summary_record(
                    event=document,
                    source_path=source_path,
                    source_line=1,
                    workload_filter=workload_filter,
                    target_filter=target_filter,
                )
                if record is not None:
                    extracted_json.append(record)
                    audit["legacy_final_summary_rows_extracted"] += 1
            else:
                audit["summary_json_files_not_pairable"] += 1
            records.extend(extracted_json)
            continue
        audit["jsonl_files_seen"] += 1
        fresh_baseline: Optional[Mapping[str, Any]] = None
        if source_path.name == "fresh_model_calibration.jsonl":
            audit["fresh_calibration_files_seen"] += 1
            baseline_path = source_path.with_name("fresh_baseline.json")
            try:
                baseline_document = json.loads(baseline_path.read_text(encoding="utf-8", errors="replace"))
            except (OSError, json.JSONDecodeError):
                audit["fresh_calibration_files_missing_valid_baseline"] += 1
                continue
            if not isinstance(baseline_document, Mapping):
                audit["fresh_calibration_files_missing_valid_baseline"] += 1
                continue
            fresh_baseline = baseline_document
        try:
            handle = source_path.open(encoding="utf-8", errors="replace")
        except OSError:
            audit["history_files_unreadable"] += 1
            continue
        round_params: Dict[int, Mapping[str, Any]] = {}
        round_validation: Dict[int, Mapping[str, Any]] = {}
        startup_params: Optional[Mapping[str, Any]] = None
        with handle:
            for source_line, line in enumerate(handle, start=1):
                if fresh_baseline is None and not any(
                    marker in line
                    for marker in (
                        '"candidate_evaluations"',
                        '"event": "final_summary"',
                        '"event":"final_summary"',
                        '"formal_search_accepted_checkpoint_validation_restored"',
                        '"gradient_optimization_round_start"',
                    )
                ):
                    audit["json_lines_fast_skipped_irrelevant_schema"] += 1
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    audit["json_lines_invalid"] += 1
                    continue
                if not isinstance(event, Mapping):
                    continue
                if fresh_baseline is not None:
                    audit["fresh_calibration_rows_seen"] += 1
                    fresh_record = _fresh_calibration_record(
                        event=event,
                        baseline=fresh_baseline,
                        source_path=source_path,
                        source_line=source_line,
                        workload_filter=workload_filter,
                        target_filter=target_filter,
                    )
                    if fresh_record is not None:
                        records.append(fresh_record)
                        audit["fresh_calibration_rows_extracted"] += 1
                        if fresh_record.parameter_attribution_eligible:
                            audit["fresh_coordinate_rows_extracted"] += 1
                        else:
                            audit["fresh_vector_rows_extracted"] += 1
                    else:
                        audit["fresh_calibration_rows_rejected"] += 1
                    continue
                accepted_params = event.get("accepted_params")
                if event.get("event") == "formal_search_accepted_checkpoint_validation_restored" and isinstance(accepted_params, Mapping):
                    startup_params = accepted_params
                start_params = event.get("start_params")
                round_index = event.get("round_index")
                if isinstance(round_index, int) and isinstance(start_params, Mapping):
                    round_params[round_index] = start_params
                if isinstance(round_index, int) and isinstance(event.get("round_start_params"), Mapping):
                    round_params[round_index] = event["round_start_params"]
                if isinstance(round_index, int) and isinstance(event.get("final_validation"), Mapping):
                    round_validation[round_index] = event["final_validation"]
                if isinstance(event.get("candidate_evaluations"), list):
                    audit["candidate_evaluation_events_seen"] += 1
                    extracted = _event_candidate_records(
                        event=event,
                        source_path=source_path,
                        source_line=source_line,
                        workload_filter=workload_filter,
                        target_filter=target_filter,
                        context_params=round_params.get(round_index, startup_params) if isinstance(round_index, int) else startup_params,
                        context_validation=round_validation.get(round_index) if isinstance(round_index, int) else None,
                    )
                    records.extend(extracted)
                    audit["candidate_rows_extracted"] += len(extracted)
                else:
                    legacy = _event_legacy_final_summary_record(
                        event=event,
                        source_path=source_path,
                        source_line=source_line,
                        workload_filter=workload_filter,
                        target_filter=target_filter,
                    )
                    if legacy is not None:
                        records.append(legacy)
                        audit["legacy_final_summary_rows_extracted"] += 1
    deduplicated: List[ReplayRecord] = []
    fingerprints = set()
    for record in records:
        fingerprint = _sha256_token(
            {
                "workload": record.workload,
                "target_resource": record.target_resource,
                "scenario": record.scenario,
                "resource_profile": record.resource_profile,
                "parameter_log_delta": record.parameter_log_delta,
                "db_qps_delta_pct": record.db_qps_delta_pct,
                "target_util_delta_pp": record.target_util_delta_pp,
                "evidence_kind": record.evidence_kind,
            }
        )
        if fingerprint in fingerprints:
            audit["semantic_duplicate_rows_removed"] += 1
            continue
        fingerprints.add(fingerprint)
        deduplicated.append(record)
    records = deduplicated
    records.sort(key=lambda item: (item.source_path, item.source_line, item.sample_id))
    sample_ids = [record.sample_id for record in records]
    if len(sample_ids) != len(set(sample_ids)):
        duplicates = sorted(
            sample_id
            for sample_id, count in Counter(sample_ids).items()
            if count > 1
        )
        raise ValueError(
            "duplicate replay sample IDs after extraction: "
            + ", ".join(duplicates[:10])
        )
    return records, dict(audit)


def _median_direction(records: Sequence[ReplayRecord]) -> Dict[str, float]:
    values: Dict[str, List[float]] = defaultdict(list)
    for record in records:
        for parameter, delta in record.parameter_log_delta.items():
            values[parameter].append(float(delta))
    medians = {
        parameter: float(statistics.median(deltas))
        for parameter, deltas in values.items()
    }
    # A symmetric lower/baseline/upper calibration scope legitimately has a
    # zero median even though individual samples are non-zero.  Passing that
    # zero vector to cosine-based failure recovery is invalid; represent it as
    # no prototype direction and let _scope_decision fail closed.
    return {
        parameter: median
        for parameter, median in medians.items()
        if abs(median) > 1e-12
    }


def _scope_decision(records: Sequence[ReplayRecord]) -> Dict[str, Any]:
    samples = [
        ReplayDirectionSample(
            parameter_log_delta=record.parameter_log_delta,
            db_qps_delta_pct=record.db_qps_delta_pct,
            target_util_delta_pp=record.target_util_delta_pp,
            sample_id=record.sample_id,
        )
        for record in records
    ]
    prototype = _median_direction(records)
    if not prototype:
        return {"status": "no_nonzero_direction", "sample_count": len(records)}
    recovery = calibrate_vector_failure_recovery(
        samples,
        prototype,
        config=VectorFailureRecoveryConfig(),
    )
    attributable_records = [record for record in records if record.parameter_attribution_eligible]
    attributable_samples = [
        ReplayDirectionSample(
            parameter_log_delta=record.parameter_log_delta,
            db_qps_delta_pct=record.db_qps_delta_pct,
            target_util_delta_pp=record.target_util_delta_pp,
            sample_id=record.sample_id,
        )
        for record in attributable_records
    ]
    attributable_prototype = _median_direction(attributable_records)
    proposed_signs = {
        parameter: 1 if value > 0.0 else -1
        for parameter, value in attributable_prototype.items()
        if value != 0.0
    }
    per_parameter = (
        calibrate_replay_directions(
            attributable_samples,
            proposed_signs,
            config=DirectionConfidenceConfig(),
        )
        if attributable_samples and proposed_signs
        else {}
    )
    return {
        "status": "calibrated",
        "sample_count": len(samples),
        "vector_evidence_sample_count": len(samples),
        "parameter_attribution_sample_count": len(attributable_samples),
        "multi_parameter_vectors_excluded_from_parameter_attribution": len(samples) - len(attributable_samples),
        "candidate_family_prototype_log_direction": prototype,
        "per_parameter": {
            name: decision.to_dict()
            for name, decision in sorted(per_parameter.items())
        },
        "vector_failure_recovery": recovery.to_dict(),
        "runtime_policy": {
            "action": recovery.action,
            "probe_only": recovery.probe_only,
            "acceptance_proven": recovery.acceptance_proven,
            "eta_multiplier": recovery.eta_multiplier,
            "direction_override_log_vector": dict(
                recovery.reverse_probe_direction_vector
            ),
            "outer_acceptance_unchanged": True,
            "outer_shrink_forbidden_until_real_acceptance": True,
        },
    }


def _parameter_direction_statistics(records: Sequence[ReplayRecord]) -> Dict[str, Any]:
    statistics_by_parameter: Dict[str, Counter] = defaultdict(Counter)
    for record in records:
        if not record.parameter_attribution_eligible:
            continue
        joint_success = record.db_qps_delta_pct > 0.0 and record.target_util_delta_pp < 0.0
        for parameter, delta in record.parameter_log_delta.items():
            direction = "positive" if delta > 0.0 else "negative"
            outcome = "joint_success" if joint_success else "joint_failure"
            statistics_by_parameter[parameter]["sample_count"] += 1
            statistics_by_parameter[parameter][f"{direction}_step_count"] += 1
            statistics_by_parameter[parameter][f"{outcome}_count"] += 1
            statistics_by_parameter[parameter][f"{direction}_{outcome}_count"] += 1
    return {
        parameter: dict(sorted(counter.items()))
        for parameter, counter in sorted(statistics_by_parameter.items())
    }


def _semantic_prior_consistency_audit(records: Sequence[ReplayRecord]) -> List[Dict[str, Any]]:
    result = []
    attributable = [record for record in records if record.parameter_attribution_eligible]
    for parameter, prior in PARAMETER_SEMANTIC_PRIORS.items():
        parameter_records = [record for record in attributable if parameter in record.parameter_log_delta]
        empirical_by_target: Dict[str, Counter] = defaultdict(Counter)
        plan_signature_count = 0
        for record in parameter_records:
            delta = record.parameter_log_delta[parameter]
            normalized_positive_step_effect = record.target_util_delta_pp * (1.0 if delta > 0.0 else -1.0)
            if normalized_positive_step_effect < -1e-12:
                effect = "decrease"
            elif normalized_positive_step_effect > 1e-12:
                effect = "increase"
            else:
                effect = "tie"
            empirical_by_target[record.target_resource][effect] += 1
            if record.candidate_plan_signature:
                plan_signature_count += 1
        comparisons: Dict[str, Any] = {}
        resource_tendency = prior.get("positive_step_resource_tendency", {})
        if isinstance(resource_tendency, Mapping):
            for target, expected in resource_tendency.items():
                counts = empirical_by_target.get(str(target), Counter())
                observed = "no_evidence"
                if counts:
                    non_tie = {key: counts[key] for key in ("decrease", "increase") if counts[key]}
                    if len(non_tie) == 1:
                        observed = next(iter(non_tie))
                    elif len(non_tie) > 1:
                        observed = "mixed"
                    else:
                        observed = "tie"
                if expected == "context_dependent":
                    consistency = "not_applicable_context_dependent_prior"
                elif observed == "no_evidence":
                    consistency = "no_real_single_parameter_evidence"
                elif observed == "mixed":
                    consistency = "mixed"
                elif observed == expected:
                    consistency = "consistent"
                else:
                    consistency = "conflict"
                comparisons[str(target)] = {
                    "expected_positive_step_effect": expected,
                    "observed_positive_step_effect": observed,
                    "observed_counts": dict(sorted(counts.items())),
                    "consistency": consistency,
                }
        result.append(
            {
                "parameter": parameter,
                "counts_as_real_sample": False,
                "eligible_for_confidence_or_runtime_direction": False,
                "semantic_prior": dict(prior),
                "real_single_parameter_sample_count": len(parameter_records),
                "real_samples_with_plan_signature_count": plan_signature_count,
                "target_resource_comparisons": comparisons,
            }
        )
    return result


def build_dataset_document(
    records: Sequence[ReplayRecord],
    audit: Mapping[str, int],
    *,
    inputs: Sequence[Path],
    workload_filter: Optional[str],
    target_filter: Optional[str],
) -> Dict[str, Any]:
    """Return a JSON-serializable evidence and direction-calibration document."""

    by_scope: Dict[str, List[ReplayRecord]] = defaultdict(list)
    by_workload_target: Dict[Tuple[str, str], List[ReplayRecord]] = defaultdict(list)
    by_workload_target_scenario: Dict[Tuple[str, str, str], List[ReplayRecord]] = defaultdict(list)
    for record in records:
        by_scope[record.scope_id].append(record)
        by_workload_target[(record.workload, record.target_resource)].append(record)
        by_workload_target_scenario[(record.workload, record.target_resource, record.scenario)].append(record)
    scopes = []
    for scope_id, scope_records in sorted(by_scope.items()):
        first = scope_records[0]
        scopes.append(
            {
                "scope_id": scope_id,
                "workload": first.workload,
                "target_resource": first.target_resource,
                "scenario": first.scenario,
                "resource_profile": dict(first.resource_profile),
                "record_ids": [record.sample_id for record in scope_records],
                "sample_count": len(scope_records),
                "joint_success_count": sum(
                    record.db_qps_delta_pct > 0.0 and record.target_util_delta_pp < 0.0
                    for record in scope_records
                ),
                "joint_failure_count": sum(
                    not (record.db_qps_delta_pct > 0.0 and record.target_util_delta_pp < 0.0)
                    for record in scope_records
                ),
                "parameter_direction_statistics": _parameter_direction_statistics(scope_records),
                "decision": _scope_decision(scope_records),
            }
        )
    groups = []
    for (workload, target), group_records in sorted(by_workload_target.items()):
        joint_success = sum(
            record.db_qps_delta_pct > 0.0 and record.target_util_delta_pp < 0.0
            for record in group_records
        )
        groups.append(
            {
                "workload": workload,
                "target_resource": target,
                "sample_count": len(group_records),
                "scope_count": len({record.scope_id for record in group_records}),
                "joint_success_count": joint_success,
                "joint_failure_count": len(group_records) - joint_success,
                "joint_success_rate": joint_success / len(group_records),
                "parameter_attribution_sample_count": sum(
                    record.parameter_attribution_eligible for record in group_records
                ),
                "vector_only_sample_count": sum(
                    not record.parameter_attribution_eligible for record in group_records
                ),
                "parameter_direction_statistics": _parameter_direction_statistics(group_records),
                "policy": (
                    "cross-scope rows are a prior only; a runtime direction override "
                    "may be emitted only by one same-incumbent scope decision"
                ),
            }
        )
    scenario_groups = []
    for (workload, target, scenario), group_records in sorted(by_workload_target_scenario.items()):
        joint_success = sum(
            record.db_qps_delta_pct > 0.0 and record.target_util_delta_pp < 0.0
            for record in group_records
        )
        scenario_groups.append(
            {
                "workload": workload,
                "target_resource": target,
                "scenario": scenario,
                "sample_count": len(group_records),
                "scope_count": len({record.scope_id for record in group_records}),
                "joint_success_count": joint_success,
                "joint_failure_count": len(group_records) - joint_success,
                "parameter_attribution_sample_count": sum(
                    record.parameter_attribution_eligible for record in group_records
                ),
                "vector_only_sample_count": sum(
                    not record.parameter_attribution_eligible for record in group_records
                ),
                "evidence_kind_counts": dict(sorted(Counter(record.evidence_kind for record in group_records).items())),
                "parameter_direction_statistics": _parameter_direction_statistics(group_records),
            }
        )
    return {
        "schema_version": 2,
        "artifact_type": "pg_real_replay_direction_training_dataset_v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "input_roots": [str(path.resolve()) for path in inputs],
        "filters": {
            "workload": workload_filter,
            "target_resource": target_filter,
            "only_real_candidate_evaluations": True,
            "requires_explicitly_stable_when_stability_present": True,
            "multi_parameter_vectors_never_used_for_parameter_attribution": True,
            "failed_attempts_and_invalidated_ancestors_rejected": True,
        },
        "extraction_audit": dict(audit),
        "workload_target_groups": groups,
        "workload_target_scenario_groups": scenario_groups,
        "semantic_plan_resource_prior_audit": _semantic_prior_consistency_audit(records),
        "same_incumbent_scope_decisions": scopes,
        "records": [asdict(record) for record in records],
    }


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        action="append",
        required=True,
        help="History JSONL file or directory; repeat for multiple roots.",
    )
    parser.add_argument("--output", required=True, help="Output JSON artifact path.")
    parser.add_argument("--workload", choices=[item[1] for item in WORKLOAD_TOKENS])
    parser.add_argument("--target-resource", choices=list(RESOURCE_NAMES))
    parser.add_argument(
        "--source-line-cap",
        action="append",
        default=[],
        metavar="PATH=LINE",
        help=(
            "Keep records from PATH only through LINE (inclusive). Repeatable; "
            "useful for freezing a dataset at completed replay rounds."
        ),
    )
    return parser.parse_args(argv)


def _parse_source_line_caps(values: Sequence[str]) -> Dict[Path, int]:
    caps: Dict[Path, int] = {}
    for raw_value in values:
        path_text, separator, line_text = str(raw_value).rpartition("=")
        if not separator or not path_text.strip():
            raise ValueError("--source-line-cap must use PATH=LINE")
        try:
            line_cap = int(line_text)
        except ValueError as exc:
            raise ValueError("--source-line-cap LINE must be an integer") from exc
        if line_cap <= 0:
            raise ValueError("--source-line-cap LINE must be positive")
        caps[Path(path_text).resolve()] = line_cap
    return caps


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    inputs = [Path(value) for value in args.input]
    records, audit = extract_replay_records(
        inputs,
        workload_filter=args.workload,
        target_filter=args.target_resource,
    )
    source_line_caps = _parse_source_line_caps(args.source_line_cap)
    if source_line_caps:
        before_count = len(records)
        records = [
            record
            for record in records
            if (
                Path(record.source_path).resolve() not in source_line_caps
                or record.source_line
                <= source_line_caps[Path(record.source_path).resolve()]
            )
        ]
        audit["records_removed_after_source_line_cap"] = before_count - len(records)
    document = build_dataset_document(
        records,
        audit,
        inputs=inputs,
        workload_filter=args.workload,
        target_filter=args.target_resource,
    )
    document["filters"]["source_line_caps"] = {
        str(path): line_cap
        for path, line_cap in sorted(
            source_line_caps.items(),
            key=lambda item: str(item[0]),
        )
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(output),
                "record_count": len(records),
                "scope_count": len(document["same_incumbent_scope_decisions"]),
                "group_count": len(document["workload_target_groups"]),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

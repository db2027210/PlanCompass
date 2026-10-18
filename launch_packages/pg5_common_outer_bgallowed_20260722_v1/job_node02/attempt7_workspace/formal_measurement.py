"""Stable-window evaluator for the frozen 2026-07-21 measurement protocol."""

from __future__ import annotations

import json
import hashlib
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import fmean, pstdev
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


RESOURCE_FIELDS = ("cpu_utilization", "memory_utilization", "io_utilization")
NORMAL_THROUGHPUT_FIELDS = ("qps",)
XP_THROUGHPUT_FIELDS = ("xap_qps", "xtp_tps")
STABILITY_GATE_CONTRACT_SHA256 = (
    "33cf52a83f4a11ba2fd1bffad703aee9c0192141749320b1ece65919a482f6bd"
)


class FrozenMeasurementError(RuntimeError):
    """Raised when samples cannot be interpreted under the frozen protocol."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class FrozenMeasurementProtocol:
    """FrozenMeasurementProtocol."""

    sample_interval_seconds: int
    warmup_seconds: int
    minimum_measurement_seconds: int
    maximum_measurement_seconds: int
    stability_window_seconds: int
    required_consecutive_stability_windows: int
    sample_coverage_min: float
    cpu_cv_max: float
    memory_cv_max: float
    io_cv_max: float
    normal_throughput_cv_max: float
    xp_xap_qps_cv_max: float
    xp_xtp_tps_cv_max: float
    low_utilization_ratio_threshold: float
    low_utilization_absolute_range_max: float
    stability_calculation: str
    normal_throughput_calculation: str

    @classmethod
    def from_frozen_bundle(cls, frozen_root: Path) -> "FrozenMeasurementProtocol":
        path = frozen_root / "measurement_protocol.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise FrozenMeasurementError(f"cannot read frozen measurement protocol: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("immutable") is not True:
            raise FrozenMeasurementError("measurement protocol is not an immutable object")
        supported_config_ids = {
            "measurement_protocol_20260721_v3",
            "measurement_protocol_20260722_v4_working_set_memory",
            "measurement_protocol_20260722_v5_online_early_stop",
            "measurement_protocol_20260722_v10_job_window_aggregate_qps",
            "measurement_protocol_20260722_v11_job_corrected_resource_bands",
            "measurement_protocol_20260722_v12_job_authoritative_gate",
            "measurement_protocol_20260722_v13_job_all_metrics_cross_window_cv10",
            "measurement_protocol_20260722_v14_job_raw_unbounded_cv10",
            "measurement_protocol_20260722_v15_job_two_terminals_cv10",
        }
        if payload.get("config_id") not in supported_config_ids:
            raise FrozenMeasurementError("unexpected measurement protocol config_id")
        timing = payload.get("timing")
        stability = payload.get("stability")
        if not isinstance(timing, dict) or not isinstance(stability, dict):
            raise FrozenMeasurementError("measurement timing or stability section is absent")
        contract_path = frozen_root / "stability_gate_contract.json"
        try:
            contract_sha256 = _sha256_file(contract_path)
            contract = json.loads(contract_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise FrozenMeasurementError(f"cannot read public stability contract: {exc}") from exc
        if contract_sha256 != STABILITY_GATE_CONTRACT_SHA256:
            raise FrozenMeasurementError("public stability contract checksum mismatch")
        contract_alignment = {
            "sample_interval_seconds": timing.get("sample_interval_seconds"),
            "warmup_seconds": timing.get("warmup_seconds"),
            "minimum_measurement_seconds": timing.get("minimum_measurement_seconds"),
            "maximum_measurement_seconds": timing.get("maximum_measurement_seconds"),
            "window_seconds": timing.get("stability_window_seconds"),
            "required_consecutive_complete_windows": timing.get(
                "required_consecutive_stability_windows"
            ),
            "per_window_coverage_min": timing.get("sample_coverage_min"),
            "aggregate_coverage_min": timing.get("sample_coverage_min"),
            "calculation": stability.get("calculation"),
            "coefficient_of_variation_max": stability.get("cpu_cv_max"),
            "within_window_one_second_cv_required": stability.get(
                "within_window_one_second_cv_required"
            ),
        }
        if any(contract.get(key) != value for key, value in contract_alignment.items()):
            raise FrozenMeasurementError("measurement protocol differs from public stability contract")
        if contract.get("normal_fields") != list(NORMAL_THROUGHPUT_FIELDS + RESOURCE_FIELDS):
            raise FrozenMeasurementError("public stability contract normal fields differ")
        if any(
            float(stability.get(field, -1.0))
            != float(contract["coefficient_of_variation_max"])
            for field in (
                "cpu_cv_max",
                "memory_cv_max",
                "io_cv_max",
                "normal_throughput_cv_max",
            )
        ):
            raise FrozenMeasurementError("metric CV limits differ from public stability contract")
        if stability.get("calculation") not in {
            "within_each_window_from_one_second_samples",
            "resources_within_each_window_and_qps_cv_across_latest_six_window_means",
            "all_metrics_window_means_cv_across_latest_six_windows",
            "coefficient_of_variation_across_six_window_means",
        }:
            raise FrozenMeasurementError("unexpected stability field calculation")
        if stability.get("all_cpu_memory_io_resources_must_be_stable") is not True:
            raise FrozenMeasurementError(
                "unexpected stability field all_cpu_memory_io_resources_must_be_stable"
            )
        protocol = cls(
            sample_interval_seconds=int(timing["sample_interval_seconds"]),
            warmup_seconds=int(timing["warmup_seconds"]),
            minimum_measurement_seconds=int(timing["minimum_measurement_seconds"]),
            maximum_measurement_seconds=int(timing["maximum_measurement_seconds"]),
            stability_window_seconds=int(timing["stability_window_seconds"]),
            required_consecutive_stability_windows=int(
                timing["required_consecutive_stability_windows"]
            ),
            sample_coverage_min=float(timing["sample_coverage_min"]),
            cpu_cv_max=float(stability["cpu_cv_max"]),
            memory_cv_max=float(stability["memory_cv_max"]),
            io_cv_max=float(stability["io_cv_max"]),
            normal_throughput_cv_max=float(stability["normal_throughput_cv_max"]),
            xp_xap_qps_cv_max=float(stability["xp_xap_qps_cv_max"]),
            xp_xtp_tps_cv_max=float(stability["xp_xtp_tps_cv_max"]),
            low_utilization_ratio_threshold=float(
                stability["low_utilization_ratio_threshold"]
            ),
            low_utilization_absolute_range_max=float(
                stability["low_utilization_absolute_range_max"]
            ),
            stability_calculation=str(stability["calculation"]),
            normal_throughput_calculation=str(
                stability.get(
                    "normal_throughput_calculation",
                    "within_each_window_from_one_second_samples",
                )
            ),
        )
        protocol.validate()
        return protocol

    def validate(self) -> None:
        """Fail closed if the frozen values cannot define exact whole windows."""
        fixed = {
            "sample_interval_seconds": 1,
            "warmup_seconds": 60,
            "minimum_measurement_seconds": 180,
            "maximum_measurement_seconds": 600,
            "stability_window_seconds": 30,
            "required_consecutive_stability_windows": 6,
            "sample_coverage_min": 0.9,
            "xp_xap_qps_cv_max": 0.1,
            "xp_xtp_tps_cv_max": 0.1,
            "low_utilization_ratio_threshold": 0.1,
            "low_utilization_absolute_range_max": 0.05,
        }
        for field, expected in fixed.items():
            if getattr(self, field) != expected:
                raise FrozenMeasurementError(f"frozen measurement value differs: {field}")
        if self.stability_calculation in {
            "all_metrics_window_means_cv_across_latest_six_windows",
            "coefficient_of_variation_across_six_window_means",
        }:
            unified_limits = {
                "cpu_cv_max": 0.10,
                "memory_cv_max": 0.10,
                "io_cv_max": 0.10,
                "normal_throughput_cv_max": 0.10,
            }
            for field, expected in unified_limits.items():
                if getattr(self, field) != expected:
                    raise FrozenMeasurementError(f"unified stability value differs: {field}")
        else:
            legacy_limits = {
                "cpu_cv_max": 0.05,
                "memory_cv_max": 0.03,
                "io_cv_max": 0.05,
                "normal_throughput_cv_max": 0.05,
            }
            for field, expected in legacy_limits.items():
                if getattr(self, field) != expected:
                    raise FrozenMeasurementError(f"legacy stability value differs: {field}")
        if self.normal_throughput_calculation not in {
            "within_each_window_from_one_second_samples",
            "per_window_mean_then_cv_across_latest_six_windows",
        }:
            raise FrozenMeasurementError("unsupported normal throughput calculation")
        if self.minimum_measurement_seconds % self.stability_window_seconds != 0:
            raise FrozenMeasurementError("minimum measurement duration does not contain whole windows")
        if (
            self.minimum_measurement_seconds // self.stability_window_seconds
            != self.required_consecutive_stability_windows
        ):
            raise FrozenMeasurementError("minimum duration and required window count disagree")


@dataclass(frozen=True)
class FieldStability:
    """Stability evidence for one metric in one fixed window."""

    field: str
    mean: float
    population_std: float
    coefficient_of_variation: Optional[float]
    absolute_range: float
    low_utilization_rule_used: bool
    limit: float
    stable: bool


@dataclass(frozen=True)
class WindowStability:
    """Complete evidence for one aligned measurement window."""

    window_index: int
    start_elapsed_seconds: float
    end_elapsed_seconds: float
    sample_count: int
    expected_sample_count: int
    coverage: float
    coverage_passed: bool
    fields: Tuple[FieldStability, ...]
    stable: bool


@dataclass(frozen=True)
class ThroughputBlockStability:
    """QPS evidence after aggregating each complete 30-second window."""

    field: str
    start_window_index: int
    end_window_index: int
    window_means: Tuple[float, ...]
    mean: float
    population_std: float
    coefficient_of_variation: Optional[float]
    limit: float
    coverage_passed: bool
    resource_windows_stable: bool
    stable: bool


@dataclass(frozen=True)
class CrossWindowFieldStability:
    """CV of one metric across the means of six complete aligned windows."""

    field: str
    window_means: Tuple[float, ...]
    mean: float
    population_std: float
    coefficient_of_variation: Optional[float]
    limit: float
    stable: bool


@dataclass(frozen=True)
class CrossWindowBlockStability:
    """Unified stability evidence for one consecutive six-window block."""

    start_window_index: int
    end_window_index: int
    coverage_passed: bool
    fields: Tuple[CrossWindowFieldStability, ...]
    stable: bool


@dataclass(frozen=True)
class MeasurementAssessment:
    """Decision about whether a single continuous replay may stop."""

    xp: bool
    elapsed_seconds: float
    complete_window_count: int
    consecutive_stable_windows: int
    earliest_stable_stop_seconds: Optional[int]
    stable: bool
    reached_hard_maximum: bool
    aggregate_sample_count: int
    aggregate_sample_coverage: float
    windows: Tuple[WindowStability, ...]
    normal_throughput_calculation: str
    throughput_blocks: Tuple[ThroughputBlockStability, ...]
    cross_window_blocks: Tuple[CrossWindowBlockStability, ...]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _finite_ratio(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FrozenMeasurementError(f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise FrozenMeasurementError(f"{field} must be finite")
    if result < 0.0:
        raise FrozenMeasurementError(f"{field} must be non-negative")
    return result


def _field_stability(
    field: str,
    values: Sequence[float],
    *,
    cv_limit: float,
    protocol: FrozenMeasurementProtocol,
) -> FieldStability:
    if not values:
        raise FrozenMeasurementError(f"window has no values for {field}")
    mean_value = fmean(values)
    std_value = pstdev(values)
    absolute_range = max(values) - min(values)
    low_rule = field in RESOURCE_FIELDS and mean_value < protocol.low_utilization_ratio_threshold
    if low_rule:
        coefficient = None if mean_value == 0.0 else std_value / mean_value
        limit = protocol.low_utilization_absolute_range_max
        stable = absolute_range <= limit
    else:
        coefficient = math.inf if mean_value == 0.0 else std_value / mean_value
        limit = cv_limit
        stable = coefficient <= limit
    return FieldStability(
        field=field,
        mean=mean_value,
        population_std=std_value,
        coefficient_of_variation=coefficient,
        absolute_range=absolute_range,
        low_utilization_rule_used=low_rule,
        limit=limit,
        stable=stable,
    )


def assess_measurement_samples(
    samples: Sequence[Mapping[str, Any]],
    *,
    measurement_start_epoch_seconds: float,
    now_epoch_seconds: float,
    xp: bool,
    protocol: FrozenMeasurementProtocol,
) -> MeasurementAssessment:
    """Assess aligned windows from one replay without combining separate attempts.

    Each sample must contain ``epoch_seconds`` plus CPU, memory and I/O utilization
    ratios. Normal runs additionally contain ``qps``; XP runs contain both frozen
    stream metrics. Only complete 30-second windows are considered.
    """
    protocol.validate()
    start = float(measurement_start_epoch_seconds)
    now = float(now_epoch_seconds)
    if not math.isfinite(start) or not math.isfinite(now) or now < start:
        raise FrozenMeasurementError("invalid measurement clock interval")
    elapsed = min(now - start, float(protocol.maximum_measurement_seconds))
    complete_windows = min(
        int(elapsed // protocol.stability_window_seconds),
        protocol.maximum_measurement_seconds // protocol.stability_window_seconds,
    )
    aggregate_all_metrics = (
        not xp
        and protocol.stability_calculation
        == "coefficient_of_variation_across_six_window_means"
    )
    aggregate_normal_qps = (
        not xp
        and protocol.normal_throughput_calculation
        == "per_window_mean_then_cv_across_latest_six_windows"
    )
    throughput_fields = XP_THROUGHPUT_FIELDS if xp else NORMAL_THROUGHPUT_FIELDS
    required_fields = (
        RESOURCE_FIELDS
        if aggregate_normal_qps or aggregate_all_metrics
        else RESOURCE_FIELDS + throughput_fields
    )
    cv_limits = {
        "cpu_utilization": protocol.cpu_cv_max,
        "memory_utilization": protocol.memory_cv_max,
        "io_utilization": protocol.io_cv_max,
        "qps": protocol.normal_throughput_cv_max,
        "xap_qps": protocol.xp_xap_qps_cv_max,
        "xtp_tps": protocol.xp_xtp_tps_cv_max,
    }
    expected = protocol.stability_window_seconds // protocol.sample_interval_seconds
    minimum_samples = int(math.ceil(expected * protocol.sample_coverage_min))

    parsed: List[Tuple[float, int, Mapping[str, Any]]] = []
    previous_epoch: Optional[float] = None
    for position, sample in enumerate(samples):
        if not isinstance(sample, Mapping):
            raise FrozenMeasurementError(f"sample {position} is not an object")
        epoch = _finite_ratio(sample.get("epoch_seconds"), f"sample[{position}].epoch_seconds")
        if previous_epoch is not None and epoch <= previous_epoch:
            raise FrozenMeasurementError("sample epoch_seconds must be strictly increasing")
        previous_epoch = epoch
        relative = epoch - start
        if relative < 0.0 or relative >= complete_windows * protocol.stability_window_seconds:
            continue
        slot = int(relative // protocol.sample_interval_seconds)
        parsed.append((relative, slot, sample))

    windows: List[WindowStability] = []
    normal_qps_window_means: List[Optional[float]] = []
    unified_fields = NORMAL_THROUGHPUT_FIELDS + RESOURCE_FIELDS
    unified_window_means: Dict[str, List[Optional[float]]] = {
        field: [] for field in unified_fields
    }
    for index in range(complete_windows):
        window_start = index * protocol.stability_window_seconds
        window_end = window_start + protocol.stability_window_seconds
        window_rows = [
            (slot, sample)
            for relative, slot, sample in parsed
            if window_start <= relative < window_end
        ]
        slots = [slot for slot, _sample in window_rows]
        rows = [sample for _slot, sample in window_rows]
        if len(rows) > expected or len(set(slots)) != len(slots):
            raise FrozenMeasurementError(
                f"window {index} contains duplicate samples in a frozen one-second slot"
            )
        coverage = min(1.0, len(rows) / expected)
        coverage_passed = len(rows) >= minimum_samples
        field_results: List[FieldStability] = []
        fields_present = True
        if rows:
            for field in required_fields:
                try:
                    values = [
                        _finite_ratio(row.get(field), f"window[{index}].{field}")
                        for row in rows
                    ]
                    field_results.append(
                        _field_stability(
                            field,
                            values,
                            cv_limit=cv_limits[field],
                            protocol=protocol,
                        )
                    )
                except FrozenMeasurementError:
                    fields_present = False
                    break
            if aggregate_all_metrics:
                for field in unified_fields:
                    try:
                        values = [
                            _finite_ratio(row.get(field), f"window[{index}].{field}")
                            for row in rows
                        ]
                        unified_window_means[field].append(fmean(values))
                    except FrozenMeasurementError:
                        fields_present = False
                        unified_window_means[field].append(None)
            elif aggregate_normal_qps:
                try:
                    qps_values = [
                        _finite_ratio(row.get("qps"), f"window[{index}].qps")
                        for row in rows
                    ]
                    normal_qps_window_means.append(fmean(qps_values))
                except FrozenMeasurementError:
                    fields_present = False
                    normal_qps_window_means.append(None)
        else:
            fields_present = False
            if aggregate_all_metrics:
                for field in unified_fields:
                    unified_window_means[field].append(None)
            elif aggregate_normal_qps:
                normal_qps_window_means.append(None)
        window_stable = (
            coverage_passed
            and fields_present
            and len(field_results) == len(required_fields)
            and (
                aggregate_all_metrics
                or all(field.stable for field in field_results)
            )
        )
        windows.append(
            WindowStability(
                window_index=index,
                start_elapsed_seconds=float(window_start),
                end_elapsed_seconds=float(window_end),
                sample_count=len(rows),
                expected_sample_count=expected,
                coverage=coverage,
                coverage_passed=coverage_passed,
                fields=tuple(field_results),
                stable=window_stable,
            )
        )

    throughput_blocks: List[ThroughputBlockStability] = []
    cross_window_blocks: List[CrossWindowBlockStability] = []
    if aggregate_all_metrics:
        block_size = protocol.required_consecutive_stability_windows
        for end_index in range(block_size - 1, len(windows)):
            start_index = end_index - block_size + 1
            block_windows = windows[start_index : end_index + 1]
            coverage_passed = all(window.coverage_passed for window in block_windows)
            field_results: List[CrossWindowFieldStability] = []
            for field in unified_fields:
                optional_means = unified_window_means[field][start_index : end_index + 1]
                have_all_means = all(value is not None for value in optional_means)
                window_means = tuple(
                    float(value) for value in optional_means if value is not None
                )
                limit = cv_limits[field]
                if have_all_means and len(window_means) == block_size:
                    mean_value = fmean(window_means)
                    std_value = pstdev(window_means)
                    coefficient = math.inf if mean_value == 0.0 else std_value / mean_value
                    stable = coefficient <= limit
                else:
                    mean_value = 0.0
                    std_value = 0.0
                    coefficient = None
                    stable = False
                field_results.append(
                    CrossWindowFieldStability(
                        field=field,
                        window_means=window_means,
                        mean=mean_value,
                        population_std=std_value,
                        coefficient_of_variation=coefficient,
                        limit=limit,
                        stable=stable,
                    )
                )
            block_stable = coverage_passed and all(field.stable for field in field_results)
            cross_window_blocks.append(
                CrossWindowBlockStability(
                    start_window_index=start_index,
                    end_window_index=end_index,
                    coverage_passed=coverage_passed,
                    fields=tuple(field_results),
                    stable=block_stable,
                )
            )
            qps_result = next(field for field in field_results if field.field == "qps")
            resources_stable = all(
                field.stable for field in field_results if field.field in RESOURCE_FIELDS
            )
            throughput_blocks.append(
                ThroughputBlockStability(
                    field="qps",
                    start_window_index=start_index,
                    end_window_index=end_index,
                    window_means=qps_result.window_means,
                    mean=qps_result.mean,
                    population_std=qps_result.population_std,
                    coefficient_of_variation=qps_result.coefficient_of_variation,
                    limit=qps_result.limit,
                    coverage_passed=coverage_passed,
                    resource_windows_stable=resources_stable,
                    stable=block_stable,
                )
            )
    elif aggregate_normal_qps:
        block_size = protocol.required_consecutive_stability_windows
        for end_index in range(block_size - 1, len(windows)):
            start_index = end_index - block_size + 1
            block_windows = windows[start_index : end_index + 1]
            block_means_optional = normal_qps_window_means[start_index : end_index + 1]
            have_all_means = all(value is not None for value in block_means_optional)
            block_means = tuple(
                float(value) for value in block_means_optional if value is not None
            )
            coverage_passed = all(window.coverage_passed for window in block_windows)
            resources_stable = all(window.stable for window in block_windows)
            if have_all_means and len(block_means) == block_size:
                mean_value = fmean(block_means)
                std_value = pstdev(block_means)
                coefficient = math.inf if mean_value == 0.0 else std_value / mean_value
                qps_stable = coefficient <= protocol.normal_throughput_cv_max
            else:
                mean_value = 0.0
                std_value = 0.0
                coefficient = None
                qps_stable = False
            throughput_blocks.append(
                ThroughputBlockStability(
                    field="qps",
                    start_window_index=start_index,
                    end_window_index=end_index,
                    window_means=block_means,
                    mean=mean_value,
                    population_std=std_value,
                    coefficient_of_variation=coefficient,
                    limit=protocol.normal_throughput_cv_max,
                    coverage_passed=coverage_passed,
                    resource_windows_stable=resources_stable,
                    stable=coverage_passed and resources_stable and qps_stable,
                )
            )

    consecutive = 0
    earliest_stop: Optional[int] = None
    cumulative_sample_count = 0
    for window in windows:
        consecutive = consecutive + 1 if window.stable else 0
        cumulative_sample_count += window.sample_count
        end_seconds = int(window.end_elapsed_seconds)
        cumulative_expected = (window.window_index + 1) * expected
        cumulative_coverage = (
            min(1.0, cumulative_sample_count / cumulative_expected)
            if cumulative_expected
            else 0.0
        )
        if aggregate_all_metrics:
            block = next(
                (
                    candidate
                    for candidate in cross_window_blocks
                    if candidate.end_window_index == window.window_index
                ),
                None,
            )
            gate_passed = bool(block is not None and block.stable)
        elif aggregate_normal_qps:
            block = next(
                (
                    candidate
                    for candidate in throughput_blocks
                    if candidate.end_window_index == window.window_index
                ),
                None,
            )
            gate_passed = bool(block is not None and block.stable)
        else:
            gate_passed = consecutive >= protocol.required_consecutive_stability_windows
        if (
            earliest_stop is None
            and end_seconds >= protocol.minimum_measurement_seconds
            and gate_passed
            and cumulative_coverage >= protocol.sample_coverage_min
        ):
            earliest_stop = end_seconds
    if aggregate_normal_qps or aggregate_all_metrics:
        blocks = cross_window_blocks if aggregate_all_metrics else throughput_blocks
        last_block = blocks[-1] if blocks else None
        consecutive = (
            protocol.required_consecutive_stability_windows
            if last_block is not None and last_block.stable
            else 0
        )
    total_expected = complete_windows * expected
    aggregate_count = sum(window.sample_count for window in windows)
    aggregate_coverage = min(1.0, aggregate_count / total_expected) if total_expected else 0.0
    reached_maximum = now - start >= protocol.maximum_measurement_seconds
    return MeasurementAssessment(
        xp=xp,
        elapsed_seconds=elapsed,
        complete_window_count=complete_windows,
        consecutive_stable_windows=consecutive,
        earliest_stable_stop_seconds=earliest_stop,
        stable=earliest_stop is not None,
        reached_hard_maximum=reached_maximum,
        aggregate_sample_count=aggregate_count,
        aggregate_sample_coverage=aggregate_coverage,
        windows=tuple(windows),
        normal_throughput_calculation=protocol.normal_throughput_calculation,
        throughput_blocks=tuple(throughput_blocks),
        cross_window_blocks=tuple(cross_window_blocks),
    )

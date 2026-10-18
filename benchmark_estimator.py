"""Resource-estimator adapters used by the unchanged optimization algorithm."""

from __future__ import annotations

import importlib
import json
import math
import os
import statistics
import subprocess
from typing import Any, Callable, Dict, List, Mapping, Optional

from mysql_cost_adapter import MySQLCostAdapter


def _normalize_resource(payload: Mapping[str, Any]) -> Dict[str, Any]:
    resource_raw = payload.get("resource", payload)
    if not isinstance(resource_raw, Mapping):
        raise ValueError("benchmark JSON field 'resource' must be an object")
    result: Dict[str, Any] = {}
    for name in ("cpu", "memory", "io"):
        value = float(resource_raw[name])
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"invalid benchmark resource {name}={value!r}")
        result[name] = value
    qps_raw = payload.get("qps")
    db_qps_raw = payload.get("db_qps")
    if qps_raw is not None and db_qps_raw is not None:
        qps = float(qps_raw)
        db_qps = float(db_qps_raw)
        if not math.isfinite(qps) or not math.isfinite(db_qps) or qps != db_qps:
            raise ValueError(f"canonical qps must equal db_qps: qps={qps_raw!r}, db_qps={db_qps_raw!r}")
        result["qps"] = qps
        result["db_qps"] = db_qps
    elif db_qps_raw is not None:
        db_qps = float(db_qps_raw)
        if not math.isfinite(db_qps):
            raise ValueError(f"invalid db_qps={db_qps_raw!r}")
        result["qps"] = db_qps
        result["db_qps"] = db_qps
    elif qps_raw is not None:
        qps = float(qps_raw)
        if not math.isfinite(qps):
            raise ValueError(f"invalid qps={qps_raw!r}")
        result["qps"] = qps
        result["db_qps"] = qps
    for optional_name in (
        "query_count", "query_time", "latency_p95_ms",
        "latency_p99_ms", "explain_samples", "_metadata", "_stats",
    ):
        if optional_name in payload:
            result[optional_name] = payload[optional_name]
    return result


def _last_json_object(output: str) -> Mapping[str, Any]:
    for line in reversed(output.splitlines()):
        candidate = line.strip()
        if candidate.startswith("RESOURCE_METRICS="):
            candidate = candidate.split("=", 1)[1].strip()
        if not candidate.startswith("{"):
            continue
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, Mapping):
            return payload
    raise ValueError("benchmark output has no JSON resource object")


class CommandResourceEstimator:
    """Apply costs, launch a fresh benchmark process, and parse its final JSON line."""

    def __init__(
        self,
        adapter: MySQLCostAdapter,
        command: List[str],
        *,
        timeout_sec: float = 1800.0,
        cwd: Optional[str] = None,
        environment: Optional[Mapping[str, Any]] = None,
        measurement_repeats: int = 1,
        measurement_min_success: Optional[int] = None,
    ) -> None:
        if not command:
            raise ValueError("estimator.command must not be empty")
        self.adapter = adapter
        self.command = [str(item) for item in command]
        self.timeout_sec = float(timeout_sec)
        self.cwd = cwd
        self.environment = {str(key): str(value) for key, value in (environment or {}).items()}
        self.measurement_repeats = int(measurement_repeats)
        if self.measurement_repeats < 1:
            raise ValueError("measurement_repeats must be positive")
        self.measurement_min_success = (
            self.measurement_repeats if measurement_min_success is None else int(measurement_min_success)
        )
        if not 1 <= self.measurement_min_success <= self.measurement_repeats:
            raise ValueError("measurement_min_success must be between 1 and measurement_repeats")
        self.measurements: List[Dict[str, Any]] = []

    def __call__(self, sql_set: Any, params: Dict[str, float]) -> Dict[str, Any]:
        _ = sql_set
        self.adapter.apply(params)
        env = os.environ.copy()
        env.update(self.environment)
        env["MYSQL_COST_PARAMS_JSON"] = json.dumps(params, sort_keys=True)
        samples: List[tuple[Mapping[str, Any], Dict[str, Any]]] = []
        failed_samples: List[Dict[str, Any]] = []
        for repeat_index in range(self.measurement_repeats):
            proc = subprocess.run(
                self.command,
                cwd=self.cwd,
                env={**env, "MYSQL_MEASUREMENT_REPEAT_INDEX": str(repeat_index + 1)},
                capture_output=True,
                text=True,
                timeout=self.timeout_sec if self.timeout_sec > 0 else None,
                check=False,
            )
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout)[-4000:].strip()
                failed_samples.append({
                    "repeat_index": repeat_index + 1,
                    "exit_code": proc.returncode,
                    "error": detail,
                })
                continue
            payload = _last_json_object(proc.stdout)
            samples.append((payload, _normalize_resource(payload)))

        if len(samples) < self.measurement_min_success:
            raise RuntimeError(
                f"benchmark measurement quorum failed: successes={len(samples)}/"
                f"{self.measurement_repeats}, required={self.measurement_min_success}, "
                f"failures={failed_samples}"
            )

        def median_optional(name: str) -> float | None:
            values = [float(normalized[name]) for _, normalized in samples if normalized.get(name) is not None]
            return statistics.median(values) if values else None

        median_qps = median_optional("qps")
        representative_index = 0
        if median_qps is not None:
            representative_index = min(
                range(len(samples)),
                key=lambda index: abs(float(samples[index][1].get("qps", median_qps)) - median_qps),
            )
        representative_payload, representative = samples[representative_index]
        resource = {
            name: statistics.median(float(normalized[name]) for _, normalized in samples)
            for name in ("cpu", "memory", "io")
        }
        metadata = representative_payload.get("_metadata")
        self.last_metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
        self.last_metadata["measurement_repeats"] = self.measurement_repeats
        self.last_metadata["measurement_successes"] = len(samples)
        self.last_metadata["measurement_failures"] = failed_samples
        self.last_metadata["measurement_replicates"] = [
            {
                "repeat_index": index,
                "qps": normalized.get("qps"),
                "latency_p95_ms": normalized.get("latency_p95_ms"),
                "latency_p99_ms": normalized.get("latency_p99_ms"),
                "resource": {name: float(normalized[name]) for name in ("cpu", "memory", "io")},
                "summary_path": (
                    payload.get("_metadata", {}).get("summary_path")
                    if isinstance(payload.get("_metadata"), Mapping) else None
                ),
            }
            for index, (payload, normalized) in enumerate(samples, 1)
        ] + failed_samples
        self.last_stats = representative_payload.get("_stats") if isinstance(representative_payload.get("_stats"), Mapping) else None
        self.measurements.append(
            {
                "label": getattr(self, "current_label", None),
                "params": dict(params),
                "resource": dict(resource),
                "stats": self.last_stats,
                "metadata": self.last_metadata,
                "qps": median_qps,
                "query_count": median_optional("query_count"),
                "query_time": median_optional("query_time"),
                "latency_p95_ms": median_optional("latency_p95_ms"),
                "latency_p99_ms": median_optional("latency_p99_ms"),
                "explain_samples": representative.get("explain_samples", []),
            }
        )
        return resource


class PythonResourceEstimator:
    """Apply costs and invoke a configured Python callable in the same process."""

    def __init__(
        self,
        adapter: MySQLCostAdapter,
        callback: Callable[[Any, Dict[str, float]], Mapping[str, Any]],
    ) -> None:
        self.adapter = adapter
        self.callback = callback

    def __call__(self, sql_set: Any, params: Dict[str, float]) -> Dict[str, Any]:
        self.adapter.apply(params)
        return _normalize_resource(self.callback(sql_set, params))


def load_python_callable(path: str) -> Callable[[Any, Dict[str, float]], Mapping[str, Any]]:
    module_name, separator, attribute_name = path.partition(":")
    if not separator or not module_name or not attribute_name:
        raise ValueError("python estimator must use 'module:function' syntax")
    callback = getattr(importlib.import_module(module_name), attribute_name)
    if not callable(callback):
        raise TypeError(f"configured estimator is not callable: {path}")
    return callback


def build_resource_estimator(
    adapter: MySQLCostAdapter,
    estimator_cfg: Mapping[str, Any],
) -> Any:
    """Build one estimator type shared by gradient, Top-15 and full replay."""
    estimator_type = str(estimator_cfg.get("type", "command"))
    if estimator_type == "python":
        return PythonResourceEstimator(
            adapter,
            load_python_callable(str(estimator_cfg["callable"])),
        )
    if estimator_type == "trained_models":
        from trained_model_estimator import build_trained_model_estimator

        return build_trained_model_estimator(adapter, estimator_cfg)
    if estimator_type in {"plan_physics", "ratio_scale"}:
        from plan_resource_estimator import (
            build_plan_resource_estimator,
            build_ratio_scale_resource_estimator,
        )

        builder = (
            build_ratio_scale_resource_estimator
            if estimator_type == "ratio_scale"
            else build_plan_resource_estimator
        )
        return builder(adapter, estimator_cfg)
    if estimator_type != "command":
        raise ValueError(
            "estimator.type must be command, python, trained_models, plan_physics or ratio_scale"
        )
    command = estimator_cfg.get("command")
    if not isinstance(command, list) or not command:
        raise ValueError("estimator.command must be a non-empty argv JSON list")
    return CommandResourceEstimator(
        adapter,
        [str(item) for item in command],
        timeout_sec=float(estimator_cfg.get("timeout_sec", 1800.0)),
        cwd=str(estimator_cfg["cwd"]) if estimator_cfg.get("cwd") else None,
        environment=estimator_cfg.get("environment", {}),
        measurement_repeats=int(estimator_cfg.get("measurement_repeats", 1)),
        measurement_min_success=(
            int(estimator_cfg["measurement_min_success"])
            if estimator_cfg.get("measurement_min_success") is not None else None
        ),
    )

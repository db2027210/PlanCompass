"""Dependency-injected formal MySQL all-subset SGD real-replay adapter.

This module performs no database or remote operation by itself.  It connects the
``mysql_parameter_optimizer`` measure/restore callback contract to independently
injected formal point services. A benchmark is runnable only when its formal
continuous-session factory is explicitly registered; missing factories fail
closed.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
import copy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shlex
import socket
import statistics
import uuid
from typing import Any, Callable, Dict, Mapping, Protocol, Sequence

from mysql_plan_clustering import validate_canonical_plan_bundle
from formal_mysql_database_qps import FormalDatabaseQPSError
from formal_job_continuous_session import FormalSamplingBoundaryError

from formal_recost_core import MYSQL_COST_NAMES
from mysql_parameter_optimizer import REQUIRED_REAL_REPLAY_EVIDENCE


ROLLING_DECISIONS = tuple(range(180, 601, 30))
KNOWN_BENCHMARKS = ("hybench_ap", "job", "tpch", "tpcc", "hybench_xp")


class FormalSGDNotReady(RuntimeError):
    """The requested workload has no registered formal continuous session."""


class FormalSGDPointError(RuntimeError):
    """A dependency violated the formal point contract."""


class FormalSGDResumeError(FormalSGDPointError):
    """A checkpoint cannot safely prove resumable formal point state."""


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


class FormalPointJournal:
    """Atomic, chained point checkpoints with a stable accounting token."""

    def __init__(self, root: Path, resume_command_prefix: tuple[str, ...]) -> None:
        if not resume_command_prefix:
            raise ValueError("resume_command_prefix must be non-empty")
        self.root = Path(root)
        self.resume_command_prefix = tuple(str(item) for item in resume_command_prefix)
        self.token: str | None = None
        self._sequence = 0
        self._previous_sha256: str | None = None
        self._document: Dict[str, Any] | None = None

    def begin(
        self, pending: Mapping[str, Any], *, token: str | None = None,
    ) -> Dict[str, Any]:
        if self.token is not None:
            raise FormalSGDPointError("journal already has an active point")
        token = str(token or uuid.uuid4().hex)
        if not token or "/" in token or token in {".", ".."}:
            raise FormalSGDPointError("journal token is invalid")
        if (self.root / token).exists():
            raise FormalSGDPointError("journal token already exists")
        self.token = token
        self._sequence = 0
        self._previous_sha256 = None
        command = " ".join(
            shlex.quote(item) for item in (
                *self.resume_command_prefix,
                "--resume-token", token,
                "--journal-root", str(self.root.resolve()),
            )
        )
        self._document = {
            "schema_version": 1,
            "resume_token": token,
            "accounting_token": token,
            "resume_command": command,
            "pending": copy.deepcopy(dict(pending)),
            "completed_stages": [],
            "measurement": None,
            "measurement_complete": False,
            "recovery_verified": False,
            "lease_released": False,
            "counted": False,
            "restart_outer_attempt_required": False,
        }
        return self.checkpoint("initialized", {})

    @property
    def attempt_dir(self) -> Path:
        if self.token is None:
            raise FormalSGDPointError("journal has no active point")
        return self.root / self.token

    def checkpoint(self, stage: str, update: Mapping[str, Any]) -> Dict[str, Any]:
        if self._document is None or self.token is None:
            raise FormalSGDPointError("journal checkpoint without begin")
        self._document.update(copy.deepcopy(dict(update)))
        completed = list(self._document.get("completed_stages") or [])
        if stage not in completed:
            completed.append(stage)
        self._document["completed_stages"] = completed
        self._sequence += 1
        record = {
            **copy.deepcopy(self._document),
            "sequence": self._sequence,
            "stage": stage,
            "previous_sha256": self._previous_sha256,
            "checkpointed_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        payload = _canonical_json(record)
        digest = hashlib.sha256(payload).hexdigest()
        history = self.attempt_dir / f"{self._sequence:04d}_{stage}.json"
        if history.exists():
            raise FormalSGDPointError(f"checkpoint history collision: {history}")
        _atomic_bytes(history, payload)
        _atomic_bytes(history.with_suffix(".json.sha256"), (digest + "\n").encode("ascii"))
        _atomic_bytes(self.attempt_dir / "current.json", payload)
        _atomic_bytes(self.attempt_dir / "current.json.sha256", (digest + "\n").encode("ascii"))
        self._previous_sha256 = digest
        return copy.deepcopy(record)

    def close(self) -> None:
        self.token = None
        self._document = None
        self._sequence = 0
        self._previous_sha256 = None

    def attach_verified(self, token: str) -> Dict[str, Any]:
        record = self.load_verified(self.root, token)
        self.token = token
        self._sequence = int(record["sequence"])
        head = (self.root / token / "current.json").read_bytes()
        self._previous_sha256 = hashlib.sha256(head).hexdigest()
        self._document = {
            key: copy.deepcopy(value)
            for key, value in record.items()
            if key not in {"sequence", "stage", "previous_sha256", "checkpointed_at_utc"}
        }
        return copy.deepcopy(record)

    @classmethod
    def load_verified(cls, root: Path, token: str) -> Dict[str, Any]:
        attempt = Path(root) / str(token)
        histories = sorted(attempt.glob("[0-9][0-9][0-9][0-9]_*.json"))
        if not histories:
            raise FormalSGDResumeError("resume token has no checkpoint history")
        previous = None
        last: Dict[str, Any] | None = None
        for path in histories:
            payload = path.read_bytes()
            actual = hashlib.sha256(payload).hexdigest()
            sidecar = path.with_suffix(".json.sha256")
            if not sidecar.is_file() or sidecar.read_text(encoding="ascii").strip() != actual:
                raise FormalSGDResumeError(f"checkpoint checksum mismatch: {path.name}")
            record = json.loads(payload)
            if record.get("resume_token") != token or record.get("previous_sha256") != previous:
                raise FormalSGDResumeError("checkpoint token or hash chain mismatch")
            previous = actual
            last = record
        current = attempt / "current.json"
        current_payload = current.read_bytes()
        if hashlib.sha256(current_payload).hexdigest() != previous:
            raise FormalSGDResumeError("current checkpoint is not the chain head")
        if (attempt / "current.json.sha256").read_text(encoding="ascii").strip() != previous:
            raise FormalSGDResumeError("current checkpoint checksum mismatch")
        if last is None or json.loads(current_payload) != last:
            raise FormalSGDResumeError("current checkpoint payload differs from chain head")
        return last

    @classmethod
    def claim_accounting(cls, root: Path, token: str) -> bool:
        record = cls.load_verified(root, token)
        if not (
            record.get("measurement_complete") is True
            and record.get("recovery_verified") is True
            and record.get("lease_released") is True
            and record.get("pending") is None
        ):
            raise FormalSGDResumeError("point cannot be counted before complete recovery")
        marker = Path(root) / token / "accounted.once"
        try:
            descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return False
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(token + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return True


class ContinuousMeasurementSession(Protocol):
    def start(self, *, warmup_seconds: int, maximum_measurement_seconds: int) -> None: ...
    def wait_until_measurement(self, measurement_seconds: int) -> None: ...
    def observation(self, measurement_seconds: int) -> Mapping[str, Any]: ...
    def stop(self, measurement_seconds: int) -> Mapping[str, Any]: ...


class ContinuousMeasurementSessionFactory(Protocol):
    def __call__(self, context: Mapping[str, Any]) -> ContinuousMeasurementSession: ...


PointService = Callable[[Mapping[str, Any]], Mapping[str, Any]]
CostApplyService = Callable[[Mapping[str, float], Mapping[str, Any]], Mapping[str, Any]]
Finalizer = Callable[[Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]]
LeaseFactory = Callable[[Mapping[str, Any]], AbstractContextManager]
UnstablePointRepeater = Callable[
    [Mapping[str, Any], Mapping[str, Any]], Sequence[Mapping[str, Any]]
]


def _mapping(value: Any, label: str) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        raise FormalSGDPointError(f"{label} must return a mapping")
    return copy.deepcopy(dict(value))


def _validate_strict_plan_capture(evidence: Mapping[str, Any]) -> None:
    bundle = evidence.get("canonical_plan_bundle")
    verification = validate_canonical_plan_bundle(bundle)
    if verification.get("valid") is not True:
        raise FormalSGDPointError(
            "post-replay plan capture has an invalid canonical bundle: "
            f"{verification.get('reasons')}"
        )
    if not isinstance(bundle, Mapping) or bundle.get("registry_authority_verified") is not True:
        raise FormalSGDPointError(
            "post-replay plan capture lacks verified registry authority"
        )
    if bundle.get("strict_cluster_usable") is not True:
        reasons = bundle.get("strict_cluster_ineligible_reasons")
        allowed_prefix = "generic_plan_semantics_unavailable:"
        if (
            not isinstance(reasons, list)
            or not reasons
            or any(
                not isinstance(reason, str)
                or not reason.startswith(allowed_prefix)
                or not reason[len(allowed_prefix):]
                for reason in reasons
            )
        ):
            raise FormalSGDPointError(
                "post-replay plan capture lacks complete representative custom coverage"
            )
        if (
            bundle.get("plan_similarity_cluster_id") is not None
            or bundle.get("strict_canonical_physical_bundle_sha256") is not None
        ):
            raise FormalSGDPointError(
                "audit-only dynamic plan capture unexpectedly has a strict cluster id"
            )
        return
    if (
        not bundle.get("plan_similarity_cluster_id")
        or bundle.get("plan_similarity_cluster_id")
        != bundle.get("strict_canonical_physical_bundle_sha256")
    ):
        raise FormalSGDPointError(
            "post-replay plan similarity id is not the strict canonical bundle SHA"
        )


def average_three_valid_independent_runs(
    attempts: Sequence[Mapping[str, Any]],
    *, expected_contract_bindings: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Average exactly three technically valid, independently identified runs.

    Technical failures are retained in the audit list but never consume one of
    the three valid slots.  This function performs no retry or remote action;
    the session owner supplies completed attempt artifacts.
    """
    valid: list[Mapping[str, Any]] = []
    excluded: list[Dict[str, Any]] = []
    run_ids: set[str] = set()
    point_id: str | None = None
    sealed_bindings: Dict[str, Any] | None = None
    evidence_bundles: list[Dict[str, Any]] = []
    binding_keys = {
        "point_parameters_float_hex_sha256", "resource_envelope_sha256",
        "template_sha256", "workload_registry_sha256", "plan_contract_sha256",
    }
    for index, raw in enumerate(attempts):
        attempt = _mapping(raw, f"independent_runs[{index}]")
        if attempt.get("technical_failure") is True:
            excluded.append({
                "run_id": attempt.get("run_id"),
                "failure_type": attempt.get("failure_type"),
                "counted": False,
            })
            continue
        run_id = attempt.get("run_id")
        current_point = attempt.get("point_id")
        if not isinstance(run_id, str) or not run_id or run_id in run_ids:
            raise FormalSGDPointError("valid independent runs require unique run_id values")
        if not isinstance(current_point, str) or not current_point:
            raise FormalSGDPointError("valid independent run lacks point_id")
        if point_id is None:
            point_id = current_point
        elif current_point != point_id:
            raise FormalSGDPointError("independent runs do not bind the same point_id")
        if attempt.get("valid_independent_run") is not True:
            raise FormalSGDPointError("non-technical attempt is not a valid independent run")
        if (
            attempt.get("correctness_passed") is not True
            or attempt.get("oom") is not False
            or attempt.get("mysql_restart") is not False
            or attempt.get("container_restart") is not False
        ):
            raise FormalSGDPointError("independent run failed correctness/restart gates")
        if attempt.get("stable") is not True:
            raise FormalSGDPointError("each valid independent run must be resource-stable")
        bindings = _mapping(attempt.get("contract_bindings"), "independent contract_bindings")
        if set(bindings) != binding_keys or any(not isinstance(value, str) or not value for value in bindings.values()):
            raise FormalSGDPointError("independent run contract bindings are incomplete")
        if sealed_bindings is None:
            sealed_bindings = bindings
        elif bindings != sealed_bindings:
            raise FormalSGDPointError("independent runs do not bind the same formal point")
        if expected_contract_bindings is not None and bindings != dict(expected_contract_bindings):
            raise FormalSGDPointError("independent run bindings differ from current point artifacts")
        if attempt.get("db_qps_source") != "database_status_counter":
            raise FormalSGDPointError("independent db_qps must come from database_status_counter")
        bundle = _mapping(attempt.get("evidence_bundle"), "independent evidence_bundle")
        missing_bundle = [name for name in REQUIRED_REAL_REPLAY_EVIDENCE if not bundle.get(name)]
        if missing_bundle:
            raise FormalSGDPointError(f"independent evidence bundle is incomplete: {missing_bundle}")
        resources = _mapping(attempt.get("resource_means"), "independent resource_means")
        if set(resources) != {"cpu", "memory", "io"}:
            raise FormalSGDPointError("independent resource_means require cpu/memory/io")
        for resource, value in resources.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise FormalSGDPointError(f"independent {resource} mean must be finite")
        run_ids.add(run_id)
        valid.append(attempt)
        evidence_bundles.append(bundle)
    if len(valid) != 3:
        raise FormalSGDPointError(
            f"independent fallback requires exactly 3 valid runs; got {len(valid)}"
        )
    resource_means = {
        resource: sum(float(run["resource_means"][resource]) for run in valid) / 3.0
        for resource in ("cpu", "memory", "io")
    }
    metric_maps = [_mapping(run.get("metrics"), "independent metrics") for run in valid]
    required_metrics = {"qps", "db_qps", "p95_ms", "p99_ms", "sample_coverage", "error_rate"}
    for metrics in metric_maps:
        if not required_metrics <= set(metrics):
            raise FormalSGDPointError("independent metrics lack qps/db_qps/latency/coverage/error_rate")
        if float(metrics["qps"]) != float(metrics["db_qps"]):
            raise FormalSGDPointError("independent run requires qps == db_qps")
        if not 0.90 <= float(metrics["sample_coverage"]) <= 1.0:
            raise FormalSGDPointError("independent run sample coverage is below 0.90")
        if not 0.0 <= float(metrics["error_rate"]) <= 0.01:
            raise FormalSGDPointError("independent run error rate exceeds 0.01")
    numeric_metric_keys = set.intersection(*(
        {
            key for key, value in metrics.items()
            if not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(float(value))
        }
        for metrics in metric_maps
    ))
    metric_means = {
        key: sum(float(run["metrics"][key]) for run in valid) / 3.0
        for key in sorted(numeric_metric_keys)
    }
    return {
        "policy": "three_valid_independent_runs_arithmetic_mean_v1",
        "trigger": "first_continuous_run_resource_unstable",
        "point_id": point_id,
        "valid_run_ids": [str(run["run_id"]) for run in valid],
        "valid_run_count": 3,
        "technical_failures_excluded": excluded,
        "resource_means": resource_means,
        "metric_means": metric_means,
        "contract_bindings": copy.deepcopy(sealed_bindings),
        "evidence_bundles": evidence_bundles,
        "arithmetic_divisor": 3,
    }


def _validate_latest_six_resource_windows(
    stability: Mapping[str, Any], expected_seconds: int,
    decision_resources: Sequence[str] = ("cpu", "memory", "io"),
) -> None:
    windows = stability.get("windows")
    if not isinstance(windows, list) or len(windows) < 6:
        raise FormalSGDPointError("stability must retain all windows and the latest six")
    expected_last = expected_seconds // 30
    expected_indices = list(range(expected_last - 5, expected_last + 1))
    latest = windows[-6:]
    if [window.get("index") for window in latest if isinstance(window, Mapping)] != expected_indices:
        raise FormalSGDPointError("latest stability windows are not consecutive or numbered from 1")
    calculation = stability.get(
        "calculation", "within_each_window_from_one_second_samples"
    )
    resource_stable = True
    for window in latest:
        if not isinstance(window, Mapping):
            raise FormalSGDPointError("stability window must be a mapping")
        metrics = window.get("metrics")
        if not isinstance(metrics, Mapping):
            raise FormalSGDPointError("stability window lacks resource metrics")
        for resource in ("cpu", "memory", "io"):
            metric = metrics.get(resource)
            if not isinstance(metric, Mapping) or type(metric.get("stable")) is not bool:
                raise FormalSGDPointError(f"stability window lacks {resource} decision")
            if (
                calculation == "within_each_window_from_one_second_samples"
                and resource in decision_resources
            ):
                resource_stable = resource_stable and bool(metric["stable"])
        if float(window.get("sample_coverage", 0.0)) < 0.9:
            resource_stable = False
    if calculation == "cv_across_latest_six_window_means":
        for resource in decision_resources:
            values = [
                float(window["metrics"][resource]["mean"])
                for window in latest
            ]
            mean = statistics.fmean(values)
            if mean < 0.1:
                resource_stable = resource_stable and (
                    max(values) - min(values) <= 0.05
                )
            else:
                resource_stable = resource_stable and (
                    statistics.pstdev(values) / mean <= 0.1
                )
    elif calculation != "within_each_window_from_one_second_samples":
        raise FormalSGDPointError(
            f"unsupported stability calculation: {calculation!r}"
        )
    if bool(stability.get("stable")) != resource_stable:
        raise FormalSGDPointError(
            "stability decision must depend only on latest-six CPU/Memory/IO windows"
        )


def _run_continuous_session(
    session: ContinuousMeasurementSession,
    *,
    maximum_measurement_seconds: int = 600,
    minimum_selection_seconds: int = 180,
    minimum_client_completions: int = 0,
) -> Dict[str, Any]:
    """Drive one workload through a 60s warm-up and rolling stability checks."""
    maximum_measurement_seconds = int(maximum_measurement_seconds)
    if maximum_measurement_seconds not in ROLLING_DECISIONS:
        raise FormalSGDPointError(
            "maximum measurement seconds must be one of 180/210/.../600"
        )
    minimum_selection_seconds = int(minimum_selection_seconds)
    minimum_client_completions = int(minimum_client_completions)
    if minimum_client_completions < 0:
        raise FormalSGDPointError("minimum client completions cannot be negative")
    if (
        minimum_selection_seconds < 180
        or minimum_selection_seconds > maximum_measurement_seconds
        or minimum_selection_seconds % 30
    ):
        raise FormalSGDPointError(
            "minimum selection seconds must be a multiple of 30 within [180, maximum]"
        )
    rolling_decisions = tuple(
        seconds for seconds in ROLLING_DECISIONS
        if minimum_selection_seconds <= seconds <= maximum_measurement_seconds
    )
    session.start(
        warmup_seconds=60,
        maximum_measurement_seconds=maximum_measurement_seconds,
    )
    last: Mapping[str, Any] | None = None
    skipped_database_boundaries: list[dict[str, Any]] = []
    for seconds in rolling_decisions:
        try:
            session.wait_until_measurement(seconds)
        except FormalSamplingBoundaryError as exc:
            if seconds == maximum_measurement_seconds:
                raise FormalSGDPointError(
                    f"no valid formal measurement boundary through {maximum_measurement_seconds}s: {exc}"
                ) from exc
            skipped_database_boundaries.append({
                "measurement_seconds": seconds,
                "reason": str(exc),
                "evidence_status": "invalid_not_used_for_stability_or_acceptance",
            })
            continue
        try:
            observation = _mapping(session.observation(seconds), "session.observation")
        except FormalDatabaseQPSError as exc:
            if seconds == maximum_measurement_seconds:
                raise FormalSGDPointError(
                    f"no valid formal measurement boundary through {maximum_measurement_seconds}s: {exc}"
                ) from exc
            skipped_database_boundaries.append({
                "measurement_seconds": seconds,
                "reason": str(exc),
                "evidence_status": "invalid_not_used_for_stability_or_acceptance",
            })
            continue
        if int(observation.get("measurement_seconds", -1)) != seconds:
            raise FormalSGDPointError("continuous observation elapsed time mismatch")
        throughput = observation.get("throughput_evidence")
        if not isinstance(throughput, Mapping) or throughput.get("reliable") is not True:
            raise FormalSGDPointError("continuous observation lacks reliable database throughput")
        source = str(throughput.get("source") or "").strip().lower()
        if source in {
            "questions", "mysql_questions", "performance_schema.questions",
            "performance_schema_global_status_questions",
        }:
            raise FormalSGDPointError("MySQL Questions is not attributable workload throughput")
        if source != "database_status_counter":
            raise FormalSGDPointError(
                "canonical DBQPS must come from database_status_counter"
            )
        try:
            observed_db_qps = float(throughput["db_qps"])
        except (KeyError, TypeError, ValueError) as exc:
            raise FormalSGDPointError("database throughput lacks finite db_qps") from exc
        if not math.isfinite(observed_db_qps) or observed_db_qps < 0.0:
            raise FormalSGDPointError("database throughput db_qps is invalid")
        stability = observation.get("stability")
        if not isinstance(stability, Mapping) or type(stability.get("stable")) is not bool:
            raise FormalSGDPointError("continuous observation lacks stability decision")
        if int(stability.get("decision_window_seconds", -1)) != 180:
            raise FormalSGDPointError("stability must use the latest 180-second window")
        _validate_latest_six_resource_windows(
            stability,
            seconds,
            tuple(
                getattr(
                    getattr(session, "evaluator", None),
                    "decision_resources",
                    ("cpu", "memory", "io"),
                )
            ),
        )
        client_throughput = observation.get("client_throughput_evidence")
        completion_count = (
            int(client_throughput.get("completion_event_count", 0))
            if isinstance(client_throughput, Mapping)
            else 0
        )
        completion_gate_passed = completion_count >= minimum_client_completions
        last = observation
        if stability["stable"] and completion_gate_passed:
            stopped = _mapping(session.stop(seconds), "session.stop")
            stopped["rolling_measurement_control"] = {
                "warmup_seconds": 60,
                "selected_measurement_seconds": seconds,
                "maximum_measurement_seconds": maximum_measurement_seconds,
                "decision_step_seconds": 30,
                "decision_window_seconds": 180,
                "minimum_client_completions": minimum_client_completions,
                "observed_client_completions": completion_count,
                "skipped_database_boundaries": copy.deepcopy(
                    skipped_database_boundaries
                ),
                "stop_reason": "latest_six_30_second_windows_stable",
                "selected_observation": observation,
            }
            return stopped
    if last is None:
        raise FormalSGDPointError(
            f"no valid formal measurement boundary through {maximum_measurement_seconds}s"
        )
    stopped = _mapping(
        session.stop(maximum_measurement_seconds),
        "session.stop",
    )
    stopped["rolling_measurement_control"] = {
        "warmup_seconds": 60,
        "selected_measurement_seconds": maximum_measurement_seconds,
        "maximum_measurement_seconds": maximum_measurement_seconds,
        "decision_step_seconds": 30,
        "decision_window_seconds": 180,
        "minimum_client_completions": minimum_client_completions,
        "observed_client_completions": (
            int(last.get("client_throughput_evidence", {}).get(
                "completion_event_count", 0
            ))
            if isinstance(last.get("client_throughput_evidence"), Mapping)
            else 0
        ),
        "skipped_database_boundaries": copy.deepcopy(
            skipped_database_boundaries
        ),
        "stop_reason": (
            "maximum_measurement_seconds_reached"
            if last.get("stability", {}).get("stable") is True
            else "maximum_measurement_seconds_reached_unstable"
        ),
        "selected_observation": dict(last),
    }
    return stopped


def _validate_cost_apply(evidence: Mapping[str, Any], expected: Mapping[str, float]) -> None:
    requested = evidence.get("params_requested") or {}
    readback = evidence.get("params_readback") or {}
    if not isinstance(requested, Mapping) or not isinstance(readback, Mapping):
        raise FormalSGDPointError("cost apply lacks requested/readback maps")
    if set(requested) != set(MYSQL_COST_NAMES) or set(readback) != set(MYSQL_COST_NAMES):
        raise FormalSGDPointError("cost apply/readback must contain all eight costs")
    for name in MYSQL_COST_NAMES:
        if float(requested[name]).hex() != float(expected[name]).hex():
            raise FormalSGDPointError(f"requested cost mismatch: {name}")
        if float(readback[name]).hex() != float(expected[name]).hex():
            raise FormalSGDPointError(f"fresh cost readback mismatch: {name}")
    if evidence.get("commit") is not True:
        raise FormalSGDPointError("cost apply did not prove COMMIT")
    if not evidence.get("fresh_connection_generation") and evidence.get("new_connection_after_flush") is not True:
        raise FormalSGDPointError("cost readback did not prove a fresh post-flush connection")


def _validate_final_measurement(
    measurement: Mapping[str, Any], context: Mapping[str, Any]
) -> None:
    if measurement.get("db_qps_source") != "database_status_counter":
        raise FormalSGDPointError(
            "final measurement db_qps must come from database_status_counter"
        )
    selected = context["continuous_measurement"]["rolling_measurement_control"][
        "selected_observation"
    ]
    aggregation = context.get("independent_run_aggregation")
    database_throughput = selected.get("database_throughput") or selected.get(
        "throughput_evidence"
    )
    if not isinstance(database_throughput, Mapping):
        raise FormalSGDPointError("selected observation lacks database throughput")
    try:
        canonical_db_qps = float(
            aggregation["metric_means"]["db_qps"]
            if isinstance(aggregation, Mapping)
            else database_throughput["db_qps"]
        )
        final_db_qps = float(measurement["db_qps"])
        final_qps = float(measurement["qps"])
    except (KeyError, TypeError, ValueError) as exc:
        raise FormalSGDPointError("final measurement lacks canonical qps/db_qps") from exc
    if not (
        math.isfinite(canonical_db_qps)
        and final_db_qps == canonical_db_qps
        and final_qps == canonical_db_qps
    ):
        raise FormalSGDPointError(
            "final qps/db_qps must exactly match the selected database-side "
            "DBQPS (or its authoritative independent-run aggregate)"
        )
    correctness = context["correctness"]
    exact = {
        "correctness_passed": correctness.get("passed"),
        "error_rate": correctness.get("error_rate"),
        "oom": correctness.get("oom"),
        "mysql_restart": correctness.get("mysql_restart"),
        "container_restart": correctness.get("container_restart"),
    }
    for name, expected in exact.items():
        if measurement.get(name) != expected:
            raise FormalSGDPointError(
                f"final measurement {name} disagrees with correctness evidence"
            )
    stable = selected["stability"]["stable"]
    expected_stable = stable or (
        isinstance(aggregation, Mapping)
        and aggregation.get("valid_run_count") == 3
    )
    if measurement.get("stable") is not expected_stable:
        raise FormalSGDPointError(
            "final measurement stability disagrees with rolling controller"
        )
    if context.get("benchmark") == "tpcc":
        for name in ("native_tps", "sensitive_tps", "combined_tps"):
            try:
                value = float(measurement[name])
            except (KeyError, TypeError, ValueError) as exc:
                raise FormalSGDPointError(
                    f"TPC-C final measurement lacks finite {name}"
                ) from exc
            if not math.isfinite(value) or value < 0.0:
                raise FormalSGDPointError(f"TPC-C {name} must be finite and non-negative")
        correctness = context["correctness"]
        if (
            correctness.get("tpcc_invariant_passed") is not True
            or not correctness.get("tpcc_pre_signature")
            or not correctness.get("tpcc_post_signature")
        ):
            raise FormalSGDPointError(
                "TPC-C correctness did not prove frozen pre/post invariants"
            )


def _validate_recovery_evidence(
    recovery: Mapping[str, Any], baseline_all_costs: Mapping[str, Any]
) -> None:
    effective_all = recovery.get("effective_all_costs") or {}
    if not isinstance(effective_all, Mapping) or set(effective_all) != set(MYSQL_COST_NAMES):
        raise FormalSGDPointError("recovery must fresh-read back all eight baseline costs")
    for name in MYSQL_COST_NAMES:
        if float(effective_all[name]).hex() != float(baseline_all_costs[name]).hex():
            raise FormalSGDPointError(f"recovery baseline cost mismatch: {name}")
    if recovery.get("succeeded") is not True:
        raise FormalSGDPointError("recovery did not succeed")
    if recovery.get("fixed_settings_verified") is not True:
        raise FormalSGDPointError("recovery fixed settings were not verified")
    if recovery.get("clean_template_verified") is not True:
        raise FormalSGDPointError("recovery clean template was not verified")
    if not recovery.get("evidence"):
        raise FormalSGDPointError("recovery evidence reference is absent")


@dataclass(frozen=True)
class FormalPointDependencies:
    lease_factory: LeaseFactory
    template_restore: PointService
    resource_fixed_readback: Callable[[Mapping[str, Any], str], Mapping[str, Any]]
    cost_apply_fresh_readback: CostApplyService
    session_factories: Mapping[str, ContinuousMeasurementSessionFactory]
    drain_flush: PointService
    correctness: PointService
    plan_capture: PointService
    finalizer: Finalizer
    recovery: PointService
    root_cause_review: PointService | None = None
    unstable_point_repeater: UnstablePointRepeater | None = None


class FormalMySQLSGDReplayDriver:
    """Optimizer callback implementing one fresh formal real point at a time."""

    def __init__(
        self,
        benchmark: str,
        registration: Mapping[str, Any],
        dependencies: FormalPointDependencies,
        journal: FormalPointJournal | None = None,
    ) -> None:
        self.benchmark = str(benchmark).strip().lower()
        if self.benchmark not in KNOWN_BENCHMARKS:
            raise ValueError(f"unknown MySQL benchmark: {self.benchmark!r}")
        if not callable(dependencies.session_factories.get(self.benchmark)):
            raise FormalSGDNotReady(
                f"NOT_READY: {self.benchmark} has no formal "
                "ContinuousMeasurementSessionFactory"
            )
        if self.benchmark == "tpcc":
            self._validate_tpcc_invariant(registration)
        self.registration = copy.deepcopy(dict(registration))
        defaults = self.registration.get("default_costs") or {}
        if set(defaults) != set(MYSQL_COST_NAMES):
            raise ValueError(
                "benchmark registration default_costs must contain all eight MySQL costs"
            )
        self.dependencies = dependencies
        self.journal = journal
        self._journal_token: str | None = None
        self.events: list[str] = []
        self._point_events: list[str] = []
        self._attempt: Dict[str, Any] | None = None

    def review_no_eligible_root_cause(
        self, diagnostic: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        """Require an injected review service before replay expands from 10 to K."""
        reviewer = self.dependencies.root_cause_review
        if not callable(reviewer):
            raise FormalSGDNotReady(
                "NOT_READY: root_cause_review service is required before Top-K expansion"
            )
        result = reviewer(
            {
                "benchmark": self.benchmark,
                "diagnostic": copy.deepcopy(dict(diagnostic)),
            }
        )
        if not isinstance(result, Mapping) or result.get("approved") is not True:
            raise FormalSGDPointError("root-cause review did not approve Top-K expansion")
        return copy.deepcopy(dict(result))

    @staticmethod
    def _validate_tpcc_invariant(registration: Mapping[str, Any]) -> None:
        invariant = registration.get("tpcc_frozen_invariant")
        if not isinstance(invariant, Mapping):
            raise FormalSGDNotReady(
                "NOT_READY: tpcc requires a frozen invariant correctness contract"
            )
        exact = {
            "native_terminals": 32,
            "warehouses": 10,
            "sensitive_injection_interval_seconds": 15,
        }
        for name, expected in exact.items():
            if invariant.get(name) != expected:
                raise FormalSGDNotReady(
                    f"NOT_READY: tpcc frozen invariant {name} must equal {expected}"
                )
        for name in (
            "version", "sha256", "pre_run_signature_ref", "post_run_validator_ref",
            "business_abort_policy_ref", "durability_readback_ref",
        ):
            if not invariant.get(name):
                raise FormalSGDNotReady(
                    f"NOT_READY: tpcc frozen invariant lacks {name}"
                )
        fields = invariant.get("throughput_fields")
        if set(fields or ()) != {"native_tps", "sensitive_tps", "combined_tps"}:
            raise FormalSGDNotReady(
                "NOT_READY: tpcc frozen invariant must register all three TPS fields"
            )

    def _event(self, name: str) -> None:
        self.events.append(name)
        self._point_events.append(name)
        if self.journal is not None and self.journal.token is not None:
            context = (self._attempt or {}).get("context") or {}
            update: Dict[str, Any] = {
                "recovery_context": copy.deepcopy(context),
                "lease_ownership": {
                    "pid": os.getpid(),
                    "host": socket.gethostname(),
                    "lease_evidence": copy.deepcopy(context.get("lease_evidence")),
                    "released": name == "lease_release",
                },
            }
            self.journal.checkpoint(name, update)

    def __call__(
        self, candidate: Mapping[str, Any], optimizer_context: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        if self._attempt is not None:
            raise FormalSGDPointError("previous real point has not been restored")
        self._point_events = []
        active = _mapping(candidate.get("optimized_params") or {}, "candidate params")
        baseline_active = _mapping(
            optimizer_context.get("baseline_params") or {}, "optimizer baseline params"
        )
        baseline_all_costs = {
            name: float(baseline_active.get(name, self.registration["default_costs"][name]))
            for name in MYSQL_COST_NAMES
        }
        all_costs = {
            name: float(active.get(name, baseline_all_costs[name]))
            for name in MYSQL_COST_NAMES
        }
        context: Dict[str, Any] = {
            **copy.deepcopy(self.registration),
            "benchmark": self.benchmark,
            "candidate": copy.deepcopy(dict(candidate)),
            "optimizer_context": copy.deepcopy(dict(optimizer_context)),
            "baseline_all_costs": baseline_all_costs,
            "requested_all_costs": all_costs,
        }
        if self.journal is not None:
            checkpoint = self.journal.begin(
                {
                    "outer_iter": optimizer_context.get("outer_iter"),
                    "subset_id": candidate.get("subset_id"),
                    "replay_ordinal": optimizer_context.get("replay_ordinal"),
                    "stage": optimizer_context.get("stage"),
                    "candidate_params": copy.deepcopy(active),
                    "baseline_params": copy.deepcopy(baseline_active),
                },
                token=(
                    str(optimizer_context["outer_replay_attempt_token"])
                    if optimizer_context.get("outer_replay_attempt_token") else None
                ),
            )
            self._journal_token = str(checkpoint["resume_token"])
            context["resume_token"] = self._journal_token
            context["resume_command"] = checkpoint["resume_command"]
        lease = self.dependencies.lease_factory(context)
        self._attempt = {"lease": lease, "context": context}
        try:
            lease_value = lease.__enter__()
            self._attempt["lease_entered"] = True
            context["lease_evidence"] = copy.deepcopy(getattr(lease_value, "evidence", lease_value))
            self._event("lease")
            context["template_restore"] = _mapping(
                self.dependencies.template_restore(context), "template_restore"
            )
            self._event("template_restore")
            context["readback_before"] = _mapping(
                self.dependencies.resource_fixed_readback(context, "before"),
                "resource_fixed_readback(before)",
            )
            self._event("resource_fixed_readback_before")
            context["cost_apply"] = _mapping(
                self.dependencies.cost_apply_fresh_readback(all_costs, context),
                "cost_apply_fresh_readback",
            )
            _validate_cost_apply(context["cost_apply"], all_costs)
            self._event("cost_apply_fresh_readback")
            session = self.dependencies.session_factories[self.benchmark](context)
            context["continuous_measurement"] = _run_continuous_session(session)
            self._event("continuous_measurement")
            rolling = context["continuous_measurement"]["rolling_measurement_control"]
            context["drain_flush"] = _mapping(
                self.dependencies.drain_flush(context), "drain_flush"
            )
            if (
                context["drain_flush"].get("drained") is not True
                or context["drain_flush"].get("flushed") is not True
            ):
                raise FormalSGDPointError("post-measurement drain/flush was not proven")
            self._event("drain_flush")
            context["correctness"] = _mapping(
                self.dependencies.correctness(context), "correctness"
            )
            self._event("correctness")
            context["plan_capture"] = _mapping(
                self.dependencies.plan_capture(context), "plan_capture"
            )
            _validate_strict_plan_capture(context["plan_capture"])
            self._event("plan_capture")
            if rolling["stop_reason"] == "maximum_measurement_seconds_reached_unstable":
                repeater = self.dependencies.unstable_point_repeater
                if not callable(repeater):
                    raise FormalSGDPointError(
                        "unstable first run requires three valid independent same-point runs"
                    )
                binding_sources = {
                    "point_parameters_float_hex_sha256": {
                        name: float(value).hex() for name, value in sorted(all_costs.items())
                    },
                    "resource_envelope_sha256": context.get("readback_before"),
                    "template_sha256": context.get("template_restore"),
                    "workload_registry_sha256": (
                        (self.registration.get("evidence") or {}).get(
                            "workload_seed_registry_weights_clients"
                        )
                    ),
                    "plan_contract_sha256": context.get("plan_capture"),
                }
                expected_bindings = {
                    name: "sha256:" + hashlib.sha256(
                        json.dumps(value, ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":"), default=str).encode("utf-8")
                    ).hexdigest()
                    for name, value in binding_sources.items()
                }
                context["expected_independent_run_contract_bindings"] = expected_bindings
                attempts = repeater(copy.deepcopy(dict(candidate)), context)
                if not isinstance(attempts, Sequence) or isinstance(attempts, (str, bytes)):
                    raise FormalSGDPointError("unstable point repeater must return an attempt sequence")
                context["independent_run_aggregation"] = average_three_valid_independent_runs(
                    attempts, expected_contract_bindings=expected_bindings,
                )
                self._event("three_valid_independent_runs_averaged")
            context["readback_after"] = _mapping(
                self.dependencies.resource_fixed_readback(context, "after"),
                "resource_fixed_readback(after)",
            )
            self._event("resource_fixed_readback_after")
            finalized = _mapping(
                self.dependencies.finalizer(context["continuous_measurement"], context),
                "finalizer",
            )
            measurement = _mapping(finalized.get("measurement"), "finalizer.measurement")
            aggregation = context.get("independent_run_aggregation")
            if isinstance(aggregation, Mapping):
                measurement = {
                    **copy.deepcopy(dict(aggregation["metric_means"])),
                    "utilization_pct": copy.deepcopy(aggregation["resource_means"]),
                    "stable": True,
                    "db_qps_source": "database_status_counter",
                    "correctness_passed": True,
                    "oom": False,
                    "mysql_restart": False,
                    "container_restart": False,
                    "independent_run_aggregation": copy.deepcopy(aggregation),
                }
            _validate_final_measurement(measurement, context)
            measurement["evidence"] = self._build_evidence(context, finalized)
            self._event("evidence")
            measurement["formal_point_trace"] = list(self._point_events)
            if self.journal is not None:
                self.journal.checkpoint(
                    "measurement_complete",
                    {"measurement": copy.deepcopy(measurement), "measurement_complete": True},
                )
                measurement["resume_token"] = self._journal_token
                measurement["accounting_token"] = self._journal_token
                measurement["resume_command"] = context["resume_command"]
            return measurement
        except BaseException as exc:
            # The optimizer invokes restore() after callback failure.  Preserve
            # the live attempt and lease until that recovery proof is collected.
            if self.journal is not None:
                self.journal.checkpoint(
                    "point_failed",
                    {
                        "failure": f"{type(exc).__name__}: {exc}",
                        "last_completed_stage": (
                            self._point_events[-1] if self._point_events else None
                        ),
                        "recovery_context": copy.deepcopy(context),
                    },
                )
            raise

    def _build_evidence(
        self, context: Mapping[str, Any], finalized: Mapping[str, Any]
    ) -> Dict[str, Any]:
        static = self.registration.get("evidence") or {}
        final_refs = finalized.get("evidence") or {}
        evidence = {
            "campaign_method_benchmark_scenario": static.get("campaign_method_benchmark_scenario"),
            "envelope_and_template_versions": {
                "registration": static.get("envelope_and_template_versions"),
                "template_restore": context.get("template_restore"),
            },
            "round_candidate_repeat": context["optimizer_context"],
            "lane_host_container_port": {
                "registration": static.get("lane_host_container_port"),
                "lease": context.get("lease_evidence"),
            },
            "software_checksums": static.get("software_checksums"),
            "workload_seed_registry_weights_clients": static.get("workload_seed_registry_weights_clients"),
            "cpuset_and_numa": static.get("cpuset_and_numa"),
            "resource_limit_readback": {
                "before": context.get("readback_before"), "after": context.get("readback_after"),
            },
            "fixed_mysql_settings_before_after": {
                "before": context.get("readback_before"), "after": context.get("readback_after"),
            },
            "requested_effective_costs": context.get("cost_apply"),
            "fresh_connection_generation": context.get("cost_apply"),
            "explain_and_fingerprints": context.get("plan_capture"),
            "estimator_model_formula": final_refs.get("estimator_model_formula"),
            "per_query_operator_breakdown": final_refs.get("per_query_operator_breakdown"),
            "per_second_samples": final_refs.get("per_second_samples") or context.get("continuous_measurement"),
            "stability_windows_and_coverage": final_refs.get("stability_windows_and_coverage") or context.get("continuous_measurement"),
            "independent_run_aggregation": context.get("independent_run_aggregation"),
            "correctness_errors_oom_restarts": context.get("correctness"),
            "utility_components": final_refs.get("utility_components"),
            "range_before_after": static.get("range_before_after"),
            "subset_sgd_trajectory_and_winner": (
                final_refs.get("subset_sgd_trajectory_and_winner")
                or static.get("subset_sgd_trajectory_and_winner")
                or {
                    "subset_id": (context.get("candidate") or {}).get("subset_id"),
                    "candidate_rank": (context.get("candidate") or {}).get("rank"),
                    "stage": context["optimizer_context"].get("stage"),
                    "optimized_active_costs": copy.deepcopy(
                        (context.get("candidate") or {}).get("optimized_params")
                    ),
                }
            ),
            "experience_table_direction_eta": static.get("experience_table_direction_eta"),
        }
        aggregation = context.get("independent_run_aggregation")
        if isinstance(aggregation, Mapping):
            bundles = aggregation.get("evidence_bundles") or []
            for name in REQUIRED_REAL_REPLAY_EVIDENCE:
                evidence[name] = {
                    "source": "three_valid_independent_run_evidence_bundles",
                    "run_values": [copy.deepcopy(bundle[name]) for bundle in bundles],
                    "contract_bindings": copy.deepcopy(aggregation.get("contract_bindings")),
                }
        missing = [name for name in REQUIRED_REAL_REPLAY_EVIDENCE if not evidence.get(name)]
        if missing:
            raise FormalSGDPointError(f"formal evidence references are incomplete: {missing}")
        return evidence

    def recover_resume(self, token: str) -> Mapping[str, Any]:
        """Recover an interrupted point without silently replaying/counting it."""
        if self.journal is None:
            raise FormalSGDResumeError("driver has no checkpoint journal")
        record = self.journal.attach_verified(token)
        if (
            record.get("recovery_verified") is True
            and record.get("lease_released") is True
            and record.get("pending") is None
        ):
            result = {
                "status": "already_complete",
                "resume_token": token,
                "accounting_token": record["accounting_token"],
                "measurement": copy.deepcopy(record.get("measurement")),
                "replay_required": False,
                "restart_outer_attempt_required": False,
            }
            self.journal.close()
            return result
        context = record.get("recovery_context")
        if not isinstance(context, Mapping):
            self.journal.checkpoint(
                "resume_state_unprovable",
                {"restart_outer_attempt_required": True},
            )
            raise FormalSGDResumeError(
                "checkpoint lacks recovery context; start a new outer attempt"
            )
        context = copy.deepcopy(dict(context))
        lease = self.dependencies.lease_factory(context)
        lease_error: BaseException | None = None
        lease_entered = False
        try:
            lease_value = lease.__enter__()
            lease_entered = True
            self.journal.checkpoint(
                "resume_lease_acquired",
                {
                    "lease_ownership": {
                        "pid": os.getpid(),
                        "host": socket.gethostname(),
                        "lease_evidence": copy.deepcopy(
                            getattr(lease_value, "evidence", lease_value)
                        ),
                        "released": False,
                        "previous_owner": copy.deepcopy(record.get("lease_ownership")),
                    }
                },
            )
            recovery = _mapping(self.dependencies.recovery(context), "resume recovery")
            _validate_recovery_evidence(recovery, context["baseline_all_costs"])
            self.journal.checkpoint(
                "resume_recovery_verified",
                {"recovery_verified": True, "recovery": recovery},
            )
        except BaseException as exc:
            lease_error = exc
            self.journal.checkpoint(
                "resume_recovery_failed",
                {
                    "restart_outer_attempt_required": True,
                    "resume_error": f"{type(exc).__name__}: {exc}",
                },
            )
            raise FormalSGDResumeError(
                "resume recovery could not prove template/cost/fixed state; "
                "start a new outer attempt"
            ) from exc
        finally:
            try:
                if lease_entered:
                    lease.__exit__(
                        type(lease_error) if lease_error else None,
                        lease_error,
                        lease_error.__traceback__ if lease_error else None,
                    )
            finally:
                self.journal.checkpoint(
                    (
                        "resume_lease_released"
                        if lease_entered else "resume_lease_not_acquired"
                    ),
                    {
                        "lease_released": lease_entered,
                        "lease_acquired_on_resume": lease_entered,
                    },
                )
        measurement_complete = record.get("measurement_complete") is True
        self.journal.checkpoint(
            "resume_decision",
            {
                "pending": None if measurement_complete else record.get("pending"),
                "restart_outer_attempt_required": not measurement_complete,
            },
        )
        if not measurement_complete:
            result = {
                "status": "restart_outer_attempt_required",
                "resume_token": token,
                "accounting_token": record["accounting_token"],
                "measurement": None,
                "replay_required": False,
                "restart_outer_attempt_required": True,
                "reason": "interrupted_before_complete_measurement",
            }
            self.journal.close()
            return result
        result = {
            "status": "measurement_recovered",
            "resume_token": token,
            "accounting_token": record["accounting_token"],
            "measurement": copy.deepcopy(record["measurement"]),
            "replay_required": False,
            "restart_outer_attempt_required": False,
        }
        self.journal.close()
        return result

    def reconcile_started_replay(
        self, started: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Recover a two-phase outer replay from the same-token point journal."""
        if self.journal is None:
            raise FormalSGDResumeError(
                "started outer replay has no point journal for reconciliation"
            )
        token = str(started.get("attempt_token") or "")
        if not token:
            raise FormalSGDResumeError("started outer replay lacks attempt token")
        result = self.recover_resume(token)
        if result.get("status") not in {"already_complete", "measurement_recovered"}:
            raise FormalSGDResumeError(
                "started replay journal cannot prove a completed measurement"
            )
        record = FormalPointJournal.load_verified(self.journal.root, token)
        recovery = record.get("recovery")
        if not isinstance(result.get("measurement"), Mapping) or not isinstance(recovery, Mapping):
            raise FormalSGDResumeError(
                "started replay journal lacks measurement or recovery evidence"
            )
        return {
            "status": "completed",
            "measurement": copy.deepcopy(dict(result["measurement"])),
            "recovery": copy.deepcopy(dict(recovery)),
            "attempt_token": token,
        }

    def restore(
        self, candidate: Mapping[str, Any], optimizer_context: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        attempt = self._attempt
        if attempt is None:
            raise FormalSGDPointError("restore called without a live formal point")
        lease = attempt["lease"]
        context = attempt["context"]
        if not attempt.get("lease_entered"):
            if self.journal is not None:
                self.journal.checkpoint(
                    "restore_without_lease",
                    {
                        "restart_outer_attempt_required": True,
                        "lease_released": False,
                    },
                )
            self._attempt = None
            raise FormalSGDResumeError(
                "point lease was never acquired; start a new outer attempt"
            )
        context["optimizer_restore_context"] = copy.deepcopy(dict(optimizer_context))
        recovery_error: BaseException | None = None
        recovery: Dict[str, Any] | None = None
        try:
            recovery = _mapping(self.dependencies.recovery(context), "recovery")
            self._event("recovery")
            _validate_recovery_evidence(recovery, context["baseline_all_costs"])
            recovery.setdefault("effective_params", optimizer_context["baseline_params"])
            if self.journal is not None:
                self.journal.checkpoint(
                    "recovery_verified",
                    {"recovery_verified": True, "recovery": copy.deepcopy(recovery)},
                )
        except BaseException as exc:
            recovery_error = exc
            raise
        finally:
            try:
                if attempt.get("lease_entered"):
                    lease.__exit__(
                        type(recovery_error) if recovery_error else None,
                        recovery_error,
                        recovery_error.__traceback__ if recovery_error else None,
                    )
                    self._event("lease_release")
            finally:
                self._attempt = None
        if recovery is None:  # pragma: no cover - successful path assigns it
            raise AssertionError("recovery result disappeared")
        recovery["lease_released"] = True
        recovery["point_trace"] = list(self._point_events)
        recovery["resume_token"] = self._journal_token
        recovery["accounting_token"] = self._journal_token
        if self.journal is not None:
            self.journal.checkpoint(
                "point_complete",
                {
                    "lease_released": True,
                    "pending": None,
                    "restart_outer_attempt_required": False,
                },
            )
            self.journal.close()
        return recovery


def build_replay_driver(
    benchmark: str,
    registration: Mapping[str, Any], dependencies: FormalPointDependencies,
    journal: FormalPointJournal | None = None,
) -> FormalMySQLSGDReplayDriver:
    return FormalMySQLSGDReplayDriver(benchmark, registration, dependencies, journal)


def build_job_replay_driver(
    registration: Mapping[str, Any], dependencies: FormalPointDependencies,
    journal: FormalPointJournal | None = None,
) -> FormalMySQLSGDReplayDriver:
    return build_replay_driver("job", registration, dependencies, journal)


def build_hybench_ap_replay_driver(
    registration: Mapping[str, Any], dependencies: FormalPointDependencies,
    journal: FormalPointJournal | None = None,
) -> FormalMySQLSGDReplayDriver:
    return build_replay_driver("hybench_ap", registration, dependencies, journal)


def build_tpch_replay_driver(
    registration: Mapping[str, Any], dependencies: FormalPointDependencies,
    journal: FormalPointJournal | None = None,
) -> FormalMySQLSGDReplayDriver:
    return build_replay_driver("tpch", registration, dependencies, journal)


def build_hybench_xp_replay_driver(
    registration: Mapping[str, Any], dependencies: FormalPointDependencies,
    journal: FormalPointJournal | None = None,
) -> FormalMySQLSGDReplayDriver:
    return build_replay_driver("hybench_xp", registration, dependencies, journal)


def build_tpcc_replay_driver(
    registration: Mapping[str, Any], dependencies: FormalPointDependencies,
    journal: FormalPointJournal | None = None,
) -> FormalMySQLSGDReplayDriver:
    return build_replay_driver("tpcc", registration, dependencies, journal)


__all__ = [
    "ContinuousMeasurementSessionFactory",
    "FormalMySQLSGDReplayDriver",
    "FormalPointJournal",
    "FormalPointDependencies",
    "FormalSGDNotReady",
    "FormalSGDPointError",
    "FormalSGDResumeError",
    "average_three_valid_independent_runs",
    "build_hybench_ap_replay_driver",
    "build_hybench_xp_replay_driver",
    "build_job_replay_driver",
    "build_replay_driver",
    "build_tpcc_replay_driver",
    "build_tpch_replay_driver",
]

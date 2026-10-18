"""Frozen Recost proposal logic for the six-session MySQL campaign.

This module deliberately contains no SQL, Docker, benchmark, or scheduler code.
The inner loop receives a pure estimator callable and can only propose a point.
Only the outer controller is allowed to replay that point against a live database
and apply the frozen acceptance policy.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import stat
from typing import Any, Callable, Mapping, TextIO, Union


MYSQL_COST_NAMES = (
    "io_block_read_cost",
    "memory_block_read_cost",
    "row_evaluate_cost",
    "key_compare_cost",
    "disk_temptable_create_cost",
    "disk_temptable_row_cost",
    "memory_temptable_create_cost",
    "memory_temptable_row_cost",
)


class FrozenRecostContractError(RuntimeError):
    """The supplied configuration is not the frozen Recost contract."""


@dataclass(frozen=True)
class ParameterSpec:
    name: str
    minimum: float
    maximum: float
    transform: str


@dataclass(frozen=True)
class RecostSettings:
    method_seed: int
    estimator_contract: str
    estimator_id: str
    estimator_version: str
    estimator_implementation_sha256: str
    estimator_training_scope: str
    outer_real_candidate_limit: int
    inner_iteration_limit_per_outer: int
    initial_eta: float
    gamma: float
    min_eta: float
    eta_growth_factor: float
    eta_shrink_factor: float
    max_eta: float
    finite_difference_epsilon_normalized: float
    max_normalized_step: float
    minimum_relative_parameter_step: float
    patience: int
    no_update_patience: int
    consecutive_outer_rejections: int
    minimum_real_candidates_before_stop: int


@dataclass(frozen=True)
class EstimatorValue:
    utility: float
    diagnostics: Mapping[str, Any]


@dataclass(frozen=True)
class RecostProposal:
    params: Mapping[str, float]
    normalized_params: Mapping[str, float]
    estimated_utility: float
    inner_iterations: int
    inner_accepted_iterations: int
    estimator_version: str
    duplicate_of_incumbent: bool
    stop_reason: str


Estimator = Callable[[Mapping[str, float]], Union[float, EstimatorValue]]


def _finite(value: Any, label: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise FrozenRecostContractError(f"{label} must be finite")
    return number


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        raise FrozenRecostContractError(
            f"{label} fields differ: missing={sorted(expected - actual)} "
            f"extra={sorted(actual - expected)}"
        )


def load_frozen_recost_contract(
    method_protocols_path: Path,
    search_spaces_path: Path,
) -> tuple[RecostSettings, tuple[ParameterSpec, ...]]:
    """Load and strictly validate the immutable Recost/search-space fields."""

    method_doc = json.loads(method_protocols_path.read_text(encoding="utf-8"))
    search_doc = json.loads(search_spaces_path.read_text(encoding="utf-8"))
    estimator_contract_path = method_protocols_path.parent / "recost_estimator.json"
    estimator_doc = json.loads(estimator_contract_path.read_text(encoding="utf-8"))
    if not isinstance(method_doc, dict) or not isinstance(search_doc, dict):
        raise FrozenRecostContractError("frozen documents must be JSON objects")
    if not isinstance(estimator_doc, dict):
        raise FrozenRecostContractError("Recost estimator document must be a JSON object")
    common = method_doc.get("common")
    if not isinstance(common, dict) or common.get("method_seed") != 20260721:
        raise FrozenRecostContractError("frozen common method_seed is missing or changed")
    method = method_doc.get("recost_gradient_descent")
    if not isinstance(method, dict):
        raise FrozenRecostContractError("recost_gradient_descent is missing")
    expected_method_fields = {
        "method_id",
        "estimator_contract",
        "estimator_id",
        "estimator_version",
        "estimator_implementation_sha256",
        "estimator_training_scope",
        "outer_real_candidate_limit",
        "inner_iteration_limit_per_outer",
        "initial_eta",
        "gamma",
        "min_eta",
        "eta_growth_factor",
        "eta_shrink_factor",
        "max_eta",
        "finite_difference_epsilon_normalized",
        "max_normalized_step",
        "minimum_relative_parameter_step",
        "patience",
        "no_update_patience",
        "inner_accept_label",
        "inner_uses_live_database",
        "outer_requires_live_replay",
        "outer_accept_label",
        "estimator_version_record_required",
        "early_stop",
    }
    _exact_keys(method, expected_method_fields, "recost_gradient_descent")
    required_literals = {
        "method_id": "recost_gradient_descent",
        "inner_accept_label": "inner_step_accepted",
        "inner_uses_live_database": False,
        "outer_requires_live_replay": True,
        "outer_accept_label": "outer_candidate_accepted",
        "estimator_version_record_required": True,
        "estimator_contract": "recost_estimator.json",
        "estimator_id": "recost_current_run_ridge_v1",
        "estimator_version": "recost-current-run-ridge-v1",
        "estimator_implementation_sha256": "3c0397fb60ea9d21c3f353a543c57bdab54d6efb8e5ca421510fb5dc82c43247",
        "estimator_training_scope": "current_engine_case_run_repeat_only",
    }
    for name, expected in required_literals.items():
        if method.get(name) != expected:
            raise FrozenRecostContractError(
                f"recost_gradient_descent.{name}={method.get(name)!r}, expected {expected!r}"
            )
    early = method.get("early_stop")
    if not isinstance(early, dict):
        raise FrozenRecostContractError("recost early_stop must be an object")
    _exact_keys(
        early,
        {"consecutive_outer_rejections", "minimum_real_candidates_before_stop"},
        "recost_gradient_descent.early_stop",
    )
    settings = RecostSettings(
        method_seed=20260721,
        estimator_contract=str(method["estimator_contract"]),
        estimator_id=str(method["estimator_id"]),
        estimator_version=str(method["estimator_version"]),
        estimator_implementation_sha256=str(method["estimator_implementation_sha256"]),
        estimator_training_scope=str(method["estimator_training_scope"]),
        outer_real_candidate_limit=int(method["outer_real_candidate_limit"]),
        inner_iteration_limit_per_outer=int(method["inner_iteration_limit_per_outer"]),
        initial_eta=_finite(method["initial_eta"], "initial_eta"),
        gamma=_finite(method["gamma"], "gamma"),
        min_eta=_finite(method["min_eta"], "min_eta"),
        eta_growth_factor=_finite(method["eta_growth_factor"], "eta_growth_factor"),
        eta_shrink_factor=_finite(method["eta_shrink_factor"], "eta_shrink_factor"),
        max_eta=_finite(method["max_eta"], "max_eta"),
        finite_difference_epsilon_normalized=_finite(
            method["finite_difference_epsilon_normalized"],
            "finite_difference_epsilon_normalized",
        ),
        max_normalized_step=_finite(method["max_normalized_step"], "max_normalized_step"),
        minimum_relative_parameter_step=_finite(
            method["minimum_relative_parameter_step"], "minimum_relative_parameter_step"
        ),
        patience=int(method["patience"]),
        no_update_patience=int(method["no_update_patience"]),
        consecutive_outer_rejections=int(early["consecutive_outer_rejections"]),
        minimum_real_candidates_before_stop=int(early["minimum_real_candidates_before_stop"]),
    )
    if not (
        settings.outer_real_candidate_limit == 10
        and settings.inner_iteration_limit_per_outer == 500
        and settings.initial_eta == 2.0
        and settings.gamma == 0.8
        and settings.min_eta == 0.001
        and settings.eta_growth_factor == 1.25
        and settings.eta_shrink_factor == 0.5
        and settings.max_eta == 50.0
        and settings.finite_difference_epsilon_normalized == 0.05
        and settings.max_normalized_step == 0.2
        and settings.minimum_relative_parameter_step == 0.1
        and settings.patience == 3
        and settings.no_update_patience == 2
        and settings.consecutive_outer_rejections == 4
        and settings.minimum_real_candidates_before_stop == 6
    ):
        raise FrozenRecostContractError("Recost numeric fields are not the frozen v3 values")

    _validate_estimator_contract(estimator_doc, settings)

    mysql = search_doc.get("mysql")
    if not isinstance(mysql, dict) or not isinstance(mysql.get("parameters"), dict):
        raise FrozenRecostContractError("mysql search space is missing")
    params = mysql["parameters"]
    if set(params) != set(MYSQL_COST_NAMES):
        raise FrozenRecostContractError("MySQL search space must contain exactly eight costs")
    specs: list[ParameterSpec] = []
    for name in MYSQL_COST_NAMES:
        raw = params[name]
        if not isinstance(raw, dict):
            raise FrozenRecostContractError(f"search space for {name} is not an object")
        _exact_keys(raw, {"min", "max", "transform", "type"}, f"search space {name}")
        minimum = _finite(raw["min"], f"{name}.min")
        maximum = _finite(raw["max"], f"{name}.max")
        if raw["transform"] != "log" or raw["type"] != "continuous":
            raise FrozenRecostContractError(f"{name} must be log continuous")
        if minimum <= 0.0 or maximum <= minimum:
            raise FrozenRecostContractError(f"invalid bounds for {name}")
        specs.append(ParameterSpec(name, minimum, maximum, "log"))
    return settings, tuple(specs)


def _validate_estimator_contract(
    estimator_doc: Mapping[str, Any],
    settings: RecostSettings,
) -> None:
    if estimator_doc.get("schema_version") != 1 or estimator_doc.get("immutable") is not True:
        raise FrozenRecostContractError("Recost estimator contract is not immutable schema_version=1")
    if estimator_doc.get("estimator_id") != settings.estimator_id:
        raise FrozenRecostContractError("Recost estimator_id differs from method protocol")
    if estimator_doc.get("estimator_version") != settings.estimator_version:
        raise FrozenRecostContractError("Recost estimator_version differs from method protocol")
    implementation = estimator_doc.get("implementation")
    if not isinstance(implementation, dict):
        raise FrozenRecostContractError("Recost estimator implementation object is missing")
    if implementation.get("source_sha256") != settings.estimator_implementation_sha256:
        raise FrozenRecostContractError("Recost estimator source SHA differs from method protocol")
    paths = (implementation.get("postgresql_path"), implementation.get("mysql_path"))
    if not all(isinstance(item, str) and item for item in paths):
        raise FrozenRecostContractError("Recost estimator source paths are missing")
    contents: list[bytes] = []
    for raw_path in paths:
        source_path = Path(str(raw_path))
        mode = source_path.lstat().st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            raise FrozenRecostContractError(
                f"Recost estimator source is not a regular file: {source_path}"
            )
        content = source_path.read_bytes()
        if hashlib.sha256(content).hexdigest() != settings.estimator_implementation_sha256:
            raise FrozenRecostContractError(f"Recost estimator source SHA mismatch: {source_path}")
        contents.append(content)
    if contents[0] != contents[1]:
        raise FrozenRecostContractError("PostgreSQL and MySQL Recost estimator sources differ")


class FrozenRecostProposalEngine:
    """Pure Inner-loop proposal engine plus frozen Outer early-stop accounting."""

    def __init__(
        self,
        settings: RecostSettings,
        parameters: tuple[ParameterSpec, ...],
        *,
        estimator_version: str,
    ) -> None:
        if estimator_version.strip() != settings.estimator_version:
            raise FrozenRecostContractError("estimator_version differs from the frozen contract")
        if tuple(spec.name for spec in parameters) != MYSQL_COST_NAMES:
            raise FrozenRecostContractError("parameter order must be the frozen MySQL order")
        self.settings = settings
        self.parameters = parameters
        self.estimator_version = estimator_version.strip()
        self.real_candidate_count = 0
        self.consecutive_outer_rejections = 0

    def normalize(self, params: Mapping[str, float]) -> dict[str, float]:
        if set(params) != set(MYSQL_COST_NAMES):
            raise FrozenRecostContractError("parameter point must contain exactly eight costs")
        result: dict[str, float] = {}
        for spec in self.parameters:
            value = _finite(params[spec.name], spec.name)
            tolerance = max(abs(spec.minimum), abs(spec.maximum), 1.0) * 1e-12
            if value < spec.minimum - tolerance or value > spec.maximum + tolerance:
                raise FrozenRecostContractError(f"{spec.name} is outside frozen bounds")
            value = min(spec.maximum, max(spec.minimum, value))
            result[spec.name] = (
                math.log(value) - math.log(spec.minimum)
            ) / (math.log(spec.maximum) - math.log(spec.minimum))
        return result

    def denormalize(self, normalized: Mapping[str, float]) -> dict[str, float]:
        if set(normalized) != set(MYSQL_COST_NAMES):
            raise FrozenRecostContractError("normalized point must contain exactly eight costs")
        result: dict[str, float] = {}
        for spec in self.parameters:
            unit = min(1.0, max(0.0, _finite(normalized[spec.name], spec.name)))
            if unit <= 0.0:
                result[spec.name] = spec.minimum
            elif unit >= 1.0:
                result[spec.name] = spec.maximum
            else:
                result[spec.name] = math.exp(
                    math.log(spec.minimum)
                    + unit * (math.log(spec.maximum) - math.log(spec.minimum))
                )
        return result

    @staticmethod
    def _estimate(estimator: Estimator, params: Mapping[str, float]) -> EstimatorValue:
        raw = estimator(params)
        if isinstance(raw, EstimatorValue):
            value = raw
        else:
            value = EstimatorValue(float(raw), {})
        utility = float(value.utility)
        if not math.isfinite(utility):
            raise FrozenRecostContractError("estimator returned non-finite utility")
        return EstimatorValue(utility, dict(value.diagnostics))

    def _minimum_normalized_delta(self, spec: ParameterSpec) -> float:
        return math.log1p(self.settings.minimum_relative_parameter_step) / (
            math.log(spec.maximum) - math.log(spec.minimum)
        )

    @staticmethod
    def _write_row(handle: TextIO | None, row: Mapping[str, Any]) -> None:
        if handle is None:
            return
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")
        handle.flush()

    def propose(
        self,
        incumbent_params: Mapping[str, float],
        estimator: Estimator,
        *,
        inner_log: TextIO | None = None,
    ) -> RecostProposal:
        current = self.normalize(incumbent_params)
        initial = dict(current)
        current_eval = self._estimate(estimator, self.denormalize(current))
        eta = self.settings.initial_eta
        velocity = {name: 0.0 for name in MYSQL_COST_NAMES}
        accepted_count = 0
        no_improvement = 0
        no_update = 0
        stop_reason = "inner_iteration_limit"
        completed = 0

        for inner_iteration in range(1, self.settings.inner_iteration_limit_per_outer + 1):
            completed = inner_iteration
            params_before_normalized = dict(current)
            params_before = self.denormalize(params_before_normalized)
            utility_before = current_eval.utility
            gradient: dict[str, float] = {}
            finite_difference: dict[str, Any] = {}
            epsilon = self.settings.finite_difference_epsilon_normalized
            for spec in self.parameters:
                center = current[spec.name]
                low = max(0.0, center - epsilon)
                high = min(1.0, center + epsilon)
                if high <= low:
                    derivative = 0.0
                    low_eval = high_eval = current_eval
                else:
                    low_point = dict(current)
                    high_point = dict(current)
                    low_point[spec.name] = low
                    high_point[spec.name] = high
                    low_eval = self._estimate(estimator, self.denormalize(low_point))
                    high_eval = self._estimate(estimator, self.denormalize(high_point))
                    derivative = (high_eval.utility - low_eval.utility) / (high - low)
                gradient[spec.name] = derivative
                finite_difference[spec.name] = {
                    "low_normalized": low,
                    "high_normalized": high,
                    "low_utility": low_eval.utility,
                    "high_utility": high_eval.utility,
                    "derivative": derivative,
                }

            norm = math.sqrt(sum(value * value for value in gradient.values()))
            candidate = dict(current)
            changed: list[str] = []
            clipped: list[str] = []
            if norm <= 1e-15:
                no_update += 1
            else:
                for spec in self.parameters:
                    unit_gradient = gradient[spec.name] / norm
                    velocity[spec.name] = (
                        self.settings.gamma * velocity[spec.name]
                        + (1.0 - self.settings.gamma) * unit_gradient
                    )
                    delta = eta * velocity[spec.name]
                    delta = max(
                        -self.settings.max_normalized_step,
                        min(self.settings.max_normalized_step, delta),
                    )
                    if abs(delta) > 1e-15:
                        minimum_delta = self._minimum_normalized_delta(spec)
                        if abs(delta) < minimum_delta:
                            delta = math.copysign(minimum_delta, delta)
                    requested = current[spec.name] + delta
                    bounded = min(1.0, max(0.0, requested))
                    if not math.isclose(requested, bounded, rel_tol=0.0, abs_tol=1e-15):
                        clipped.append(spec.name)
                    if not math.isclose(current[spec.name], bounded, rel_tol=0.0, abs_tol=1e-15):
                        candidate[spec.name] = bounded
                        changed.append(spec.name)
                no_update = 0 if changed else no_update + 1

            candidate_params = self.denormalize(candidate)
            candidate_eval = self._estimate(estimator, candidate_params)
            inner_accepted = bool(changed) and candidate_eval.utility > current_eval.utility
            eta_before = eta
            if inner_accepted:
                current = candidate
                current_eval = candidate_eval
                accepted_count += 1
                no_improvement = 0
                eta = min(self.settings.max_eta, eta * self.settings.eta_growth_factor)
                reason = "estimated_utility_improved"
            else:
                no_improvement += 1
                eta = max(self.settings.min_eta, eta * self.settings.eta_shrink_factor)
                reason = "no_parameter_update" if not changed else "estimated_utility_not_improved"

            self._write_row(
                inner_log,
                {
                    "schema_version": 1,
                    "inner_iteration": inner_iteration,
                    "label": "inner_step_accepted",
                    "inner_step_accepted": inner_accepted,
                    "reason": reason,
                    "estimator_version": self.estimator_version,
                    "estimator_access": "offline_only_no_live_database",
                    "params_before_normalized": params_before_normalized,
                    "params_before": params_before,
                    "params_candidate_normalized": candidate,
                    "params_candidate": candidate_params,
                    "candidate_estimated_utility": candidate_eval.utility,
                    "current_estimated_utility_before": utility_before,
                    "current_estimated_utility_after": current_eval.utility,
                    "candidate_estimator_diagnostics": dict(candidate_eval.diagnostics),
                    "gradient": gradient,
                    "finite_difference": finite_difference,
                    "gradient_l2_norm": norm,
                    "eta_before": eta_before,
                    "eta_after": eta,
                    "changed_parameters": changed,
                    "clipped_parameters": clipped,
                },
            )

            if no_update >= self.settings.no_update_patience:
                stop_reason = "no_update_patience"
                break
            if no_improvement >= self.settings.patience:
                stop_reason = "inner_patience"
                break
            if eta <= self.settings.min_eta and not inner_accepted:
                stop_reason = "min_eta_after_rejection"
                break

        duplicate = all(
            math.isclose(current[name], initial[name], rel_tol=0.0, abs_tol=1e-15)
            for name in MYSQL_COST_NAMES
        )
        return RecostProposal(
            params=self.denormalize(current),
            normalized_params=current,
            estimated_utility=current_eval.utility,
            inner_iterations=completed,
            inner_accepted_iterations=accepted_count,
            estimator_version=self.estimator_version,
            duplicate_of_incumbent=duplicate,
            stop_reason=stop_reason,
        )

    def record_outer_result(self, *, outer_candidate_accepted: bool) -> bool:
        """Record one live Outer replay and return whether frozen early-stop fires."""

        if self.real_candidate_count >= self.settings.outer_real_candidate_limit:
            raise FrozenRecostContractError("Outer real-candidate budget is already exhausted")
        self.real_candidate_count += 1
        if outer_candidate_accepted:
            self.consecutive_outer_rejections = 0
        else:
            self.consecutive_outer_rejections += 1
        return (
            self.real_candidate_count >= self.settings.minimum_real_candidates_before_stop
            and self.consecutive_outer_rejections
            >= self.settings.consecutive_outer_rejections
        )

    def state(self) -> Mapping[str, Any]:
        return {
            "settings": asdict(self.settings),
            "estimator_version": self.estimator_version,
            "real_candidate_count": self.real_candidate_count,
            "consecutive_outer_rejections": self.consecutive_outer_rejections,
        }

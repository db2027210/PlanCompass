"""Deterministic current-run-only offline estimator for formal Recost.

The estimator never contacts a database. It starts with the current Run's
baseline at objective zero, uses a deterministic seeded linear exploration
prior, and refits a ridge-linear surrogate after each Outer live replay.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from typing import Any, Dict, List, Mapping, Sequence, Tuple


ESTIMATOR_ID = "recost_current_run_ridge_v1"
ESTIMATOR_VERSION = "recost-current-run-ridge-v1"
USES_LIVE_DATABASE = False


class FrozenEstimatorError(RuntimeError):
    """Raised when inputs violate the frozen estimator contract."""


@dataclass(frozen=True)
class EstimatorParameter:
    """One ordered transformed-space parameter."""

    name: str
    minimum: float
    maximum: float
    transform: str


@dataclass(frozen=True)
class EstimatorObservation:
    """One current-incumbent-relative Outer observation."""

    normalized_params: Tuple[float, ...]
    objective_improvement: float
    outer_candidate_accepted: bool


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FrozenEstimatorError(f"{label} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise FrozenEstimatorError(f"{label} must be finite")
    return number


def _solve_linear_system(matrix: Sequence[Sequence[float]], vector: Sequence[float]) -> List[float]:
    """Solve a small dense system with deterministic partial pivoting."""

    size = len(vector)
    if size == 0 or len(matrix) != size or any(len(row) != size for row in matrix):
        raise FrozenEstimatorError("ridge system has invalid dimensions")
    augmented = [
        [float(value) for value in matrix[row]] + [float(vector[row])]
        for row in range(size)
    ]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: (abs(augmented[row][column]), -row))
        if abs(augmented[pivot][column]) <= 1e-15:
            raise FrozenEstimatorError("ridge system is singular")
        if pivot != column:
            augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            if abs(factor) <= 1e-18:
                continue
            augmented[row] = [
                augmented[row][index] - factor * augmented[column][index]
                for index in range(size + 1)
            ]
    result = [augmented[row][-1] for row in range(size)]
    if not all(math.isfinite(value) for value in result):
        raise FrozenEstimatorError("ridge solution is non-finite")
    return result


class FrozenOnlineRecostEstimator:
    """Ridge-linear surrogate with a deterministic decaying exploration prior."""

    version = ESTIMATOR_VERSION
    uses_live_database = USES_LIVE_DATABASE

    def __init__(
        self,
        parameters: Sequence[EstimatorParameter],
        initial_params: Mapping[str, float],
        *,
        method_seed: int,
        engine: str,
        case_id: str,
        scenario: str,
        ridge_lambda: float = 0.01,
        intercept_regularization: float = 1e-12,
        exploration_weight: float = 0.05,
        failure_objective: float = -1.0,
        objective_clip_abs: float = 2.0,
    ) -> None:
        self.parameters = tuple(parameters)
        if not self.parameters:
            raise FrozenEstimatorError("parameters must not be empty")
        names = tuple(parameter.name for parameter in self.parameters)
        if len(set(names)) != len(names):
            raise FrozenEstimatorError("parameter names must be unique")
        for parameter in self.parameters:
            if parameter.transform not in {"linear", "log"}:
                raise FrozenEstimatorError(f"unsupported transform for {parameter.name}")
            if not math.isfinite(parameter.minimum) or not math.isfinite(parameter.maximum):
                raise FrozenEstimatorError(f"non-finite bounds for {parameter.name}")
            if parameter.maximum <= parameter.minimum:
                raise FrozenEstimatorError(f"invalid bounds for {parameter.name}")
            if parameter.transform == "log" and parameter.minimum <= 0.0:
                raise FrozenEstimatorError(f"log bound must be positive for {parameter.name}")
        if tuple(initial_params.keys()) != names:
            raise FrozenEstimatorError("initial parameter order differs from estimator contract")
        if not isinstance(method_seed, int) or isinstance(method_seed, bool):
            raise FrozenEstimatorError("method_seed must be an integer")
        if engine not in {"postgresql", "mysql"}:
            raise FrozenEstimatorError("engine must be postgresql or mysql")
        if scenario not in {"memory", "io", "none"}:
            raise FrozenEstimatorError("scenario must be memory, io, or none")
        if not case_id:
            raise FrozenEstimatorError("case_id must not be empty")

        self.method_seed = method_seed
        self.engine = engine
        self.case_id = case_id
        self.scenario = scenario
        self.ridge_lambda = _finite(ridge_lambda, "ridge_lambda")
        self.intercept_regularization = _finite(
            intercept_regularization, "intercept_regularization"
        )
        self.exploration_weight = _finite(exploration_weight, "exploration_weight")
        self.failure_objective = _finite(failure_objective, "failure_objective")
        self.objective_clip_abs = _finite(objective_clip_abs, "objective_clip_abs")
        if self.ridge_lambda <= 0.0 or self.intercept_regularization <= 0.0:
            raise FrozenEstimatorError("ridge regularization must be positive")
        if self.exploration_weight <= 0.0 or self.objective_clip_abs <= 0.0:
            raise FrozenEstimatorError("exploration weight and clip must be positive")

        initial = self._normalize(initial_params)
        self._origin = initial
        self._incumbent = initial
        self._observations: List[EstimatorObservation] = [
            EstimatorObservation(initial, 0.0, True)
        ]
        self._outer_observation_count = 0
        self._coefficients: Tuple[float, ...] = ()
        self._refit()

    @property
    def parameter_names(self) -> Tuple[str, ...]:
        return tuple(parameter.name for parameter in self.parameters)

    @property
    def observation_count(self) -> int:
        return len(self._observations)

    def _normalize(self, params: Mapping[str, float]) -> Tuple[float, ...]:
        if tuple(params.keys()) != self.parameter_names:
            raise FrozenEstimatorError("parameter order/set differs from estimator contract")
        result: List[float] = []
        for parameter in self.parameters:
            value = _finite(params[parameter.name], parameter.name)
            tolerance = max(abs(parameter.minimum), abs(parameter.maximum), 1.0) * 1e-12
            if value < parameter.minimum - tolerance or value > parameter.maximum + tolerance:
                raise FrozenEstimatorError(f"{parameter.name} is outside frozen bounds")
            value = min(parameter.maximum, max(parameter.minimum, value))
            if parameter.transform == "linear":
                normalized = (value - parameter.minimum) / (
                    parameter.maximum - parameter.minimum
                )
            else:
                normalized = (
                    math.log(value) - math.log(parameter.minimum)
                ) / (math.log(parameter.maximum) - math.log(parameter.minimum))
            result.append(min(1.0, max(0.0, normalized)))
        return tuple(result)

    def _features(self, normalized: Sequence[float]) -> Tuple[float, ...]:
        return (1.0,) + tuple(
            float(normalized[index]) - self._origin[index]
            for index in range(len(self.parameters))
        )

    def _refit(self) -> None:
        feature_count = len(self.parameters) + 1
        matrix = [[0.0] * feature_count for _ in range(feature_count)]
        vector = [0.0] * feature_count
        for observation in self._observations:
            features = self._features(observation.normalized_params)
            target = observation.objective_improvement
            for row in range(feature_count):
                vector[row] += features[row] * target
                for column in range(feature_count):
                    matrix[row][column] += features[row] * features[column]
        matrix[0][0] += self.intercept_regularization
        for index in range(1, feature_count):
            matrix[index][index] += self.ridge_lambda
        self._coefficients = tuple(_solve_linear_system(matrix, vector))

    def _exploration_direction(self) -> Tuple[float, ...]:
        values: List[float] = []
        for parameter in self.parameters:
            token = (
                f"{self.method_seed}|{self.engine}|{self.case_id}|{self.scenario}|"
                f"{self._outer_observation_count}|{parameter.name}"
            ).encode("utf-8")
            digest = hashlib.sha256(token).digest()
            signed = 1.0 if digest[0] & 1 else -1.0
            magnitude = 0.5 + int.from_bytes(digest[1:3], "big") / 131070.0
            values.append(signed * magnitude)
        norm = math.sqrt(sum(value * value for value in values))
        if norm <= 0.0:
            raise FrozenEstimatorError("exploration direction has zero norm")
        return tuple(value / norm for value in values)

    def _estimated_objective(self, params: Mapping[str, float]) -> Tuple[float, Dict[str, Any]]:
        normalized = self._normalize(params)
        features = self._features(normalized)
        predicted = sum(
            self._coefficients[index] * features[index]
            for index in range(len(features))
        )
        direction = self._exploration_direction()
        prior_weight = self.exploration_weight / math.sqrt(
            1.0 + self._outer_observation_count
        )
        exploration = prior_weight * sum(
            direction[index] * (normalized[index] - self._incumbent[index])
            for index in range(len(normalized))
        )
        objective = max(
            -self.objective_clip_abs,
            min(self.objective_clip_abs, predicted + exploration),
        )
        metadata = {
            "estimator_id": ESTIMATOR_ID,
            "estimator_version": self.version,
            "uses_live_database": False,
            "engine": self.engine,
            "case_id": self.case_id,
            "scenario": self.scenario,
            "observation_count": len(self._observations),
            "outer_observation_count": self._outer_observation_count,
            "ridge_prediction": predicted,
            "exploration_bonus": exploration,
            "exploration_weight_effective": prior_weight,
            "objective_clipped": objective,
        }
        return objective, metadata

    def evaluate(self, params: Mapping[str, float]) -> Mapping[str, Any]:
        """Return the PG formal-core estimator mapping."""

        objective, metadata = self._estimated_objective(params)
        return {"estimated_objective": objective, "metadata": metadata}

    def __call__(self, params: Mapping[str, float]) -> float:
        """Return the MySQL formal-core scalar estimator value."""

        objective, _ = self._estimated_objective(params)
        return objective

    def observe_outer(
        self,
        *,
        params: Mapping[str, float],
        observed_utility_improvement: float | None,
        outer_candidate_accepted: bool,
    ) -> None:
        """Update only after one budgeted Outer live replay."""

        normalized = self._normalize(params)
        target = (
            self.failure_objective
            if observed_utility_improvement is None
            else _finite(observed_utility_improvement, "observed_utility_improvement")
        )
        self._outer_observation_count += 1
        if outer_candidate_accepted:
            self._origin = normalized
            self._incumbent = normalized
            self._observations = [EstimatorObservation(normalized, 0.0, True)]
        else:
            self._observations.append(
                EstimatorObservation(normalized, target, False)
            )
        self._refit()

    def state(self) -> Mapping[str, Any]:
        """Return a JSON-safe deterministic audit snapshot."""

        return {
            "estimator_id": ESTIMATOR_ID,
            "estimator_version": self.version,
            "uses_live_database": False,
            "method_seed": self.method_seed,
            "engine": self.engine,
            "case_id": self.case_id,
            "scenario": self.scenario,
            "ridge_lambda": self.ridge_lambda,
            "intercept_regularization": self.intercept_regularization,
            "exploration_weight": self.exploration_weight,
            "failure_objective": self.failure_objective,
            "objective_clip_abs": self.objective_clip_abs,
            "parameter_names": list(self.parameter_names),
            "origin": list(self._origin),
            "incumbent": list(self._incumbent),
            "outer_observation_count": self._outer_observation_count,
            "observations": [
                {
                    "normalized_params": list(observation.normalized_params),
                    "objective_improvement": observation.objective_improvement,
                    "outer_candidate_accepted": observation.outer_candidate_accepted,
                }
                for observation in self._observations
            ],
            "coefficients": list(self._coefficients),
        }

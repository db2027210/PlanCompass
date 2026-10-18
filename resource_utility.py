"""Module."""


import math
import numpy as np
from typing import Any, Dict, Callable, Literal, Optional


class ResourceUtilityCalculator:
    """ResourceUtilityCalculator."""

    DEFAULT_TARGET_RANGES = {
        'cpu': {'min': 0.65, 'max': 0.80, 'hard': 0.90},
        'memory': {'min': 0.70, 'max': 0.80, 'hard': 0.90},
        'io': {'min': 0.50, 'max': 0.75, 'hard': 0.90},
    }

    DEFAULT_PERFORMANCE_IO_WEIGHTS = {
        'cpu': 1.0,
        'memory': 1.0,
        'io': 3.0,
    }


    SIGMOID_LINEAR_K = 5.0
    SIGMOID_LINEAR_M = -10.0
    SIGMOID_LINEAR_OVERLOAD_QUAD = -30.0
    SIGMOID_LINEAR_A = 1.0


    REWARD_FUNCTIONS = {
        'linear': lambda u: u,
        'log': lambda u: np.log(1 + u),
        'sigmoid': lambda u: 2 / (1 + np.exp(-5 * u)),
        'exponential': lambda u: np.exp(u) - 1,
        # sigmoid_linear reward: A * ((sigmoid(kx)-0.5)/(sigmoid(k)-0.5)) for 0<=x<=1
        'sigmoid_linear': lambda u: ResourceUtilityCalculator.SIGMOID_LINEAR_A * (
            ((1 / (1 + np.exp(-ResourceUtilityCalculator.SIGMOID_LINEAR_K * u))) - 0.5) /
            ((1 / (1 + np.exp(-ResourceUtilityCalculator.SIGMOID_LINEAR_K))) - 0.5)
        )
    }


    PENALTY_FUNCTIONS = {
        'linear': lambda u, max_reward: max_reward - (u - 1),
        'power': lambda u, max_reward: max_reward - (u - 1) ** 2,
        'log': lambda u, max_reward: max_reward - np.log(1 + (u - 1)),
        'sigmoid': lambda u, max_reward: max_reward * 2 / (1 + np.exp(5 * (u - 1))),
        'exponential': lambda u, max_reward: max_reward * np.exp(-(u - 1)),
        # sigmoid_linear penalty: stronger linear+quadratic overload punishment for x>=1
        'sigmoid_linear': lambda u, max_reward: (
            1.0
            + ResourceUtilityCalculator.SIGMOID_LINEAR_M * (u - 1)
            + ResourceUtilityCalculator.SIGMOID_LINEAR_OVERLOAD_QUAD * ((u - 1) ** 2)
        )
    }


    CONTINUOUS_FUNCTIONS = {
        'gaussian': lambda u: np.exp(-(u - 1) ** 2),
        'inverse': lambda u: 1 / (1 + abs(u - 1)),
        'tanh': lambda u: 1 - np.tanh(abs(u - 1)),
    }

    def __init__(
        self,
        reward_type: Literal['linear', 'log', 'sigmoid', 'exponential', 'sigmoid_linear'] = 'log',
        penalty_type: Literal['linear', 'power', 'log', 'sigmoid', 'exponential', 'sigmoid_linear'] = 'power',
        use_continuous: bool = False,
        continuous_type: Literal['gaussian', 'inverse', 'tanh'] = 'gaussian',
        overload_threshold: float = 0.8,
        score_mode: Literal['threshold', 'performance_io', 'band_target'] = 'threshold',
        resource_weights: Optional[Dict[str, float]] = None,
        resource_reward_weights: Optional[Dict[str, float]] = None,
        resource_penalty_weights: Optional[Dict[str, float]] = None,
        resource_target_ranges: Optional[Dict[str, Dict[str, Any]]] = None,
    ):
        """  init  ."""


        self.reward_type = reward_type
        self.penalty_type = penalty_type
        self.use_continuous = use_continuous
        self.continuous_type = continuous_type
        self.score_mode = str(score_mode or 'threshold')
        if self.score_mode not in {'threshold', 'performance_io', 'band_target'}:
            raise ValueError(f"score_mode: {score_mode}")
        default_weights = (
            self.DEFAULT_PERFORMANCE_IO_WEIGHTS
            if self.score_mode == 'performance_io'
            else {}
        )
        self.resource_weights: Dict[str, float] = {
            str(key).lower(): float(value)
            for key, value in default_weights.items()
        }
        if resource_weights:
            for key, value in resource_weights.items():
                self.resource_weights[str(key).lower()] = float(value)
        self.resource_reward_weights: Dict[str, float] = {}
        self.resource_penalty_weights: Dict[str, float] = {}
        if resource_reward_weights:
            for key, value in resource_reward_weights.items():
                self.resource_reward_weights[str(key).lower()] = float(value)
        if resource_penalty_weights:
            for key, value in resource_penalty_weights.items():
                self.resource_penalty_weights[str(key).lower()] = float(value)
        self.resource_target_ranges = self._normalize_target_ranges(resource_target_ranges)
        self.overload_threshold = float(overload_threshold)
        if self.overload_threshold <= 0:
            raise ValueError("overload_threshold0")

        if use_continuous:
            if continuous_type not in self.CONTINUOUS_FUNCTIONS:
                raise ValueError(f": {continuous_type}")
            self.score_func = self.CONTINUOUS_FUNCTIONS[continuous_type]
        else:
            if reward_type not in self.REWARD_FUNCTIONS:
                raise ValueError(f": {reward_type}")
            if penalty_type not in self.PENALTY_FUNCTIONS:
                raise ValueError(f": {penalty_type}")
            self.reward_func = self.REWARD_FUNCTIONS[reward_type]
            self.penalty_func = self.PENALTY_FUNCTIONS[penalty_type]

    @staticmethod
    def _is_io_resource(resource_name: Optional[str]) -> bool:
        if not resource_name:
            return False
        name = str(resource_name).lower()
        return name in {'io', 'disk', 'disk_io'} or name.startswith('io_')

    def _lookup_resource_weight(self, weights: Dict[str, float], resource_name: Optional[str]) -> float:
        if not resource_name:
            return 1.0
        name = str(resource_name).lower()
        if name in weights:
            return weights[name]
        if self._is_io_resource(name) and 'io' in weights:
            return weights['io']
        if name in {'mem', 'memory'} and 'memory' in weights:
            return weights['memory']
        return 1.0

    def _resource_weight(self, resource_name: Optional[str]) -> float:
        return self._lookup_resource_weight(self.resource_weights, resource_name)

    def _resource_reward_weight(self, resource_name: Optional[str]) -> float:
        if self.resource_reward_weights:
            return self._lookup_resource_weight(self.resource_reward_weights, resource_name)
        return self._resource_weight(resource_name)

    def _resource_penalty_weight(self, resource_name: Optional[str]) -> float:
        if self.resource_penalty_weights:
            return self._lookup_resource_weight(self.resource_penalty_weights, resource_name)
        return self._resource_weight(resource_name)

    @staticmethod
    def _normalize_util_value(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            result = float(value)
        except (TypeError, ValueError):
            return None
        if result > 1.0:
            result /= 100.0
        return max(0.0, result)

    def _normalize_resource_name(self, resource_name: Optional[str]) -> str:
        if not resource_name:
            return ''
        name = str(resource_name).lower()
        if self._is_io_resource(name):
            return 'io'
        if name in {'mem', 'memory'}:
            return 'memory'
        return name

    def _normalize_target_ranges(
        self,
        ranges: Optional[Dict[str, Dict[str, Any]]],
    ) -> Dict[str, Dict[str, float]]:
        normalized = {
            name: dict(values)
            for name, values in self.DEFAULT_TARGET_RANGES.items()
        }
        if not isinstance(ranges, dict):
            return normalized

        for raw_name, raw_values in ranges.items():
            if not isinstance(raw_values, dict):
                continue
            name = self._normalize_resource_name(raw_name)
            if not name:
                continue
            current = dict(normalized.get(name, {}))
            min_value = self._normalize_util_value(
                raw_values.get('target_min_pct', raw_values.get('min_pct', raw_values.get('min')))
            )
            max_value = self._normalize_util_value(
                raw_values.get('target_max_pct', raw_values.get('max_pct', raw_values.get('max')))
            )
            hard_value = self._normalize_util_value(
                raw_values.get(
                    'hard_overload_pct',
                    raw_values.get('overload_util_pct', raw_values.get('hard_pct', raw_values.get('hard'))),
                )
            )
            if min_value is not None:
                current['min'] = min_value
            if max_value is not None:
                current['max'] = max_value
            if hard_value is not None:
                current['hard'] = hard_value
            if current.get('max', 0.0) < current.get('min', 0.0):
                current['min'], current['max'] = current['max'], current['min']
            if current.get('hard', 0.0) < current.get('max', 0.0):
                current['hard'] = current['max']
            normalized[name] = current
        return normalized

    def _resource_target_range(self, resource_name: Optional[str]) -> Dict[str, float]:
        name = self._normalize_resource_name(resource_name)
        return dict(
            self.resource_target_ranges.get(
                name,
                {'min': self.overload_threshold, 'max': self.overload_threshold, 'hard': self.overload_threshold},
            )
        )

    def _band_target_score(
        self,
        utilization: float,
        resource_name: Optional[str] = None,
    ) -> float:
        util = max(0.0, float(utilization))
        target = self._resource_target_range(resource_name)
        target_min = max(float(target.get('min', self.overload_threshold)), 1e-9)
        target_max = max(float(target.get('max', target_min)), target_min)
        hard_max = max(float(target.get('hard', target_max)), target_max)
        weight = self._resource_weight(resource_name)

        if util < target_min:
            gap = (target_min - util) / target_min
            return float(weight * (1.0 - gap * gap))
        if util <= target_max:
            return float(weight)

        span = max(hard_max - target_max, 0.05)
        gap = (util - target_max) / span
        hard_excess = max(0.0, util - hard_max) / span
        penalty = gap * gap + 4.0 * hard_excess
        return float(weight * (1.0 - penalty))

    def calculate_resource_score(
        self,
        utilization: float,
        resource_name: Optional[str] = None,
    ) -> float:
        """calculate resource score."""


        if self.score_mode == 'performance_io':
            util = max(0.0, float(utilization))
            weight = self._resource_weight(resource_name)
            if self._is_io_resource(resource_name):
                return float(-weight * util)
            return float(weight * util)

        if self.score_mode == 'band_target':
            return self._band_target_score(utilization, resource_name=resource_name)

        effective_utilization = float(utilization) / self.overload_threshold

        if self.use_continuous:

            reward_weight = self._resource_reward_weight(resource_name)
            penalty_weight = self._resource_penalty_weight(resource_name)
            weight = reward_weight if effective_utilization <= 1.0 else penalty_weight
            return float(weight * self.score_func(effective_utilization))
        else:

            if effective_utilization <= 1.0:

                weight = self._resource_reward_weight(resource_name)
                return float(weight * self.reward_func(effective_utilization))
            else:

                weight = self._resource_penalty_weight(resource_name)
                max_reward = self.reward_func(1.0)
                return float(weight * self.penalty_func(effective_utilization, max_reward))

    def resource_score_derivative(
        self,
        utilization: float,
        resource_name: Optional[str] = None,
    ) -> float:
        """Return the analytic derivative ``d score / d utilization``.

        The formal experience-table SGD composes this derivative with the
        frozen sensitivity table.  It must never probe the estimator or the
        score function at perturbed points.
        """
        util = max(0.0, float(utilization))
        if not math.isfinite(util):
            raise ValueError("utilization must be finite")

        if self.score_mode == 'performance_io':
            weight = self._resource_weight(resource_name)
            return float(-weight if self._is_io_resource(resource_name) else weight)

        if self.score_mode == 'band_target':
            target = self._resource_target_range(resource_name)
            target_min = max(float(target.get('min', self.overload_threshold)), 1e-9)
            target_max = max(float(target.get('max', target_min)), target_min)
            hard_max = max(float(target.get('hard', target_max)), target_max)
            weight = self._resource_weight(resource_name)
            if util < target_min:
                return float(weight * 2.0 * (target_min - util) / (target_min ** 2))
            if util <= target_max:
                return 0.0
            span = max(hard_max - target_max, 0.05)
            derivative = -2.0 * (util - target_max) / (span ** 2)
            if util > hard_max:
                derivative -= 4.0 / span
            return float(weight * derivative)

        x = util / self.overload_threshold
        if self.use_continuous:
            if x == 1.0:
                base_derivative = 0.0
            elif self.continuous_type == 'gaussian':
                base_derivative = -2.0 * (x - 1.0) * math.exp(-((x - 1.0) ** 2))
            elif self.continuous_type == 'inverse':
                sign = 1.0 if x < 1.0 else -1.0
                base_derivative = sign / ((1.0 + abs(x - 1.0)) ** 2)
            elif self.continuous_type == 'tanh':
                sign = 1.0 if x < 1.0 else -1.0
                base_derivative = sign / (math.cosh(abs(x - 1.0)) ** 2)
            else:  # constructor validation makes this unreachable
                raise ValueError(f"unsupported continuous_type: {self.continuous_type}")
            weight = (
                self._resource_reward_weight(resource_name)
                if x <= 1.0 else self._resource_penalty_weight(resource_name)
            )
            return float(weight * base_derivative / self.overload_threshold)

        if x <= 1.0:
            if self.reward_type == 'linear':
                derivative = 1.0
            elif self.reward_type == 'log':
                derivative = 1.0 / (1.0 + x)
            elif self.reward_type == 'sigmoid':
                exp_term = math.exp(-5.0 * x)
                derivative = 10.0 * exp_term / ((1.0 + exp_term) ** 2)
            elif self.reward_type == 'exponential':
                derivative = math.exp(x)
            elif self.reward_type == 'sigmoid_linear':
                exp_term = math.exp(-self.SIGMOID_LINEAR_K * x)
                denominator = 1.0 / (1.0 + math.exp(-self.SIGMOID_LINEAR_K)) - 0.5
                derivative = (
                    self.SIGMOID_LINEAR_A * self.SIGMOID_LINEAR_K * exp_term
                    / (((1.0 + exp_term) ** 2) * denominator)
                )
            else:
                raise ValueError(f"unsupported reward_type: {self.reward_type}")
            weight = self._resource_reward_weight(resource_name)
        else:
            delta = x - 1.0
            max_reward = float(self.reward_func(1.0))
            if self.penalty_type == 'linear':
                derivative = -1.0
            elif self.penalty_type == 'power':
                derivative = -2.0 * delta
            elif self.penalty_type == 'log':
                derivative = -1.0 / (1.0 + delta)
            elif self.penalty_type == 'sigmoid':
                exp_term = math.exp(5.0 * delta)
                derivative = -10.0 * max_reward * exp_term / ((1.0 + exp_term) ** 2)
            elif self.penalty_type == 'exponential':
                derivative = -max_reward * math.exp(-delta)
            elif self.penalty_type == 'sigmoid_linear':
                derivative = self.SIGMOID_LINEAR_M + 2.0 * self.SIGMOID_LINEAR_OVERLOAD_QUAD * delta
            else:
                raise ValueError(f"unsupported penalty_type: {self.penalty_type}")
            weight = self._resource_penalty_weight(resource_name)
        return float(weight * derivative / self.overload_threshold)

    def calculate_utility_score(
        self,
        resource: Dict[str, float],
        capability: Dict[str, float]
    ) -> float:
        """calculate utility score."""


        total_score: float = 0.0

        for resource_name, cap_value in capability.items():
            if resource_name not in resource:
                continue

            if cap_value == 0:
                raise ValueError(f" '{resource_name}' capability0")


            utilization = resource[resource_name] / cap_value


            total_score += self.calculate_resource_score(
                utilization,
                resource_name=resource_name,
            )

        return float(total_score)

    def certified_resource_score_upper(
        self,
        resource_name: str,
        utilization_lower: float,
        utilization_upper: float,
    ) -> Dict[str, Any]:
        """Return a certified one-resource maximum/supremum on a closed interval.

        The scoring implementation is separable.  Its supported modes are
        piecewise monotone with extrema at interval endpoints or documented
        breakpoints.  A threshold interval crossing unequal reward/penalty
        weights is discontinuous and has no generally attained maximum under
        the implementation's branch convention, so certification fails open.
        """
        lower = float(utilization_lower)
        upper = float(utilization_upper)
        if not all(math.isfinite(value) for value in (lower, upper)):
            raise ValueError("certified Utility interval must be finite")
        if lower < 0.0 or upper < lower:
            raise ValueError("certified Utility interval must satisfy 0 <= lower <= upper")

        candidates: list[tuple[str, float, float]] = []

        def add(label: str, utilization: float) -> None:
            if lower <= utilization <= upper:
                score = float(self.calculate_resource_score(utilization, resource_name))
                if not math.isfinite(score):
                    raise ValueError("Utility produced a non-finite score")
                candidates.append((label, utilization, score))

        add("interval_lower", lower)
        add("interval_upper", upper)
        proof = "piecewise_monotone_endpoints"
        if self.score_mode == "band_target":
            target = self._resource_target_range(resource_name)
            add("target_min", float(target["min"]))
            add("target_max", float(target["max"]))
            add("hard_overload", float(target["hard"]))
            proof = "band_target_piecewise_quadratic_breakpoints_and_endpoints"
        elif self.score_mode == "threshold":
            threshold = self.overload_threshold
            if lower <= threshold < upper:
                reward_weight = self._resource_reward_weight(resource_name)
                penalty_weight = self._resource_penalty_weight(resource_name)
                if reward_weight != penalty_weight:
                    raise ValueError(
                        "unequal threshold weights make the true maximum uncertifiable"
                    )
            add("threshold", threshold)
            proof = "threshold_piecewise_monotone_equal_weight_breakpoint_and_endpoints"
        elif self.score_mode == "performance_io":
            proof = "performance_io_linear_endpoints"
        else:  # constructor currently prevents this; retain fail-closed proof.
            raise ValueError(f"unsupported certified score mode: {self.score_mode}")
        if not candidates:
            raise ValueError("certified Utility found no candidates")
        label, argmax, score_upper = max(candidates, key=lambda item: item[2])
        return {
            "score_upper": score_upper,
            "argmax_utilization": argmax,
            "argmax_kind": label,
            "proof": proof,
            "evaluated_candidates": [
                {"kind": item[0], "utilization": item[1], "score": item[2]}
                for item in candidates
            ],
        }

    def is_improved(
        self,
        resource_old: Dict[str, float],
        resource_new: Dict[str, float],
        capability: Dict[str, float]
    ) -> bool:
        """is improved."""


        score_old = self.calculate_utility_score(resource_old, capability)
        score_new = self.calculate_utility_score(resource_new, capability)

        return bool(score_new > score_old)

    def get_detailed_scores(
        self,
        resource: Dict[str, float],
        capability: Dict[str, float]
    ) -> Dict[str, Dict]:
        """get detailed scores."""


        details = {}
        total_score = 0.0

        for resource_name, cap_value in capability.items():
            if resource_name not in resource:
                continue
            if cap_value == 0:
                raise ValueError(f" '{resource_name}' capability0")

            utilization = resource[resource_name] / cap_value
            score = self.calculate_resource_score(
                utilization,
                resource_name=resource_name,
            )
            if self.score_mode == 'performance_io':
                status = (
                    'io_lower_better'
                    if self._is_io_resource(resource_name)
                    else 'higher_better'
                )
                effective_utilization = utilization
            elif self.score_mode == 'band_target':
                target_range = self._resource_target_range(resource_name)
                status = (
                    'under_target'
                    if utilization < target_range['min']
                    else 'in_target'
                    if utilization <= target_range['max']
                    else 'over_target'
                )
                effective_utilization = utilization
            else:
                status = 'normal' if utilization <= self.overload_threshold else 'overload'
                effective_utilization = utilization / self.overload_threshold

            details[resource_name] = {
                'usage': resource[resource_name],
                'capability': cap_value,
                'utilization': utilization,
                'effective_utilization': effective_utilization,
                'overload_threshold': self.overload_threshold,
                'score_mode': self.score_mode,
                'target_range': self._resource_target_range(resource_name),
                'weight': self._resource_weight(resource_name),
                'reward_weight': self._resource_reward_weight(resource_name),
                'penalty_weight': self._resource_penalty_weight(resource_name),
                'score': score,
                'status': status,
            }
            total_score += score

        details['total_score'] = total_score
        return details


def is_improved(
    resource_old: Dict[str, float],
    resource_new: Dict[str, float],
    capability: Dict[str, float],
    reward_type: str = 'log',
    penalty_type: str = 'power',
    use_continuous: bool = False,
    continuous_type: str = 'gaussian',
    overload_threshold: float = 0.8,
    score_mode: str = 'threshold',
    resource_weights: Optional[Dict[str, float]] = None,
    resource_reward_weights: Optional[Dict[str, float]] = None,
    resource_penalty_weights: Optional[Dict[str, float]] = None,
    resource_target_ranges: Optional[Dict[str, Dict[str, Any]]] = None,
) -> bool:
    """is improved."""


    calculator = ResourceUtilityCalculator(
        reward_type=reward_type,
        penalty_type=penalty_type,
        use_continuous=use_continuous,
        continuous_type=continuous_type,
        overload_threshold=overload_threshold,
        score_mode=score_mode,
        resource_weights=resource_weights,
        resource_reward_weights=resource_reward_weights,
        resource_penalty_weights=resource_penalty_weights,
        resource_target_ranges=resource_target_ranges,
    )
    return calculator.is_improved(resource_old, resource_new, capability)


if __name__ == "__main__":

    print("=" * 60)
    print(" - ")
    print("=" * 60)


    capability = {
        'cpu': 4.0,
        'memory': 8.0,
        'disk': 100.0
    }


    resource_old = {
        'cpu': 1.0,
        'memory': 2.0,
        'disk': 20.0
    }


    resource_new = {
        'cpu': 3.5,
        'memory': 7.0,
        'disk': 80.0
    }


    resource_overload = {
        'cpu': 5.0,
        'memory': 10.0,
        'disk': 120.0
    }


    configs = [
        ('log', 'power', False, None),
        ('linear', 'linear', False, None),
        ('sigmoid', 'exponential', False, None),
        (None, None, True, 'gaussian'),
    ]

    for config in configs:
        reward, penalty, continuous, cont_type = config

        if continuous:
            print(f"\n: {cont_type}")
            calculator = ResourceUtilityCalculator(
                use_continuous=True,
                continuous_type=cont_type
            )
        else:
            print(f"\n - : {reward}, : {penalty}")
            calculator = ResourceUtilityCalculator(
                reward_type=reward,
                penalty_type=penalty
            )


        score_old = calculator.calculate_utility_score(resource_old, capability)
        score_new = calculator.calculate_utility_score(resource_new, capability)
        score_overload = calculator.calculate_utility_score(resource_overload, capability)

        print(f"  Low utilization: {score_old:.4f}")
        print(f"  High utilization: {score_new:.4f}")
        print(f"  : {score_overload:.4f}")
        print(f"  High utilization: {score_new > score_old}")
        print(f"  High utilization: {score_overload > score_new}")


    print("\n" + "=" * 60)
    print(" log + power")
    print("=" * 60)

    calculator = ResourceUtilityCalculator(reward_type='log', penalty_type='power')

    for name, res in [('Low utilization', resource_old),
                      ('High utilization', resource_new),
                      ('', resource_overload)]:
        print(f"\n{name}:")
        details = calculator.get_detailed_scores(res, capability)
        for resource_name in ['cpu', 'memory', 'disk']:
            info = details[resource_name]
            print(f"  {resource_name}: "
                  f"={info['usage']:.1f}, "
                  f"={info['capability']:.1f}, "
                  f"={info['utilization']:.2%}, "
                  f"={info['score']:.4f}, "
                  f"={info['status']}")
        print(f"  : {details['total_score']:.4f}")

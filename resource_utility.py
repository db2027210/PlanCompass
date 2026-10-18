"""Module."""


import math

import numpy as np
from typing import Any, Dict, Callable, Literal, Mapping, Optional, Tuple


class ResourceUtilityCalculator:
    """ResourceUtilityCalculator."""

    BAND_TARGET_CONTRACT = "directional_band_target_v1"
    BAND_DIRECTIONAL_SPAN = 0.2

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
        self.resource_target_ranges = self._normalize_target_ranges(
            resource_target_ranges
        )
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
    ) -> Dict[str, Dict[str, Any]]:
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
            role = str(raw_values.get('role', '')).strip().lower()
            if role:
                if role not in {
                    'target_bottleneck',
                    'non_bottleneck',
                    'non_bottleneck_io_cost',
                }:
                    raise ValueError(
                        f" {name!r}  band_target role : {role!r}"
                    )
                current['role'] = role
            if current.get('max', 0.0) < current.get('min', 0.0):
                current['min'], current['max'] = current['max'], current['min']
            if current.get('hard', 0.0) < current.get('max', 0.0):
                current['hard'] = current['max']
            normalized[name] = current
        return normalized

    def _resource_target_range(self, resource_name: Optional[str]) -> Dict[str, Any]:
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
        role = str(target.get('role', '')).strip().lower()
        weight = self._resource_weight(resource_name)
        band_width = max(target_max - target_min, 1e-9)
        directional_span = self.BAND_DIRECTIONAL_SPAN

        if role == 'target_bottleneck':
            if util < target_min:
                gap = (target_min - util) / target_min
                # Outer admission requires the bottleneck resource to decrease.
                # Keep that direction below the nominal target band as well:
                # a lower utilization must never receive a lower inner score.
                # The previous quadratic expression reversed the derivative
                # below ``target_min`` and made Algorithm C rank target-up
                # candidates ahead of target-down candidates.
                return float(weight * (1.0 + directional_span * gap))
            if util <= target_max:
                position = (util - target_min) / band_width
                return float(weight * (1.0 - directional_span * position))

            span = max(hard_max - target_max, 0.05)
            gap = (util - target_max) / span
            hard_excess = max(0.0, util - hard_max) / span
            penalty = gap * gap + 4.0 * hard_excess
            return float(
                weight * (1.0 - directional_span - penalty)
            )

        if role == 'non_bottleneck':
            lower_band_score = 1.0 - directional_span
            if util < target_min:
                gap = (target_min - util) / target_min
                return float(
                    weight * lower_band_score * (1.0 - gap * gap)
                )
            if util <= target_max:
                position = (util - target_min) / band_width
                return float(
                    weight * (lower_band_score + directional_span * position)
                )

            span = max(hard_max - target_max, 0.05)
            gap = (util - target_max) / span
            hard_excess = max(0.0, util - hard_max) / span
            penalty = gap * gap + 4.0 * hard_excess
            return float(weight * (1.0 - penalty))

        if role == 'non_bottleneck_io_cost':
            # IO is the only non-target resource that has a direct external
            # cost (device pressure and interference). Keep a weak,
            # monotonic cost instead of rewarding IO toward the generic
            # 30--50% utilization band. The target=io path still uses the
            # stronger target_bottleneck contract above.
            hard_excess = max(0.0, util - hard_max) / max(hard_max, 0.05)
            return float(weight * (1.0 - util - 4.0 * hard_excess))

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

    def directional_band_target_contract_audit(
        self,
        *,
        target_resource: str,
    ) -> Dict[str, Any]:
        """Fail closed unless this calculator implements the formal direction.

        The target bottleneck must prefer the 85% side of its band, CPU/memory
        non-targets prefer the 50% side, and non-target IO prefers the lower
        side. Checking the loaded
        implementation catches stale flat-band modules before they can corrupt
        ``dScore/dU`` and the full gradient trajectory.
        """

        target = self._normalize_resource_name(target_resource)
        if self.score_mode != 'band_target':
            raise ValueError(
                "directional band-target contract requires score_mode=band_target"
            )
        if target not in {'cpu', 'memory', 'io'}:
            raise ValueError(
                "directional band-target contract requires one target resource"
            )
        if not math.isclose(
            float(self.BAND_DIRECTIONAL_SPAN),
            0.2,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ValueError(
                "directional band-target span must be exactly 0.2"
            )
        checks: Dict[str, Dict[str, Any]] = {}
        for resource_name in ('cpu', 'memory', 'io'):
            resource_range = self._resource_target_range(resource_name)
            expected_role = (
                'target_bottleneck'
                if resource_name == target
                else (
                    'non_bottleneck_io_cost'
                    if resource_name == 'io'
                    else 'non_bottleneck'
                )
            )
            role = str(resource_range.get('role') or '').strip().lower()
            if role != expected_role:
                raise ValueError(
                    "directional band-target role mismatch for "
                    f"{resource_name}: {role!r} != {expected_role!r}"
                )
            expected_band = (
                (0.85, 0.90, 0.95)
                if expected_role == 'target_bottleneck'
                else (0.30, 0.50, 0.90)
            )
            observed_band = (
                float(resource_range.get('min', math.nan)),
                float(resource_range.get('max', math.nan)),
                float(resource_range.get('hard', math.nan)),
            )
            if any(
                not math.isclose(
                    observed,
                    expected,
                    rel_tol=0.0,
                    abs_tol=1e-12,
                )
                for observed, expected in zip(observed_band, expected_band)
            ):
                raise ValueError(
                    "directional band-target range mismatch for "
                    f"{resource_name}: {observed_band} != {expected_band}"
                )
            weight = float(self._resource_weight(resource_name))
            expected_weight = (
                3.0
                if resource_name == 'io' and target == 'io'
                else 0.5 if resource_name == 'io' else 1.0
            )
            if not math.isclose(
                weight,
                expected_weight,
                rel_tol=0.0,
                abs_tol=1e-12,
            ):
                raise ValueError(
                    "directional band-target weight mismatch for "
                    f"{resource_name}: {weight}"
                )
            band_min = float(resource_range['min'])
            band_max = float(resource_range['max'])
            score_at_min = float(
                self.calculate_resource_score(
                    band_min,
                    resource_name=resource_name,
                )
            )
            score_at_max = float(
                self.calculate_resource_score(
                    band_max,
                    resource_name=resource_name,
                )
            )
            direction_valid = (
                score_at_min > score_at_max
                if expected_role in {
                    'target_bottleneck',
                    'non_bottleneck_io_cost',
                }
                else score_at_max > score_at_min
            )
            if not direction_valid:
                raise ValueError(
                    "directional band-target endpoint ordering failed for "
                    f"{resource_name}: min={score_at_min}, max={score_at_max}"
                )
            below_min = 0.9 * band_min
            score_below_min = float(
                self.calculate_resource_score(
                    below_min,
                    resource_name=resource_name,
                )
            )
            if (
                expected_role == 'target_bottleneck'
                and not score_below_min > score_at_min
            ):
                raise ValueError(
                    "directional band-target below-band ordering failed for "
                    f"{resource_name}: below_min={score_below_min}, "
                    f"min={score_at_min}"
                )
            checks[resource_name] = {
                'role': role,
                'weight': weight,
                'band_min': band_min,
                'band_max': band_max,
                'hard_overload': float(resource_range['hard']),
                'directional_span': float(self.BAND_DIRECTIONAL_SPAN),
                'score_at_min': score_at_min,
                'score_at_max': score_at_max,
                'score_below_min': score_below_min,
                'preferred_endpoint': (
                    'min'
                    if expected_role in {
                        'target_bottleneck',
                        'non_bottleneck_io_cost',
                    }
                    else 'max'
                ),
                'passed': True,
            }
        return {
            'contract_id': self.BAND_TARGET_CONTRACT,
            'target_resource': target,
            'checks': checks,
            'passed': True,
        }

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

    def maximize_utility_over_utilization_box(
        self,
        utilization_intervals: Mapping[str, Tuple[float, float]],
    ) -> Tuple[float, Dict[str, Dict[str, Any]]]:
        """maximize utility over utilization box."""


        total_upper = 0.0
        details: Dict[str, Dict[str, Any]] = {}
        for raw_name, raw_interval in utilization_intervals.items():
            if not isinstance(raw_interval, (tuple, list)) or len(raw_interval) != 2:
                raise ValueError(f" {raw_name!r} ")
            lower = float(raw_interval[0])
            upper = float(raw_interval[1])
            if not math.isfinite(lower) or not math.isfinite(upper):
                raise ValueError(f" {raw_name!r} ")
            lower = max(0.0, lower)
            upper = max(0.0, upper)
            if upper < lower:
                lower, upper = upper, lower

            target = self._resource_target_range(raw_name)
            breakpoints = {
                lower,
                upper,
                self.overload_threshold,
                float(target.get('min', self.overload_threshold)),
                float(target.get('max', self.overload_threshold)),
                float(target.get('hard', self.overload_threshold)),
            }
            candidates = sorted(
                point for point in breakpoints if lower <= point <= upper
            )
            scored = [
                (point, self.calculate_resource_score(point, resource_name=raw_name))
                for point in candidates
            ]
            maximizing_utilization, resource_upper = max(
                scored,
                key=lambda item: item[1],
            )
            total_upper += float(resource_upper)
            details[str(raw_name)] = {
                'utilization_lower': lower,
                'utilization_upper': upper,
                'checked_breakpoints': candidates,
                'maximizing_utilization': maximizing_utilization,
                'resource_score_upper': float(resource_upper),
            }
        return float(total_upper), details

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

"""Module."""


from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Set, Tuple
import copy
import itertools
import math
import time

from resource_utility import ResourceUtilityCalculator
from logger_utils import OptimizationLogger


@dataclass
class ParameterRange:
    """ParameterRange."""

    min_value: float
    max_value: float


@dataclass
class OptimizationResult:
    """OptimizationResult."""

    optimized_params: Dict[str, float]
    initial_params: Dict[str, float]
    initial_resource: Dict[str, float]
    final_resource: Dict[str, float]
    initial_score: float
    final_score: float
    score_improvement: float
    score_improvement_pct: Optional[float]
    initial_utilization: Dict[str, float]
    final_utilization: Dict[str, float]
    initial_qps: Optional[float]
    final_qps: Optional[float]
    qps_improvement: Optional[float]
    qps_improvement_pct: Optional[float]
    initial_query_count: Optional[float]
    final_query_count: Optional[float]
    initial_query_time: Optional[float]
    final_query_time: Optional[float]
    degraded: Optional[bool]
    degradation_pct: Optional[float]
    iterations: int
    history: List[Dict[str, Any]]
    initial_estimation_metadata: Optional[Dict[str, Any]] = None
    final_estimation_metadata: Optional[Dict[str, Any]] = None
    candidate_trajectory: List[Dict[str, Any]] = field(default_factory=list)
    candidate_trajectory_complete: bool = False


class GradientDescentParameterOptimizer:
    """GradientDescentParameterOptimizer."""

    def __init__(
        self,
        resource_estimation_func: Callable[[Any, Dict[str, float]], Dict[str, float]],
        capability: Dict[str, float],
        reward_type: str = "log",
        penalty_type: str = "power",
        use_continuous: bool = False,
        continuous_type: str = "gaussian",
        overload_threshold: float = 0.8,
        score_mode: str = "threshold",
        resource_weights: Optional[Dict[str, float]] = None,
        resource_reward_weights: Optional[Dict[str, float]] = None,
        resource_penalty_weights: Optional[Dict[str, float]] = None,
        resource_target_ranges: Optional[Dict[str, Dict[str, Any]]] = None,
        verbose: bool = True,
    ) -> None:
        self.resource_estimation_func = resource_estimation_func
        self.capability = capability
        self.verbose = verbose
        self.utility_calculator = ResourceUtilityCalculator(
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
        self.history: List[Dict[str, Any]] = []

    @staticmethod
    def _clip(value: float, param_range: ParameterRange) -> float:
        """ clip."""
        return max(param_range.min_value, min(param_range.max_value, value))

    def _is_improved(
        self, resource_old: Dict[str, float], resource_new: Dict[str, float]
    ) -> bool:
        """ is improved."""
        return self.utility_calculator.is_improved(
            resource_old, resource_new, self.capability
        )

    def _calculate_direction(
        self,
        param_name: str,
        sensitivity_table: Dict[str, Dict[str, float]],
        resource_now: Dict[str, float],
        capability: Dict[str, float],
        *,
        audit_sink: Optional[Dict[str, Any]] = None,
        original_gradient_only: bool = False,
        historical_bottleneck_resources: Optional[Set[str]] = None,
        target_bottleneck_resources: Optional[Set[str]] = None,
        target_bottleneck_utilization_override: Optional[Dict[str, float]] = None,
        active_bottleneck_underuse_scale: float = 1.0,
        post_bottleneck_target_util: float = 0.9,
        post_bottleneck_non_bottleneck_pressure_scale: float = 2.0,
    ) -> Tuple[float, Optional[str]]:
        """ calculate direction."""


        if param_name not in sensitivity_table:
            if audit_sink is not None:
                audit_sink.clear()
                audit_sink.update(
                    {
                        "param": param_name,
                        "confidence": None,
                        "resources": {},
                        "g_j": 0.0,
                        "gradient_source": "missing_experience_table_entry",
                    }
                )
            return 0.0, None

        total_gradient = 0.0
        resource_sensitivity = sensitivity_table[param_name]
        confidence = float(resource_sensitivity.get("confidence", 1.0) or 0.0)
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise ValueError(
                f" {param_name!r}.confidence  [0,1] "
            )


        details = []
        resource_audit: Dict[str, Dict[str, Any]] = {}

        def utility_slope(resource_name: str, utilization: float) -> float:
            """Numerically estimate d(score)/d(utilization) for the shared utility function."""
            eps = 1e-4
            util = float(utilization)
            if self.utility_calculator.score_mode in {"performance_io", "band_target"}:
                lower = max(0.0, util - eps)
                upper = util + eps
                if upper <= lower:
                    return 0.0
                lower_score = self.utility_calculator.calculate_resource_score(
                    lower,
                    resource_name=resource_name,
                )
                upper_score = self.utility_calculator.calculate_resource_score(
                    upper,
                    resource_name=resource_name,
                )
                return float((upper_score - lower_score) / (upper - lower))

            threshold = float(self.utility_calculator.overload_threshold)
            if abs(util - threshold) <= eps:
                return 0.0
            if util < threshold:
                lower = max(0.0, util - eps)
                upper = min(threshold, util + eps)
            else:
                lower = max(threshold, util - eps)
                upper = util + eps
            if upper <= lower:
                return 0.0
            lower_score = self.utility_calculator.calculate_resource_score(
                lower,
                resource_name=resource_name,
            )
            upper_score = self.utility_calculator.calculate_resource_score(
                upper,
                resource_name=resource_name,
            )
            return float((upper_score - lower_score) / (upper - lower))

        threshold = float(self.utility_calculator.overload_threshold)
        historical_bottlenecks = set(historical_bottleneck_resources or set())
        target_bottlenecks = set(target_bottleneck_resources or set())
        target_util_override = {
            str(name): float(value)
            for name, value in (target_bottleneck_utilization_override or {}).items()
            if str(name).strip()
        }
        current_utilization: Dict[str, float] = {}
        for resource_name in resource_sensitivity:
            if resource_name not in resource_now or resource_name not in capability:
                continue
            cap_val = capability[resource_name]
            if cap_val == 0:
                continue
            current_utilization[resource_name] = resource_now[resource_name] / cap_val
        for resource_name, util in target_util_override.items():
            if not original_gradient_only and resource_name in target_bottlenecks:
                current_utilization[resource_name] = util
        active_bottlenecks = {
            name for name, util in current_utilization.items() if util > threshold
        }
        active_target_bottleneck = False
        if self.utility_calculator.score_mode == "band_target" and target_bottlenecks:
            for name in target_bottlenecks:
                if name not in current_utilization:
                    continue
                target_range = self.utility_calculator._resource_target_range(name)
                target_min = float(target_range.get("min", 0.0) or 0.0)
                target_max = float(target_range.get("max", threshold) or threshold)
                util = float(current_utilization.get(name, 0.0) or 0.0)
                if util < target_min or util > target_max:
                    active_target_bottleneck = True
                    break
        use_post_bottleneck_target = bool(historical_bottlenecks) and not active_bottlenecks

        for resource_name, sensitivity in resource_sensitivity.items():
            if resource_name not in resource_now or resource_name not in capability:
                continue

            cap_val = capability[resource_name]
            if cap_val == 0:
                continue

            util = current_utilization[resource_name]
            pressure_scale = 1.0
            raw_score_slope = utility_slope(resource_name, util)
            if original_gradient_only:
                pressure = raw_score_slope
                phase = "original_gradient"
            elif self.utility_calculator.score_mode in {"performance_io", "band_target"}:
                pressure = raw_score_slope
                if (
                    self.utility_calculator.score_mode == "band_target"
                    and active_target_bottleneck
                    and target_bottlenecks
                    and resource_name not in target_bottlenecks
                ):
                    pressure_scale = max(0.0, float(active_bottleneck_underuse_scale))
                phase = self.utility_calculator.score_mode
            elif active_bottlenecks:
                pressure = raw_score_slope
                if resource_name not in active_bottlenecks and pressure > 0.0:
                    pressure_scale = max(0.0, float(active_bottleneck_underuse_scale))
                phase = "active_bottleneck"
            elif use_post_bottleneck_target:
                if resource_name in historical_bottlenecks:
                    # Once the bottleneck is below threshold, keep it as a
                    # constraint instead of pushing it toward the 0.9 target.
                    pressure = raw_score_slope if util > threshold else 0.0
                    phase = "post_bottleneck_hold"
                else:
                    pressure = float(post_bottleneck_target_util) - float(util)
                    pressure_scale = max(0.0, float(post_bottleneck_non_bottleneck_pressure_scale))
                    phase = "post_bottleneck_target"
            else:
                pressure = raw_score_slope
                phase = "utility"
            pressure *= pressure_scale
            grad_component = pressure * sensitivity * confidence
            total_gradient += grad_component

            resource_audit[resource_name] = {
                "predicted_resource": float(resource_now[resource_name]),
                "capability": float(cap_val),
                "utilization": float(util),
                "dScore/dU": float(raw_score_slope),
                "effective_dScore/dU": float(pressure),
                "T": float(sensitivity),
                "confidence": float(confidence),
                "g_component": float(grad_component),
                "phase": phase,
                "pressure_scale": float(pressure_scale),
            }

            details.append(
                f"{resource_name}(U={util:.3f}*P={pressure:.3f}*S={sensitivity:.2f}"
                f"*C={confidence:.2f}"
                f"*phase={phase}*scale={pressure_scale:.2f}={grad_component:.4f})"
            )

        log_msg = None
        if details:
            log_msg = f"[Gradient] {param_name:<25} | Total: {total_gradient:+.4f} | Components: {', '.join(details)}"

        if audit_sink is not None:
            audit_sink.clear()
            audit_sink.update(
                {
                    "param": param_name,
                    "confidence": float(confidence),
                    "resources": resource_audit,
                    "g_j": float(total_gradient),
                    "gradient_source": (
                        "original_dScore_dU_times_T_times_confidence"
                        if original_gradient_only
                        else "experience_table_T_with_phase_pressure"
                    ),
                }
            )

        return total_gradient, log_msg

    def optimize(
        self,
        p_real: Dict[str, float],
        sensitivity_table: Dict[str, Dict[str, float]],
        param_ranges: Dict[str, ParameterRange],
        sql_set: Any,
        p_start: Optional[Dict[str, float]] = None,
        eta: float = 0.1,
        max_iter: int = 20,
        patience: int = 3,
        gamma: float = 0.5,
        active_params: Optional[List[str]] = None,
        identify_sys_func: Optional[Callable[[], Dict[str, float]]] = None,
        log_path: Optional[str] = None,
        **compat_options: Any,
    ) -> OptimizationResult:
        """optimize."""


        if not math.isfinite(eta) or eta <= 0.0:
            raise ValueError("eta ")
        if max_iter <= 0:
            raise ValueError("max_iter ")
        if patience <= 0:
            raise ValueError("patience  0")
        if not math.isfinite(gamma) or not 0.0 < gamma < 1.0:
            raise ValueError("gamma  (0, 1)")

        min_eta = float(compat_options.get("min_eta", 1e-4))
        if not math.isfinite(min_eta) or min_eta < 0.0:
            raise ValueError("min_eta ")
        plateau_exploration_enabled = bool(
            compat_options.get("plateau_exploration_enabled", False)
        )
        original_gradient_only = bool(
            compat_options.get("original_gradient_only", True)
        )
        parameter_boundary_corner_scan_enabled = compat_options.get(
            "parameter_boundary_corner_scan_enabled",
            False,
        )
        if not isinstance(parameter_boundary_corner_scan_enabled, bool):
            raise ValueError(
                "parameter_boundary_corner_scan_enabled "
            )
        directional_scoring_contract_required = compat_options.get(
            "directional_scoring_contract_required",
            False,
        )
        if not isinstance(directional_scoring_contract_required, bool):
            raise ValueError(
                "directional_scoring_contract_required "
            )
        plateau_score_epsilon = float(
            compat_options.get("plateau_score_epsilon", 1e-12)
        )
        if not math.isfinite(plateau_score_epsilon) or plateau_score_epsilon < 0.0:
            raise ValueError("plateau_score_epsilon ")

        boundary_aware_eta_enabled = bool(
            compat_options.get("boundary_aware_eta_enabled", False)
        )
        min_param_delta_rel_pct = float(
            compat_options.get("min_param_delta_rel_pct", 0.0)
        )
        max_param_delta_rel_pct = float(
            compat_options.get("max_param_delta_rel_pct", 100.0)
        )
        boundary_eta_growth_factor = float(
            compat_options.get("boundary_eta_growth_factor", 2.0)
        )
        raw_relative_step_probes = compat_options.get("relative_step_probe_pct")
        relative_step_probe_pct: Optional[List[float]] = None
        if raw_relative_step_probes is not None:
            if not isinstance(raw_relative_step_probes, (list, tuple)):
                raise ValueError("relative_step_probe_pct ")
            relative_step_probe_pct = []
            for raw_probe in raw_relative_step_probes:
                probe = float(raw_probe)
                if not math.isfinite(probe) or probe <= 0.0:
                    raise ValueError("relative_step_probe_pct ")
                relative_step_probe_pct.append(probe)
            if not relative_step_probe_pct:
                raise ValueError("relative_step_probe_pct ")
            if any(
                right <= left
                for left, right in zip(
                    relative_step_probe_pct,
                    relative_step_probe_pct[1:],
                )
            ):
                raise ValueError("relative_step_probe_pct ")
            max_iter = len(relative_step_probe_pct)
        if (
            not math.isfinite(min_param_delta_rel_pct)
            or min_param_delta_rel_pct < 0.0
        ):
            raise ValueError("min_param_delta_rel_pct must be finite and nonnegative")
        if (
            not math.isfinite(max_param_delta_rel_pct)
            or max_param_delta_rel_pct <= 0.0
            or max_param_delta_rel_pct < min_param_delta_rel_pct
        ):
            raise ValueError(
                "max_param_delta_rel_pct must be finite, positive, and >= minimum"
            )
        if (
            not math.isfinite(boundary_eta_growth_factor)
            or boundary_eta_growth_factor <= 1.0
        ):
            raise ValueError("boundary_eta_growth_factor must be finite and > 1")

        raw_direction_override = compat_options.get("direction_override_log_vector")
        if raw_direction_override is None:
            direction_override_log_vector: Dict[str, float] = {}
        elif not isinstance(raw_direction_override, Mapping):
            raise ValueError("direction_override_log_vector ")
        else:
            direction_override_log_vector = {}
            for raw_name, raw_value in raw_direction_override.items():
                name = str(raw_name).strip()
                if not name:
                    raise ValueError("direction_override_log_vector ")
                try:
                    value = float(raw_value)
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        f"direction_override_log_vector[{name!r}] "
                    ) from exc
                if not math.isfinite(value):
                    raise ValueError(
                        f"direction_override_log_vector[{name!r}] "
                    )
                direction_override_log_vector[name] = value

        logger = OptimizationLogger(log_path, self.verbose)
        self.history = []
        start_time = time.perf_counter()

        def _estimate(
            params: Dict[str, float],
            label: str,
        ) -> Tuple[
            Dict[str, float],
            Optional[Dict[str, Dict[str, float]]],
            Optional[Dict[str, Any]],
        ]:
            setattr(self.resource_estimation_func, "current_label", label)
            estimate_start = time.perf_counter()
            result = self.resource_estimation_func(sql_set, copy.deepcopy(params))
            duration_s = time.perf_counter() - estimate_start
            stats = getattr(self.resource_estimation_func, "last_stats", None)
            metadata = getattr(self.resource_estimation_func, "last_metadata", None)
            if isinstance(result, tuple) and len(result) in {2, 3}:
                resource = result[0]
                tuple_stats = result[1]
                if isinstance(tuple_stats, dict):
                    stats = tuple_stats
                if len(result) == 3 and isinstance(result[2], dict):
                    metadata = result[2]
            else:
                resource = result
            if not isinstance(resource, dict):
                raise TypeError(
                    "Resource_Estimation (, )"
                    " (, , )"
                )
            parsed_resource: Dict[str, float] = {}
            for name, value in resource.items():
                try:
                    parsed_value = float(value)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f" {name!r} : {value!r}") from exc
                if not math.isfinite(parsed_value):
                    raise ValueError(f" {name!r} : {value!r}")
                parsed_resource[str(name)] = parsed_value
            logger.log_struct(
                {
                    "event": "resource_estimation",
                    "label": label,
                    "params": copy.deepcopy(params),
                    "resource": copy.deepcopy(parsed_resource),
                    "duration_s": duration_s,
                }
            )
            return parsed_resource, copy.deepcopy(stats), copy.deepcopy(metadata)

        def _avg(
            stats: Optional[Dict[str, Dict[str, float]]],
            name: str,
        ) -> Optional[float]:
            if not isinstance(stats, dict):
                return None
            value = stats.get(name)
            if not isinstance(value, dict) or value.get("avg") is None:
                return None
            try:
                return float(value["avg"])
            except (TypeError, ValueError):
                return None

        def _utilization(
            resource: Dict[str, float],
            capabilities: Dict[str, float],
        ) -> Dict[str, float]:
            utilization: Dict[str, float] = {}
            for name, value in resource.items():
                capacity = float(capabilities.get(name, 0.0) or 0.0)
                if capacity > 0.0:
                    utilization[name] = float(value) / capacity
            return utilization

        capabilities = {
            str(name): float(value)
            for name, value in (
                identify_sys_func() if identify_sys_func is not None else self.capability
            ).items()
        }
        initial_params = copy.deepcopy(p_real)
        active_param_names = [
            str(name).strip()
            for name in (
                active_params
                if active_params is not None
                else [name for name in p_real if name in param_ranges]
            )
            if str(name).strip()
        ]
        if not active_param_names:
            raise ValueError("active_params ")
        if len(set(active_param_names)) != len(active_param_names):
            raise ValueError("active_params ")
        missing_ranges = [name for name in active_param_names if name not in param_ranges]
        missing_params = [name for name in active_param_names if name not in p_real]
        if missing_ranges:
            raise ValueError(f"active_params : {missing_ranges}")
        if missing_params:
            raise ValueError(f"active_params  p_real : {missing_params}")

        for name in active_param_names:
            param_range = param_ranges[name]
            low = float(param_range.min_value)
            high = float(param_range.max_value)
            if not math.isfinite(low) or not math.isfinite(high) or low > high:
                raise ValueError(f" {name!r} : [{low}, {high}]")
            try:
                current_value = float(p_real[name])
            except (TypeError, ValueError) as exc:
                raise ValueError(f" {name!r} : {p_real[name]!r}") from exc
            if not math.isfinite(current_value):
                raise ValueError(f" {name!r} : {current_value!r}")

        target_bottlenecks = {
            str(name).strip()
            for name in compat_options.get("target_bottleneck_resources", []) or []
            if str(name).strip() and str(name).strip().lower() != "none"
        }
        directional_scoring_audit: Optional[Dict[str, Any]] = None
        if directional_scoring_contract_required:
            if len(target_bottlenecks) != 1:
                raise ValueError(
                    "formal directional scoring requires exactly one target "
                    "bottleneck resource"
                )
            target_resource = next(iter(target_bottlenecks))
            directional_scoring_audit = (
                self.utility_calculator.directional_band_target_contract_audit(
                    target_resource=target_resource,
                )
            )
            logger.log_struct(
                {
                    "event": "gradient_directional_scoring_contract_validation",
                    **copy.deepcopy(directional_scoring_audit),
                }
            )
        target_util_override = compat_options.get(
            "target_bottleneck_utilization_override", {}
        )
        active_underuse_scale = float(
            compat_options.get("active_bottleneck_underuse_scale", 1.0)
        )
        post_target_util = float(
            compat_options.get("post_bottleneck_target_util", 0.9)
        )
        post_pressure_scale = float(
            compat_options.get("post_bottleneck_non_bottleneck_pressure_scale", 2.0)
        )

        if self.verbose:
            logger.log("=== Experience-Table Subset SGD Start ===", force=True)
            logger.log(
                f"active_params={active_param_names}, eta={eta}, max_iter={max_iter}, "
                f"patience={patience}, gamma={gamma}",
                force=True,
            )
            if p_start is not None and p_start != p_real:
                logger.log("p_start ignored: frozen protocol starts from p_real", force=True)

        try:
            resource_real, resource_real_stats, resource_real_metadata = _estimate(
                p_real, "p_real"
            )
            initial_score = float(
                self.utility_calculator.calculate_utility_score(resource_real, capabilities)
            )
            p_opt = copy.deepcopy(p_real)
            resource_opt = copy.deepcopy(resource_real)
            resource_opt_stats = copy.deepcopy(resource_real_stats)
            resource_opt_metadata = copy.deepcopy(resource_real_metadata)
            no_improve_cnt = 0
            current_eta = float(eta)
            requested_eta = float(eta)
            candidate_trajectory: List[Dict[str, Any]] = []
            boundary_corner_gradient_snapshot: Optional[
                Dict[str, Any]
            ] = None
            nonincumbent_plateau_candidate: Optional[Dict[str, Any]] = None
            historical_bottlenecks = {
                name
                for name, value in resource_real.items()
                if float(capabilities.get(name, 0.0) or 0.0) > 0.0
                and float(value) / float(capabilities[name])
                > float(self.utility_calculator.overload_threshold)
            }
            completed_iterations = 0

            for iteration in range(1, max_iter + 1):
                completed_iterations = iteration
                fixed_probe_mode = relative_step_probe_pct is not None
                gradient_resource = resource_real if fixed_probe_mode else resource_opt
                parameter_base = p_real if fixed_probe_mode else p_opt
                p_tmp = copy.deepcopy(parameter_base)
                directions: Dict[str, float] = {}
                direction_logs: List[str] = []
                gradient_audit: Dict[str, Dict[str, Any]] = {}

                for name in active_param_names:
                    param_audit: Dict[str, Any] = {}
                    if not original_gradient_only and name in direction_override_log_vector:
                        direction = direction_override_log_vector[name]
                        detail = (
                            f"{name}: direction=replay_calibrated_override "
                            f"({direction:+.6g})"
                        )
                        param_audit = {
                            "param": name,
                            "confidence": None,
                            "resources": {},
                            "g_j": float(direction),
                            "gradient_source": "replay_calibrated_override",
                        }
                    else:
                        direction, detail = self._calculate_direction(
                            name,
                            sensitivity_table,
                            gradient_resource,
                            capabilities,
                            audit_sink=param_audit,
                            original_gradient_only=original_gradient_only,
                            historical_bottleneck_resources=historical_bottlenecks,
                            target_bottleneck_resources=target_bottlenecks,
                            target_bottleneck_utilization_override=target_util_override,
                            active_bottleneck_underuse_scale=active_underuse_scale,
                            post_bottleneck_target_util=post_target_util,
                            post_bottleneck_non_bottleneck_pressure_scale=post_pressure_scale,
                        )
                    if not math.isfinite(direction):
                        raise ValueError(
                            f" {name!r} : {direction!r}"
                        )
                    directions[name] = float(direction)
                    gradient_audit[name] = copy.deepcopy(param_audit)
                    if detail:
                        direction_logs.append(detail)

                if (
                    parameter_boundary_corner_scan_enabled
                    and boundary_corner_gradient_snapshot is None
                ):
                    if not original_gradient_only:
                        raise ValueError(
                            "parameter boundary corners require "
                            "original_gradient_only=true"
                        )
                    for name in active_param_names:
                        audit = gradient_audit.get(name) or {}
                        confidence = audit.get("confidence")
                        try:
                            confidence_value = float(confidence)
                        except (TypeError, ValueError) as exc:
                            raise ValueError(
                                "parameter boundary corners require finite "
                                f"confidence for {name}"
                            ) from exc
                        if not math.isfinite(confidence_value):
                            raise ValueError(
                                "parameter boundary corners require finite "
                                f"confidence for {name}"
                            )
                        recomputed_gradient = sum(
                            float(values.get("dScore/dU"))
                            * float(values.get("T"))
                            * confidence_value
                            for values in (
                                (audit.get("resources") or {}).values()
                            )
                        )
                        if not math.isclose(
                            recomputed_gradient,
                            float(directions[name]),
                            rel_tol=1e-12,
                            abs_tol=1e-12,
                        ):
                            raise RuntimeError(
                                "parameter boundary corner gradient audit "
                                f"mismatch for {name}: "
                                f"{recomputed_gradient} != {directions[name]}"
                            )
                    boundary_corner_gradient_snapshot = {
                        "directions": copy.deepcopy(directions),
                        "gradient_audit": copy.deepcopy(gradient_audit),
                        "gradient_resource": copy.deepcopy(
                            gradient_resource
                        ),
                        "gradient_score": float(
                            self.utility_calculator.calculate_utility_score(
                                gradient_resource,
                                capabilities,
                            )
                        ),
                    }

                direction_peak = max(
                    (abs(float(value)) for value in directions.values()),
                    default=0.0,
                )
                eta_before_normalization = float(current_eta)
                eta_min_for_relative_step: Optional[float] = None
                eta_max_for_relative_step: Optional[float] = None
                if boundary_aware_eta_enabled and direction_peak > 0.0:
                    eta_min_for_relative_step = (
                        min_param_delta_rel_pct / 100.0 / direction_peak
                    )
                    eta_max_for_relative_step = (
                        max_param_delta_rel_pct / 100.0 / direction_peak
                    )
                    current_eta = min(
                        max(current_eta, eta_min_for_relative_step),
                        eta_max_for_relative_step,
                    )
                requested_relative_step_pct: Optional[float] = None
                if fixed_probe_mode:
                    requested_relative_step_pct = float(
                        relative_step_probe_pct[iteration - 1]
                    )
                    current_eta = (
                        requested_relative_step_pct / 100.0 / direction_peak
                        if direction_peak > 0.0
                        else 0.0
                    )

                delta_param: Dict[str, Dict[str, Any]] = {}
                for name in active_param_names:
                    current_value = float(parameter_base[name])
                    proposed = current_value * (1.0 + current_eta * directions[name])
                    clipped = self._clip(proposed, param_ranges[name])
                    p_tmp[name] = clipped
                    absolute_delta = float(clipped - current_value)
                    delta_param[name] = {
                        "before": current_value,
                        "proposed_before_clip": float(proposed),
                        "after_clip": float(clipped),
                        "absolute": absolute_delta,
                        "relative": (
                            absolute_delta / current_value
                            if current_value != 0.0
                            else None
                        ),
                        "clipped": bool(clipped != proposed),
                    }


                for name, value in p_real.items():
                    if name not in active_param_names:
                        p_tmp[name] = copy.deepcopy(value)

                resource_tmp, stats_tmp, metadata_tmp = _estimate(
                    p_tmp,
                    f"sgd_iteration_{iteration:04d}",
                )
                incumbent_score = float(
                    self.utility_calculator.calculate_utility_score(resource_opt, capabilities)
                )
                score_tmp = float(
                    self.utility_calculator.calculate_utility_score(resource_tmp, capabilities)
                )
                gradient_score = float(
                    self.utility_calculator.calculate_utility_score(
                        gradient_resource,
                        capabilities,
                    )
                )
                dscore_du = {
                    param: {
                        resource: values["dScore/dU"]
                        for resource, values in audit.get("resources", {}).items()
                    }
                    for param, audit in gradient_audit.items()
                }
                experience_t = {
                    param: {
                        resource: values["T"]
                        for resource, values in audit.get("resources", {}).items()
                    }
                    for param, audit in gradient_audit.items()
                }
                confidence_audit = {
                    param: audit.get("confidence")
                    for param, audit in gradient_audit.items()
                }
                plateau_exploration = bool(
                    plateau_exploration_enabled
                    and p_tmp != p_real
                    and abs(score_tmp - initial_score) <= plateau_score_epsilon
                )
                parameter_noop = bool(p_tmp == p_real)
                initial_score_plateau = bool(
                    not parameter_noop
                    and abs(score_tmp - initial_score) <= plateau_score_epsilon
                )
                incumbent_score_plateau = bool(
                    not parameter_noop
                    and abs(score_tmp - incumbent_score) <= plateau_score_epsilon
                )
                boundary_plateau = bool(
                    boundary_aware_eta_enabled and incumbent_score_plateau
                )
                # Any score plateau is an offline exploration point only.  In
                # particular, a later point can tie the initial score without
                # tying a previously improved incumbent; it must still never
                # be eligible to update the outer incumbent.
                offline_plateau = bool(
                    parameter_noop
                    or initial_score_plateau
                    or incumbent_score_plateau
                )
                incumbent_update_eligible = not offline_plateau
                candidate_origin = (
                    "boundary_plateau_probe"
                    if boundary_plateau
                    else (
                        "initial_score_plateau_probe"
                        if initial_score_plateau
                        else (
                            "incumbent_score_plateau_probe"
                            if incumbent_score_plateau
                            else (
                                "parameter_noop_probe"
                                if parameter_noop
                                else "gradient_probe"
                            )
                        )
                    )
                )
                improved = bool(
                    incumbent_update_eligible
                    and self._is_improved(
                        resource_opt,
                        resource_tmp,
                    )
                )
                trajectory_index = len(candidate_trajectory) + 1
                gradient_debug = {
                    "event": "score_gradient_debug",
                    "iteration": iteration,
                    "trajectory_index": trajectory_index,
                    "probe_kind": "gradient_relative_step_probe",
                    "used_for_gradient": True,
                    "used_for_inner_incumbent_update": bool(improved),
                    "gradient_source": (
                        "original_dScore_dU_times_T_times_confidence"
                        if original_gradient_only
                        else "experience_table_T_with_phase_pressure"
                    ),
                    "directional_scoring_contract": copy.deepcopy(
                        directional_scoring_audit
                    ),
                    "feature_ratio": copy.deepcopy(
                        metadata_tmp.get("feature_ratios", {})
                        if isinstance(metadata_tmp, dict)
                        else {}
                    ),
                    "feature_ratio_unclipped": copy.deepcopy(
                        metadata_tmp.get("feature_ratios_unclipped", {})
                        if isinstance(metadata_tmp, dict)
                        else {}
                    ),
                    "workload_plan_fingerprint": (
                        metadata_tmp.get("workload_plan_fingerprint")
                        if isinstance(metadata_tmp, dict)
                        else None
                    ),
                    "predicted_resource": copy.deepcopy(resource_tmp),
                    "utilization": _utilization(resource_tmp, capabilities),
                    "gradient_point": {
                        "predicted_resource": copy.deepcopy(
                            gradient_resource
                        ),
                        "utilization": _utilization(
                            gradient_resource,
                            capabilities,
                        ),
                        "score": gradient_score,
                    },
                    "initial_score": initial_score,
                    "dScore/dU": dscore_du,
                    "T": experience_t,
                    "confidence": confidence_audit,
                    "gradient_components": {
                        param: copy.deepcopy(audit.get("resources", {}))
                        for param, audit in gradient_audit.items()
                    },
                    "g_j": copy.deepcopy(directions),
                    "eta": float(current_eta),
                    "eta_semantics": "gradient_direction_multiplier",
                    "eta_applied_to_candidate": True,
                    "eta_requested": requested_eta,
                    "eta_before_normalization": eta_before_normalization,
                    "eta_min_for_relative_step": eta_min_for_relative_step,
                    "eta_max_for_relative_step": eta_max_for_relative_step,
                    "requested_relative_step_pct": requested_relative_step_pct,
                    "fixed_relative_step_probe": fixed_probe_mode,
                    "boundary_aware_eta_enabled": boundary_aware_eta_enabled,
                    "direction_peak": direction_peak,
                    "delta_param": copy.deepcopy(delta_param),
                    "candidate_score": score_tmp,
                    "candidate_score_delta": score_tmp - incumbent_score,
                    "candidate_score_delta_vs_initial": score_tmp - initial_score,
                    "candidate_origin": candidate_origin,
                    "parameter_noop": parameter_noop,
                    "initial_score_plateau": initial_score_plateau,
                    "incumbent_score_plateau": incumbent_score_plateau,
                    "boundary_plateau": boundary_plateau,
                    "offline_plateau": offline_plateau,
                    "incumbent_update_eligible": incumbent_update_eligible,
                }
                logger.log_struct(gradient_debug)
                candidate_trajectory.append({
                    "iteration": iteration,
                    "trajectory_index": trajectory_index,
                    "params": copy.deepcopy(p_tmp),
                    "resource": copy.deepcopy(resource_tmp),
                    "resource_stats": copy.deepcopy(stats_tmp),
                    "estimation_metadata": copy.deepcopy(metadata_tmp),
                    "score": float(score_tmp),
                    "eta": float(current_eta),
                    "eta_semantics": "gradient_direction_multiplier",
                    "eta_applied_to_candidate": True,
                    "requested_relative_step_pct": requested_relative_step_pct,
                    "delta_param": copy.deepcopy(delta_param),
                    "dScore/dU": copy.deepcopy(dscore_du),
                    "T": copy.deepcopy(experience_t),
                    "confidence": copy.deepcopy(confidence_audit),
                    "gradient_components": copy.deepcopy(
                        gradient_debug["gradient_components"]
                    ),
                    "g_j": copy.deepcopy(directions),
                    "directional_scoring_contract": copy.deepcopy(
                        directional_scoring_audit
                    ),
                    "probe_kind": "gradient_relative_step_probe",
                    "used_for_gradient": True,
                    "used_for_inner_incumbent_update": bool(improved),
                    "candidate_origin": candidate_origin,
                    "parameter_noop": parameter_noop,
                    "initial_score_plateau": initial_score_plateau,
                    "incumbent_score_plateau": incumbent_score_plateau,
                    "boundary_plateau": boundary_plateau,
                    "offline_plateau": offline_plateau,
                    "incumbent_update_eligible": incumbent_update_eligible,
                })
                if boundary_plateau:
                    probe_distance = max(
                        (
                            abs(float(item.get("relative") or 0.0))
                            for item in delta_param.values()
                        ),
                        default=0.0,
                    )
                    previous_distance = float(
                        (nonincumbent_plateau_candidate or {}).get(
                            "max_relative_delta", -1.0
                        )
                    )
                    if probe_distance > previous_distance:
                        nonincumbent_plateau_candidate = {
                            "params": copy.deepcopy(p_tmp),
                            "resource": copy.deepcopy(resource_tmp),
                            "stats": copy.deepcopy(stats_tmp),
                            "metadata": copy.deepcopy(metadata_tmp),
                            "score": float(score_tmp),
                            "eta": float(current_eta),
                            "max_relative_delta": float(probe_distance),
                            "iteration": iteration,
                        }
                eta_before = current_eta
                if improved:
                    p_opt = copy.deepcopy(p_tmp)
                    resource_opt = copy.deepcopy(resource_tmp)
                    resource_opt_stats = copy.deepcopy(stats_tmp)
                    resource_opt_metadata = copy.deepcopy(metadata_tmp)
                    no_improve_cnt = 0
                else:
                    no_improve_cnt += 1
                    if fixed_probe_mode:
                        no_improve_cnt = 0
                    elif boundary_plateau:
                        current_eta *= boundary_eta_growth_factor
                        if eta_max_for_relative_step is not None:
                            current_eta = min(
                                current_eta,
                                eta_max_for_relative_step,
                            )
                        no_improve_cnt = 0
                    elif no_improve_cnt >= patience:
                        current_eta *= gamma

                record = {
                    "algorithm": "experience_table_subset_sgd_v1",
                    "iteration": iteration,
                    "active_params": list(active_param_names),
                    "acceptance_reference": "incumbent",
                    "directions": copy.deepcopy(directions),
                    "candidate_params": copy.deepcopy(p_tmp),
                    "candidate_resource": copy.deepcopy(resource_tmp),
                    "acceptance_reference_score": initial_score,
                    "incumbent_score": incumbent_score,
                    "candidate_score": score_tmp,
                    "accepted_by_resource_utility": bool(improved),
                    "accepted_by_plateau_exploration": False,
                    "parameter_noop": parameter_noop,
                    "initial_score_plateau": initial_score_plateau,
                    "incumbent_score_plateau": incumbent_score_plateau,
                    "boundary_plateau": boundary_plateau,
                    "offline_plateau": offline_plateau,
                    "acceptance_rule": (
                        "candidate_score_strictly_above_incumbent_score"
                    ),
                    "direction_override_log_vector": copy.deepcopy(
                        direction_override_log_vector
                    ),
                    "no_improve_count": no_improve_cnt,
                    "eta_before": eta_before,
                    "eta_after": current_eta,
                    "gradient_debug": copy.deepcopy(gradient_debug),
                }
                self.history.append(copy.deepcopy(record))
                logger.log_struct({"event": "experience_table_sgd_iteration", **record})
                for detail in direction_logs:
                    logger.log(detail)

                if (
                    not fixed_probe_mode
                    and not improved
                    and current_eta < min_eta
                ):
                    logger.log_struct(
                        {
                            "event": "experience_table_sgd_converged",
                            "reason": "eta_below_min_eta",
                            "iteration": iteration,
                            "eta": current_eta,
                            "min_eta": min_eta,
                        }
                    )
                    break

            if parameter_boundary_corner_scan_enabled:
                if boundary_corner_gradient_snapshot is None:
                    raise RuntimeError(
                        "parameter boundary corner scan lacks a frozen "
                        "original-gradient snapshot"
                    )
                if len(active_param_names) > 10:
                    raise ValueError(
                        "parameter boundary corner scan supports at most "
                        "10 active parameters"
                    )
                p_opt_before_corners = copy.deepcopy(p_opt)
                resource_opt_before_corners = copy.deepcopy(resource_opt)
                resource_opt_stats_before_corners = copy.deepcopy(
                    resource_opt_stats
                )
                resource_opt_metadata_before_corners = copy.deepcopy(
                    resource_opt_metadata
                )
                current_eta_before_corners = float(current_eta)
                no_improve_before_corners = int(no_improve_cnt)
                historical_bottlenecks_before_corners = copy.deepcopy(
                    historical_bottlenecks
                )
                corner_directions = copy.deepcopy(
                    boundary_corner_gradient_snapshot["directions"]
                )
                corner_gradient_audit = copy.deepcopy(
                    boundary_corner_gradient_snapshot["gradient_audit"]
                )
                corner_dscore_du = {
                    param: {
                        resource: values["dScore/dU"]
                        for resource, values in (
                            audit.get("resources", {})
                        ).items()
                    }
                    for param, audit in corner_gradient_audit.items()
                }
                corner_experience_t = {
                    param: {
                        resource: values["T"]
                        for resource, values in (
                            audit.get("resources", {})
                        ).items()
                    }
                    for param, audit in corner_gradient_audit.items()
                }
                corner_confidence = {
                    param: audit.get("confidence")
                    for param, audit in corner_gradient_audit.items()
                }
                corner_incumbent_score = float(
                    self.utility_calculator.calculate_utility_score(
                        resource_opt,
                        capabilities,
                    )
                )
                corner_count = 0
                for corner_index, corner_bits in enumerate(
                    itertools.product(
                        (0, 1),
                        repeat=len(active_param_names),
                    )
                ):
                    p_corner = copy.deepcopy(p_real)
                    corner_mask = "".join(str(bit) for bit in corner_bits)
                    corner_sides: Dict[str, str] = {}
                    corner_delta_param: Dict[str, Dict[str, Any]] = {}
                    for name, bit in zip(
                        active_param_names,
                        corner_bits,
                    ):
                        side = "max" if bit else "min"
                        boundary_value = float(
                            param_ranges[name].max_value
                            if bit
                            else param_ranges[name].min_value
                        )
                        before = float(p_real[name])
                        p_corner[name] = boundary_value
                        absolute_delta = boundary_value - before
                        corner_sides[name] = side
                        corner_delta_param[name] = {
                            "before": before,
                            "proposed_before_clip": boundary_value,
                            "after_clip": boundary_value,
                            "absolute": absolute_delta,
                            "relative": (
                                absolute_delta / before
                                if before != 0.0
                                else None
                            ),
                            "clipped": False,
                            "boundary_side": side,
                        }
                    (
                        corner_resource,
                        corner_stats,
                        corner_metadata,
                    ) = _estimate(
                        p_corner,
                        (
                            "parameter_boundary_corner_"
                            f"{corner_index:04d}_{corner_mask}"
                        ),
                    )
                    corner_score = float(
                        self.utility_calculator.calculate_utility_score(
                            corner_resource,
                            capabilities,
                        )
                    )
                    parameter_noop = bool(p_corner == p_real)
                    initial_score_plateau = bool(
                        not parameter_noop
                        and abs(corner_score - initial_score)
                        <= plateau_score_epsilon
                    )
                    incumbent_score_plateau = bool(
                        not parameter_noop
                        and abs(
                            corner_score - corner_incumbent_score
                        )
                        <= plateau_score_epsilon
                    )
                    offline_plateau = bool(
                        parameter_noop
                        or initial_score_plateau
                        or incumbent_score_plateau
                    )
                    incumbent_update_eligible = not offline_plateau
                    trajectory_index = len(candidate_trajectory) + 1
                    corner_debug = {
                        "event": "score_gradient_debug",
                        "iteration": completed_iterations,
                        "trajectory_index": trajectory_index,
                        "probe_kind": "parameter_boundary_corner",
                        "used_for_gradient": False,
                        "used_for_inner_incumbent_update": False,
                        "gradient_source": (
                            "frozen_original_dScore_dU_times_T_times_confidence"
                        ),
                        "directional_scoring_contract": copy.deepcopy(
                            directional_scoring_audit
                        ),
                        "feature_ratio": copy.deepcopy(
                            corner_metadata.get("feature_ratios", {})
                            if isinstance(corner_metadata, dict)
                            else {}
                        ),
                        "feature_ratio_unclipped": copy.deepcopy(
                            corner_metadata.get(
                                "feature_ratios_unclipped",
                                {},
                            )
                            if isinstance(corner_metadata, dict)
                            else {}
                        ),
                        "workload_plan_fingerprint": (
                            corner_metadata.get(
                                "workload_plan_fingerprint"
                            )
                            if isinstance(corner_metadata, dict)
                            else None
                        ),
                        "predicted_resource": copy.deepcopy(
                            corner_resource
                        ),
                        "utilization": _utilization(
                            corner_resource,
                            capabilities,
                        ),
                        "gradient_point": {
                            "predicted_resource": copy.deepcopy(
                                boundary_corner_gradient_snapshot[
                                    "gradient_resource"
                                ]
                            ),
                            "utilization": _utilization(
                                boundary_corner_gradient_snapshot[
                                    "gradient_resource"
                                ],
                                capabilities,
                            ),
                            "score": float(
                                boundary_corner_gradient_snapshot[
                                    "gradient_score"
                                ]
                            ),
                        },
                        "initial_score": initial_score,
                        "dScore/dU": copy.deepcopy(corner_dscore_du),
                        "T": copy.deepcopy(corner_experience_t),
                        "confidence": copy.deepcopy(corner_confidence),
                        "gradient_components": {
                            param: copy.deepcopy(
                                audit.get("resources", {})
                            )
                            for param, audit in (
                                corner_gradient_audit.items()
                            )
                        },
                        "g_j": copy.deepcopy(corner_directions),
                        "eta": 0.0,
                        "eta_semantics": (
                            "not_applicable_parameter_boundary_corner"
                        ),
                        "eta_applied_to_candidate": False,
                        "eta_requested": requested_eta,
                        "eta_before_normalization": 0.0,
                        "eta_min_for_relative_step": None,
                        "eta_max_for_relative_step": None,
                        "requested_relative_step_pct": None,
                        "fixed_relative_step_probe": False,
                        "boundary_aware_eta_enabled": (
                            boundary_aware_eta_enabled
                        ),
                        "direction_peak": max(
                            (
                                abs(float(value))
                                for value in corner_directions.values()
                            ),
                            default=0.0,
                        ),
                        "delta_param": copy.deepcopy(
                            corner_delta_param
                        ),
                        "candidate_score": corner_score,
                        "candidate_score_delta": (
                            corner_score - corner_incumbent_score
                        ),
                        "candidate_score_delta_vs_initial": (
                            corner_score - initial_score
                        ),
                        "candidate_origin": (
                            "parameter_boundary_corner_probe"
                        ),
                        "boundary_corner_index": corner_index,
                        "boundary_corner_mask": corner_mask,
                        "boundary_corner_sides": copy.deepcopy(
                            corner_sides
                        ),
                        "parameter_noop": parameter_noop,
                        "initial_score_plateau": initial_score_plateau,
                        "incumbent_score_plateau": (
                            incumbent_score_plateau
                        ),
                        "boundary_plateau": False,
                        "offline_plateau": offline_plateau,
                        "incumbent_update_eligible": (
                            incumbent_update_eligible
                        ),
                    }
                    logger.log_struct(corner_debug)
                    candidate_trajectory.append({
                        "iteration": completed_iterations,
                        "trajectory_index": trajectory_index,
                        "params": copy.deepcopy(p_corner),
                        "resource": copy.deepcopy(corner_resource),
                        "resource_stats": copy.deepcopy(corner_stats),
                        "estimation_metadata": copy.deepcopy(
                            corner_metadata
                        ),
                        "score": corner_score,
                        "eta": 0.0,
                        "eta_semantics": (
                            "not_applicable_parameter_boundary_corner"
                        ),
                        "eta_applied_to_candidate": False,
                        "requested_relative_step_pct": None,
                        "delta_param": copy.deepcopy(
                            corner_delta_param
                        ),
                        "dScore/dU": copy.deepcopy(
                            corner_dscore_du
                        ),
                        "T": copy.deepcopy(corner_experience_t),
                        "confidence": copy.deepcopy(
                            corner_confidence
                        ),
                        "gradient_components": copy.deepcopy(
                            corner_debug["gradient_components"]
                        ),
                        "g_j": copy.deepcopy(corner_directions),
                        "directional_scoring_contract": copy.deepcopy(
                            directional_scoring_audit
                        ),
                        "probe_kind": "parameter_boundary_corner",
                        "used_for_gradient": False,
                        "used_for_inner_incumbent_update": False,
                        "candidate_origin": (
                            "parameter_boundary_corner_probe"
                        ),
                        "boundary_corner_index": corner_index,
                        "boundary_corner_mask": corner_mask,
                        "boundary_corner_sides": copy.deepcopy(
                            corner_sides
                        ),
                        "parameter_noop": parameter_noop,
                        "initial_score_plateau": initial_score_plateau,
                        "incumbent_score_plateau": (
                            incumbent_score_plateau
                        ),
                        "boundary_plateau": False,
                        "offline_plateau": offline_plateau,
                        "incumbent_update_eligible": (
                            incumbent_update_eligible
                        ),
                    })
                    corner_count += 1
                if (
                    p_opt != p_opt_before_corners
                    or resource_opt != resource_opt_before_corners
                    or resource_opt_stats
                    != resource_opt_stats_before_corners
                    or resource_opt_metadata
                    != resource_opt_metadata_before_corners
                    or not math.isclose(
                        current_eta,
                        current_eta_before_corners,
                        rel_tol=0.0,
                        abs_tol=0.0,
                    )
                    or no_improve_cnt != no_improve_before_corners
                    or historical_bottlenecks
                    != historical_bottlenecks_before_corners
                ):
                    raise RuntimeError(
                        "parameter boundary corner scan mutated inner "
                        "incumbent state"
                    )
                logger.log_struct(
                    {
                        "event": "parameter_boundary_corner_scan_complete",
                        "active_params": list(active_param_names),
                        "corner_count": corner_count,
                        "used_for_gradient": False,
                        "used_for_inner_incumbent_update": False,
                        "inner_incumbent_unchanged": True,
                    }
                )

            returned_nonincumbent_plateau = False
            if p_opt == p_real and nonincumbent_plateau_candidate is not None:
                logger.log_struct(
                    {
                        "event": "boundary_plateau_candidate_retained_offline_only",
                        "iteration": nonincumbent_plateau_candidate["iteration"],
                        "eta": nonincumbent_plateau_candidate["eta"],
                        "max_relative_delta": nonincumbent_plateau_candidate[
                            "max_relative_delta"
                        ],
                        "params": copy.deepcopy(
                            nonincumbent_plateau_candidate["params"]
                        ),
                        "resource": copy.deepcopy(
                            nonincumbent_plateau_candidate["resource"]
                        ),
                        "score": nonincumbent_plateau_candidate["score"],
                        "updates_outer_incumbent": False,
                    }
                )

            final_resource = copy.deepcopy(resource_opt)
            final_score = float(
                self.utility_calculator.calculate_utility_score(final_resource, capabilities)
            )
            initial_utilization = _utilization(resource_real, capabilities)
            final_utilization = _utilization(final_resource, capabilities)
            initial_qps = _avg(resource_real_stats, "qps")
            final_qps = _avg(resource_opt_stats, "qps")
            initial_query_count = _avg(resource_real_stats, "query_count")
            final_query_count = _avg(resource_opt_stats, "query_count")
            initial_query_time = _avg(resource_real_stats, "query_time")
            final_query_time = _avg(resource_opt_stats, "query_time")
            score_improvement = final_score - initial_score
            score_improvement_pct = (
                score_improvement / initial_score * 100.0
                if initial_score != 0.0
                else None
            )
            qps_improvement = (
                final_qps - initial_qps
                if initial_qps is not None and final_qps is not None
                else None
            )
            qps_improvement_pct = (
                qps_improvement / initial_qps * 100.0
                if qps_improvement is not None and initial_qps not in (None, 0.0)
                else None
            )
            degradation_pct = (
                (final_query_time / initial_query_time - 1.0) * 100.0
                if initial_query_time not in (None, 0.0) and final_query_time is not None
                else None
            )
            result = OptimizationResult(
                optimized_params=copy.deepcopy(p_opt),
                initial_params=initial_params,
                initial_resource=copy.deepcopy(resource_real),
                final_resource=final_resource,
                initial_score=initial_score,
                final_score=final_score,
                score_improvement=score_improvement,
                score_improvement_pct=score_improvement_pct,
                initial_utilization=initial_utilization,
                final_utilization=final_utilization,
                initial_qps=initial_qps,
                final_qps=final_qps,
                qps_improvement=qps_improvement,
                qps_improvement_pct=qps_improvement_pct,
                initial_query_count=initial_query_count,
                final_query_count=final_query_count,
                initial_query_time=initial_query_time,
                final_query_time=final_query_time,
                degraded=(degradation_pct > 10.0) if degradation_pct is not None else None,
                degradation_pct=degradation_pct,
                iterations=completed_iterations,
                history=copy.deepcopy(self.history),
            )
            result.initial_estimation_metadata = copy.deepcopy(resource_real_metadata)
            result.final_estimation_metadata = copy.deepcopy(resource_opt_metadata)
            result.candidate_trajectory = copy.deepcopy(candidate_trajectory)
            result.candidate_trajectory_complete = True
            setattr(
                result,
                "returned_nonincumbent_plateau",
                returned_nonincumbent_plateau,
            )
            logger.log_struct(
                {
                    "event": "experience_table_subset_sgd_complete",
                    "optimized_params": copy.deepcopy(result.optimized_params),
                    "final_resource": copy.deepcopy(result.final_resource),
                    "iterations": result.iterations,
                    "total_wall_time_s": time.perf_counter() - start_time,
                }
            )
            return result
        finally:
            logger.close()

def optimize_with_gradient_descent(
    resource_estimation_func: Callable[[Any, Dict[str, float]], Dict[str, float]],
    capability: Dict[str, float],
    p_real: Dict[str, float],
    p_candidate: Dict[str, float],
    sensitivity_table: Dict[str, Dict[str, float]],
    param_ranges: Dict[str, ParameterRange],
    sql_set: Any,
    eta: float = 0.1,
    max_iter: int = 20,
    patience: int = 3,
    gamma: float = 0.5,
    eta_growth_factor: Optional[float] = None,
    eta_shrink_factor: float = 0.5,
    initial_exploration_multiplier: float = 2.0,
    max_eta: Optional[float] = None,
    reexplore_after_no_improve: bool = True,
    min_eta: float = 1e-4,
    max_param_delta_ratio: float = 0.20,
    min_param_delta_rel_pct: float = 10.0,
    max_param_delta_rel_pct: float = 20.0,
    relative_step_probe_pct: Optional[List[float]] = None,
    boundary_aware_eta_enabled: bool = False,
    boundary_eta_growth_factor: float = 2.0,
    repeated_rejected_direction_patience: int = 0,
    repeated_rejected_direction_action: str = "off",
    repeated_rejected_direction_shrink_factor: float = 0.5,
    adaptive_rejection_enabled: bool = True,
    adaptive_rejection_shrink_first: float = 0.5,
    adaptive_rejection_shrink_first_after: int = 3,
    adaptive_rejection_shrink_second: float = 0.25,
    adaptive_rejection_shrink_second_after: int = 4,
    adaptive_rejection_focus_after: int = 5,
    adaptive_rejection_focus_top_n: int = 2,
    adaptive_rejection_stop_after: int = 10,
    exploration_acceptance_enabled: bool = True,
    exploration_acceptance_rel_tol: float = 0.02,
    plateau_exploration_enabled: bool = False,
    plateau_score_epsilon: float = 1e-12,
    parameter_boundary_corner_scan_enabled: bool = False,
    directional_scoring_contract_required: bool = False,
    repeat_candidate_jump_enabled: bool = True,
    repeat_candidate_jump_multiplier: float = 1.5,
    active_bottleneck_underuse_scale: float = 1.0,
    post_bottleneck_target_util: float = 0.9,
    post_bottleneck_non_bottleneck_pressure_scale: float = 2.0,
    direction_override_log_vector: Optional[Dict[str, float]] = None,
    post_bottleneck_guard_util_tolerance: float = 0.0,
    target_bottleneck_resources: Optional[List[str]] = None,
    target_bottleneck_utilization_override: Optional[Dict[str, float]] = None,
    target_accept_guard_enabled: bool = True,
    target_accept_guard_upper_util: Optional[float] = None,
    target_accept_guard_tolerance: float = 0.0,
    target_accept_guard_min_improvement_util: float = 1e-4,
    convergence_target_util: Optional[float] = None,
    convergence_tolerance: float = 0.05,
    convergence_overload_util: float = 1.0,
    post_convergence_explore_iter: int = 0,
    param_step_scale: Optional[Dict[str, float]] = None,
    active_params: Optional[List[str]] = None,
    no_update_patience: int = 3,
    boosted_params: Optional[List[str]] = None,
    aggressive_trial_enabled: bool = False,
    aggressive_trial_every: int = 0,
    aggressive_trial_after_no_improve: int = 0,
    aggressive_trial_after_no_plan_change: int = 0,
    aggressive_trial_eta_multiplier: float = 1.0,
    aggressive_trial_scale_multiplier: float = 1.0,
    aggressive_trial_params: Optional[List[str]] = None,
    reward_type: str = "log",
    penalty_type: str = "power",
    use_continuous: bool = False,
    continuous_type: str = "gaussian",
    overload_threshold: float = 0.8,
    score_mode: str = "threshold",
    resource_weights: Optional[Dict[str, float]] = None,
    resource_reward_weights: Optional[Dict[str, float]] = None,
    resource_penalty_weights: Optional[Dict[str, float]] = None,
    resource_target_ranges: Optional[Dict[str, Dict[str, Any]]] = None,
    verbose: bool = True,
    log_path: Optional[str] = None,
    identify_sys_func: Optional[Callable[[], Dict[str, float]]] = None,
) -> OptimizationResult:
    """optimize with gradient descent."""


    optimizer = GradientDescentParameterOptimizer(
        resource_estimation_func=resource_estimation_func,
        capability=capability,
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
        verbose=verbose,
    )

    return optimizer.optimize(
        p_real=p_real,
        p_start=p_candidate,
        sensitivity_table=sensitivity_table,
        param_ranges=param_ranges,
        sql_set=sql_set,
        eta=eta,
        max_iter=max_iter,
        patience=patience,
        gamma=gamma,
        eta_growth_factor=eta_growth_factor,
        eta_shrink_factor=eta_shrink_factor,
        initial_exploration_multiplier=initial_exploration_multiplier,
        max_eta=max_eta,
        reexplore_after_no_improve=reexplore_after_no_improve,
        min_eta=min_eta,
        max_param_delta_ratio=max_param_delta_ratio,
        min_param_delta_rel_pct=min_param_delta_rel_pct,
        max_param_delta_rel_pct=max_param_delta_rel_pct,
        relative_step_probe_pct=relative_step_probe_pct,
        boundary_aware_eta_enabled=boundary_aware_eta_enabled,
        boundary_eta_growth_factor=boundary_eta_growth_factor,
        repeated_rejected_direction_patience=repeated_rejected_direction_patience,
        repeated_rejected_direction_action=repeated_rejected_direction_action,
        repeated_rejected_direction_shrink_factor=repeated_rejected_direction_shrink_factor,
        adaptive_rejection_enabled=adaptive_rejection_enabled,
        adaptive_rejection_shrink_first=adaptive_rejection_shrink_first,
        adaptive_rejection_shrink_first_after=adaptive_rejection_shrink_first_after,
        adaptive_rejection_shrink_second=adaptive_rejection_shrink_second,
        adaptive_rejection_shrink_second_after=adaptive_rejection_shrink_second_after,
        adaptive_rejection_focus_after=adaptive_rejection_focus_after,
        adaptive_rejection_focus_top_n=adaptive_rejection_focus_top_n,
        adaptive_rejection_stop_after=adaptive_rejection_stop_after,
        exploration_acceptance_enabled=exploration_acceptance_enabled,
        exploration_acceptance_rel_tol=exploration_acceptance_rel_tol,
        plateau_exploration_enabled=plateau_exploration_enabled,
        plateau_score_epsilon=plateau_score_epsilon,
        parameter_boundary_corner_scan_enabled=(
            parameter_boundary_corner_scan_enabled
        ),
        directional_scoring_contract_required=(
            directional_scoring_contract_required
        ),
        repeat_candidate_jump_enabled=repeat_candidate_jump_enabled,
        repeat_candidate_jump_multiplier=repeat_candidate_jump_multiplier,
        active_bottleneck_underuse_scale=active_bottleneck_underuse_scale,
        post_bottleneck_target_util=post_bottleneck_target_util,
        post_bottleneck_non_bottleneck_pressure_scale=post_bottleneck_non_bottleneck_pressure_scale,
        direction_override_log_vector=direction_override_log_vector,
        post_bottleneck_guard_util_tolerance=post_bottleneck_guard_util_tolerance,
        target_bottleneck_resources=target_bottleneck_resources,
        target_bottleneck_utilization_override=target_bottleneck_utilization_override,
        target_accept_guard_enabled=target_accept_guard_enabled,
        target_accept_guard_upper_util=target_accept_guard_upper_util,
        target_accept_guard_tolerance=target_accept_guard_tolerance,
        target_accept_guard_min_improvement_util=target_accept_guard_min_improvement_util,
        convergence_target_util=convergence_target_util,
        convergence_tolerance=convergence_tolerance,
        convergence_overload_util=convergence_overload_util,
        post_convergence_explore_iter=post_convergence_explore_iter,
        param_step_scale=param_step_scale,
        active_params=active_params,
        no_update_patience=no_update_patience,
        boosted_params=boosted_params,
        aggressive_trial_enabled=aggressive_trial_enabled,
        aggressive_trial_every=aggressive_trial_every,
        aggressive_trial_after_no_improve=aggressive_trial_after_no_improve,
        aggressive_trial_after_no_plan_change=aggressive_trial_after_no_plan_change,
        aggressive_trial_eta_multiplier=aggressive_trial_eta_multiplier,
        aggressive_trial_scale_multiplier=aggressive_trial_scale_multiplier,
        aggressive_trial_params=aggressive_trial_params,
        identify_sys_func=identify_sys_func,
        log_path=log_path,
    )

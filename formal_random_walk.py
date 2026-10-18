"""MySQL ``random_walk`` compatibility entry and legacy 2026-07-21 state.

The current method named ``random_walk`` no longer generates a Gaussian point.
It delegates to the exact same all-nonempty-subset, two-center Cartesian resource
grid and staged Top-5/Top-10 replay implementation as ``experience_table_sgd``.

``RandomWalkState`` remains only for reading/resuming the immutable 2026-07-21
campaign artifacts. New runs must call ``run_subset_grid_random_walk`` or
``generate_subset_grid_random_walk_candidates`` below.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import random
from typing import Any, Mapping, Sequence


TERMINAL_OUTCOMES = {
    "outer_accepted",
    "outer_rejected",
    "duplicate",
    "failed",
    "timeout",
    "oom",
    "correctness_rejected",
    "unstable",
}


def _finite_number(value: Any, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a finite number")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"{name} must be finite")
    return parsed


def _tuple_tree(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(_tuple_tree(item) for item in value)
    return value


@dataclass(frozen=True)
class ParameterAxis:
    name: str
    minimum: float
    maximum: float
    transform: str

    def __post_init__(self) -> None:
        minimum = _finite_number(self.minimum, name=f"{self.name}.minimum")
        maximum = _finite_number(self.maximum, name=f"{self.name}.maximum")
        if not minimum < maximum:
            raise ValueError(f"invalid bounds for {self.name}: {minimum}..{maximum}")
        if self.transform not in {"linear", "log"}:
            raise ValueError(f"unsupported transform for {self.name}: {self.transform}")
        if self.transform == "log" and minimum <= 0:
            raise ValueError(f"log axis {self.name} requires a positive lower bound")

    def normalize(self, value: float) -> float:
        parsed = _finite_number(value, name=self.name)
        if parsed < self.minimum or parsed > self.maximum:
            raise ValueError(
                f"{self.name}={parsed} is outside frozen bounds "
                f"[{self.minimum}, {self.maximum}]"
            )
        if self.transform == "log":
            return (math.log(parsed) - math.log(self.minimum)) / (
                math.log(self.maximum) - math.log(self.minimum)
            )
        return (parsed - self.minimum) / (self.maximum - self.minimum)

    def denormalize(self, normalized: float) -> float:
        value = _finite_number(normalized, name=f"{self.name}.normalized")
        if value < 0.0 or value > 1.0:
            raise ValueError(f"normalized coordinate for {self.name} is outside [0,1]")
        if self.transform == "log":
            return math.exp(
                math.log(self.minimum)
                + value * (math.log(self.maximum) - math.log(self.minimum))
            )
        return self.minimum + value * (self.maximum - self.minimum)


def axes_from_frozen_search_space(payload: Mapping[str, Any]) -> tuple[ParameterAxis, ...]:
    """Build axes in the exact JSON insertion order of ``mysql.parameters``."""

    mysql = payload.get("mysql")
    if not isinstance(mysql, Mapping):
        raise ValueError("search space is missing mysql")
    parameters = mysql.get("parameters")
    if not isinstance(parameters, Mapping):
        raise ValueError("search space is missing mysql.parameters")
    axes: list[ParameterAxis] = []
    for name, spec in parameters.items():
        if not isinstance(name, str) or not isinstance(spec, Mapping):
            raise ValueError("invalid mysql parameter specification")
        if spec.get("type") != "continuous":
            raise ValueError(f"formal random walk requires continuous axis {name}")
        axes.append(
            ParameterAxis(
                name=name,
                minimum=spec.get("min"),
                maximum=spec.get("max"),
                transform=str(spec.get("transform")),
            )
        )
    if len(axes) != 8:
        raise ValueError(f"formal MySQL search space must contain 8 axes, got {len(axes)}")
    return tuple(axes)


def _reflect_then_clip(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError("cannot reflect a non-finite proposal")
    reflected = value % 2.0
    if reflected > 1.0:
        reflected = 2.0 - reflected
    return min(1.0, max(0.0, reflected))


def _signature(axes: Sequence[ParameterAxis], params: Mapping[str, float]) -> str:
    return "|".join(f"{axis.name}={float(params[axis.name]).hex()}" for axis in axes)


@dataclass
class RandomWalkState:
    """Legacy Gaussian state; forbidden as the candidate generator for new runs."""
    axes: tuple[ParameterAxis, ...]
    incumbent: list[float]
    seed: int
    sigma: float
    maximum_budget: int
    minimum_before_stop: int
    rejection_stop: int
    sigma_accept_multiplier: float = 1.1
    sigma_reject_multiplier: float = 0.7
    sigma_minimum: float = 0.05
    sigma_maximum: float = 0.4
    consumed_budget: int = 0
    consecutive_rejections: int = 0
    proposals: list[list[float]] = field(default_factory=list)
    pending_observation: bool = False
    pending_duplicate: bool = False
    seen_signatures: set[str] = field(default_factory=set)
    _rng: random.Random = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if len(self.axes) != len(self.incumbent):
            raise ValueError("axis and incumbent dimensions differ")
        if any(value < 0.0 or value > 1.0 for value in self.incumbent):
            raise ValueError("incumbent normalized coordinates must be in [0,1]")
        if self.maximum_budget <= 0:
            raise ValueError("maximum budget must be positive")
        if self.minimum_before_stop < 0 or self.rejection_stop <= 0:
            raise ValueError("invalid early-stop settings")
        self._rng = random.Random(int(self.seed))
        if not self.seen_signatures:
            default_params = {
                axis.name: axis.denormalize(value)
                for axis, value in zip(self.axes, self.incumbent)
            }
            self.seen_signatures.add(_signature(self.axes, default_params))

    @classmethod
    def from_frozen(
        cls,
        search_space: Mapping[str, Any],
        method_protocols: Mapping[str, Any],
    ) -> "RandomWalkState":
        axes = axes_from_frozen_search_space(search_space)
        mysql = search_space["mysql"]
        defaults = mysql.get("default")
        if not isinstance(defaults, Mapping) or set(defaults) != {axis.name for axis in axes}:
            raise ValueError("MySQL defaults do not exactly match the frozen axes")
        common = method_protocols.get("common")
        protocol = method_protocols.get("random_walk")
        if not isinstance(common, Mapping) or not isinstance(protocol, Mapping):
            raise ValueError("method protocols are missing common/random_walk")
        common_expected = {
            "method_seed": 20260721,
            "maximum_non_default_real_candidates": 10,
            "outer_acceptance_policy": "acceptance_policy.json",
            "search_space": "search_spaces.json",
            "default_and_final_independent_replays": 3,
            "method_may_not_modify_shared_configs": True,
        }
        if set(common) != set(common_expected) or any(
            common.get(key) != value for key, value in common_expected.items()
        ):
            raise ValueError("common method protocol differs from frozen v2")
        required = {
            "method_id": "random_walk",
            "space": "normalized_transformed_space",
            "start": "default",
            "proposal": "seeded_isotropic_gaussian",
            "initial_sigma": 0.25,
            "sigma_after_outer_accept": "min(0.4,sigma*1.1)",
            "sigma_after_outer_reject": "max(0.05,sigma*0.7)",
            "boundary_handling": "reflect_then_clip",
            "random_seed": 20260721,
            "duplicate_consumes_budget": True,
        }
        expected_protocol_fields = set(required) | {"early_stop"}
        if set(protocol) != expected_protocol_fields:
            raise ValueError("random-walk protocol fields differ from frozen v2")
        mismatch = {key: (protocol.get(key), value) for key, value in required.items() if protocol.get(key) != value}
        if mismatch:
            raise ValueError(f"random-walk protocol mismatch: {mismatch}")
        early = protocol.get("early_stop")
        if not isinstance(early, Mapping):
            raise ValueError("random-walk protocol is missing early_stop")
        if set(early) != {
            "consecutive_outer_rejections",
            "minimum_real_candidates_before_stop",
        } or early.get("consecutive_outer_rejections") != 4 or early.get(
            "minimum_real_candidates_before_stop"
        ) != 6:
            raise ValueError("random-walk early-stop protocol differs from frozen v2")
        return cls(
            axes=axes,
            incumbent=[axis.normalize(defaults[axis.name]) for axis in axes],
            seed=int(protocol["random_seed"]),
            sigma=float(protocol["initial_sigma"]),
            maximum_budget=int(common["maximum_non_default_real_candidates"]),
            minimum_before_stop=int(early["minimum_real_candidates_before_stop"]),
            rejection_stop=int(early["consecutive_outer_rejections"]),
        )

    @property
    def should_stop(self) -> bool:
        return self.consumed_budget >= self.maximum_budget or (
            self.consumed_budget >= self.minimum_before_stop
            and self.consecutive_rejections >= self.rejection_stop
        )

    def propose(self) -> dict[str, float]:
        if self.pending_observation:
            raise RuntimeError("the previous durable proposal has no outer decision")
        if self.should_stop:
            raise RuntimeError("frozen random-walk stopping rule has been reached")
        normalized = [
            _reflect_then_clip(value + self._rng.gauss(0.0, self.sigma))
            for value in self.incumbent
        ]
        self.proposals.append(normalized)
        self.consumed_budget += 1
        self.pending_observation = True
        params = {
            axis.name: axis.denormalize(value)
            for axis, value in zip(self.axes, normalized)
        }
        self.pending_duplicate = _signature(self.axes, params) in self.seen_signatures
        return params

    def observe(self, *, accepted: bool) -> None:
        if self.pending_duplicate:
            raise RuntimeError("a duplicate proposal must be recorded as duplicate")
        self.record_outcome("outer_accepted" if accepted else "outer_rejected")

    def record_outcome(self, outcome: str) -> None:
        if outcome not in TERMINAL_OUTCOMES:
            raise ValueError(f"unsupported random-walk outcome: {outcome}")
        if (
            not self.pending_observation
            or not self.proposals
            or len(self.proposals) != self.consumed_budget
        ):
            raise RuntimeError("observe requires exactly one outstanding durable proposal")
        if outcome == "duplicate" and not self.pending_duplicate:
            raise ValueError("a non-duplicate proposal cannot be recorded as duplicate")
        if self.pending_duplicate and outcome != "duplicate":
            raise ValueError("a duplicate proposal must be recorded as duplicate")
        accepted = outcome == "outer_accepted"
        if accepted:
            self.incumbent = list(self.proposals[-1])
            self.consecutive_rejections = 0
            self.sigma = min(self.sigma_maximum, self.sigma * self.sigma_accept_multiplier)
        else:
            self.consecutive_rejections += 1
            self.sigma = max(self.sigma_minimum, self.sigma * self.sigma_reject_multiplier)
        params = {
            axis.name: axis.denormalize(value)
            for axis, value in zip(self.axes, self.proposals[-1])
        }
        self.seen_signatures.add(_signature(self.axes, params))
        self.pending_observation = False
        self.pending_duplicate = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "rng_implementation": "python_random_mt19937_gauss",
            "axes": [axis.name for axis in self.axes],
            "seed": self.seed,
            "sigma": self.sigma,
            "incumbent_normalized": list(self.incumbent),
            "maximum_budget": self.maximum_budget,
            "minimum_before_stop": self.minimum_before_stop,
            "rejection_stop": self.rejection_stop,
            "consumed_budget": self.consumed_budget,
            "consecutive_rejections": self.consecutive_rejections,
            "pending_observation": self.pending_observation,
            "pending_duplicate": self.pending_duplicate,
            "seen_signatures": sorted(self.seen_signatures),
            "proposals_normalized": [list(row) for row in self.proposals],
            "rng_state": self._rng.getstate(),
        }

    @classmethod
    def restore(
        cls,
        payload: Mapping[str, Any],
        axes: Sequence[ParameterAxis],
    ) -> "RandomWalkState":
        if payload.get("schema_version") != 1:
            raise ValueError("unsupported random-walk state schema")
        if payload.get("rng_implementation") != "python_random_mt19937_gauss":
            raise ValueError("random-walk RNG implementation mismatch")
        axis_tuple = tuple(axes)
        if payload.get("axes") != [axis.name for axis in axis_tuple]:
            raise ValueError("random-walk state axes mismatch")
        state = cls(
            axes=axis_tuple,
            incumbent=[float(value) for value in payload["incumbent_normalized"]],
            seed=int(payload["seed"]),
            sigma=float(payload["sigma"]),
            maximum_budget=int(payload["maximum_budget"]),
            minimum_before_stop=int(payload["minimum_before_stop"]),
            rejection_stop=int(payload["rejection_stop"]),
            consumed_budget=int(payload["consumed_budget"]),
            consecutive_rejections=int(payload["consecutive_rejections"]),
            pending_observation=bool(payload["pending_observation"]),
            pending_duplicate=bool(payload["pending_duplicate"]),
            seen_signatures={str(value) for value in payload["seen_signatures"]},
            proposals=[
                [float(value) for value in row]
                for row in payload["proposals_normalized"]
            ],
        )
        if len(state.proposals) != state.consumed_budget:
            raise ValueError("proposal count and consumed budget differ")
        state._rng.setstate(_tuple_tree(payload["rng_state"]))
        return state

    @classmethod
    def restore_frozen(
        cls,
        payload: Mapping[str, Any],
        search_space: Mapping[str, Any],
        method_protocols: Mapping[str, Any],
    ) -> "RandomWalkState":
        """Restore legacy durable state and re-bind immutable method settings."""

        frozen = cls.from_frozen(search_space, method_protocols)
        state = cls.restore(payload, frozen.axes)
        immutable_actual = {
            "seed": state.seed,
            "maximum_budget": state.maximum_budget,
            "minimum_before_stop": state.minimum_before_stop,
            "rejection_stop": state.rejection_stop,
            "sigma_accept_multiplier": state.sigma_accept_multiplier,
            "sigma_reject_multiplier": state.sigma_reject_multiplier,
            "sigma_minimum": state.sigma_minimum,
            "sigma_maximum": state.sigma_maximum,
        }
        immutable_expected = {
            "seed": frozen.seed,
            "maximum_budget": frozen.maximum_budget,
            "minimum_before_stop": frozen.minimum_before_stop,
            "rejection_stop": frozen.rejection_stop,
            "sigma_accept_multiplier": frozen.sigma_accept_multiplier,
            "sigma_reject_multiplier": frozen.sigma_reject_multiplier,
            "sigma_minimum": frozen.sigma_minimum,
            "sigma_maximum": frozen.sigma_maximum,
        }
        if immutable_actual != immutable_expected:
            raise ValueError(
                "durable random-walk state differs from frozen method: "
                f"actual={immutable_actual} expected={immutable_expected}"
            )
        if not state.sigma_minimum <= state.sigma <= state.sigma_maximum:
            raise ValueError("durable random-walk sigma is outside frozen bounds")
        return state


def generate_subset_grid_random_walk_candidates(**kwargs: Any) -> Any:
    """Generate the current random-walk candidate set with the shared engine.

    The accepted keyword arguments are exactly those of
    :func:`experience_table_subset_optimizer.optimize_all_experience_table_subsets`.
    Keeping this as a direct delegation guarantees that random-walk and
    experience-table runs enumerate the same ``2**k-1`` subsets, evaluate the
    same ``2**|S|`` points for each subset, apply the same exact deduplication and
    return the same ranked subset winners.
    """
    from experience_table_subset_optimizer import optimize_all_experience_table_subsets

    result = optimize_all_experience_table_subsets(**kwargs)
    setattr(result.best_result, "method", "random_walk")
    setattr(
        result.best_result,
        "candidate_generator",
        "all_nonempty_subsets_two_center_cartesian_resource_grid_v1",
    )
    return result


def run_subset_grid_random_walk(
    config: Mapping[str, Any],
    *,
    client: Any = None,
    real_replay_callback: Any = None,
    baseline_validation: Mapping[str, Any] | None = None,
) -> Any:
    """Run the shared MySQL candidate, pruning and Top-5/10 replay pipeline.

    This is the formal current ``random_walk`` entry.  It calls the same
    ``mysql_parameter_optimizer.run_optimization`` function used by
    ``experience_table_sgd`` and changes only the method label written to logs.
    """
    from mysql_parameter_optimizer import run_optimization

    return run_optimization(
        config,
        client=client,
        real_replay_callback=real_replay_callback,
        baseline_validation=baseline_validation,
        method_id="random_walk",
    )

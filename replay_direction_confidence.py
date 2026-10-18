"""Conservative direction calibration from real benchmark replay evidence.

The module is intentionally independent from the optimizer runtime.  It never
changes parameters or mutates an experience table.  A caller supplies replay
outcomes relative to one incumbent and asks whether an experience-table
direction should be allowed, forbidden, or reversed.

Only the joint sign objective is a success:

* ``db_qps_delta_pct`` is strictly positive; and
* ``target_util_delta_pp`` is strictly negative.

By default, a replay is attributable to parameter ``p`` only when ``p`` owns at
least 80% of the absolute log movement in that replay.  This deliberately
excludes heavily confounded multi-parameter candidates.  A direction becomes
strong only with enough attributable samples and a sufficiently high one-sided
Wilson lower confidence bound.  Sparse evidence therefore forbids a strong
direction instead of guessing.

The separate full-vector recovery gate uses cosine alignment against the
current experience-table direction.  Repeated, statistically confident joint
failures can authorize only a conservative reverse probe; they never prove
that the probe should be accepted.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import statistics
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


ACTION_ALLOW = "allow"
ACTION_FORBID = "forbid"
ACTION_REVERSE = "reverse"
ACTION_REVERSE_PROBE = "reverse_probe"


@dataclass(frozen=True)
class ReplayDirectionSample:
    """One real replay outcome measured against the same incumbent."""

    parameter_log_delta: Mapping[str, float]
    db_qps_delta_pct: float
    target_util_delta_pp: float
    sample_id: Optional[str] = None


@dataclass(frozen=True)
class DirectionConfidenceConfig:
    """Fail-closed thresholds for direction evidence."""

    min_samples_per_direction: int = 4
    min_joint_successes: int = 3
    confidence_z: float = 1.2815515655446004  # one-sided 90%
    min_wilson_lower_bound: float = 0.60
    min_abs_log_delta: float = 1e-6
    min_focal_movement_share: float = 0.80
    min_db_qps_gain_pct: float = 0.0
    min_target_util_drop_pp: float = 0.0

    def validate(self) -> None:
        if self.min_samples_per_direction <= 0:
            raise ValueError("min_samples_per_direction must be positive")
        if self.min_joint_successes <= 0:
            raise ValueError("min_joint_successes must be positive")
        if self.min_joint_successes > self.min_samples_per_direction:
            raise ValueError(
                "min_joint_successes cannot exceed min_samples_per_direction"
            )
        if not math.isfinite(self.confidence_z) or self.confidence_z <= 0.0:
            raise ValueError("confidence_z must be finite and positive")
        if not 0.0 <= self.min_wilson_lower_bound <= 1.0:
            raise ValueError("min_wilson_lower_bound must be in [0,1]")
        if not math.isfinite(self.min_abs_log_delta) or self.min_abs_log_delta <= 0.0:
            raise ValueError("min_abs_log_delta must be finite and positive")
        if not 0.0 < self.min_focal_movement_share <= 1.0:
            raise ValueError("min_focal_movement_share must be in (0,1]")
        if not math.isfinite(self.min_db_qps_gain_pct):
            raise ValueError("min_db_qps_gain_pct must be finite")
        if self.min_db_qps_gain_pct < 0.0:
            raise ValueError("min_db_qps_gain_pct cannot be negative")
        if not math.isfinite(self.min_target_util_drop_pp):
            raise ValueError("min_target_util_drop_pp must be finite")
        if self.min_target_util_drop_pp < 0.0:
            raise ValueError("min_target_util_drop_pp cannot be negative")


@dataclass(frozen=True)
class DirectionEvidence:
    """Auditable evidence for one parameter sign (+1 or -1)."""

    direction: int
    sample_count: int
    joint_success_count: int
    joint_success_rate: float
    joint_success_wilson_lower: float
    qps_positive_count: int
    qps_positive_wilson_lower: float
    target_down_count: int
    target_down_wilson_lower: float
    median_db_qps_delta_pct: Optional[float]
    median_target_util_delta_pp: Optional[float]
    median_abs_log_delta: Optional[float]
    sample_ids: Sequence[str]
    supported: bool
    support_failures: Sequence[str]


@dataclass(frozen=True)
class DirectionDecision:
    """Decision for one experience-table direction."""

    parameter: str
    proposed_direction: int
    action: str
    recommended_direction: int
    reason: str
    proposed_evidence: DirectionEvidence
    opposite_evidence: DirectionEvidence
    ignored_sample_count: int
    ignored_reason_counts: Mapping[str, int]

    @property
    def allowed(self) -> bool:
        return self.action == ACTION_ALLOW

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class VectorFailureRecoveryConfig:
    """Thresholds for a probe-only reversal of the whole direction vector."""

    min_aligned_samples: int = 4
    min_cosine_alignment: float = 0.80
    confidence_z: float = 1.2815515655446004  # one-sided 90%
    min_failure_wilson_lower_bound: float = 0.60
    min_vector_norm: float = 1e-12
    eta_multiplier_floor: float = 0.10
    eta_multiplier_cap: float = 0.25

    def validate(self) -> None:
        if self.min_aligned_samples <= 0:
            raise ValueError("min_aligned_samples must be positive")
        if not -1.0 <= self.min_cosine_alignment <= 1.0:
            raise ValueError("min_cosine_alignment must be in [-1,1]")
        if not math.isfinite(self.confidence_z) or self.confidence_z <= 0.0:
            raise ValueError("confidence_z must be finite and positive")
        if not 0.0 <= self.min_failure_wilson_lower_bound <= 1.0:
            raise ValueError("min_failure_wilson_lower_bound must be in [0,1]")
        if not math.isfinite(self.min_vector_norm) or self.min_vector_norm <= 0.0:
            raise ValueError("min_vector_norm must be finite and positive")
        if not 0.0 < self.eta_multiplier_floor <= self.eta_multiplier_cap <= 1.0:
            raise ValueError(
                "eta multipliers must satisfy 0 < floor <= cap <= 1"
            )


@dataclass(frozen=True)
class VectorFailureRecoveryDecision:
    """Probe-only recovery decision for one complete experience direction."""

    action: str
    reason: str
    probe_only: bool
    acceptance_proven: bool
    aligned_sample_count: int
    joint_failure_count: int
    joint_failure_rate: float
    joint_failure_wilson_lower: float
    min_failure_wilson_lower_bound: float
    median_cosine_alignment: Optional[float]
    aligned_sample_ids: Sequence[str]
    ignored_low_alignment_count: int
    reverse_probe_direction_vector: Mapping[str, float]
    eta_multiplier: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _finite_float(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return result


def wilson_lower_bound(successes: int, total: int, z: float) -> float:
    """Return the one-sided Wilson lower bound for a Bernoulli proportion."""

    if total < 0 or successes < 0 or successes > total:
        raise ValueError("invalid Wilson successes/total")
    if not math.isfinite(z) or z <= 0.0:
        raise ValueError("Wilson z must be finite and positive")
    if total == 0:
        return 0.0
    proportion = successes / total
    z_squared = z * z
    denominator = 1.0 + z_squared / total
    centre = proportion + z_squared / (2.0 * total)
    radius = z * math.sqrt(
        proportion * (1.0 - proportion) / total
        + z_squared / (4.0 * total * total)
    )
    return max(0.0, (centre - radius) / denominator)


def _normalized_sample(sample: ReplayDirectionSample) -> ReplayDirectionSample:
    if not isinstance(sample.parameter_log_delta, Mapping):
        raise ValueError("parameter_log_delta must be a mapping")
    deltas = {
        str(name): _finite_float(value, f"parameter_log_delta[{name!r}]")
        for name, value in sample.parameter_log_delta.items()
    }
    if not deltas:
        raise ValueError("parameter_log_delta cannot be empty")
    return ReplayDirectionSample(
        parameter_log_delta=deltas,
        db_qps_delta_pct=_finite_float(
            sample.db_qps_delta_pct,
            "db_qps_delta_pct",
        ),
        target_util_delta_pp=_finite_float(
            sample.target_util_delta_pp,
            "target_util_delta_pp",
        ),
        sample_id=None if sample.sample_id is None else str(sample.sample_id),
    )


def _normalize_samples(
    samples: Sequence[ReplayDirectionSample],
) -> List[ReplayDirectionSample]:
    normalized = [_normalized_sample(sample) for sample in samples]
    explicit_sample_ids = [
        sample.sample_id for sample in normalized if sample.sample_id is not None
    ]
    if len(explicit_sample_ids) != len(set(explicit_sample_ids)):
        raise ValueError("explicit replay sample_id values must be unique")
    return normalized


def _direction_evidence(
    parameter: str,
    direction: int,
    samples: Sequence[ReplayDirectionSample],
    config: DirectionConfidenceConfig,
) -> Tuple[DirectionEvidence, Dict[str, int]]:
    if direction not in {-1, 1}:
        raise ValueError("direction must be -1 or +1")
    eligible: List[ReplayDirectionSample] = []
    eligible_abs_delta: List[float] = []
    ignored: Dict[str, int] = {
        "parameter_missing_or_zero": 0,
        "opposite_direction": 0,
        "confounded_low_focal_share": 0,
    }
    for sample in samples:
        focal_delta = float(sample.parameter_log_delta.get(parameter, 0.0))
        if abs(focal_delta) < config.min_abs_log_delta:
            ignored["parameter_missing_or_zero"] += 1
            continue
        observed_direction = 1 if focal_delta > 0.0 else -1
        if observed_direction != direction:
            ignored["opposite_direction"] += 1
            continue
        total_movement = sum(
            abs(float(value)) for value in sample.parameter_log_delta.values()
        )
        focal_share = abs(focal_delta) / total_movement if total_movement > 0.0 else 0.0
        if focal_share + 1e-15 < config.min_focal_movement_share:
            ignored["confounded_low_focal_share"] += 1
            continue
        eligible.append(sample)
        eligible_abs_delta.append(abs(focal_delta))

    qps_successes = [
        sample.db_qps_delta_pct > config.min_db_qps_gain_pct
        for sample in eligible
    ]
    target_successes = [
        sample.target_util_delta_pp < -config.min_target_util_drop_pp
        for sample in eligible
    ]
    joint_successes = [
        qps_ok and target_ok
        for qps_ok, target_ok in zip(qps_successes, target_successes)
    ]
    total = len(eligible)
    joint_count = sum(joint_successes)
    qps_count = sum(qps_successes)
    target_count = sum(target_successes)
    joint_lower = wilson_lower_bound(joint_count, total, config.confidence_z)
    qps_lower = wilson_lower_bound(qps_count, total, config.confidence_z)
    target_lower = wilson_lower_bound(target_count, total, config.confidence_z)
    median_qps = (
        float(statistics.median(sample.db_qps_delta_pct for sample in eligible))
        if eligible
        else None
    )
    median_target = (
        float(statistics.median(sample.target_util_delta_pp for sample in eligible))
        if eligible
        else None
    )
    median_abs_delta = (
        float(statistics.median(eligible_abs_delta))
        if eligible_abs_delta
        else None
    )
    failures: List[str] = []
    if total < config.min_samples_per_direction:
        failures.append("insufficient_samples")
    if joint_count < config.min_joint_successes:
        failures.append("insufficient_joint_successes")
    if joint_lower < config.min_wilson_lower_bound:
        failures.append("joint_confidence_below_threshold")
    if qps_lower < config.min_wilson_lower_bound:
        failures.append("qps_sign_confidence_below_threshold")
    if target_lower < config.min_wilson_lower_bound:
        failures.append("target_sign_confidence_below_threshold")
    if median_qps is None or median_qps <= config.min_db_qps_gain_pct:
        failures.append("median_qps_not_positive")
    if median_target is None or median_target >= -config.min_target_util_drop_pp:
        failures.append("median_target_not_down")

    evidence = DirectionEvidence(
        direction=direction,
        sample_count=total,
        joint_success_count=joint_count,
        joint_success_rate=(joint_count / total if total else 0.0),
        joint_success_wilson_lower=joint_lower,
        qps_positive_count=qps_count,
        qps_positive_wilson_lower=qps_lower,
        target_down_count=target_count,
        target_down_wilson_lower=target_lower,
        median_db_qps_delta_pct=median_qps,
        median_target_util_delta_pp=median_target,
        median_abs_log_delta=median_abs_delta,
        sample_ids=tuple(
            sample.sample_id or f"sample_{index}"
            for index, sample in enumerate(eligible, start=1)
        ),
        supported=not failures,
        support_failures=tuple(failures),
    )
    return evidence, ignored


def _fully_ignored_reasons(
    parameter: str,
    samples: Sequence[ReplayDirectionSample],
    config: DirectionConfidenceConfig,
) -> Dict[str, int]:
    reasons = {
        "parameter_missing_or_zero": 0,
        "confounded_low_focal_share": 0,
    }
    for sample in samples:
        focal_delta = float(sample.parameter_log_delta.get(parameter, 0.0))
        if abs(focal_delta) < config.min_abs_log_delta:
            reasons["parameter_missing_or_zero"] += 1
            continue
        total_movement = sum(
            abs(float(value)) for value in sample.parameter_log_delta.values()
        )
        focal_share = abs(focal_delta) / total_movement if total_movement > 0.0 else 0.0
        if focal_share + 1e-15 < config.min_focal_movement_share:
            reasons["confounded_low_focal_share"] += 1
    return reasons


def calibrate_replay_directions(
    samples: Sequence[ReplayDirectionSample],
    proposed_directions: Mapping[str, int],
    *,
    config: Optional[DirectionConfidenceConfig] = None,
) -> Dict[str, DirectionDecision]:
    """Calibrate experience-table signs using real replay outcomes.

    Args:
        samples: Real replay outcomes, all measured against the same incumbent.
        proposed_directions: Experience-table signs by parameter (only -1/+1).
        config: Optional conservative evidence thresholds.

    Returns:
        One decision per proposed parameter:

        * ``allow``: the proposed sign has strong joint evidence;
        * ``reverse``: the proposed sign lacks support, while the opposite sign
          independently has strong evidence;
        * ``forbid``: neither conclusion is safe, including sparse evidence.

    Raises:
        ValueError: malformed/non-finite evidence or invalid directions.
    """

    cfg = config or DirectionConfidenceConfig()
    cfg.validate()
    normalized_samples = _normalize_samples(samples)
    if not isinstance(proposed_directions, Mapping) or not proposed_directions:
        raise ValueError("proposed_directions must be a non-empty mapping")

    decisions: Dict[str, DirectionDecision] = {}
    for raw_parameter, raw_direction in proposed_directions.items():
        parameter = str(raw_parameter)
        try:
            proposed = int(raw_direction)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"proposed direction for {parameter} must be -1 or +1"
            ) from exc
        if proposed not in {-1, 1} or float(raw_direction) != float(proposed):
            raise ValueError(
                f"proposed direction for {parameter} must be -1 or +1"
            )
        proposed_evidence, proposed_ignored = _direction_evidence(
            parameter,
            proposed,
            normalized_samples,
            cfg,
        )
        opposite_evidence, opposite_ignored = _direction_evidence(
            parameter,
            -proposed,
            normalized_samples,
            cfg,
        )
        _ = proposed_ignored, opposite_ignored
        ignored_reason_counts = _fully_ignored_reasons(
            parameter,
            normalized_samples,
            cfg,
        )
        observed_count = (
            proposed_evidence.sample_count + opposite_evidence.sample_count
        )
        ignored_count = max(0, len(normalized_samples) - observed_count)
        if proposed_evidence.supported:
            action = ACTION_ALLOW
            recommended = proposed
            reason = "proposed_direction_has_confident_joint_sign_support"
        elif opposite_evidence.supported:
            action = ACTION_REVERSE
            recommended = -proposed
            reason = "opposite_direction_has_confident_joint_sign_support"
        else:
            action = ACTION_FORBID
            recommended = 0
            reason = "no_direction_has_sufficient_confident_joint_sign_support"
        decisions[parameter] = DirectionDecision(
            parameter=parameter,
            proposed_direction=proposed,
            action=action,
            recommended_direction=recommended,
            reason=reason,
            proposed_evidence=proposed_evidence,
            opposite_evidence=opposite_evidence,
            ignored_sample_count=ignored_count,
            ignored_reason_counts=ignored_reason_counts,
        )
    return decisions


def _cosine_similarity(
    left: Mapping[str, float],
    right: Mapping[str, float],
    *,
    min_norm: float,
) -> float:
    names = set(left) | set(right)
    left_norm = math.sqrt(sum(float(left.get(name, 0.0)) ** 2 for name in names))
    right_norm = math.sqrt(sum(float(right.get(name, 0.0)) ** 2 for name in names))
    if left_norm < min_norm or right_norm < min_norm:
        raise ValueError("cosine direction vectors must have non-zero finite norm")
    dot = sum(
        float(left.get(name, 0.0)) * float(right.get(name, 0.0))
        for name in names
    )
    return max(-1.0, min(1.0, dot / (left_norm * right_norm)))


def calibrate_vector_failure_recovery(
    samples: Sequence[ReplayDirectionSample],
    experience_direction_vector: Mapping[str, float],
    *,
    config: Optional[VectorFailureRecoveryConfig] = None,
) -> VectorFailureRecoveryDecision:
    """Decide whether repeated aligned failures justify a reverse probe.

    This is a recovery mechanism, not an acceptance decision.  Only samples
    whose complete log-delta vector has sufficiently high cosine similarity to
    the current experience-table direction are counted.  A counted replay is a
    joint failure when QPS is non-positive *or* target utilization does not go
    down.  Four or more aligned samples and a strong Wilson lower bound on that
    failure probability produce ``reverse_probe`` with a conservative eta
    multiplier.  Every other case returns ``forbid`` and eta zero.
    """

    cfg = config or VectorFailureRecoveryConfig()
    cfg.validate()
    if not isinstance(experience_direction_vector, Mapping) or not (
        experience_direction_vector
    ):
        raise ValueError("experience_direction_vector must be a non-empty mapping")
    direction = {
        str(name): _finite_float(
            value,
            f"experience_direction_vector[{name!r}]",
        )
        for name, value in experience_direction_vector.items()
    }
    direction_norm = math.sqrt(sum(value * value for value in direction.values()))
    if direction_norm < cfg.min_vector_norm:
        raise ValueError("experience_direction_vector must have non-zero finite norm")
    normalized_samples = _normalize_samples(samples)

    aligned: List[ReplayDirectionSample] = []
    alignments: List[float] = []
    for sample in normalized_samples:
        cosine = _cosine_similarity(
            sample.parameter_log_delta,
            direction,
            min_norm=cfg.min_vector_norm,
        )
        if cosine + 1e-15 < cfg.min_cosine_alignment:
            continue
        aligned.append(sample)
        alignments.append(cosine)

    failure_flags = [
        sample.db_qps_delta_pct <= 0.0 or sample.target_util_delta_pp >= 0.0
        for sample in aligned
    ]
    aligned_count = len(aligned)
    failure_count = sum(failure_flags)
    failure_lower = wilson_lower_bound(
        failure_count,
        aligned_count,
        cfg.confidence_z,
    )
    median_alignment = (
        float(statistics.median(alignments)) if alignments else None
    )
    enough_samples = aligned_count >= cfg.min_aligned_samples
    confident_failure = failure_lower >= cfg.min_failure_wilson_lower_bound
    trigger = bool(enough_samples and confident_failure)
    if trigger:
        confidence_span = max(
            1e-12,
            1.0 - cfg.min_failure_wilson_lower_bound,
        )
        confidence_excess = min(
            1.0,
            max(
                0.0,
                (
                    failure_lower - cfg.min_failure_wilson_lower_bound
                ) / confidence_span,
            ),
        )
        alignment_strength = max(0.0, float(median_alignment or 0.0))
        eta_multiplier = min(
            cfg.eta_multiplier_cap,
            cfg.eta_multiplier_floor
            + (cfg.eta_multiplier_cap - cfg.eta_multiplier_floor)
            * confidence_excess
            * alignment_strength,
        )
        action = ACTION_REVERSE_PROBE
        reason = "aligned_real_replays_confidently_fail_joint_objective"
        reverse_vector = {name: -value for name, value in direction.items()}
    else:
        eta_multiplier = 0.0
        action = ACTION_FORBID
        reason = (
            "insufficient_high_alignment_samples"
            if not enough_samples
            else "aligned_joint_failure_confidence_below_threshold"
        )
        reverse_vector = {}

    return VectorFailureRecoveryDecision(
        action=action,
        reason=reason,
        probe_only=True,
        acceptance_proven=False,
        aligned_sample_count=aligned_count,
        joint_failure_count=failure_count,
        joint_failure_rate=(failure_count / aligned_count if aligned_count else 0.0),
        joint_failure_wilson_lower=failure_lower,
        min_failure_wilson_lower_bound=cfg.min_failure_wilson_lower_bound,
        median_cosine_alignment=median_alignment,
        aligned_sample_ids=tuple(
            sample.sample_id or f"sample_{index}"
            for index, sample in enumerate(aligned, start=1)
        ),
        ignored_low_alignment_count=len(normalized_samples) - aligned_count,
        reverse_probe_direction_vector=reverse_vector,
        eta_multiplier=eta_multiplier,
    )


__all__ = [
    "ACTION_ALLOW",
    "ACTION_FORBID",
    "ACTION_REVERSE",
    "ACTION_REVERSE_PROBE",
    "DirectionConfidenceConfig",
    "DirectionDecision",
    "DirectionEvidence",
    "ReplayDirectionSample",
    "VectorFailureRecoveryConfig",
    "VectorFailureRecoveryDecision",
    "calibrate_replay_directions",
    "calibrate_vector_failure_recovery",
    "wilson_lower_bound",
]

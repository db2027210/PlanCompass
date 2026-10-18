"""Module."""


from __future__ import annotations

import copy
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ReplayLane(str, Enum):
    """ReplayLane."""


    EXPLAINABLE = "explainable"
    """conservative bound"""

    FALLBACK = "fallback"
    """DB QPS proxy  DB-QPS """

    DIAGNOSTIC = "diagnostic"
    """ / plateau /  long-term-goal hit"""

    MANDATORY_ANCHOR = "mandatory_anchor"
    """ global best / forced anchor explainable """

    RESERVE = "reserve"
    """ optimistic upper bound  best """


class AdaptiveReplayDecision(str, Enum):
    """AdaptiveReplayDecision."""


    STOP_FIX = "STOP_FIX"
    """safety/model hard gate  explainable lane=0  pairwise inversion>0.10"""

    STOP_FIX_RANKING = "STOP_FIX_RANKING"
    """I2==0  I4>0.10explainable lane  ranking  ranking/error-bound/model"""

    STOP_EARLY_KEEP_BEST = "STOP_EARLY_KEEP_BEST"
    """ upper bound  best  ranking  max_budget"""

    CONTINUE_NEXT_BATCH = "CONTINUE_NEXT_BATCH"
    """ conservative-pass  replay  ranking """

    EXPAND_DIAGNOSTIC = "EXPAND_DIAGNOSTIC"
    """I2==0  I4<=0.10ranking  plateau  explainable """

    EXPAND_ONE_BATCH = "EXPAND_ONE_BATCH"
    """budget  optimistic upper bound  best 2 """

    PROCEED_TO_OUTER_LOOP = "PROCEED_TO_OUTER_LOOP"
    """I2>=3  I3>=5%  I7>=3  I4<=0.10 score tuning """


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class LaneQuotaConfig:
    """LaneQuotaConfig."""


    max_budget: int = 24
    """"""

    min_evidence: int = 12
    """ evidence lane  count-fill"""

    batch_size: int = 6
    """"""

    max_expansions: int = 2
    """ EXPAND_ONE_BATCH  +6"""

    explainable: int = 7
    fallback: int = 5
    diagnostic: int = 5
    mandatory_anchor: int = 2
    reserve: int = 5

    def total_lane_quota(self) -> int:
        """total lane quota."""
        return (
            self.explainable
            + self.fallback
            + self.diagnostic
            + self.mandatory_anchor
        )

    def quota_for(self, lane: ReplayLane) -> int:
        return {
            ReplayLane.EXPLAINABLE: self.explainable,
            ReplayLane.FALLBACK: self.fallback,
            ReplayLane.DIAGNOSTIC: self.diagnostic,
            ReplayLane.MANDATORY_ANCHOR: self.mandatory_anchor,
            ReplayLane.RESERVE: self.reserve,
        }[lane]


@dataclass
class BatchInputs:
    """BatchInputs."""


    # I1: safety/model hard gates
    safety_model_gate_passed: bool
    io_source_valid: bool
    io_stable_sample_count_positive: bool

    # I2: explainable lane conservative-pass count (cumulative this round)
    explainable_conservative_pass_count: int

    # I3: DB-QPS fallback best Δ%
    fallback_best_db_qps_delta_pct: Optional[float]

    # I4: pairwise inversion rate (batch-internal or cumulative)
    pairwise_inversion_rate: float

    # I5: remaining optimistic upper bound > current best
    remaining_upper_bound_beats_best: bool

    # I6: ranking stability (top-3 in each lane stable across last 2 batches)
    ranking_stable: bool

    # I7: cumulative Memory-down evidence count (unique plan signatures)
    cumulative_target_down_evidence_count: int

    # --- batch / round state ---
    batch_index: int
    """0-indexed"""

    batches_so_far: int
    """"""

    replayed_count: int
    """"""

    expansions_used: int
    """ EXPAND_ONE_BATCH """

    # --- lane counts (cumulative this round, including current batch) ---
    lane_counts: Dict[str, int] = field(default_factory=dict)
    """ lane """

    # --- thresholds ---
    pairwise_inversion_threshold: float = 0.10
    min_explainable_for_proceed: int = 3
    min_fallback_delta_pct_for_proceed: float = 5.0
    min_target_down_evidence_for_proceed: int = 3
    min_target_down_evidence_for_early_stop: int = 3
    max_consecutive_batches_no_improvement: int = 2


@dataclass
class BatchDecision:
    """BatchDecision."""


    decision: AdaptiveReplayDecision
    reason: str
    """Reason"""

    inputs_snapshot: Dict[str, Any]
    """ JSONL """

    remaining_upper_bound_beats_best: bool
    ranking_stable: bool
    pairwise_inversion_rate: float
    pairwise_inversion_estimate_upper: Optional[float] = None
    """pairwise inversion """

    lane_counts: Dict[str, int] = field(default_factory=dict)
    """ lane """

    expansion_delta: int = 0
    """ EXPAND_*"""

    fallback_best_record: Optional[Dict[str, Any]] = None
    """ DB-QPS fallback best cand19 """

    explainable_lane_empty: bool = False
    """explainable lane  emit count-fill"""

    def to_jsonl_record(self) -> Dict[str, Any]:
        """to jsonl record."""
        return {
            "event": "adaptive_replay_decision",
            "decision": self.decision.value,
            "reason": self.reason,
            "inputs_snapshot": self.inputs_snapshot,
            "remaining_upper_bound_beats_best": self.remaining_upper_bound_beats_best,
            "ranking_stable": self.ranking_stable,
            "pairwise_inversion_rate": self.pairwise_inversion_rate,
            "pairwise_inversion_estimate_upper": self.pairwise_inversion_estimate_upper,
            "lane_counts": dict(self.lane_counts),
            "expansion_delta": self.expansion_delta,
            "fallback_best_record": self.fallback_best_record,
            "explainable_lane_empty": self.explainable_lane_empty,
        }


# ---------------------------------------------------------------------------
# Lane classification helpers
# ---------------------------------------------------------------------------


def classify_candidate_lane(
    candidate: Dict[str, Any],
    *,
    target_resource: str,
    baseline_resource: Dict[str, float],
    capability: Dict[str, float],
    min_delta_util_pct: float = 0.50,
    min_other_up_util_pct: float = 0.50,
    is_mandatory_anchor: bool = False,
    is_forced_anchor: bool = False,
    conservative_error_bound_pp: Optional[float] = None,
    db_qps_proxy_delta_pct: Optional[float] = None,
    plateau_predicted_values: Optional[Sequence[float]] = None,
    plateau_tolerance_pp: float = 0.40,
) -> ReplayLane:
    """classify candidate lane."""


    if is_mandatory_anchor or is_forced_anchor:
        return ReplayLane.MANDATORY_ANCHOR

    predicted = candidate.get("predicted_resource") or {}
    if not predicted:

        if db_qps_proxy_delta_pct is not None and db_qps_proxy_delta_pct >= 3.0:
            return ReplayLane.FALLBACK
        return ReplayLane.DIAGNOSTIC

    target_set = {target_resource}
    deltas_pp: Dict[str, float] = {}
    for name in ("cpu", "memory", "io"):
        if name not in capability or name not in baseline_resource:
            continue
        cap = float(capability.get(name, 0.0)) or 1.0
        base = float(baseline_resource.get(name, 0.0))
        pred = float(predicted.get(name, base))
        deltas_pp[name] = (pred - base) / cap * 100.0

    target_delta = deltas_pp.get(target_resource, 0.0)
    other_deltas = [v for n, v in deltas_pp.items() if n not in target_set]
    best_other_up = max(other_deltas) if other_deltas else float("-inf")

    if target_resource not in deltas_pp:
        # No-bottleneck: at least one resource must move upward toward its sweet zone.
        if best_other_up > min_other_up_util_pct:
            return ReplayLane.EXPLAINABLE
        if db_qps_proxy_delta_pct is not None and db_qps_proxy_delta_pct >= 3.0:
            return ReplayLane.FALLBACK
        return ReplayLane.DIAGNOSTIC


    if conservative_error_bound_pp is not None:
        conservative_target_down = max(0.0, -target_delta) - conservative_error_bound_pp
    else:
        conservative_target_down = max(0.0, -target_delta)

    if (
        conservative_target_down >= min_delta_util_pct
        and best_other_up > min_other_up_util_pct
    ):
        return ReplayLane.EXPLAINABLE


    if plateau_predicted_values is not None:
        pred_target = float(predicted.get(target_resource, 0.0))
        cap = float(capability.get(target_resource, 1.0)) or 1.0
        base = float(baseline_resource.get(target_resource, 0.0))
        pred_target_util_pp = (pred_target - base) / cap * 100.0 if cap > 0 else 0.0
        for plateau_val in plateau_predicted_values:
            plateau_util_pp = (float(plateau_val) - base) / cap * 100.0 if cap > 0 else 0.0
            if abs(pred_target_util_pp - plateau_util_pp) <= plateau_tolerance_pp:
                return ReplayLane.DIAGNOSTIC


    if db_qps_proxy_delta_pct is not None and db_qps_proxy_delta_pct >= 3.0:
        return ReplayLane.FALLBACK


    return ReplayLane.DIAGNOSTIC


def assign_lanes(
    candidates: List[Dict[str, Any]],
    *,
    target_resource: str,
    baseline_resource: Dict[str, float],
    capability: Dict[str, float],
    mandatory_anchor_indices: Sequence[int],
    forced_anchor_indices: Sequence[int],
    min_delta_util_pct: float = 0.50,
    min_other_up_util_pct: float = 0.50,
    conservative_error_bound_pp: Optional[float] = None,
    db_qps_proxy_delta_pct_field: str = "predicted_db_qps_delta_pct",
    plateau_predicted_values: Optional[Sequence[float]] = None,
    plateau_tolerance_pp: float = 0.40,
) -> Dict[str, List[int]]:
    """assign lanes."""


    mandatory_set = set(mandatory_anchor_indices)
    forced_set = set(forced_anchor_indices)
    lanes: Dict[str, List[int]] = {lane.value: [] for lane in ReplayLane}
    for idx, candidate in enumerate(candidates):
        forced_lane_raw = candidate.get("_adaptive_replay_forced_lane")
        if forced_lane_raw is not None:
            try:
                forced_lane = ReplayLane(str(forced_lane_raw))
            except ValueError as exc:
                raise ValueError(
                    f"invalid _adaptive_replay_forced_lane={forced_lane_raw!r}"
                ) from exc
            lanes[forced_lane.value].append(idx)
            continue
        db_qps_delta = candidate.get(db_qps_proxy_delta_pct_field)
        db_qps_delta_f = float(db_qps_delta) if db_qps_delta is not None else None
        lane = classify_candidate_lane(
            candidate,
            target_resource=target_resource,
            baseline_resource=baseline_resource,
            capability=capability,
            min_delta_util_pct=min_delta_util_pct,
            min_other_up_util_pct=min_other_up_util_pct,
            is_mandatory_anchor=idx in mandatory_set,
            is_forced_anchor=idx in forced_set,
            conservative_error_bound_pp=conservative_error_bound_pp,
            db_qps_proxy_delta_pct=db_qps_delta_f,
            plateau_predicted_values=plateau_predicted_values,
            plateau_tolerance_pp=plateau_tolerance_pp,
        )
        lanes[lane.value].append(idx)
    return lanes


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------


class AdaptiveReplayController:
    """AdaptiveReplayController."""


    def __init__(
        self,
        quota: Optional[LaneQuotaConfig] = None,
        *,
        target_resource: str = "memory",
    ) -> None:
        self.quota = quota or LaneQuotaConfig()
        self.target_resource = target_resource

    def decide_batch(self, inputs: BatchInputs) -> BatchDecision:
        """decide batch."""


        inputs_snapshot = self._snapshot_inputs(inputs)

        # I1: safety/model hard gates
        i1 = (
            inputs.safety_model_gate_passed
            and inputs.io_source_valid
            and inputs.io_stable_sample_count_positive
        )
        if not i1:
            return BatchDecision(
                decision=AdaptiveReplayDecision.STOP_FIX,
                reason=(
                    "safety/model hard gate  IO source "
                    "io_source!=vm_disk_device  stable_sample_count.io==0"
                ),
                inputs_snapshot=inputs_snapshot,
                remaining_upper_bound_beats_best=inputs.remaining_upper_bound_beats_best,
                ranking_stable=inputs.ranking_stable,
                pairwise_inversion_rate=inputs.pairwise_inversion_rate,
                lane_counts=dict(inputs.lane_counts),
                explainable_lane_empty=inputs.explainable_conservative_pass_count == 0,
            )

        i2 = inputs.explainable_conservative_pass_count
        i3 = inputs.fallback_best_db_qps_delta_pct
        i3_pass = i3 is not None and i3 >= inputs.min_fallback_delta_pct_for_proceed
        i4 = inputs.pairwise_inversion_rate
        i5 = inputs.remaining_upper_bound_beats_best
        i6_stable = inputs.ranking_stable
        i7 = inputs.cumulative_target_down_evidence_count

        # PROCEED_TO_OUTER_LOOP
        if (
            i2 >= inputs.min_explainable_for_proceed
            and i3_pass
            and i7 >= inputs.min_target_down_evidence_for_proceed
            and i4 <= inputs.pairwise_inversion_threshold
        ):
            return BatchDecision(
                decision=AdaptiveReplayDecision.PROCEED_TO_OUTER_LOOP,
                reason=(
                    f"explainable lane  {i2} >= {inputs.min_explainable_for_proceed}"
                    f"DB-QPS fallback best {i3}% >= {inputs.min_fallback_delta_pct_for_proceed}%"
                    f" target-down evidence {i7} >= "
                    f"{inputs.min_target_down_evidence_for_proceed}"
                    f"pairwise inversion {i4:.4f} <= "
                    f"{inputs.pairwise_inversion_threshold}"
                ),
                inputs_snapshot=inputs_snapshot,
                remaining_upper_bound_beats_best=i5,
                ranking_stable=i6_stable,
                pairwise_inversion_rate=i4,
                lane_counts=dict(inputs.lane_counts),
            )


        if i2 == 0 and i4 > inputs.pairwise_inversion_threshold:
            return BatchDecision(
                decision=AdaptiveReplayDecision.STOP_FIX_RANKING,
                reason=(
                    f"explainable lane=0  pairwise inversion {i4:.4f} > "
                    f"{inputs.pairwise_inversion_threshold}ranking "
                    " count-fill  Memory  / error-bound / lane "
                ),
                inputs_snapshot=inputs_snapshot,
                remaining_upper_bound_beats_best=i5,
                ranking_stable=i6_stable,
                pairwise_inversion_rate=i4,
                lane_counts=dict(inputs.lane_counts),
                explainable_lane_empty=True,
            )


        if i2 == 0 and i4 <= inputs.pairwise_inversion_threshold:
            return BatchDecision(
                decision=AdaptiveReplayDecision.EXPAND_DIAGNOSTIC,
                reason=(
                    "explainable lane=0  ranking pairwise inversion "
                    f"{i4:.4f} <= {inputs.pairwise_inversion_threshold}"
                    " plateau  conservative-pass "
                    " 3  diagnostic  error bound"
                ),
                inputs_snapshot=inputs_snapshot,
                remaining_upper_bound_beats_best=i5,
                ranking_stable=i6_stable,
                pairwise_inversion_rate=i4,
                lane_counts=dict(inputs.lane_counts),
                expansion_delta=3,
                explainable_lane_empty=True,
            )


        if (not i5) and i6_stable:
            return BatchDecision(
                decision=AdaptiveReplayDecision.STOP_EARLY_KEEP_BEST,
                reason=(
                    " optimistic upper bound  best  "
                    "ranking  max_budget"
                ),
                inputs_snapshot=inputs_snapshot,
                remaining_upper_bound_beats_best=i5,
                ranking_stable=i6_stable,
                pairwise_inversion_rate=i4,
                lane_counts=dict(inputs.lane_counts),
            )

        # Exhausted budgets must be handled before the generic I5 continue
        # branches. Otherwise an optimistic remaining candidate can keep
        # returning CONTINUE_NEXT_BATCH forever and EXPAND_ONE_BATCH is
        # unreachable.
        budget_exhausted = inputs.replayed_count >= self.quota.max_budget
        if budget_exhausted and i5:
            if inputs.expansions_used < self.quota.max_expansions:
                return BatchDecision(
                    decision=AdaptiveReplayDecision.EXPAND_ONE_BATCH,
                    reason=(
                        f"budget {inputs.replayed_count}/"
                        f"{self.quota.max_budget} optimistic upper bound "
                        " best "
                        f"{inputs.expansions_used + 1}/{self.quota.max_expansions} "
                        f"+{self.quota.batch_size}"
                    ),
                    inputs_snapshot=inputs_snapshot,
                    remaining_upper_bound_beats_best=i5,
                    ranking_stable=i6_stable,
                    pairwise_inversion_rate=i4,
                    lane_counts=dict(inputs.lane_counts),
                    expansion_delta=self.quota.batch_size,
                )
            return BatchDecision(
                decision=AdaptiveReplayDecision.STOP_EARLY_KEEP_BEST,
                reason=(
                    "budget  global/fallback best"
                    ""
                ),
                inputs_snapshot=inputs_snapshot,
                remaining_upper_bound_beats_best=i5,
                ranking_stable=i6_stable,
                pairwise_inversion_rate=i4,
                lane_counts=dict(inputs.lane_counts),
            )


        if i5 and (not i6_stable):
            return BatchDecision(
                decision=AdaptiveReplayDecision.CONTINUE_NEXT_BATCH,
                reason=(
                    " optimistic upper bound  best  ranking "
                    ""
                ),
                inputs_snapshot=inputs_snapshot,
                remaining_upper_bound_beats_best=i5,
                ranking_stable=i6_stable,
                pairwise_inversion_rate=i4,
                lane_counts=dict(inputs.lane_counts),
            )

        # Calibrated upper bound still beats the real best: keep one more batch even
        # when the observed prefix ordering is currently stable.
        if i5:
            return BatchDecision(
                decision=AdaptiveReplayDecision.CONTINUE_NEXT_BATCH,
                reason=(
                    " optimistic upper bound  "
                    "DB-QPS best"
                ),
                inputs_snapshot=inputs_snapshot,
                remaining_upper_bound_beats_best=i5,
                ranking_stable=i6_stable,
                pairwise_inversion_rate=i4,
                lane_counts=dict(inputs.lane_counts),
            )


        if i2 > 0 and not i3_pass:
            return BatchDecision(
                decision=AdaptiveReplayDecision.CONTINUE_NEXT_BATCH,
                reason=(
                    f"explainable lane  {i2}  conservative-pass  "
                    f"DB-QPS fallback best {i3}% < "
                    f"{inputs.min_fallback_delta_pct_for_proceed}%"
                    " explainable + hybrid "
                ),
                inputs_snapshot=inputs_snapshot,
                remaining_upper_bound_beats_best=i5,
                ranking_stable=i6_stable,
                pairwise_inversion_rate=i4,
                lane_counts=dict(inputs.lane_counts),
            )

        # else: STOP_EARLY_KEEP_BEST
        return BatchDecision(
            decision=AdaptiveReplayDecision.STOP_EARLY_KEEP_BEST,
            reason=(
                " upper bound  best  expansions "
                " best max_budget"
            ),
            inputs_snapshot=inputs_snapshot,
            remaining_upper_bound_beats_best=i5,
            ranking_stable=i6_stable,
            pairwise_inversion_rate=i4,
            lane_counts=dict(inputs.lane_counts),
        )

    @staticmethod
    def _snapshot_inputs(inputs: BatchInputs) -> Dict[str, Any]:
        """ snapshot inputs."""
        return {
            "I1_safety_model_gate_passed": inputs.safety_model_gate_passed,
            "I1_io_source_valid": inputs.io_source_valid,
            "I1_io_stable_sample_count_positive": inputs.io_stable_sample_count_positive,
            "I2_explainable_conservative_pass_count": inputs.explainable_conservative_pass_count,
            "I3_fallback_best_db_qps_delta_pct": inputs.fallback_best_db_qps_delta_pct,
            "I4_pairwise_inversion_rate": inputs.pairwise_inversion_rate,
            "I5_remaining_upper_bound_beats_best": inputs.remaining_upper_bound_beats_best,
            "I6_ranking_stable": inputs.ranking_stable,
            "I7_cumulative_target_down_evidence_count": inputs.cumulative_target_down_evidence_count,
            "batch_index": inputs.batch_index,
            "batches_so_far": inputs.batches_so_far,
            "replayed_count": inputs.replayed_count,
            "expansions_used": inputs.expansions_used,
            "lane_counts": dict(inputs.lane_counts),
            "thresholds": {
                "pairwise_inversion_threshold": inputs.pairwise_inversion_threshold,
                "min_explainable_for_proceed": inputs.min_explainable_for_proceed,
                "min_fallback_delta_pct_for_proceed": inputs.min_fallback_delta_pct_for_proceed,
                "min_target_down_evidence_for_proceed": inputs.min_target_down_evidence_for_proceed,
                "min_target_down_evidence_for_early_stop": inputs.min_target_down_evidence_for_early_stop,
                "max_consecutive_batches_no_improvement": inputs.max_consecutive_batches_no_improvement,
            },
        }


# ---------------------------------------------------------------------------
# Lane quota enforcement (no cross-lane count-fill)
# ---------------------------------------------------------------------------


def enforce_lane_quotas(
    lane_assignments: Dict[str, List[int]],
    quota: LaneQuotaConfig,
) -> Dict[str, Any]:
    """enforce lane quotas."""


    lane_counts = {lane: len(idxs) for lane, idxs in lane_assignments.items()}
    quota_exceeded: Dict[str, int] = {}
    for lane_name, count in lane_counts.items():
        lane_enum = ReplayLane(lane_name)
        quota_for_lane = quota.quota_for(lane_enum)
        if count > quota_for_lane:
            quota_exceeded[lane_name] = count - quota_for_lane

    return {
        "lane_counts": lane_counts,
        "lane_quota_exceeded": quota_exceeded,
        "explainable_lane_empty": lane_counts.get("explainable", 0) == 0,
        "no_count_fill_applied": True,
        "quota_config": {
            "max_budget": quota.max_budget,
            "min_evidence": quota.min_evidence,
            "batch_size": quota.batch_size,
            "max_expansions": quota.max_expansions,
            "explainable": quota.explainable,
            "fallback": quota.fallback,
            "diagnostic": quota.diagnostic,
            "mandatory_anchor": quota.mandatory_anchor,
            "reserve": quota.reserve,
        },
    }


def select_batch_candidates(
    lane_assignments: Dict[str, List[int]],
    quota: LaneQuotaConfig,
    *,
    batch_size: Optional[int] = None,
    reserve_enabled: bool = False,
) -> Dict[str, List[int]]:
    """select batch candidates."""


    bs = batch_size if batch_size is not None else quota.batch_size
    selected: Dict[str, List[int]] = {lane: [] for lane in lane_assignments}
    remaining = bs


    mandatory_pool = list(lane_assignments.get("mandatory_anchor", []))
    mandatory_take = min(len(mandatory_pool), quota.mandatory_anchor, remaining)
    if mandatory_take > 0:
        selected["mandatory_anchor"] = mandatory_pool[:mandatory_take]
        remaining -= mandatory_take


    explainable_pool = list(lane_assignments.get("explainable", []))
    explainable_take = min(
        len(explainable_pool), quota.explainable, remaining
    )
    if explainable_take > 0:
        selected["explainable"] = explainable_pool[:explainable_take]
        remaining -= explainable_take

    # fallback
    fallback_pool = list(lane_assignments.get("fallback", []))
    fallback_take = min(len(fallback_pool), quota.fallback, remaining)
    if fallback_take > 0:
        selected["fallback"] = fallback_pool[:fallback_take]
        remaining -= fallback_take

    # diagnostic
    diagnostic_pool = list(lane_assignments.get("diagnostic", []))
    diagnostic_take = min(len(diagnostic_pool), quota.diagnostic, remaining)
    if diagnostic_take > 0:
        selected["diagnostic"] = diagnostic_pool[:diagnostic_take]
        remaining -= diagnostic_take


    if reserve_enabled:
        reserve_pool = list(lane_assignments.get("reserve", []))
        reserve_take = min(len(reserve_pool), quota.reserve, remaining)
        if reserve_take > 0:
            selected["reserve"] = reserve_pool[:reserve_take]
            remaining -= reserve_take

    return selected


# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------


def record_fallback_best(
    candidate_index: int,
    db_qps_delta_pct: float,
    params: Dict[str, float],
    plan_change_count: int,
    capture_file: Optional[str] = None,
    *,
    target_resource: str = "memory",
    target_resource_delta_pp: Optional[float] = None,
    reason: str = "fallback_best_dbqps_not_explainable",
) -> Dict[str, Any]:
    """record fallback best."""


    return {
        "candidate_index": candidate_index,
        "db_qps_delta_pct": db_qps_delta_pct,
        "params": dict(params),
        "plan_change_count": plan_change_count,
        "capture_file": capture_file,
        "target_resource": target_resource,
        "target_resource_delta_pp": target_resource_delta_pp,
        "reason": reason,
        "explainable": False,
        "permanent": True,
    }


# ---------------------------------------------------------------------------
# Module self-test
# ---------------------------------------------------------------------------


def _self_test() -> int:
    """ self test."""
    print("=" * 70, flush=True)
    print("adaptive_replay_controller self-test", flush=True)
    print("=" * 70, flush=True)

    controller = AdaptiveReplayController(target_resource="memory")
    pass_count = 0
    fail_count = 0

    def _check(name: str, cond: bool, detail: str = "") -> None:
        nonlocal pass_count, fail_count
        if cond:
            print(f"[PASS] {name}", flush=True)
            pass_count += 1
        else:
            print(f"[FAIL] {name}: {detail}", flush=True)
            fail_count += 1

    # 1. STOP_FIX: I1=false
    inp = BatchInputs(
        safety_model_gate_passed=False,
        io_source_valid=True,
        io_stable_sample_count_positive=True,
        explainable_conservative_pass_count=0,
        fallback_best_db_qps_delta_pct=None,
        pairwise_inversion_rate=0.0,
        remaining_upper_bound_beats_best=False,
        ranking_stable=True,
        cumulative_target_down_evidence_count=0,
        batch_index=0,
        batches_so_far=1,
        replayed_count=6,
        expansions_used=0,
    )
    dec = controller.decide_batch(inp)
    _check(
        "STOP_FIX on safety gate fail",
        dec.decision == AdaptiveReplayDecision.STOP_FIX,
        f"got {dec.decision}",
    )

    # 2. STOP_FIX_RANKING: I2=0, I4>0.10
    inp = BatchInputs(
        safety_model_gate_passed=True,
        io_source_valid=True,
        io_stable_sample_count_positive=True,
        explainable_conservative_pass_count=0,
        fallback_best_db_qps_delta_pct=0.0,
        pairwise_inversion_rate=0.7218,
        remaining_upper_bound_beats_best=False,
        ranking_stable=False,
        cumulative_target_down_evidence_count=0,
        batch_index=0,
        batches_so_far=1,
        replayed_count=6,
        expansions_used=0,
    )
    dec = controller.decide_batch(inp)
    _check(
        "STOP_FIX_RANKING on I2=0 + I4>0.10",
        dec.decision == AdaptiveReplayDecision.STOP_FIX_RANKING,
        f"got {dec.decision}",
    )
    _check(
        "STOP_FIX_RANKING emits explainable_lane_empty",
        dec.explainable_lane_empty,
        "expected explainable_lane_empty=True",
    )

    # 3. EXPAND_DIAGNOSTIC: I2=0, I4<=0.10
    inp = BatchInputs(
        safety_model_gate_passed=True,
        io_source_valid=True,
        io_stable_sample_count_positive=True,
        explainable_conservative_pass_count=0,
        fallback_best_db_qps_delta_pct=0.0,
        pairwise_inversion_rate=0.05,
        remaining_upper_bound_beats_best=False,
        ranking_stable=True,
        cumulative_target_down_evidence_count=0,
        batch_index=0,
        batches_so_far=1,
        replayed_count=6,
        expansions_used=0,
    )
    dec = controller.decide_batch(inp)
    _check(
        "EXPAND_DIAGNOSTIC on I2=0 + I4<=0.10",
        dec.decision == AdaptiveReplayDecision.EXPAND_DIAGNOSTIC,
        f"got {dec.decision}",
    )
    _check(
        "EXPAND_DIAGNOSTIC expansion_delta=3",
        dec.expansion_delta == 3,
        f"got {dec.expansion_delta}",
    )

    # 4. STOP_EARLY_KEEP_BEST: I5=false, I6=stable
    inp = BatchInputs(
        safety_model_gate_passed=True,
        io_source_valid=True,
        io_stable_sample_count_positive=True,
        explainable_conservative_pass_count=1,
        fallback_best_db_qps_delta_pct=2.0,
        pairwise_inversion_rate=0.05,
        remaining_upper_bound_beats_best=False,
        ranking_stable=True,
        cumulative_target_down_evidence_count=1,
        batch_index=2,
        batches_so_far=3,
        replayed_count=18,
        expansions_used=0,
    )
    dec = controller.decide_batch(inp)
    _check(
        "STOP_EARLY_KEEP_BEST on I5=false + I6=stable",
        dec.decision == AdaptiveReplayDecision.STOP_EARLY_KEEP_BEST,
        f"got {dec.decision}",
    )

    # 5. CONTINUE_NEXT_BATCH: I5=true, I6=moving
    inp = BatchInputs(
        safety_model_gate_passed=True,
        io_source_valid=True,
        io_stable_sample_count_positive=True,
        explainable_conservative_pass_count=1,
        fallback_best_db_qps_delta_pct=2.0,
        pairwise_inversion_rate=0.05,
        remaining_upper_bound_beats_best=True,
        ranking_stable=False,
        cumulative_target_down_evidence_count=1,
        batch_index=1,
        batches_so_far=2,
        replayed_count=12,
        expansions_used=0,
    )
    dec = controller.decide_batch(inp)
    _check(
        "CONTINUE_NEXT_BATCH on I5=true + I6=moving",
        dec.decision == AdaptiveReplayDecision.CONTINUE_NEXT_BATCH,
        f"got {dec.decision}",
    )

    # 6. PROCEED_TO_OUTER_LOOP: I2>=3, I3>=5%, I7>=3, I4<=0.10
    inp = BatchInputs(
        safety_model_gate_passed=True,
        io_source_valid=True,
        io_stable_sample_count_positive=True,
        explainable_conservative_pass_count=3,
        fallback_best_db_qps_delta_pct=7.0,
        pairwise_inversion_rate=0.05,
        remaining_upper_bound_beats_best=False,
        ranking_stable=True,
        cumulative_target_down_evidence_count=3,
        batch_index=3,
        batches_so_far=4,
        replayed_count=24,
        expansions_used=0,
    )
    dec = controller.decide_batch(inp)
    _check(
        "PROCEED_TO_OUTER_LOOP on full evidence",
        dec.decision == AdaptiveReplayDecision.PROCEED_TO_OUTER_LOOP,
        f"got {dec.decision}",
    )

    # 7. EXPAND_ONE_BATCH: budget exhausted, I5=true, I6=stable, expansions<2
    #    Must skip #7 (I2>0 AND I3<5%) by requiring I3>=5%; must skip PROCEED
    #    by keeping I2<3 OR I7<3.
    inp = BatchInputs(
        safety_model_gate_passed=True,
        io_source_valid=True,
        io_stable_sample_count_positive=True,
        explainable_conservative_pass_count=1,
        fallback_best_db_qps_delta_pct=6.0,
        pairwise_inversion_rate=0.05,
        remaining_upper_bound_beats_best=True,
        ranking_stable=True,
        cumulative_target_down_evidence_count=1,
        batch_index=3,
        batches_so_far=4,
        replayed_count=24,
        expansions_used=0,
    )
    dec = controller.decide_batch(inp)
    _check(
        "EXPAND_ONE_BATCH on budget exhausted + I5=true + I6=stable + expansions<2",
        dec.decision == AdaptiveReplayDecision.EXPAND_ONE_BATCH,
        f"got {dec.decision}",
    )
    _check(
        "EXPAND_ONE_BATCH expansion_delta=6",
        dec.expansion_delta == 6,
        f"got {dec.expansion_delta}",
    )

    # 8. lane classification
    baseline = {"cpu": 50.0, "memory": 90.0, "io": 20.0}
    capability = {"cpu": 100.0, "memory": 100.0, "io": 100.0}
    cands = [
        # explainable: Memory -7pp conservative, IO +0.6pp
        {"predicted_resource": {"cpu": 40.0, "memory": 83.0, "io": 20.6}},
        # fallback: Memory up, db_qps proxy +10%
        {
            "predicted_resource": {"cpu": 50.0, "memory": 95.0, "io": 22.0},
            "predicted_db_qps_delta_pct": 10.0,
        },
        # diagnostic: plateau Memory = baseline 90.0
        {"predicted_resource": {"cpu": 50.0, "memory": 90.0, "io": 20.0}},
    ]
    lanes = assign_lanes(
        cands,
        target_resource="memory",
        baseline_resource=baseline,
        capability=capability,
        mandatory_anchor_indices=[],
        forced_anchor_indices=[],
        conservative_error_bound_pp=2.0,
        plateau_predicted_values=[90.0],
    )
    _check(
        "classify explainable candidate",
        0 in lanes["explainable"],
        f"lanes={lanes}",
    )
    _check(
        "classify fallback candidate",
        1 in lanes["fallback"],
        f"lanes={lanes}",
    )
    _check(
        "classify diagnostic plateau candidate",
        2 in lanes["diagnostic"],
        f"lanes={lanes}",
    )

    # 9. mandatory anchor does not swallow explainable quota
    cands2 = [
        {"predicted_resource": {"cpu": 40.0, "memory": 83.0, "io": 20.6}},
        {"predicted_resource": {"cpu": 50.0, "memory": 95.0, "io": 22.0}},
    ]
    lanes2 = assign_lanes(
        cands2,
        target_resource="memory",
        baseline_resource=baseline,
        capability=capability,
        mandatory_anchor_indices=[1],
        forced_anchor_indices=[],
        conservative_error_bound_pp=2.0,
    )
    _check(
        "mandatory anchor assigned to mandatory_anchor lane",
        1 in lanes2["mandatory_anchor"],
        f"lanes={lanes2}",
    )
    _check(
        "explainable lane still has its candidate (not swallowed by anchor)",
        0 in lanes2["explainable"],
        f"lanes={lanes2}",
    )

    # 10. select_batch_candidates: no cross-lane count-fill
    selected = select_batch_candidates(
        lanes2,
        LaneQuotaConfig(),
        batch_size=6,
        reserve_enabled=False,
    )
    # explainable has 1 candidate, mandatory has 1, fallback/diagnostic/reserve empty
    _check(
        "no cross-lane count-fill: explainable stays at 1",
        len(selected["explainable"]) == 1,
        f"selected={selected}",
    )
    _check(
        "no cross-lane count-fill: fallback stays at 0",
        len(selected["fallback"]) == 0,
        f"selected={selected}",
    )
    _check(
        "no cross-lane count-fill: diagnostic stays at 0",
        len(selected["diagnostic"]) == 0,
        f"selected={selected}",
    )
    _check(
        "mandatory anchor selected",
        len(selected["mandatory_anchor"]) == 1,
        f"selected={selected}",
    )

    # 11. enforce_lane_quotas: explainable empty flagged
    check = enforce_lane_quotas(lanes2, LaneQuotaConfig())
    _check(
        "enforce_lane_quotas flags explainable not empty",
        not check["explainable_lane_empty"],
        f"check={check}",
    )
    # now test with empty explainable
    lanes3 = {lane: [] for lane in ("explainable", "fallback", "diagnostic", "mandatory_anchor", "reserve")}
    lanes3["fallback"] = [0, 1, 2, 3, 4, 5, 6, 7]  # 8 > quota 5
    check3 = enforce_lane_quotas(lanes3, LaneQuotaConfig())
    _check(
        "enforce_lane_quotas flags explainable empty",
        check3["explainable_lane_empty"],
        f"check={check3}",
    )
    _check(
        "enforce_lane_quotas flags fallback quota exceeded",
        check3["lane_quota_exceeded"].get("fallback") == 3,
        f"check={check3}",
    )
    _check(
        "enforce_lane_quotas never applies count-fill",
        check3["no_count_fill_applied"],
        f"check={check3}",
    )

    # 12. fallback best record
    rec = record_fallback_best(
        candidate_index=19,
        db_qps_delta_pct=30.64,
        params={
            "seq_page_cost": 1.02737,
            "random_page_cost": 2.44005,
            "cpu_tuple_cost": 1e-06,
            "cpu_index_tuple_cost": 0.00497895,
            "cpu_operator_cost": 0.0025,
            "parallel_setup_cost": 1000.0,
            "parallel_tuple_cost": 0.1,
        },
        plan_change_count=6,
        capture_file="memory/hybench_ap_plan_captures/feedback_final_0_cand19_20260706_062734.json",
        target_resource="memory",
        target_resource_delta_pp=5.50,
    )
    _check(
        "fallback best record permanent + not explainable",
        rec["permanent"] and not rec["explainable"],
        f"rec={rec}",
    )
    _check(
        "fallback best record has 7 cost params",
        len(rec["params"]) == 7,
        f"rec={rec}",
    )
    _check(
        "fallback best record reason=fallback_best_dbqps_not_explainable",
        rec["reason"] == "fallback_best_dbqps_not_explainable",
        f"rec={rec}",
    )

    # 13. to_jsonl_record serialization
    inp = BatchInputs(
        safety_model_gate_passed=True,
        io_source_valid=True,
        io_stable_sample_count_positive=True,
        explainable_conservative_pass_count=3,
        fallback_best_db_qps_delta_pct=7.0,
        pairwise_inversion_rate=0.05,
        remaining_upper_bound_beats_best=False,
        ranking_stable=True,
        cumulative_target_down_evidence_count=3,
        batch_index=3,
        batches_so_far=4,
        replayed_count=24,
        expansions_used=0,
    )
    dec = controller.decide_batch(inp)
    rec_jsonl = dec.to_jsonl_record()
    _check(
        "to_jsonl_record has event=adaptive_replay_decision",
        rec_jsonl["event"] == "adaptive_replay_decision",
        f"rec={rec_jsonl}",
    )
    _check(
        "to_jsonl_record has decision value",
        rec_jsonl["decision"] == AdaptiveReplayDecision.PROCEED_TO_OUTER_LOOP.value,
        f"rec={rec_jsonl}",
    )

    print("=" * 70, flush=True)
    print(
        f"adaptive_replay_controller self-test: {pass_count} passed, "
        f"{fail_count} failed",
        flush=True,
    )
    print("=" * 70, flush=True)
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    import sys

    sys.exit(_self_test())

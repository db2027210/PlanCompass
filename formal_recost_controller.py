"""Pure causal Outer-loop controller for one formal MySQL Recost run."""

from __future__ import annotations

from dataclasses import dataclass
from io import StringIO
import json
import math
from pathlib import Path
from typing import Callable, Mapping, Protocol, Sequence

from formal_acceptance_policy import LivePoint, OuterDecision, decide_outer_candidate
from formal_recost_core import FrozenRecostProposalEngine, RecostProposal
from formal_recost_estimator import EstimatorParameter, FrozenOnlineRecostEstimator


@dataclass(frozen=True)
class ReplayResult:
    status: str
    params: Mapping[str, float]
    measurement: LivePoint | None
    plan_fingerprints: Mapping[str, str]
    failure_reason: str | None = None
    artifact_relative_path: str | None = None

    @property
    def successful(self) -> bool:
        return self.status == "complete" and self.measurement is not None


@dataclass(frozen=True)
class CandidateRequest:
    candidate_index: int
    params_before: Mapping[str, float]
    proposal: RecostProposal
    inner_iterations: tuple[Mapping[str, object], ...]


@dataclass(frozen=True)
class CandidateOutcome:
    candidate_index: int
    request: CandidateRequest
    replay: ReplayResult
    decision: OuterDecision | None
    outer_candidate_accepted: bool
    budget_consumed: bool
    early_stop_after_candidate: bool


@dataclass(frozen=True)
class OptimizationTrace:
    baseline: ReplayResult
    candidates: tuple[CandidateOutcome, ...]
    final_search_point: ReplayResult
    final_source: str
    final_candidate_index: int | None
    real_candidate_count: int
    accepted_candidate_count: int
    stop_reason: str


CandidateExecutor = Callable[[CandidateRequest], ReplayResult]


class OuterAdaptiveEstimator(Protocol):
    """Explicitly injected estimator interface; no implementation is selected here."""

    def __call__(self, params: Mapping[str, float]) -> object: ...

    def observe_outer(
        self,
        *,
        params: Mapping[str, float],
        observed_utility_improvement: float,
        outer_candidate_accepted: bool,
    ) -> None: ...


EstimatorFactory = Callable[
    [FrozenRecostProposalEngine, ReplayResult, str],
    OuterAdaptiveEstimator,
]


def build_frozen_estimator_factory(case_id: str) -> EstimatorFactory:
    """Return the production current-Run-only estimator factory for one case."""

    if not case_id:
        raise ValueError("case_id must not be empty")

    def factory(
        proposal_engine: FrozenRecostProposalEngine,
        incumbent: ReplayResult,
        scenario: str,
    ) -> FrozenOnlineRecostEstimator:
        parameters = tuple(
            EstimatorParameter(item.name, item.minimum, item.maximum, item.transform)
            for item in proposal_engine.parameters
        )
        estimator = FrozenOnlineRecostEstimator(
            parameters,
            incumbent.params,
            method_seed=proposal_engine.settings.method_seed,
            engine="mysql",
            case_id=case_id,
            scenario=scenario,
        )
        if estimator.version != proposal_engine.settings.estimator_version:
            raise ValueError("constructed Recost estimator has the wrong frozen version")
        return estimator

    return factory


def _resources(point: LivePoint) -> Mapping[str, float]:
    return {
        "cpu": point.cpu_utilization,
        "memory": point.memory_utilization,
        "io": point.io_utilization,
    }


def _throughput(point: LivePoint, xp: bool) -> float:
    if xp:
        if point.xap_qps is None or point.xtp_tps is None:
            raise ValueError("XP replay lacks one throughput stream")
        return math.sqrt(point.xap_qps * point.xtp_tps)
    if point.qps is None:
        raise ValueError("normal replay lacks qps")
    return point.qps


def _parse_inner_log(buffer: StringIO) -> tuple[Mapping[str, object], ...]:
    rows: list[Mapping[str, object]] = []
    for line in buffer.getvalue().splitlines():
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError("Inner log line is not an object")
        if value.get("label") != "inner_step_accepted" or "outer_candidate_accepted" in value:
            raise ValueError("Inner log contains an Outer decision label")
        rows.append(value)
    if not rows:
        raise ValueError("Recost candidate must retain Inner iterations")
    return tuple(rows)


class FrozenRecostOuterController:
    def __init__(
        self,
        proposal_engine: FrozenRecostProposalEngine,
        *,
        acceptance_policy_path: Path,
        scenario: str,
        xp: bool,
        estimator_factory: EstimatorFactory,
    ) -> None:
        if scenario not in {"memory", "io", "none"}:
            raise ValueError("scenario must be memory, io, or none")
        self.engine = proposal_engine
        self.policy_path = acceptance_policy_path
        self.scenario = scenario
        self.xp = bool(xp)
        self.estimator_factory = estimator_factory

    def _estimator(self, incumbent: ReplayResult) -> OuterAdaptiveEstimator:
        if not incumbent.successful or incumbent.measurement is None:
            raise ValueError("Outer incumbent must be a successful replay")
        estimator = self.estimator_factory(self.engine, incumbent, self.scenario)
        if estimator is None or not callable(estimator) or not hasattr(estimator, "observe_outer"):
            raise TypeError("estimator_factory returned an invalid explicit estimator")
        return estimator

    def _select_final(
        self,
        baseline: ReplayResult,
        outcomes: Sequence[CandidateOutcome],
    ) -> tuple[ReplayResult, str, int | None]:
        if baseline.measurement is None:
            raise ValueError("baseline measurement is absent")
        eligible: list[tuple[tuple[float, float, float, int], ReplayResult, str, int | None]] = []
        baseline_target = (
            0.0
            if self.scenario == "none"
            else _resources(baseline.measurement)[self.scenario]
        )
        eligible.append(
            (
                (0.0, _throughput(baseline.measurement, self.xp), -baseline_target, 0),
                baseline,
                "default",
                None,
            )
        )
        for outcome in outcomes:
            if not outcome.outer_candidate_accepted or outcome.replay.measurement is None:
                continue
            global_decision = decide_outer_candidate(
                self.policy_path,
                baseline.measurement,
                outcome.replay.measurement,
                bottleneck=self.scenario,
                xp=self.xp,
                plan_changed=(
                    dict(outcome.replay.plan_fingerprints) != dict(baseline.plan_fingerprints)
                ),
            )
            utility = global_decision.utility_improvement
            if utility is None:
                raise ValueError("eligible candidate has no utility")
            target = (
                0.0
                if self.scenario == "none"
                else _resources(outcome.replay.measurement)[self.scenario]
            )
            eligible.append(
                (
                    (
                        utility,
                        _throughput(outcome.replay.measurement, self.xp),
                        -target,
                        -outcome.candidate_index,
                    ),
                    outcome.replay,
                    "outer_accepted_candidate",
                    outcome.candidate_index,
                )
            )
        _, replay, source, index = max(eligible, key=lambda item: item[0])
        return replay, source, index

    def optimize(
        self,
        baseline: ReplayResult,
        execute_candidate: CandidateExecutor,
    ) -> OptimizationTrace:
        if not baseline.successful or baseline.measurement is None:
            raise ValueError("baseline must be status complete with a measurement")
        incumbent = baseline
        estimator = self._estimator(incumbent)
        outcomes: list[CandidateOutcome] = []
        stop_reason = "outer_real_candidate_limit"

        while self.engine.real_candidate_count < self.engine.settings.outer_real_candidate_limit:
            index = self.engine.real_candidate_count + 1
            inner_buffer = StringIO()
            proposal = self.engine.propose(incumbent.params, estimator, inner_log=inner_buffer)
            request = CandidateRequest(
                candidate_index=index,
                params_before=dict(incumbent.params),
                proposal=proposal,
                inner_iterations=_parse_inner_log(inner_buffer),
            )
            try:
                replay = execute_candidate(request)
            except Exception as exc:
                replay = ReplayResult(
                    status="failed",
                    params=dict(proposal.params),
                    measurement=None,
                    plan_fingerprints={},
                    # Exception text can contain a client command line or
                    # credential detail.  The executor owns a separately
                    # sanitized raw error artifact; the controller records only
                    # the stable exception class for causal accounting.
                    failure_reason=f"executor_exception:{type(exc).__name__}",
                )
            params_match = False
            try:
                requested_normalized = self.engine.normalize(proposal.params)
                applied_normalized = self.engine.normalize(replay.params)
                params_match = all(
                    math.isclose(
                        requested_normalized[name],
                        applied_normalized[name],
                        rel_tol=0.0,
                        abs_tol=1e-12,
                    )
                    for name in requested_normalized
                )
            except Exception:
                params_match = False
            if replay.successful and not params_match:
                replay = ReplayResult(
                    status="failed",
                    params=dict(replay.params),
                    measurement=replay.measurement,
                    plan_fingerprints=dict(replay.plan_fingerprints),
                    failure_reason="params_applied_mismatch",
                    artifact_relative_path=replay.artifact_relative_path,
                )
            decision: OuterDecision | None = None
            accepted = False
            observed_utility = -1.0
            if replay.successful and replay.measurement is not None:
                decision = decide_outer_candidate(
                    self.policy_path,
                    incumbent.measurement,
                    replay.measurement,
                    bottleneck=self.scenario,
                    xp=self.xp,
                    plan_changed=(
                        dict(replay.plan_fingerprints) != dict(incumbent.plan_fingerprints)
                    ),
                )
                accepted = decision.accepted
                if decision.utility_improvement is not None:
                    observed_utility = decision.utility_improvement
            early_stop = self.engine.record_outer_result(outer_candidate_accepted=accepted)
            outcome = CandidateOutcome(
                candidate_index=index,
                request=request,
                replay=replay,
                decision=decision,
                outer_candidate_accepted=accepted,
                budget_consumed=True,
                early_stop_after_candidate=early_stop,
            )
            outcomes.append(outcome)
            if accepted:
                incumbent = replay
                estimator = self._estimator(incumbent)
            else:
                estimator.observe_outer(
                    params=replay.params,
                    observed_utility_improvement=observed_utility,
                    outer_candidate_accepted=False,
                )
            if early_stop:
                stop_reason = "consecutive_outer_rejections_after_minimum_candidates"
                break

        final, final_source, final_index = self._select_final(baseline, outcomes)
        return OptimizationTrace(
            baseline=baseline,
            candidates=tuple(outcomes),
            final_search_point=final,
            final_source=final_source,
            final_candidate_index=final_index,
            real_candidate_count=self.engine.real_candidate_count,
            accepted_candidate_count=sum(item.outer_candidate_accepted for item in outcomes),
            stop_reason=stop_reason,
        )

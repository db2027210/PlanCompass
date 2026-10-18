"""Durable causal Outer controller for the frozen MySQL random walk."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from formal_acceptance_policy import (
    EligibleFinalPoint,
    FinalSelection,
    LivePoint,
    OuterDecision,
    decide_outer_candidate,
    select_final_point,
)
from formal_random_walk import RandomWalkState


FAILED_STATUSES = {
    "failed",
    "timeout",
    "oom",
    "correctness_rejected",
    "unstable",
}


class RandomWalkControllerError(RuntimeError):
    """Causal or durable controller state is invalid."""


@dataclass(frozen=True)
class AcceptedPoint:
    candidate_index: int
    label: str
    params: Mapping[str, float]
    point: LivePoint


@dataclass(frozen=True)
class CandidateRecord:
    candidate_index: int
    params_before: Mapping[str, float]
    params_candidate: Mapping[str, float]
    params_applied: Mapping[str, float] | None
    outcome: str
    accepted: bool
    reason: str
    decision: Mapping[str, Any] | None
    point: Mapping[str, Any] | None
    bad_configuration: bool


class FrozenRandomWalkController:
    """One complete Run's state, excluding any database/resource implementation."""

    def __init__(
        self,
        state: RandomWalkState,
        *,
        policy_path: Path,
        scenario: str,
        xp: bool,
        default_params: Mapping[str, float],
        baseline: LivePoint,
    ) -> None:
        if scenario not in {"memory", "io", "none"}:
            raise RandomWalkControllerError("scenario must be memory, io, or none")
        if set(default_params) != {axis.name for axis in state.axes}:
            raise RandomWalkControllerError("default parameter names differ from frozen axes")
        self.state = state
        self.policy_path = policy_path
        self.scenario = scenario
        self.xp = bool(xp)
        self.default_params = {name: float(value) for name, value in default_params.items()}
        self.baseline = baseline
        self.incumbent_params = dict(self.default_params)
        self.incumbent = baseline
        self.accepted_points: list[AcceptedPoint] = []
        self.records: list[CandidateRecord] = []
        self._pending_params: dict[str, float] | None = None

    @property
    def should_stop(self) -> bool:
        return self.state.should_stop

    def propose(self) -> Mapping[str, float]:
        if self._pending_params is not None:
            raise RandomWalkControllerError("previous candidate has no durable outcome")
        candidate = self.state.propose()
        self._pending_params = dict(candidate)
        return dict(candidate)

    @staticmethod
    def _params_equal(left: Mapping[str, float], right: Mapping[str, float]) -> bool:
        return set(left) == set(right) and all(
            math.isclose(float(left[name]), float(right[name]), rel_tol=1e-12, abs_tol=1e-15)
            for name in left
        )

    def record_duplicate(self) -> CandidateRecord:
        if self._pending_params is None or not self.state.pending_duplicate:
            raise RandomWalkControllerError("no pending frozen duplicate")
        index = self.state.consumed_budget
        record = CandidateRecord(
            candidate_index=index,
            params_before=dict(self.incumbent_params),
            params_candidate=dict(self._pending_params),
            params_applied=None,
            outcome="duplicate",
            accepted=False,
            reason="duplicate_consumed_budget_without_real_replay",
            decision=None,
            point=None,
            bad_configuration=False,
        )
        self.state.record_outcome("duplicate")
        self.records.append(record)
        self._pending_params = None
        return record

    def record_failed(
        self,
        status: str,
        *,
        params_applied: Mapping[str, float] | None,
        reason: str,
        bad_configuration: bool,
    ) -> CandidateRecord:
        if status not in FAILED_STATUSES:
            raise RandomWalkControllerError(f"unsupported failed candidate status: {status}")
        if self._pending_params is None or self.state.pending_duplicate:
            raise RandomWalkControllerError("no pending non-duplicate candidate")
        index = self.state.consumed_budget
        mismatch = params_applied is not None and not self._params_equal(
            self._pending_params, params_applied
        )
        outcome = "failed" if mismatch else status
        final_reason = "params_applied_mismatch" if mismatch else reason
        record = CandidateRecord(
            candidate_index=index,
            params_before=dict(self.incumbent_params),
            params_candidate=dict(self._pending_params),
            params_applied=dict(params_applied) if params_applied is not None else None,
            outcome=outcome,
            accepted=False,
            reason=final_reason,
            decision=None,
            point=None,
            bad_configuration=bool(bad_configuration or mismatch),
        )
        self.state.record_outcome(outcome)
        self.records.append(record)
        self._pending_params = None
        return record

    def record_complete(
        self,
        *,
        params_applied: Mapping[str, float],
        point: LivePoint,
        plan_changed: bool,
    ) -> tuple[CandidateRecord, OuterDecision | None]:
        if self._pending_params is None or self.state.pending_duplicate:
            raise RandomWalkControllerError("no pending non-duplicate candidate")
        if not self._params_equal(self._pending_params, params_applied):
            return (
                self.record_failed(
                    "failed",
                    params_applied=params_applied,
                    reason="params_applied_mismatch",
                    bad_configuration=True,
                ),
                None,
            )
        decision = decide_outer_candidate(
            self.policy_path,
            self.incumbent,
            point,
            bottleneck=self.scenario,
            xp=self.xp,
            plan_changed=plan_changed,
        )
        index = self.state.consumed_budget
        record = CandidateRecord(
            candidate_index=index,
            params_before=dict(self.incumbent_params),
            params_candidate=dict(self._pending_params),
            params_applied=dict(params_applied),
            outcome="outer_accepted" if decision.accepted else "outer_rejected",
            accepted=decision.accepted,
            reason=decision.reason,
            decision=asdict(decision),
            point=asdict(point),
            bad_configuration=False,
        )
        self.state.observe(accepted=decision.accepted)
        if decision.accepted:
            accepted = AcceptedPoint(
                candidate_index=index,
                label=f"candidate_{index}",
                params=dict(params_applied),
                point=point,
            )
            self.accepted_points.append(accepted)
            self.incumbent_params = dict(params_applied)
            self.incumbent = point
        self.records.append(record)
        self._pending_params = None
        return record, decision

    def select_final(self) -> tuple[FinalSelection, Mapping[str, float]]:
        if self._pending_params is not None:
            raise RandomWalkControllerError("cannot select final with a pending candidate")
        selection = select_final_point(
            self.policy_path,
            self.baseline,
            [
                EligibleFinalPoint(item.label, item.candidate_index, item.point)
                for item in self.accepted_points
            ],
            bottleneck=self.scenario,
            xp=self.xp,
        )
        if selection.candidate_index == 0:
            params = dict(self.default_params)
        else:
            matches = [
                item for item in self.accepted_points
                if item.candidate_index == selection.candidate_index
            ]
            if len(matches) != 1:
                raise RandomWalkControllerError("final selection cannot resolve candidate params")
            params = dict(matches[0].params)
        return selection, params

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "scenario": self.scenario,
            "xp": self.xp,
            "default_params": dict(self.default_params),
            "baseline": asdict(self.baseline),
            "incumbent_params": dict(self.incumbent_params),
            "incumbent": asdict(self.incumbent),
            "accepted_points": [
                {
                    "candidate_index": item.candidate_index,
                    "label": item.label,
                    "params": dict(item.params),
                    "point": asdict(item.point),
                }
                for item in self.accepted_points
            ],
            "records": [asdict(item) for item in self.records],
            "pending_params": dict(self._pending_params) if self._pending_params else None,
            "random_walk_state": self.state.as_dict(),
        }

    @classmethod
    def restore_frozen(
        cls,
        payload: Mapping[str, Any],
        *,
        policy_path: Path,
        search_space: Mapping[str, Any],
        method_protocols: Mapping[str, Any],
    ) -> "FrozenRandomWalkController":
        if payload.get("schema_version") != 1:
            raise RandomWalkControllerError("unsupported controller schema")
        state = RandomWalkState.restore_frozen(
            payload["random_walk_state"], search_space, method_protocols
        )
        baseline = LivePoint(**payload["baseline"])
        controller = cls(
            state,
            policy_path=policy_path,
            scenario=str(payload["scenario"]),
            xp=bool(payload["xp"]),
            default_params=payload["default_params"],
            baseline=baseline,
        )
        controller.incumbent_params = {
            name: float(value) for name, value in payload["incumbent_params"].items()
        }
        controller.incumbent = LivePoint(**payload["incumbent"])
        controller.accepted_points = [
            AcceptedPoint(
                candidate_index=int(item["candidate_index"]),
                label=str(item["label"]),
                params={name: float(value) for name, value in item["params"].items()},
                point=LivePoint(**item["point"]),
            )
            for item in payload["accepted_points"]
        ]
        controller.records = [CandidateRecord(**item) for item in payload["records"]]
        pending = payload.get("pending_params")
        controller._pending_params = (
            {name: float(value) for name, value in pending.items()} if pending else None
        )
        if (controller._pending_params is None) != (not state.pending_observation):
            raise RandomWalkControllerError("controller/random-walk pending states disagree")
        if len(controller.records) + (1 if state.pending_observation else 0) != state.consumed_budget:
            raise RandomWalkControllerError("controller records do not match consumed budget")
        return controller

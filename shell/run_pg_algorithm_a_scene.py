#!/usr/bin/env python3
"""Run exhaustive binary-factorial Algorithm A on one PostgreSQL lane.

Each retained parameter interval contributes two deterministic interior points
(L/R).  A formal search step physically validates the complete Cartesian
product over the lane's active parameters.  Predictions are retained only as
audit metadata; they never filter, score-order, or truncate physical replay.
"""

from __future__ import annotations

import argparse
import copy
import itertools
import json
import math
import os
import re
import signal
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from bonect_interval_lineage import interval_pair_points
from experience_table_subset_optimizer import attach_three_level_fingerprint
from resource_utility import ResourceUtilityCalculator
from shell import run_pg_algorithm_c_scene as framework


ALGORITHM_A_METHOD = "exhaustive_binary_factorial_algorithm_a_v2"
ALGORITHM_A_SEARCH_CONTRACT = (
    "lane_local_complete_lr_cartesian_physical_attempt_v2"
)
ALGORITHM_A_BATCH_KIND = "algorithm_a_full_factorial"
ALGORITHM_A_TRANSITION_BATCH_KIND = "algorithm_a_worker_cohort_transition"
ALGORITHM_A_MAX_TECHNICAL_RETRIES_PER_ROUND = 3
ALGORITHM_A_ROUND_BOUNDARY_STOP_FILE = "algorithm_a_stop_after_round.json"
ALGORITHM_A_WORKER_COHORT_TRANSITION_FILE = (
    "algorithm_a_worker_cohort_transition.json"
)
ALGORITHM_A_WORKER_COHORT_TRANSITION_CONTRACT = (
    "algorithm_a_worker_cohort_transition_v1"
)
ALGORITHM_A_PARALLELISM_INFERRED_COMPARISON = (
    "parallelism_inferred_comparison_v1"
)


def full_factorial_parameter_vectors(
    ranges: Mapping[str, framework.ParameterRange],
    parameter_names: Sequence[str] = framework.ACTIVE_PARAMS,
) -> List[Tuple[int, Dict[str, float], Dict[str, str]]]:
    """Return the deterministic lane-local L/R Cartesian product."""

    names = tuple(str(name) for name in parameter_names)
    if not names:
        raise ValueError("Algorithm A requires at least one active parameter")
    levels: Dict[str, Tuple[float, float]] = {}
    for name in names:
        if name not in ranges:
            raise KeyError(f"Algorithm A range missing for {name}")
        value = ranges[name]
        levels[name] = interval_pair_points(
            float(value.min_value),
            float(value.max_value),
        )
    vectors: List[Tuple[int, Dict[str, float], Dict[str, str]]] = []
    for mask, sides in enumerate(itertools.product((0, 1), repeat=len(names))):
        params = {
            name: float(levels[name][side])
            for name, side in zip(names, sides)
        }
        labels = {
            name: ("R" if side else "L")
            for name, side in zip(names, sides)
        }
        vectors.append((mask, params, labels))
    expected = 1 << len(names)
    signatures = {
        tuple(format(float(params[name]), ".17g") for name in names)
        for _mask, params, _labels in vectors
    }
    if len(vectors) != expected or len(signatures) != expected:
        raise RuntimeError("Algorithm A Cartesian product is not complete and unique")
    return vectors


class AlgorithmALaneController(framework.LaneController):
    """Exhaustive Algorithm A with the proven physical outer framework."""

    def configured_replay_parallelism(self) -> int:
        """Allow an explicit, bounded Algorithm A replay farm width."""

        algorithm_a = self.cfg.get("algorithm_a")
        if not isinstance(algorithm_a, Mapping):
            raise RuntimeError("Algorithm A configuration is missing")
        method = str(algorithm_a.get("method") or "")
        if method != ALGORITHM_A_METHOD:
            raise RuntimeError(
                f"Algorithm A method mismatch: {method!r} != {ALGORITHM_A_METHOD!r}"
            )
        parallelism = int(algorithm_a.get("real_replay_parallelism") or 0)
        if not 2 <= parallelism <= 16:
            raise RuntimeError(
                "Algorithm A real_replay_parallelism must be between 2 and 16: "
                f"{parallelism}"
            )
        return parallelism

    def __init__(self, cfg_path: Path, output_dir: Path, target: str) -> None:
        super().__init__(cfg_path, output_dir, target)
        algorithm_a = self.cfg.get("algorithm_a")
        if not isinstance(algorithm_a, Mapping):
            raise RuntimeError("Algorithm A configuration is missing")
        configured_parameters = tuple(
            str(name) for name in (algorithm_a.get("active_parameters") or ())
        )
        if configured_parameters != tuple(framework.ACTIVE_PARAMS):
            raise RuntimeError(
                "Algorithm A active parameter order differs from the formal lane contract"
            )
        expected_count = 1 << len(configured_parameters)
        if int(algorithm_a.get("candidates_per_round") or 0) != expected_count:
            raise RuntimeError("Algorithm A candidate cardinality contract mismatch")
        if int(algorithm_a.get("real_replay_parallelism") or 0) != int(
            self.replay_parallelism
        ):
            raise RuntimeError("Algorithm A replay parallelism contract mismatch")
        if self.outer_pruning_policy.get("enabled") is True:
            raise RuntimeError("Algorithm A forbids outer pre-replay pruning")
        if self.algorithm_cfg.get("historical_valid_replay_seed_path"):
            raise RuntimeError("fresh Algorithm A must not import historical replay labels")
        self.algorithm_a_cfg = copy.deepcopy(dict(algorithm_a))
        self.algorithm_a_candidates_per_round = expected_count
        self.algorithm_a_max_technical_retries = int(
            algorithm_a.get("max_technical_retries_per_round")
            or ALGORITHM_A_MAX_TECHNICAL_RETRIES_PER_ROUND
        )

    def load_or_initialize_state(self) -> Dict[str, Any]:
        self.state_path = self.output_dir / "algorithm_a_state.json"
        self.log_path = self.output_dir / "algorithm_a.jsonl"
        state = super().load_or_initialize_state()
        method = str(state.get("method") or "")
        if self.state_path.is_file() and method not in {"", ALGORITHM_A_METHOD}:
            raise RuntimeError(
                f"Algorithm A state method mismatch: {method!r} != {ALGORITHM_A_METHOD!r}"
            )
        state["method"] = ALGORITHM_A_METHOD
        state["algorithm_variant"] = "A"
        state["candidate_search_contract"] = ALGORITHM_A_SEARCH_CONTRACT
        state.setdefault("algorithm_a_round_checkpoint", None)
        return state

    def log(self, event: str, **extra: Any) -> None:
        super().log(
            event,
            algorithm_variant="A",
            method=ALGORITHM_A_METHOD,
            candidate_search_contract=ALGORITHM_A_SEARCH_CONTRACT,
            inherited_event_namespace="algorithm_c_outer_framework_compatibility",
            **extra,
        )

    def physical_replay_batch_limit(self, batch_kind: str) -> int:
        if batch_kind in {
            ALGORITHM_A_BATCH_KIND,
            ALGORITHM_A_TRANSITION_BATCH_KIND,
        }:
            return self.algorithm_a_candidates_per_round
        return super().physical_replay_batch_limit(batch_kind)

    def physical_replay_candidate_slot(
        self,
        candidate: Mapping[str, Any],
        *,
        local_position: int,
        batch_kind: str,
    ) -> int:
        if batch_kind != ALGORITHM_A_BATCH_KIND:
            return super().physical_replay_candidate_slot(
                candidate,
                local_position=local_position,
                batch_kind=batch_kind,
            )
        candidate_rank = int(candidate.get("rank") or 0)
        if not 1 <= candidate_rank <= self.algorithm_a_candidates_per_round:
            raise ValueError(
                "Algorithm A candidate rank cannot define a stable physical slot: "
                f"rank={candidate_rank} expected=1..{self.algorithm_a_candidates_per_round}"
            )
        return candidate_rank

    def physical_replay_label_suffix(
        self,
        candidate: Mapping[str, Any],
        *,
        candidate_slot: int,
        batch_kind: str,
    ) -> str:
        if batch_kind != ALGORITHM_A_BATCH_KIND:
            return super().physical_replay_label_suffix(
                candidate,
                candidate_slot=candidate_slot,
                batch_kind=batch_kind,
            )
        _ = candidate, candidate_slot
        next_wave_sequence = int(
            self.state.get("formal_candidate_wave_sequence") or 0
        ) + 1
        return f"_a{next_wave_sequence:04d}"

    def physical_replay_candidate_slot_width(self, batch_kind: str) -> int:
        if batch_kind == ALGORITHM_A_BATCH_KIND:
            return 3
        return super().physical_replay_candidate_slot_width(batch_kind)

    def validate_top5_audit_records(
        self,
        candidates: Sequence[Mapping[str, Any]],
    ) -> None:
        """Validate exhaustive candidate evidence without Top-K plan pruning."""

        expected_resources = {"cpu", "memory", "io"}
        seen_signatures: Set[Tuple[str, ...]] = set()
        for position, candidate in enumerate(candidates, start=1):
            missing = sorted(framework.TOP5_AUDIT_FIELDS - set(candidate))
            if missing:
                raise RuntimeError(
                    f"formal Algorithm A candidate {position} missing audit fields: {missing}"
                )
            for field in ("feature_ratio", "predicted_resource", "utilization"):
                value = candidate.get(field)
                if not isinstance(value, Mapping) or not expected_resources.issubset(value):
                    raise RuntimeError(
                        f"formal Algorithm A candidate {position} has incomplete {field}"
                    )
            try:
                score = float(candidate["candidate_score"])
            except (TypeError, ValueError) as exc:
                raise RuntimeError(
                    f"formal Algorithm A candidate {position} has invalid candidate_score"
                ) from exc
            if not math.isfinite(score):
                raise RuntimeError(
                    f"formal Algorithm A candidate {position} has non-finite candidate_score"
                )
            framework.validate_zero_gradient_delta(candidate, position)
            if set(candidate.get("active_subset") or ()) != set(framework.ACTIVE_PARAMS):
                raise RuntimeError(
                    f"formal Algorithm A candidate {position} does not move every active parameter"
                )
            params = candidate.get("optimized_params")
            if not isinstance(params, Mapping) or set(params) != set(framework.ACTIVE_PARAMS):
                raise RuntimeError(
                    f"formal Algorithm A candidate {position} parameter vector is incomplete"
                )
            signature = framework.exact_param_signature(params)
            if signature in seen_signatures:
                raise RuntimeError(
                    f"formal Algorithm A candidate {position} duplicates a parameter vector"
                )
            seen_signatures.add(signature)
            if not framework.candidate_full_plan_fingerprint(candidate):
                raise RuntimeError(
                    f"formal Algorithm A candidate {position} lacks a full plan fingerprint"
                )
            if candidate.get("qps_used_for_inner_scoring") is not False:
                raise RuntimeError("Algorithm A candidate audit must exclude QPS")

    def _utility_calculator(self) -> ResourceUtilityCalculator:
        options = copy.deepcopy(framework.formal_inner_scoring_options(self.target))
        options.pop("directional_scoring_contract_required", None)
        return ResourceUtilityCalculator(**options)

    def generate_rankings(
        self,
        round_index: int,
        *,
        parameter_ranges: Optional[Mapping[str, framework.ParameterRange]] = None,
        ranking_scope: str = "incumbent_ranges",
        relative_step_probe_pct: Optional[Sequence[float]] = None,
    ) -> List[Dict[str, Any]]:
        if relative_step_probe_pct is not None:
            raise RuntimeError("Algorithm A does not support gradient-step expansion")
        selected_ranges = parameter_ranges or self.ranges
        vectors = full_factorial_parameter_vectors(selected_ranges)
        incumbent_params = {
            name: float(value)
            for name, value in self.state["incumbent_params"].items()
        }
        incumbent_validation = (
            self.state.get("incumbent_validation")
            or self.state.get("canonical_baseline")
        )
        if not isinstance(incumbent_validation, Mapping):
            raise RuntimeError("Algorithm A requires a valid baseline before ranking")
        baseline_real = {
            name: float((incumbent_validation.get("resources") or {})[name])
            for name in ("cpu", "memory", "io")
        }
        queries, baseline_probe, predictor_cfg = self.queries_and_probe(
            incumbent_params
        )
        ratio_estimator = framework.RatioScaleEstimator(
            predictor_cfg,
            queries,
            baseline_probe,
            baseline_real,
            incumbent_params,
            self.experience_artifact["resource_prediction_calibration"],
        )
        calculator = self._utility_calculator()
        initial_score = calculator.calculate_utility_score(
            baseline_real,
            self.capacity,
        )
        rankings: List[Dict[str, Any]] = []
        accepted_index = int(self.state.get("accepted") or 0)
        for canonical_order, (mask, params, side_labels) in enumerate(
            vectors,
            start=1,
        ):
            resources, _stats, metadata = ratio_estimator(
                {"contract": "read_only_full_workload_plan_set"},
                copy.deepcopy(params),
            )
            final_score = calculator.calculate_utility_score(
                resources,
                self.capacity,
            )
            ranking: Dict[str, Any] = {
                "subset_id": f"algorithm_a_a{accepted_index:02d}_mask_{mask:03d}",
                "active_subset": list(framework.ACTIVE_PARAMS),
                "canonical_subset_order": canonical_order,
                "optimized_params": copy.deepcopy(params),
                "initial_params": copy.deepcopy(incumbent_params),
                "initial_resource": copy.deepcopy(baseline_real),
                "final_resource": copy.deepcopy(resources),
                "initial_score": initial_score,
                "final_score": final_score,
                "score_improvement": final_score - initial_score,
                "iterations": 0,
                "log_path": str(
                    self.output_dir
                    / "factorial_logs"
                    / f"accepted_{accepted_index:02d}"
                ),
                "final_estimation_metadata": copy.deepcopy(metadata),
                "candidate_origin": "algorithm_a_lane_local_full_factorial",
                "probe_kind": "complete_lr_cartesian_product",
                "used_for_gradient": False,
                "algorithm_a_non_gradient_search": True,
                "algorithm_a_factor_mask": mask,
                "algorithm_a_factor_levels": copy.deepcopy(side_labels),
                "algorithm_a_canonical_order": canonical_order,
                "qps_used_for_inner_scoring": False,
                "prediction_used_for_selection": False,
                "prediction_used_for_ordering": False,
            }
            enriched = attach_three_level_fingerprint(ranking, self.capacity)
            enriched["dScore/dU"] = {
                "available": False,
                "reason": "algorithm_a_is_exhaustive_not_gradient_search",
            }
            enriched["T"] = {
                "available": False,
                "reason": "prediction_is_audit_only_for_algorithm_a",
            }
            enriched["confidence"] = {
                "value": float(
                    min((metadata.get("plan_model_confidence") or {}).values() or [0.0])
                ),
                "classification": "audit_only_not_physical_admission",
            }
            enriched["g_j_semantics"] = (
                "normalized_parameter_direction_audit_not_gradient"
            )
            enriched["rank"] = canonical_order
            rankings.append(enriched)

        plan_counts: Dict[str, int] = {}
        for ranking in rankings:
            fingerprint = framework.candidate_full_plan_fingerprint(ranking)
            plan_counts[fingerprint] = plan_counts.get(fingerprint, 0) + 1
        for ranking in rankings:
            fingerprint = framework.candidate_full_plan_fingerprint(ranking)
            ranking["numeric_refinement"] = plan_counts[fingerprint] > 1
            ranking["numeric_refinement_reason"] = (
                "algorithm_a_replays_every_parameter_vector_regardless_of_plan_identity"
                if ranking["numeric_refinement"]
                else "unique_full_plan_for_parameter_vector"
            )
        if len(rankings) != self.algorithm_a_candidates_per_round:
            raise RuntimeError("Algorithm A generated an incomplete factorial round")
        self.validate_top5_audit_records(rankings)
        self.log(
            "algorithm_a_full_factorial_generated",
            round_index=round_index,
            accepted_index=accepted_index,
            ranking_scope=ranking_scope,
            active_parameters=list(framework.ACTIVE_PARAMS),
            candidate_count=len(rankings),
            expected_candidate_count=self.algorithm_a_candidates_per_round,
            distinct_parameter_vector_count=len(
                {
                    framework.exact_param_signature(row["optimized_params"])
                    for row in rankings
                }
            ),
            distinct_full_plan_count=len(plan_counts),
            same_plan_parameter_vectors_retained=True,
            exhaustive_search_completed=True,
            prediction_used_for_selection=False,
            prediction_used_for_ordering=False,
            qps_used_for_inner_scoring=False,
            candidates=copy.deepcopy(rankings),
            physical_workload_invocations=0,
        )
        return rankings

    def select_outer_admitted_candidates(
        self,
        candidates: Sequence[Dict[str, Any]],
        *,
        round_index: int,
        batch_kind: str,
        limit: int = 0,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """Exclude only exact valid identities; never prune or reorder A."""

        requested_limit = int(limit) if int(limit) > 0 else len(candidates)
        identity_memory = self.state.get("valid_replay_identity_memory")
        if not isinstance(identity_memory, Mapping):
            identity_memory = framework.empty_valid_replay_identity_memory(
                self.workload_contract_sha256,
                self.target,
            )
        selected: List[Dict[str, Any]] = []
        cached: List[Dict[str, Any]] = []
        attempted: List[Dict[str, Any]] = []
        range_excluded: List[Dict[str, Any]] = []
        attempted_signatures = getattr(
            self,
            "_algorithm_a_attempted_signatures",
            set(),
        )
        for candidate in candidates:
            range_audit = framework.parameter_vector_range_audit(
                candidate.get("optimized_params"),
                self.ranges,
            )
            if range_audit.get("passed") is not True:
                range_excluded.append(
                    {
                        "rank": candidate.get("rank"),
                        "range_audit": range_audit,
                    }
                )
                continue
            signature = framework.exact_param_signature(
                candidate.get("optimized_params") or {}
            )
            if signature in attempted_signatures:
                attempted.append(
                    {
                        "rank": candidate.get("rank"),
                        "parameter_signature": list(signature),
                    }
                )
                continue
            identity = framework.assess_valid_replay_identity(
                identity_memory,
                candidate,
            )
            if identity.get("seen_valid_replay") is True:
                cached.append(
                    {
                        "rank": candidate.get("rank"),
                        "identity_assessment": identity,
                    }
                )
                continue
            if len(selected) < requested_limit:
                selected.append(candidate)
        audit = {
            "contract": ALGORITHM_A_SEARCH_CONTRACT,
            "round_index": round_index,
            "batch_kind": batch_kind,
            "input_candidate_count": len(candidates),
            "selected_count": len(selected),
            "terminal_physical_attempt_excluded_count": len(attempted),
            "terminal_physical_attempt_excluded": attempted,
            "exact_valid_replay_excluded_count": len(cached),
            "exact_valid_replay_excluded": cached,
            "range_excluded_count": len(range_excluded),
            "range_excluded": range_excluded,
            "hard_pruned_count": 0,
            "soft_deprioritized_count": 0,
            "prediction_direction_used": False,
            "candidate_score_used": False,
            "canonical_order_preserved": True,
            "technical_failures_used_as_valid_replay_identities": False,
        }
        self.log("algorithm_a_physical_replay_admission", **audit)
        return selected, audit

    def attempted_parameter_signatures(
        self,
        rankings: Sequence[Mapping[str, Any]],
    ) -> Tuple[Set[Tuple[str, ...]], Dict[str, int]]:
        """Recover every terminal physical attempt in the current factorial.

        Every reserved real-replay attempt is a terminal physical slot.  This
        includes technical failures before the workload process starts (for
        example, a timeout while applying PostgreSQL parameters).  Invalid
        results remain excluded from valid replay and acceptance, including a
        successful companion invalidated by the two-point wave completion
        domain.  They must not be replayed merely to fill the factorial again.
        """

        expected = {
            framework.exact_param_signature(row.get("optimized_params") or {})
            for row in rankings
        }
        outcomes: Dict[Tuple[str, ...], str] = {}
        if not self.log_path.is_file():
            return set(), {"valid": 0, "technical": 0}
        with self.log_path.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                try:
                    event = json.loads(raw_line)
                except (TypeError, ValueError):
                    continue
                if event.get("event") != "algorithm_c_candidate_replay":
                    continue
                validation = event.get("validation")
                if not isinstance(validation, Mapping):
                    continue
                candidate = validation.get("candidate_ranking")
                if not isinstance(candidate, Mapping):
                    continue
                signature = framework.exact_param_signature(
                    candidate.get("optimized_params") or {}
                )
                if signature not in expected:
                    continue
                reserved_real_replay_slot = bool(
                    validation.get("used_real_replay") is True
                )
                if not reserved_real_replay_slot:
                    continue
                # The first physical invocation owns this factorial slot.
                # Retained evidence from an older buggy retry must remain
                # auditable but cannot rewrite the original slot outcome.
                if signature in outcomes:
                    continue
                if framework.physical_valid(validation):
                    outcomes[signature] = "valid"
                else:
                    outcomes.setdefault(signature, "technical")
        return set(outcomes), {
            "valid": sum(value == "valid" for value in outcomes.values()),
            "technical": sum(
                value == "technical" for value in outcomes.values()
            ),
        }

    def _round_context(self) -> Dict[str, Any]:
        return {
            "accepted": int(self.state.get("accepted") or 0),
            "incumbent_params": copy.deepcopy(self.state.get("incumbent_params") or {}),
            "ranges": {
                name: {
                    "min_value": float(self.ranges[name].min_value),
                    "max_value": float(self.ranges[name].max_value),
                }
                for name in framework.ACTIVE_PARAMS
            },
            "active_parameters": list(framework.ACTIVE_PARAMS),
            "search_contract": ALGORITHM_A_SEARCH_CONTRACT,
        }

    def _rankings_artifact_path(self, context_sha256: str) -> Path:
        return (
            self.output_dir
            / "offline_factorial_candidates"
            / f"accepted_{int(self.state.get('accepted') or 0):02d}_{context_sha256[:16]}.json"
        )

    def load_or_generate_rankings(
        self,
        round_index: int,
        context_sha256: str,
    ) -> List[Dict[str, Any]]:
        path = self._rankings_artifact_path(context_sha256)
        if path.is_file():
            artifact = framework.read_json(path)
            rankings = artifact.get("candidates")
            if (
                artifact.get("contract") != ALGORITHM_A_SEARCH_CONTRACT
                or artifact.get("context_sha256") != context_sha256
                or not isinstance(rankings, list)
                or artifact.get("candidates_sha256")
                != framework.canonical_sha256(rankings)
            ):
                raise RuntimeError(f"Algorithm A cached ranking artifact is invalid: {path}")
            self.validate_top5_audit_records(rankings)
            self.log(
                "algorithm_a_full_factorial_artifact_reused",
                round_index=round_index,
                path=str(path),
                candidate_count=len(rankings),
                candidates_sha256=artifact["candidates_sha256"],
                prediction_recalculation_invocations=0,
                physical_workload_invocations=0,
            )
            return copy.deepcopy(rankings)
        rankings = self.generate_rankings(round_index)
        artifact = {
            "contract": ALGORITHM_A_SEARCH_CONTRACT,
            "context_sha256": context_sha256,
            "candidate_count": len(rankings),
            "candidates_sha256": framework.canonical_sha256(rankings),
            "candidates": rankings,
        }
        framework.write_json(path, artifact)
        return rankings

    def _start_or_resume_round(self, context_sha256: str) -> Tuple[int, bool]:
        checkpoint = self.state.get("algorithm_a_round_checkpoint")
        if (
            isinstance(checkpoint, Mapping)
            and checkpoint.get("context_sha256") == context_sha256
            and int(
                checkpoint.get("accepted_index")
                if checkpoint.get("accepted_index") is not None
                else -1
            )
            == int(self.state.get("accepted") or 0)
        ):
            return int(checkpoint["round_index"]), True
        round_index = int(self.state.get("round_index") or 0) + 1
        self.state["round_index"] = round_index
        self.state["algorithm_a_round_checkpoint"] = {
            "contract": "algorithm_a_crash_resumable_round_v1",
            "round_index": round_index,
            "accepted_index": int(self.state.get("accepted") or 0),
            "context_sha256": context_sha256,
            "technical_retry_count": 0,
            "status": "offline_generation",
        }
        self.state["phase"] = "offline_algorithm_a_full_factorial"
        self.save_state()
        return round_index, False

    def _complete_nonconvergence(self, reason: str, round_index: int) -> None:
        self.state["phase"] = "algorithm_a_nonconvergence_clean"
        self.state["nonconvergence"] = {
            "reason": reason,
            "round_index": round_index,
            "accepted": int(self.state.get("accepted") or 0),
            "accepted_target": self.accepted_target,
            "complete_factorial_evidence_required": True,
            "accepted_unchanged": True,
            "incumbent_unchanged": True,
            "ranges_unchanged": True,
        }
        self.state["algorithm_a_round_checkpoint"] = None
        self.save_state()
        self.restore_clean_baseline(reason=f"algorithm_a_nonconvergence_{reason}")
        self.log("algorithm_a_nonconvergence_clean_stop", **self.state["nonconvergence"])

    def requested_round_boundary_stop(self) -> Optional[Dict[str, Any]]:
        """Return an operator-authored clean boundary stop request, if present."""

        path = self.output_dir / ALGORITHM_A_ROUND_BOUNDARY_STOP_FILE
        if not path.is_file():
            return None
        try:
            payload = framework.read_json(path)
        except (OSError, ValueError, TypeError) as exc:
            raise RuntimeError(
                f"invalid Algorithm A round-boundary stop request: {path}: {exc}"
            ) from exc
        if not isinstance(payload, Mapping):
            raise RuntimeError(
                f"Algorithm A round-boundary stop request must be an object: {path}"
            )
        return copy.deepcopy(dict(payload))

    def round_boundary_stop_is_due(self, request: Mapping[str, Any]) -> bool:
        """Return True only after the requested complete round boundary.

        A resumable checkpoint means a formal round is still in progress.  A
        process restart inside that round must therefore resume the remaining
        physical points instead of treating startup as a round boundary.
        """

        target_raw = request.get("target_completed_round")
        if target_raw is None:
            return True
        try:
            target_round = int(target_raw)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                "Algorithm A round-boundary stop target must be an integer"
            ) from exc
        if target_round < 1:
            raise RuntimeError(
                "Algorithm A round-boundary stop target must be positive"
            )
        checkpoint = self.state.get("algorithm_a_round_checkpoint")
        if isinstance(checkpoint, Mapping):
            return False
        return int(self.state.get("round_index") or 0) >= target_round

    def continue_after_candidate_terminal_qualification_failure(
        self,
        qualification: Mapping[str, Any],
        *,
        batch_kind: str,
    ) -> bool:
        """Continue exhaustive replay after candidate-local failures only."""

        if batch_kind != ALGORITHM_A_BATCH_KIND:
            return False
        errors = [
            str(value)
            for value in (qualification.get("errors") or [])
            if str(value)
        ]
        if not errors:
            return False
        if qualification.get("semantic_non_target_bottleneck_resources"):
            return False
        return all(
            re.fullmatch(
                r"[A-Za-z0-9_.-]+:base_physical_validation_failed",
                value,
            )
            is not None
            for value in errors
        )

    def prepare_worker_execution_cohort_transition(
        self,
        *,
        baseline_keys: Set[str],
        configured_keys: Set[str],
    ) -> bool:
        """Archive the old domain and authorize a fresh N-worker baseline."""

        request_path = (
            self.output_dir / ALGORITHM_A_WORKER_COHORT_TRANSITION_FILE
        )
        if not request_path.is_file():
            return False
        request = framework.read_json(request_path)
        if not isinstance(request, Mapping):
            raise RuntimeError("Algorithm A worker cohort transition must be an object")
        if (
            request.get("contract")
            != ALGORITHM_A_WORKER_COHORT_TRANSITION_CONTRACT
            or request.get("authorized") is not True
            or request.get("preserve_accepted") is not True
            or request.get("preserve_ranges") is not True
            or request.get("preserve_canonical_comparison_scale") is not True
        ):
            raise RuntimeError("Algorithm A worker cohort transition contract mismatch")
        transition_id = str(request.get("transition_id") or "")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,96}", transition_id):
            raise RuntimeError("invalid Algorithm A worker cohort transition_id")
        if self.state.get("phase") != "stopped_by_signal_clean":
            raise RuntimeError(
                "worker cohort transition requires stopped_by_signal_clean state"
            )
        if self.state.get("algorithm_a_round_checkpoint") is not None:
            raise RuntimeError(
                "worker cohort transition requires a complete round boundary"
            )
        requested_from = {
            str(value) for value in (request.get("from_worker_keys") or [])
        }
        requested_to = {
            str(value) for value in (request.get("to_worker_keys") or [])
        }
        if requested_from != baseline_keys or requested_to != configured_keys:
            raise RuntimeError(
                "worker cohort transition key set mismatch: "
                f"request_from={sorted(requested_from)} "
                f"baseline={sorted(baseline_keys)} "
                f"request_to={sorted(requested_to)} "
                f"configured={sorted(configured_keys)}"
            )
        if int(request.get("from_replay_parallelism") or 0) != len(baseline_keys):
            raise RuntimeError("worker cohort transition source parallelism mismatch")
        if int(request.get("to_replay_parallelism") or 0) != self.replay_parallelism:
            raise RuntimeError("worker cohort transition target parallelism mismatch")
        requested_restore_parallelism = int(
            request.get("to_global_restore_parallelism") or 0
        )
        configured_restore_parallelism = max(
            1,
            framework.safe_int(
                os.environ.get("PG_ALGORITHM_C_GLOBAL_RESTORE_PARALLELISM"),
                2,
            ),
        )
        if requested_restore_parallelism != configured_restore_parallelism:
            raise RuntimeError(
                "worker cohort transition target restore parallelism mismatch: "
                f"request={requested_restore_parallelism} "
                f"configured={configured_restore_parallelism}"
            )

        requested_source_restore_parallelism = int(
            request.get("from_global_restore_parallelism") or 0
        )
        prior_restore_parallelism_values = {
            int(((row.get("restore") or {}).get("global_restore_parallelism")))
            for row in (self.state.get("baseline_replay_rows") or [])
            if isinstance(row, Mapping)
            and isinstance(row.get("restore"), Mapping)
            and (row.get("restore") or {}).get("global_restore_parallelism")
            is not None
        }
        if (
            not prior_restore_parallelism_values
            or prior_restore_parallelism_values
            != {requested_source_restore_parallelism}
        ):
            raise RuntimeError(
                "worker cohort transition source restore parallelism mismatch: "
                f"request={requested_source_restore_parallelism} "
                f"baseline={sorted(prior_restore_parallelism_values)}"
            )

        protected_fields = (
            "accepted",
            "round_index",
            "ranges",
            "incumbent_params",
            "best_feasible_champion",
            "accepted_candidate_archive",
        )
        protected_before = {
            key: copy.deepcopy(self.state.get(key)) for key in protected_fields
        }
        prior_domain_fields = (
            "baseline_replay_rows",
            "baseline_by_worker",
            "canonical_baseline",
            "incumbent_validation",
            "qps_noise_model",
            "executor_telemetry_baseline_model",
            "baseline_execution_domain",
            "worker_execution_cohort",
        )
        prior_domain = {
            key: copy.deepcopy(self.state.get(key)) for key in prior_domain_fields
        }
        if not isinstance(prior_domain.get("canonical_baseline"), Mapping):
            raise RuntimeError("worker cohort transition lacks prior canonical baseline")
        archive = {
            "contract": ALGORITHM_A_WORKER_COHORT_TRANSITION_CONTRACT,
            "transition_id": transition_id,
            "request": copy.deepcopy(dict(request)),
            "protected_semantic_state": protected_before,
            "protected_semantic_state_sha256": framework.canonical_sha256(
                protected_before
            ),
            "prior_execution_domain": prior_domain,
            "prior_execution_domain_sha256": framework.canonical_sha256(
                prior_domain
            ),
            "evidence_epoch_transition": copy.deepcopy(
                self.state.get(
                    "pending_execution_domain_evidence_epoch_transition"
                )
            ),
            "created_at_epoch_s": time.time(),
        }
        archive_dir = self.output_dir / "execution_domain_transitions"
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive_path = archive_dir / f"{transition_id}_prior_domain.json"
        if archive_path.exists():
            raise RuntimeError(f"worker cohort transition archive exists: {archive_path}")
        framework.write_json(archive_path, archive)

        comparison_mode = str(request.get("comparison_mode") or "physical_remeasure")
        if comparison_mode == ALGORITHM_A_PARALLELISM_INFERRED_COMPARISON:
            if (
                request.get("user_authorized") is not True
                or request.get("fresh_baseline_per_worker") is not False
                or request.get("incumbent_replay_required") is not False
                or int(request.get("physical_workload_invocations") or 0) != 0
                or int(request.get("valid_replay_count") or 0) != 0
                or int(request.get("accepted_increment") or 0) != 0
            ):
                raise RuntimeError(
                    "parallelism-inferred transition authorization contract mismatch"
                )
            source_map = request.get("inference_source_worker_map")
            if not isinstance(source_map, Mapping):
                raise RuntimeError(
                    "parallelism-inferred transition source worker map is missing"
                )
            normalized_source_map = {
                str(target_key): str(source_key)
                for target_key, source_key in source_map.items()
            }
            if set(normalized_source_map) != configured_keys:
                raise RuntimeError(
                    "parallelism-inferred transition target map mismatch"
                )
            if not set(normalized_source_map.values()).issubset(baseline_keys):
                raise RuntimeError(
                    "parallelism-inferred transition references an unknown source worker"
                )
            prior_by_worker = prior_domain.get("baseline_by_worker")
            if not isinstance(prior_by_worker, Mapping):
                raise RuntimeError(
                    "parallelism-inferred transition lacks source worker baselines"
                )
            inferred_by_worker: Dict[str, Dict[str, Any]] = {}
            inferred_worker_keys: List[str] = []
            for target_key in sorted(configured_keys):
                source_key = normalized_source_map[target_key]
                source_row = prior_by_worker.get(source_key)
                if not isinstance(source_row, Mapping):
                    raise RuntimeError(
                        "parallelism-inferred source baseline is missing: "
                        f"{source_key}"
                    )
                row = copy.deepcopy(dict(source_row))
                if target_key != source_key:
                    inferred_worker_keys.append(target_key)
                    row["label"] = (
                        f"algorithm_a_inferred_baseline_{target_key}_from_{source_key}"
                    )
                    row["worker_key"] = target_key
                    row["worker"] = target_key
                    row["used_real_replay"] = False
                    row["forced_new_real_replay"] = False
                    row["physical_workload_invocations"] = 0
                    row["valid_replay_count"] = 0
                    row["inferred_baseline"] = True
                    row["inference_contract"] = (
                        ALGORITHM_A_PARALLELISM_INFERRED_COMPARISON
                    )
                    row["inference_source_worker"] = source_key
                    row["comparison_source"] = (
                        "prior_two_worker_canonical_physical_baseline"
                    )
                inferred_by_worker[target_key] = row

            self.state["baseline_by_worker"] = inferred_by_worker
            self.state["baseline_replay_rows"] = copy.deepcopy(
                prior_domain.get("baseline_replay_rows") or []
            )
            self.state["canonical_baseline"] = copy.deepcopy(
                prior_domain["canonical_baseline"]
            )
            for field in (
                "incumbent_validation",
                "qps_noise_model",
                "executor_telemetry_baseline_model",
            ):
                if field in prior_domain:
                    self.state[field] = copy.deepcopy(prior_domain[field])
            self.state["worker_execution_cohort"] = (
                self.worker_execution_cohort_audit()
            )
            inferred_domain = framework.baseline_execution_domain_audit(
                [
                    row
                    for row in self.state["baseline_replay_rows"]
                    if isinstance(row, Mapping)
                ],
                self.state["canonical_baseline"],
                expected_no_mutex=(
                    framework.explicit_user_authorized_no_mutex_from_env()
                ),
                cohort=framework.algorithm_c_cohort_config_from_env(),
                allow_single_lane_no_mutex=(
                    framework.algorithm_a_single_lane_no_mutex_authorized(
                        getattr(self, "algorithm_cfg", {}),
                        framework.algorithm_c_cohort_config_from_env(),
                    )
                ),
            )
            if inferred_domain.get("passed") is not True:
                raise RuntimeError(
                    "parallelism-inferred baseline execution domain failed: "
                    f"{inferred_domain.get('errors')}"
                )
            self.state["baseline_execution_domain"] = inferred_domain
            transition = {
                "contract": ALGORITHM_A_WORKER_COHORT_TRANSITION_CONTRACT,
                "transition_id": transition_id,
                "status": "complete",
                "comparison_mode": comparison_mode,
                "archive_path": str(archive_path),
                "archive_sha256": framework.canonical_sha256(archive),
                "protected_semantic_state_sha256": archive[
                    "protected_semantic_state_sha256"
                ],
                "from_worker_keys": sorted(baseline_keys),
                "to_worker_keys": sorted(configured_keys),
                "inference_source_worker_map": normalized_source_map,
                "inferred_worker_keys": inferred_worker_keys,
                "from_replay_parallelism": len(baseline_keys),
                "to_replay_parallelism": self.replay_parallelism,
                "from_global_restore_parallelism": (
                    requested_source_restore_parallelism
                ),
                "to_global_restore_parallelism": configured_restore_parallelism,
                "accepted_preserved": int(self.state.get("accepted") or 0),
                "ranges_preserved": True,
                "incumbent_params_preserved": True,
                "canonical_comparison_scale_preserved": True,
                "physical_workload_invocations": 0,
                "valid_replay_count": 0,
                "accepted_increment": 0,
                "completed_at_epoch_s": time.time(),
            }
            protected_after = {
                key: copy.deepcopy(self.state.get(key))
                for key in protected_fields
            }
            if framework.canonical_sha256(protected_after) != archive[
                "protected_semantic_state_sha256"
            ]:
                raise RuntimeError(
                    "parallelism-inferred transition mutated protected semantic state"
                )
            self.state["execution_domain_transition"] = transition
            self.state.pop(
                "pending_execution_domain_evidence_epoch_transition",
                None,
            )
            self.state["phase"] = "formal_search"
            self.save_state()
            self.log(
                "algorithm_a_parallelism_inferred_worker_cohort_transition_complete",
                transition=copy.deepcopy(transition),
                baseline_execution_domain=copy.deepcopy(inferred_domain),
                worker_execution_cohort=copy.deepcopy(
                    self.state["worker_execution_cohort"]
                ),
                physical_workload_invocations=0,
                valid_replay_count=0,
                accepted_increment=0,
                accepted_unchanged=True,
                incumbent_unchanged=True,
                ranges_unchanged=True,
            )
            return True
        if comparison_mode != "physical_remeasure":
            raise RuntimeError(
                f"unsupported worker cohort comparison mode: {comparison_mode}"
            )

        for key in prior_domain_fields:
            self.state.pop(key, None)
        self.state["execution_domain_transition"] = {
            "contract": ALGORITHM_A_WORKER_COHORT_TRANSITION_CONTRACT,
            "transition_id": transition_id,
            "status": "fresh_baseline_pending",
            "archive_path": str(archive_path),
            "archive_sha256": framework.canonical_sha256(archive),
            "protected_semantic_state_sha256": archive[
                "protected_semantic_state_sha256"
            ],
            "from_worker_keys": sorted(baseline_keys),
            "to_worker_keys": sorted(configured_keys),
            "from_replay_parallelism": len(baseline_keys),
            "to_replay_parallelism": self.replay_parallelism,
            "from_global_restore_parallelism": (
                requested_source_restore_parallelism
            ),
            "to_global_restore_parallelism": configured_restore_parallelism,
            "accepted_preserved": int(self.state.get("accepted") or 0),
            "ranges_preserved": True,
            "incumbent_params_preserved": True,
            "canonical_comparison_scale_pending_restore": True,
        }
        self.state["phase"] = "execution_domain_transition_baseline_pending"
        self.save_state()
        self.log(
            "algorithm_a_worker_cohort_transition_authorized",
            transition=copy.deepcopy(self.state["execution_domain_transition"]),
            physical_workload_invocations=0,
            accepted_unchanged=True,
            incumbent_unchanged=True,
            ranges_unchanged=True,
        )
        return True

    def prepare_valid_replay_identity_context_transition(
        self,
        state: Dict[str, Any],
        identity_memory: Mapping[str, Any],
    ) -> bool:
        """Start a new evidence epoch for an authorized replay-farm transition."""

        request_path = (
            self.output_dir / ALGORITHM_A_WORKER_COHORT_TRANSITION_FILE
        )
        if not request_path.is_file():
            return False
        request = framework.read_json(request_path)
        if not isinstance(request, Mapping):
            return False
        if (
            request.get("contract")
            != ALGORITHM_A_WORKER_COHORT_TRANSITION_CONTRACT
            or request.get("authorized") is not True
            or request.get("preserve_accepted") is not True
            or request.get("preserve_ranges") is not True
            or request.get("preserve_canonical_comparison_scale") is not True
        ):
            return False
        if (
            state.get("phase") != "stopped_by_signal_clean"
            or state.get("algorithm_a_round_checkpoint") is not None
        ):
            return False
        transition_id = str(request.get("transition_id") or "")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,96}", transition_id):
            return False
        epoch_start_s = time.time()
        archive = {
            "contract": ALGORITHM_A_WORKER_COHORT_TRANSITION_CONTRACT,
            "transition_id": transition_id,
            "archived_at_epoch_s": epoch_start_s,
            "prior_valid_replay_identity_memory": copy.deepcopy(
                dict(identity_memory)
            ),
            "prior_valid_replay_identity_memory_sha256": (
                framework.canonical_sha256(identity_memory)
            ),
            "prior_outer_accept_reject_pruning": copy.deepcopy(
                state.get("outer_accept_reject_pruning")
            ),
            "prior_outer_accept_reject_pruning_sha256": (
                framework.canonical_sha256(
                    state.get("outer_accept_reject_pruning")
                )
            ),
            "old_workload_contract_sha256": str(
                identity_memory.get("workload_contract_sha256") or ""
            ),
            "new_workload_contract_sha256": self.workload_contract_sha256,
        }
        archives = state.get("execution_domain_evidence_epoch_archives")
        archives = archives if isinstance(archives, list) else []
        archives.append(archive)
        state["execution_domain_evidence_epoch_archives"] = archives
        state["valid_replay_identity_memory"] = (
            framework.empty_valid_replay_identity_memory(
                self.workload_contract_sha256,
                self.target,
            )
        )
        state["outer_accept_reject_pruning"] = (
            framework.empty_outer_pruning_memory()
        )
        state["valid_replay_identity_epoch_start_s"] = epoch_start_s
        state["outer_pruning_epoch_start_s"] = epoch_start_s
        state["pending_execution_domain_evidence_epoch_transition"] = {
            "contract": ALGORITHM_A_WORKER_COHORT_TRANSITION_CONTRACT,
            "transition_id": transition_id,
            "epoch_start_s": epoch_start_s,
            "prior_valid_replay_identity_memory_sha256": archive[
                "prior_valid_replay_identity_memory_sha256"
            ],
            "prior_outer_accept_reject_pruning_sha256": archive[
                "prior_outer_accept_reject_pruning_sha256"
            ],
            "old_workload_contract_sha256": archive[
                "old_workload_contract_sha256"
            ],
            "new_workload_contract_sha256": self.workload_contract_sha256,
            "old_evidence_excluded_from_new_epoch": True,
            "technical_failures_excluded": True,
        }
        return True

    def complete_pending_worker_execution_cohort_transition(self) -> bool:
        """Restore the canonical scale and remeasure the incumbent on N workers."""

        transition = self.state.get("execution_domain_transition")
        if not isinstance(transition, Mapping):
            return True
        status = str(transition.get("status") or "")
        if status == "complete":
            return True
        if status not in {
            "fresh_baseline_pending",
            "incumbent_replay_pending",
            "incumbent_replay_technical_retry_required",
        }:
            raise RuntimeError(f"unsupported worker cohort transition status: {status}")
        archive_path = Path(str(transition.get("archive_path") or ""))
        archive = framework.read_json(archive_path)
        if framework.canonical_sha256(archive) != str(
            transition.get("archive_sha256") or ""
        ):
            raise RuntimeError("worker cohort transition archive SHA mismatch")
        protected_before = archive.get("protected_semantic_state")
        prior_domain = archive.get("prior_execution_domain")
        if not isinstance(protected_before, Mapping) or not isinstance(
            prior_domain, Mapping
        ):
            raise RuntimeError("worker cohort transition archive is incomplete")
        old_canonical = prior_domain.get("canonical_baseline")
        if not isinstance(old_canonical, Mapping):
            raise RuntimeError("worker cohort transition old canonical is missing")
        transition = copy.deepcopy(dict(transition))
        if status == "fresh_baseline_pending":
            measured_canonical = self.state.get("canonical_baseline")
            if not isinstance(measured_canonical, Mapping):
                raise RuntimeError("worker cohort transition fresh canonical is missing")
            transition["measured_new_cohort_canonical_sha256"] = (
                framework.canonical_sha256(measured_canonical)
            )
            transition["old_canonical_comparison_scale_sha256"] = (
                framework.canonical_sha256(old_canonical)
            )
            transition["canonical_comparison_scale_pending_restore"] = False
            transition["status"] = "incumbent_replay_pending"
            self.state["execution_domain_transition"] = transition
            self.state["canonical_baseline"] = copy.deepcopy(dict(old_canonical))
            transition_domain = framework.baseline_execution_domain_audit(
                [
                    row
                    for row in (self.state.get("baseline_replay_rows") or [])
                    if isinstance(row, Mapping)
                ],
                self.state["canonical_baseline"],
                expected_no_mutex=(
                    framework.explicit_user_authorized_no_mutex_from_env()
                ),
                cohort=framework.algorithm_c_cohort_config_from_env(),
                allow_single_lane_no_mutex=(
                    framework.algorithm_a_single_lane_no_mutex_authorized(
                        getattr(self, "algorithm_cfg", {}),
                        framework.algorithm_c_cohort_config_from_env(),
                    )
                ),
            )
            if transition_domain.get("passed") is not True:
                raise RuntimeError(
                    "worker cohort transition canonical scale has an incompatible "
                    f"execution domain: {transition_domain.get('errors')}"
                )
            self.state["baseline_execution_domain"] = transition_domain
            self.save_state()

        params = {
            name: float((self.state.get("incumbent_params") or {})[name])
            for name in framework.ACTIVE_PARAMS
        }
        candidates = [
            {
                "rank": index + 1,
                "optimized_params": copy.deepcopy(params),
                "confirmation_only": True,
                "three_level_fingerprint": {
                    "plan_level": (
                        f"worker_cohort_transition_{transition['transition_id']}_"
                        f"worker_{index + 1}"
                    )
                },
            }
            for index in range(self.replay_parallelism)
        ]
        rows = self.replay_batch(
            candidates,
            round_index=int(self.state.get("round_index") or 0),
            batch_kind=ALGORITHM_A_TRANSITION_BATCH_KIND,
        )
        if self.stop_requested:
            return False
        if not (
            len(rows) == self.replay_parallelism
            and all(framework.physical_valid(row) for row in rows)
        ):
            transition["status"] = "incumbent_replay_technical_retry_required"
            transition["valid_replay_count"] = sum(
                framework.physical_valid(row) for row in rows
            )
            self.state["execution_domain_transition"] = transition
            self.state["phase"] = "execution_domain_transition_technical_retry"
            self.save_state()
            self.log(
                "algorithm_a_worker_cohort_transition_incumbent_technical_failure",
                transition=copy.deepcopy(transition),
                accepted_unchanged=True,
                incumbent_unchanged=True,
                ranges_unchanged=True,
            )
            return False
        normalized = [
            framework.normalize_to_worker_baseline(
                row,
                self.state["baseline_by_worker"][str(row["worker_key"])],
                self.state["canonical_baseline"],
                self.capacity,
            )
            for row in rows
        ]
        incumbent = framework.aggregate_baseline(normalized, self.capacity)
        incumbent["label"] = "algorithm_a_transition_incumbent_aggregate"
        incumbent["candidate_ranking"] = {
            "optimized_params": copy.deepcopy(params),
            "candidate_origin": "worker_cohort_transition_incumbent_replay",
        }
        self.state["incumbent_validation"] = incumbent

        protected_after = {
            key: copy.deepcopy(self.state.get(key))
            for key in protected_before
        }
        if framework.canonical_sha256(protected_after) != str(
            transition.get("protected_semantic_state_sha256") or ""
        ):
            raise RuntimeError(
                "worker cohort transition mutated accepted/ranges/champion semantic state"
            )
        transition["status"] = "complete"
        transition["completed_at_epoch_s"] = time.time()
        transition["incumbent_replay_count"] = len(rows)
        transition["incumbent_validation_sha256"] = framework.canonical_sha256(
            incumbent
        )
        transition["accepted_unchanged"] = True
        transition["ranges_unchanged"] = True
        transition["champion_history_unchanged"] = True
        transition["evidence_epoch_transition"] = copy.deepcopy(
            self.state.get("pending_execution_domain_evidence_epoch_transition")
        )
        transition["old_valid_replay_evidence_excluded"] = True
        transition["old_outer_pruning_evidence_excluded"] = True
        self.state["execution_domain_transition"] = transition
        self.state.pop(
            "pending_execution_domain_evidence_epoch_transition",
            None,
        )
        self.state["phase"] = "formal_search"
        self.save_state()
        self.log(
            "algorithm_a_worker_cohort_transition_complete",
            transition=copy.deepcopy(transition),
            worker_execution_cohort=copy.deepcopy(
                self.state.get("worker_execution_cohort")
            ),
            accepted_unchanged=True,
            ranges_unchanged=True,
            champion_history_unchanged=True,
        )
        return True

    def run(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.log(
            "algorithm_a_lane_start",
            config=str(self.cfg_path),
            accepted=int(self.state["accepted"]),
            accepted_target=self.accepted_target,
            normalized_width_threshold=self.normalized_width_threshold,
            stability_confirmation_rounds=self.stability_confirmation_rounds,
            post_convergence_stability_required=False,
            active_parameters=list(framework.ACTIVE_PARAMS),
            candidates_per_round=self.algorithm_a_candidates_per_round,
            replay_parallelism=self.replay_parallelism,
            lane_local_parameter_ranges=True,
            prediction_used_for_selection=False,
            workload_contract=self.algorithm_cfg.get("workload_contract"),
        )
        if self.state.get("phase") in {
            "signal_stop_pending_clean_restore",
            "signal_stop_clean_restore_failed",
        }:
            self.log(
                "algorithm_a_interrupted_signal_stop_restore_recovery_start",
                prior_phase=self.state.get("phase"),
                physical_workload_invocations=0,
                formal_state_mutation=False,
            )
            self.finish_signal_stop(
                "startup_recover_interrupted_signal_stop"
            )
            return
        completed_without_stability = bool(
            self.state.get("phase") == "complete"
            and self.state.get("completion_contract")
            == framework.POST_CONVERGENCE_STABILITY_POLICY
            and self.state.get("post_convergence_stability_required") is False
        )
        completed_with_legacy_stability = bool(
            self.state.get("phase") == "complete"
            and self.state.get("stability_confirmation_protocol")
            == framework.STABILITY_CONFIRMATION_PROTOCOL
            and (self.state.get("repeatability") or {}).get("passed") is True
        )
        if completed_without_stability or completed_with_legacy_stability:
            self.write_authority_manifest(float(self.state["normalized_width"]))
            return
        self.baseline()
        if self.stop_requested:
            self.finish_signal_stop("signal_stop_after_baseline_boundary")
            return
        if not self.complete_pending_worker_execution_cohort_transition():
            if self.stop_requested:
                self.finish_signal_stop(
                    "signal_stop_during_worker_cohort_transition"
                )
            return
        self.verify_baseline_execution_domain()
        self.restore_clean_baseline(reason="post_startup_baseline")
        if not self.state.get("formal_started"):
            self.state["formal_started"] = True
            self.state["formal_started_at_epoch_s"] = time.time()
            self.state["phase"] = "formal_search"
            self.save_state()

        while (
            int(self.state["accepted"]) < self.accepted_target
            and not self.stop_requested
        ):
            boundary_stop = self.requested_round_boundary_stop()
            if (
                boundary_stop is not None
                and self.round_boundary_stop_is_due(boundary_stop)
            ):
                self.log(
                    "algorithm_a_requested_round_boundary_stop",
                    request=boundary_stop,
                    accepted=int(self.state.get("accepted") or 0),
                    round_index=int(self.state.get("round_index") or 0),
                    algorithm_a_round_checkpoint=self.state.get(
                        "algorithm_a_round_checkpoint"
                    ),
                    replay_parallelism=self.replay_parallelism,
                    formal_state_mutation=False,
                )
                self.finish_signal_stop("algorithm_a_requested_round_boundary")
                return
            if boundary_stop is not None:
                self.log(
                    "algorithm_a_requested_round_boundary_stop_deferred",
                    request=boundary_stop,
                    accepted=int(self.state.get("accepted") or 0),
                    round_index=int(self.state.get("round_index") or 0),
                    algorithm_a_round_checkpoint=self.state.get(
                        "algorithm_a_round_checkpoint"
                    ),
                    replay_parallelism=self.replay_parallelism,
                    formal_state_mutation=False,
                )
            context = self._round_context()
            context_sha256 = framework.canonical_sha256(context)
            checkpoint_before_round = self.state.get(
                "algorithm_a_round_checkpoint"
            )
            resumable_checkpoint = bool(
                isinstance(checkpoint_before_round, Mapping)
                and checkpoint_before_round.get("context_sha256")
                == context_sha256
                and int(
                    checkpoint_before_round.get("accepted_index")
                    if checkpoint_before_round.get("accepted_index") is not None
                    else -1
                )
                == int(self.state.get("accepted") or 0)
            )
            if (
                int(self.state.get("round_index") or 0) >= self.max_rounds
                and not resumable_checkpoint
            ):
                break
            round_index, resumed = self._start_or_resume_round(context_sha256)
            rankings = self.load_or_generate_rankings(round_index, context_sha256)
            candidate_set_sha256 = framework.canonical_sha256(
                [row["optimized_params"] for row in rankings]
            )
            checkpoint = self.state["algorithm_a_round_checkpoint"]
            checkpoint["candidate_set_sha256"] = candidate_set_sha256
            checkpoint["candidate_count"] = len(rankings)
            checkpoint["status"] = "physical_replay"
            self.state["phase"] = "algorithm_a_full_factorial_real_replay"
            self.save_state()

            (
                attempted_before,
                attempted_outcomes_before,
            ) = self.attempted_parameter_signatures(rankings)
            self._algorithm_a_attempted_signatures = attempted_before
            missing_candidates, admission = self.select_outer_admitted_candidates(
                rankings,
                round_index=round_index,
                batch_kind=ALGORITHM_A_BATCH_KIND,
                limit=self.algorithm_a_candidates_per_round,
            )
            self.log(
                "algorithm_a_round_resume_audit",
                round_index=round_index,
                resumed=resumed,
                complete_candidate_count=len(rankings),
                terminal_attempted_candidate_count=len(attempted_before),
                terminal_valid_candidate_count=attempted_outcomes_before["valid"],
                terminal_technical_candidate_count=(
                    attempted_outcomes_before["technical"]
                ),
                missing_physical_candidate_count=len(missing_candidates),
                candidate_set_sha256=candidate_set_sha256,
                complete_factorial_required_before_outer_decision=True,
            )
            new_rows: List[Dict[str, Any]] = []
            if missing_candidates:
                new_rows = self.replay_batch(
                    missing_candidates,
                    round_index=round_index,
                    batch_kind=ALGORITHM_A_BATCH_KIND,
                )
            if self.stop_requested:
                self.finish_signal_stop("signal_stop_during_algorithm_a_round")
                return

            attempted_after, attempted_outcomes_after = (
                self.attempted_parameter_signatures(rankings)
            )
            self._algorithm_a_attempted_signatures = attempted_after
            cached_rows = self.cached_valid_replay_rows(
                rankings,
                round_index=round_index,
                exclude_rows=new_rows,
                limit=self.algorithm_a_candidates_per_round,
            )
            valid_by_signature: Dict[Tuple[str, ...], Dict[str, Any]] = {}
            for row in [*cached_rows, *new_rows]:
                if not framework.physical_valid(row):
                    continue
                candidate = row.get("candidate_ranking")
                if not isinstance(candidate, Mapping):
                    continue
                signature = framework.exact_param_signature(
                    candidate.get("optimized_params") or {}
                )
                valid_by_signature[signature] = row
            expected_signatures = [
                framework.exact_param_signature(row["optimized_params"])
                for row in rankings
            ]
            complete_rows = [
                valid_by_signature[signature]
                for signature in expected_signatures
                if signature in valid_by_signature
            ]
            missing_attempt_signatures = [
                list(signature)
                for signature in expected_signatures
                if signature not in attempted_after
            ]
            if len(attempted_after) != self.algorithm_a_candidates_per_round:
                checkpoint = self.state["algorithm_a_round_checkpoint"]
                new_terminal_progress = len(attempted_after) - len(
                    attempted_before
                )
                checkpoint["technical_retry_count"] = (
                    0
                    if new_terminal_progress > 0
                    else int(checkpoint.get("technical_retry_count") or 0) + 1
                )
                checkpoint["status"] = "incomplete_technical_retry_required"
                checkpoint["valid_candidate_count"] = len(complete_rows)
                checkpoint["attempted_candidate_count"] = len(attempted_after)
                checkpoint["terminal_technical_candidate_count"] = (
                    attempted_outcomes_after["technical"]
                )
                checkpoint["missing_candidate_count"] = len(
                    missing_attempt_signatures
                )
                self.state["phase"] = "algorithm_a_incomplete_round_technical"
                self.save_state()
                self.log(
                    "algorithm_a_round_incomplete_technical",
                    round_index=round_index,
                    valid_candidate_count=len(complete_rows),
                    attempted_candidate_count=len(attempted_after),
                    terminal_technical_candidate_count=(
                        attempted_outcomes_after["technical"]
                    ),
                    expected_candidate_count=self.algorithm_a_candidates_per_round,
                    missing_candidate_count=len(missing_attempt_signatures),
                    missing_parameter_signatures=missing_attempt_signatures,
                    new_terminal_progress=new_terminal_progress,
                    consecutive_no_progress_retry_count=(
                        checkpoint["technical_retry_count"]
                    ),
                    technical_candidates_excluded_from_valid_and_acceptance=True,
                    accepted_unchanged=True,
                    incumbent_unchanged=True,
                    ranges_unchanged=True,
                )
                if (
                    int(checkpoint["technical_retry_count"])
                    >= self.algorithm_a_max_technical_retries
                ):
                    self.state["phase"] = "algorithm_a_technical_failure_stopped_clean"
                    self.save_state()
                    self.restore_clean_baseline(
                        reason="algorithm_a_technical_retry_budget_exhausted"
                    )
                    return
                continue

            required_tier = self.required_acceptance_tier()
            if not complete_rows:
                self._complete_nonconvergence(
                    "complete_factorial_has_no_valid_candidate",
                    round_index,
                )
                return
            accepted, valid_count = self.evaluate_batch(
                complete_rows,
                round_index=round_index,
                batch_kind=ALGORITHM_A_BATCH_KIND,
                evidence_source=(
                    "physical_and_cached_complete_factorial"
                    if cached_rows
                    else "physical_complete_factorial"
                ),
            )
            if valid_count != len(complete_rows):
                raise RuntimeError("Algorithm A valid replay accounting mismatch")
            self.log(
                "algorithm_a_complete_factorial_attempt_accounting",
                round_index=round_index,
                attempted_candidate_count=len(attempted_after),
                valid_candidate_count=valid_count,
                terminal_technical_candidate_count=(
                    attempted_outcomes_after["technical"]
                ),
                technical_candidates_excluded_from_valid_and_acceptance=True,
                all_parameter_vectors_physically_attempted=True,
            )
            if accepted is not None:
                self.accept(accepted, round_index)
                self.state["algorithm_a_round_checkpoint"] = None
                self.save_state()
                self.log(
                    "algorithm_a_round_accepted_complete_factorial",
                    round_index=round_index,
                    accepted=int(self.state["accepted"]),
                    attempted_candidate_count=len(attempted_after),
                    valid_candidate_count=valid_count,
                    terminal_technical_candidate_count=(
                        attempted_outcomes_after["technical"]
                    ),
                    required_acceptance_tier=required_tier,
                    candidate_set_sha256=candidate_set_sha256,
                )
                continue

            self.state["consecutive_missed_outer_rounds"] = int(
                self.state.get("consecutive_missed_outer_rounds") or 0
            ) + 1
            self.state["algorithm_a_round_checkpoint"] = None
            self.state["phase"] = "formal_search"
            self.save_state()
            self.log(
                "algorithm_a_round_rejected_complete_factorial",
                round_index=round_index,
                accepted=int(self.state["accepted"]),
                attempted_candidate_count=len(attempted_after),
                valid_candidate_count=valid_count,
                terminal_technical_candidate_count=(
                    attempted_outcomes_after["technical"]
                ),
                required_acceptance_tier=required_tier,
                next_required_acceptance_tier=self.required_acceptance_tier(),
                candidate_set_sha256=candidate_set_sha256,
                accepted_unchanged=True,
                incumbent_unchanged=True,
                ranges_unchanged=True,
                cached_redecision_next_round=True,
            )
            if required_tier == "fallback":
                self._complete_nonconvergence(
                    "complete_factorial_has_no_fallback_acceptable_candidate",
                    round_index,
                )
                return

        if self.stop_requested:
            self.finish_signal_stop("signal_stop")
            return
        if int(self.state["accepted"]) < self.accepted_target:
            self._complete_nonconvergence("max_rounds_exhausted", int(self.state["round_index"]))
            return
        final_normalized_width = 0.5 ** int(self.state["accepted"])
        if final_normalized_width > self.normalized_width_threshold + 1e-12:
            raise RuntimeError(
                "Algorithm A accepted target reached without satisfying width threshold"
            )
        self.finalize_without_post_convergence_stability(final_normalized_width)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--target",
        required=True,
        choices=("cpu", "memory", "io", "none"),
    )
    parser.add_argument("--finalize-existing-stability", action="store_true")
    args = parser.parse_args()
    controller = AlgorithmALaneController(
        args.config.resolve(),
        args.output_dir.resolve(),
        args.target,
    )

    def request_stop(signum: int, _frame: Any) -> None:
        controller.stop_requested = True
        controller.log(
            "algorithm_a_stop_requested",
            signal=signal.Signals(signum).name,
        )

    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, request_stop)
    if args.finalize_existing_stability:
        if not controller.finalize_existing_stability_evidence():
            raise RuntimeError("existing Algorithm A stability evidence does not pass")
    else:
        controller.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""XP v38 phase-aware state and strict automatic re-exploration gate."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
import statistics
import time
from typing import Any, Mapping


PHASE_CONTRACT = "xp_v38_atomic_offline_reexploration_phase_state_v1"
REEXPLORATION_CONTRACT = "xp_v38_stable_separable_shortfall_reexploration_v1"
EXPECTED_ROLES = [
    "trusted_calibrated_rank",
    "trusted_calibrated_rank",
    "trusted_calibrated_rank",
    "maximin_uncertain_probe",
    "maximin_uncertain_probe",
]
FORMAL_KEYS = (
    "accepted_count",
    "accepted_fingerprints",
    "incumbent",
    "incumbent_score",
    "incumbent_params",
    "parameter_ranges",
    "range_state",
    "gradient_state",
)


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _formal_snapshot(state: Mapping[str, Any]) -> dict[str, Any]:
    return {key: copy.deepcopy(state.get(key)) for key in FORMAL_KEYS}


def _selection_gate(
    selection: Mapping[str, Any],
    replayed_fingerprints: Any = (),
) -> dict[str, Any]:
    top5 = list(selection.get("top5") or [])
    roles = [str(item.get("selection_role") or "") for item in top5]
    identifiers = [
        str((item.get("three_level_fingerprint") or {}).get("id") or "")
        for item in top5
    ]
    replayed = {str(value) for value in (replayed_fingerprints or [])}
    probe5 = roles == ["maximin_uncertain_probe"] * 5
    trusted3_probe2 = roles == EXPECTED_ROLES
    fallback = selection.get("v36_resource_safe_maximin_fallback") or {}
    bounded_probe_bootstrap = bool(
        selection.get("batch_mode") == "diagnostic_validation"
        and probe5
        and fallback.get("contract")
        == "xp_v36_resource_safe_maximin_probe5_fallback_v1"
        and fallback.get("all_candidates_remain_uncertain_probes") is True
        and fallback.get("trusted_promotion_applied") is False
        and fallback.get("resource_envelope_relaxed") is False
        and fallback.get("manual_candidate_injection") is False
    )
    unknown_regime_probe_only = all(
        not (
            (item.get("plan_regime_calibration") or {}).get(
                "unknown_plan_probe_only"
            )
            is True
            and str(item.get("selection_role") or "").startswith("trusted")
        )
        for item in top5
    )
    checks = {
        "exactly_five": len(top5) == 5,
        "unique_three_level_fingerprints": (
            len(identifiers) == 5 and "" not in identifiers and len(set(identifiers)) == 5
        ),
        "all_novel_noncorner": all(
            item.get("boundary_corner") is False
            and item.get("near_corner_excluded_from_noncorner_gate") is not True
            for item in top5
        )
        and not (set(identifiers) & replayed),
        "role_stage_contract": bool(
            trusted3_probe2
            or bounded_probe_bootstrap
        ),
        "formal_requires_trusted3_probe2": bool(
            selection.get("batch_mode") != "formal" or trusted3_probe2
        ),
        "unknown_regime_probe_only": unknown_regime_probe_only,
        "replay_authorized": selection.get("replay_authorized") is True,
        "supported_batch_mode": selection.get("batch_mode")
        in {"diagnostic_validation", "formal"},
    }
    return {
        "contract": "xp_v38_automatic_explain_top5_preworkload_gate_v1",
        "checks": checks,
        "passed": all(checks.values()),
        "roles": roles,
        "selection_stage": (
            "trusted3_probe2_validation"
            if trusted3_probe2
            else "discovery_bootstrap_probe5"
            if bounded_probe_bootstrap
            else "invalid"
        ),
        "fingerprint_ids": identifiers,
        "manual_candidate_injection": False,
        "maximum_physical_calls": 5,
    }


def _finite(value: Any, label: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise RuntimeError(f"XP v38 {label} is not finite")
    return number


def build_two_observation_plan_calibration(
    original: Any,
    selection: Mapping[str, Any],
    replay: Mapping[str, Any],
) -> dict[str, Any]:
    """Aggregate the sealed prior and current exact-plan observations.

    The legacy builder counted only the current replay even though the repeat
    selection carries a SHA-verified prior observation.  Consequently all five
    known plan regimes stayed at observation_count=1 and were mislabeled as
    unknown probes.  This function admits exactly that sealed prior point; no
    historical or cross-plan observation is accepted.
    """

    calibration = copy.deepcopy(original(selection, replay))
    observations = {
        str(item.get("fingerprint_id") or ""): item
        for item in replay.get("successful") or []
    }
    upgraded = []
    for candidate in selection.get("top5") or []:
        fingerprint = candidate.get("three_level_fingerprint") or {}
        identifier = str(fingerprint.get("id") or "")
        plan = str(fingerprint.get("plan_level") or "")
        current = observations.get(identifier)
        history = list(candidate.get("exact_plan_regime_observation_history") or [])
        regime = (calibration.get("plan_regimes") or {}).get(plan)
        if current is None or regime is None or len(history) != 1:
            continue
        prior = history[0]
        source_path = Path(str(prior.get("source_replay") or ""))
        declared_sha = str(prior.get("source_replay_sha256") or "")
        if (
            not source_path.is_absolute()
            or not source_path.is_file()
            or not declared_sha
            or _sha256(source_path) != declared_sha
            or prior.get("score_admission_eligible") is not True
            or prior.get("score_regime") != "xp_v33_fresh0_band_target_v1"
        ):
            raise RuntimeError("XP v38 prior exact-plan observation SHA mismatch")
        predicted = candidate.get("predicted_resource") or {}
        prior_resource = prior.get("actual_resource") or {}
        current_resource = current.get("actual_resource") or {}
        resources = {}
        for name in ("cpu", "io", "memory"):
            base = _finite(predicted[name], f"predicted.{name}")
            first = _finite(prior_resource[name], f"prior.{name}")
            second = _finite(current_resource[name], f"current.{name}")
            if base <= 0.0 or first <= 0.0 or second <= 0.0:
                raise RuntimeError("XP v38 exact-plan resource must be positive")
            if not math.isclose(base, first, rel_tol=0.0, abs_tol=1e-12):
                raise RuntimeError(
                    "XP v38 repeat prediction is not the sealed first observation"
                )
            ratios = [1.0, second / first]
            median_scale = statistics.median(ratios)
            applied_scale = (
                median_scale if abs(median_scale - 1.0) >= 0.015 else 1.0
            )
            residuals = [value / applied_scale - 1.0 for value in ratios]
            median_residual = statistics.median(residuals)
            mad = statistics.median(
                abs(value - median_residual) for value in residuals
            )
            resources[name] = {
                "sample_count": 2,
                "median_actual_over_predicted": median_scale,
                "applied_scale": applied_scale,
                "robust_relative_sigma": max(0.005, 1.4826 * mad),
                "ratios": ratios,
                "source": "two_sealed_same_plan_exact_point_observations",
            }
        regime.update(
            {
                "observation_count": 2,
                "resources": resources,
                "trusted_eligible": True,
                "aggregation_contract": (
                    "xp_v38_two_sealed_same_plan_observation_calibration_v1"
                ),
                "prior_source_replay": str(source_path),
                "prior_source_replay_sha256": declared_sha,
                "current_fingerprint_id": identifier,
            }
        )
        upgraded.append(plan)
    calibration["v38_two_observation_plan_calibration"] = {
        "contract": "xp_v38_two_sealed_same_plan_observation_calibration_v1",
        "upgraded_plan_regimes": sorted(upgraded),
        "upgraded_plan_regime_count": len(upgraded),
        "unknown_plan_regime_probe_only": True,
        "historical_pre_fresh0_scores_used": False,
    }
    calibration["contract"] = (
        "xp_v38_exact_plan_two_observation_calibrated_acquisition_v1"
    )
    return calibration


def install_bundle(bundle_module: Any) -> Any:
    """Install exact-plan aggregation before deterministic bundle selection."""

    original = bundle_module.build_calibration
    original_calibrated_candidates = bundle_module._calibrated_candidates

    def build_calibration(
        selection: Mapping[str, Any],
        replay: Mapping[str, Any],
    ) -> dict[str, Any]:
        return build_two_observation_plan_calibration(
            original,
            selection,
            replay,
        )

    bundle_module.build_calibration = build_calibration

    def calibrated_candidates(
        raw_candidates: Any,
        calibration: Mapping[str, Any],
        excluded_ids: set[str],
    ) -> Any:
        transformed = []
        for raw in raw_candidates:
            # ``raw_candidates`` is a fresh per-build list. Mutate only its
            # compact fingerprint record here; deep-copying every 180-MiB
            # diagnostic row would double peak memory before the sealed
            # builder performs its own copy.
            candidate = dict(raw)
            fingerprint = copy.deepcopy(
                candidate.get("three_level_fingerprint") or {}
            )
            plan = str(fingerprint.get("plan_level") or "")
            regime = (calibration.get("plan_regimes") or {}).get(plan) or {}
            if int(regime.get("observation_count", 0)) >= 2:
                predicted = candidate.get("final_resource") or candidate.get(
                    "predicted_resource"
                ) or {}
                capability = calibration.get("capability") or {}
                utilization = {
                    name: (
                        float(predicted[name])
                        * float(regime["resources"][name]["applied_scale"])
                        / float(capability[name])
                    )
                    for name in ("cpu", "io", "memory")
                }
                resource_level = {
                    name: int(round(float(utilization[name]) / 0.005))
                    for name in ("cpu", "io", "memory")
                }
                feature_level = copy.deepcopy(
                    fingerprint.get("feature_ratio_level") or {}
                )
                if set(feature_level) != {"cpu", "io", "memory"}:
                    raise RuntimeError("XP v38 feature-ratio level is incomplete")
                payload = {
                    "plan": plan,
                    "feature_ratio_q005": feature_level,
                    "resource_utilization_q005": resource_level,
                }
                fingerprint.update(
                    {
                        "contract": "plan_feature_resource_three_level_v1",
                        "plan_level": plan,
                        "predicted_resource_level": resource_level,
                        "id": "three-level-sha256:" + _digest(payload),
                        "valid": True,
                        "validation_errors": [],
                        "v38_post_calibration_rebuilt": True,
                    }
                )
                candidate["three_level_fingerprint"] = fingerprint
                candidate["v38_post_calibration_fingerprint"] = {
                    "contract": (
                        "xp_v38_post_calibration_three_level_fingerprint_v1"
                    ),
                    "source_raw_fingerprint_id": str(
                        (raw.get("three_level_fingerprint") or {}).get("id") or ""
                    ),
                    "rebuilt_fingerprint_id": fingerprint["id"],
                    "exact_plan_observation_count": int(
                        regime["observation_count"]
                    ),
                    "unknown_regime_promoted": False,
                }
            transformed.append(candidate)
        return original_calibrated_candidates(
            transformed,
            calibration,
            excluded_ids,
        )

    bundle_module._calibrated_candidates = calibrated_candidates
    return bundle_module


def install_adapter(adapter: Any) -> Any:
    """Make the formal entry gate use two diagnostics plus formal third."""

    original = adapter._diagnostic_entry_gate

    def diagnostic_entry_gate(
        selection_path: Path,
        replay_path: Path,
    ) -> dict[str, Any]:
        gate = copy.deepcopy(original(selection_path, replay_path))
        if gate.get("contract") != "xp_fresh0_formal_entry_robust_exact_regime_v4":
            return gate
        selection = json.loads(selection_path.read_text(encoding="utf-8"))
        replay = json.loads(replay_path.read_text(encoding="utf-8"))
        current = {
            str(item.get("fingerprint_id") or ""): item
            for item in replay.get("successful") or []
        }
        trusted = [
            item
            for item in selection.get("top5") or []
            if str(item.get("selection_role") or "").startswith("trusted")
        ]
        sealed_two = len(trusted) == 3
        for candidate in trusted:
            identifier = str(
                (candidate.get("three_level_fingerprint") or {}).get("id") or ""
            )
            history = list(
                candidate.get("exact_plan_regime_observation_history") or []
            )
            if len(history) != 1 or identifier not in current:
                sealed_two = False
                break
            prior = history[0]
            source = Path(str(prior.get("source_replay") or ""))
            if (
                not source.is_absolute()
                or not source.is_file()
                or _sha256(source)
                != str(prior.get("source_replay_sha256") or "")
                or prior.get("score_admission_eligible") is not True
                or current[identifier].get("technical_failure") is True
            ):
                sealed_two = False
                break
        checks = dict(gate.get("checks") or {})
        checks.pop("robust_trusted_three_independent_stable_observations", None)
        checks["three_trusted_have_two_sealed_diagnostic_observations"] = sealed_two
        gate["checks"] = checks
        gate["contract"] = (
            "xp_v38_two_diagnostics_then_formal_third_entry_v1"
        )
        gate["contract_check_names"] = list(checks)
        gate["passed"] = all(bool(value) for value in checks.values())
        gate["formal_observation_policy"] = (
            "third_independent_exact_point_observation_median_rescore"
        )
        gate["formal_state_mutation_before_third_observation"] = False
        return gate

    adapter._diagnostic_entry_gate = diagnostic_entry_gate
    return adapter


class PhaseAwareStateStore:
    """Delegate state I/O while preserving truthful failure phase status."""

    def __init__(self, base: Any) -> None:
        self.base = base

    def load(self) -> dict[str, Any]:
        return self.base.load()

    def commit(self, state: Mapping[str, Any]) -> None:
        outgoing = copy.deepcopy(dict(state))
        current = self.base.load()
        phase = current.get("active_phase") or {}
        if outgoing.get("status") == "blocked_contract" and phase.get("contract") == PHASE_CONTRACT:
            stage = str(phase.get("stage") or "")
            outgoing["active_phase"] = copy.deepcopy(phase)
            if stage in {"offline_reexploration", "diagnostic_ready_preworkload", "formal_ready_preworkload"}:
                outgoing["status"] = "blocked_preworkload"
                outgoing["active_phase"]["terminal_status"] = "blocked_preworkload"
                outgoing["active_phase"]["physical_workload_calls"] = 0
            elif stage.endswith("replay_running"):
                outgoing["status"] = "blocked_replay"
                outgoing["active_phase"]["terminal_status"] = "blocked_replay"
        self.base.commit(outgoing)


def install_controller(continuation: Any) -> Any:
    """Install phase-aware wrappers around the existing controller."""

    base_controller = continuation.UnattendedContinuationController

    class V38Controller(base_controller):
        def __init__(
            self,
            *,
            state_store: Any,
            batch_root: Path,
            candidate_factory: Any,
            batch_runner: Any,
            baseline_restore: Any,
            cpuset: str = continuation.REQUIRED_CPUSET,
            max_batches: int = 100,
        ) -> None:
            phase_store = PhaseAwareStateStore(state_store)

            def phase_candidate_factory(
                batch_id: str,
                current: Mapping[str, Any],
                batch_dir: Path,
            ) -> Mapping[str, Any]:
                disk_state = phase_store.load()
                if _formal_snapshot(disk_state) != _formal_snapshot(current):
                    raise RuntimeError("XP v38 candidate phase formal-state mismatch")
                before = _formal_snapshot(disk_state)
                disk_state["status"] = "offline_reexploration"
                disk_state["active_phase"] = {
                    "contract": PHASE_CONTRACT,
                    "stage": "offline_reexploration",
                    "batch_id": batch_id,
                    "reason": "stable_or_separable_exact_regime_shortfall",
                    "started_unix_seconds": time.time(),
                    "formal_state_sha256_before": _digest(before),
                    "physical_workload_calls": 0,
                    "manual_candidate_injection": False,
                }
                phase_store.commit(disk_state)
                selection = candidate_factory(batch_id, current, batch_dir)
                gate = _selection_gate(
                    selection,
                    current.get("replayed_fingerprints") or [],
                )
                repeat_rejection = batch_dir / "exact_plan_repeat/automatic_reexploration.json"
                if repeat_rejection.is_file():
                    rejection = json.loads(repeat_rejection.read_text(encoding="utf-8"))
                    if rejection.get("reason") not in {
                        "insufficient_stable_separable_exact_regimes",
                        "observed_span_not_above_replay_noise",
                    }:
                        raise RuntimeError("XP v38 unknown exact-repeat rejection")
                    gate["exact_repeat_rejection"] = {
                        "path": str(repeat_rejection.resolve()),
                        "sha256": _sha256(repeat_rejection),
                        "reason": rejection.get("reason"),
                        "captured": True,
                    }
                if gate["passed"] is not True:
                    raise RuntimeError(
                        "XP v38 deterministic EXPLAIN Top-5 gate failed: "
                        + json.dumps(gate["checks"], sort_keys=True)
                    )
                after = phase_store.load()
                if _formal_snapshot(after) != before:
                    raise RuntimeError("XP v38 offline exploration mutated formal state")
                ready_stage = (
                    "formal_ready_preworkload"
                    if selection.get("batch_mode") == "formal"
                    else "diagnostic_ready_preworkload"
                )
                after["status"] = ready_stage
                after["active_phase"].update(
                    {
                        "stage": ready_stage,
                        "selection_gate": gate,
                        "selection_sha256": _digest(selection),
                        "candidate_count": 5,
                        "maximum_physical_calls": 5,
                    }
                )
                phase_store.commit(after)
                selected = copy.deepcopy(dict(selection))
                if selection.get("batch_mode") == "diagnostic_validation":
                    selected["v38_automatic_reexploration"] = {
                        "contract": REEXPLORATION_CONTRACT,
                        "selection_gate": gate,
                        "candidate_generation": "existing_deterministic_explain_three_level_pipeline",
                        "manual_candidate_injection": False,
                        "formal_state_mutation": False,
                    }
                return selected

            def phase_batch_runner(
                selection: Mapping[str, Any],
                batch_dir: Path,
                environment: Mapping[str, str],
            ) -> Mapping[str, Any]:
                disk_state = phase_store.load()
                phase = disk_state.get("active_phase") or {}
                expected_stage = (
                    "formal_ready_preworkload"
                    if selection.get("batch_mode") == "formal"
                    else "diagnostic_ready_preworkload"
                )
                if phase.get("stage") != expected_stage:
                    raise RuntimeError("XP v38 replay lacks ready preworkload phase")
                phase["stage"] = (
                    "formal_replay_running"
                    if selection.get("batch_mode") == "formal"
                    else "diagnostic_replay_running"
                )
                phase["maximum_physical_calls"] = 5
                disk_state["status"] = phase["stage"]
                disk_state["active_phase"] = phase
                phase_store.commit(disk_state)
                return batch_runner(selection, batch_dir, environment)

            super().__init__(
                state_store=phase_store,
                batch_root=batch_root,
                candidate_factory=phase_candidate_factory,
                batch_runner=phase_batch_runner,
                baseline_restore=baseline_restore,
                cpuset=cpuset,
                max_batches=max_batches,
            )

    continuation.UnattendedContinuationController = V38Controller
    return continuation

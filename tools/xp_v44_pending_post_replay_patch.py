#!/usr/bin/env python3
"""XP v44 post-replay routing for a completed v39 pending 3T+2P batch.

The second exact-point diagnostic has two possible automatic transitions:

* pass every fixed formal-entry check and promote only the three pending
  trusted points; or
* retain the clean replay as diagnostic evidence and ask the existing
  deterministic EXPLAIN pipeline for five new fingerprints.

In particular, a failed pending batch must not be fed back into the legacy
``probe5`` exact-repeat branch.  That branch expects a different source
contract and was the cause of the v43 pre-workload stop.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping


CONTRACT = "xp_v44_pending_second_observation_post_replay_gate_v1"
REEXPLORATION_CONTRACT = (
    "xp_v44_failed_pending_gate_routes_to_new_fingerprint_exploration_v1"
)
PENDING_CONTRACT = "xp_v39_exact_point_pending_trust_second_diagnostic_v1"
EXPECTED_ROLES = [
    "trusted_calibrated_rank",
    "trusted_calibrated_rank",
    "trusted_calibrated_rank",
    "maximin_uncertain_probe",
    "maximin_uncertain_probe",
]
RESOURCES = ("cpu", "io", "memory")
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
REQUIRED_CANDIDATE_FIELDS = (
    "feature_ratio",
    "predicted_resource",
    "utilization",
    "dScore/dU",
    "T",
    "confidence",
    "g_j",
    "eta",
    "delta_param",
    "candidate_score",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def formal_snapshot_digest(state: Mapping[str, Any]) -> str:
    snapshot = {key: copy.deepcopy(state.get(key)) for key in FORMAL_KEYS}
    return _digest(snapshot)


def _read_object(path: Path, label: str) -> dict[str, Any]:
    if not path.is_absolute() or not path.is_file():
        raise RuntimeError(f"XP v44 missing absolute {label}: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"XP v44 {label} is not an object")
    return value


def _fingerprint_id(candidate: Mapping[str, Any]) -> str:
    return str((candidate.get("three_level_fingerprint") or {}).get("id") or "")


def _finite(value: Any) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise RuntimeError("XP v44 metric is not finite")
    return number


def _is_pending(selection: Mapping[str, Any]) -> bool:
    return bool(
        (selection.get("v39_pending_trust") or {}).get("contract")
        == PENDING_CONTRACT
    )


def build_post_replay_gate(
    base_gate: Mapping[str, Any],
    selection_path: Path,
    replay_path: Path,
    pending_module: Any,
) -> dict[str, Any]:
    """Recompute the fixed gate from a sealed pending selection and clean5."""

    selection_path = selection_path.resolve()
    replay_path = replay_path.resolve()
    selection = _read_object(selection_path, "pending selection")
    replay = _read_object(replay_path, "pending replay")
    if not _is_pending(selection):
        return copy.deepcopy(dict(base_gate))

    exact_audit = pending_module._second_observation_audit(
        selection,
        replay,
        replay_path,
    )
    candidates = list(selection.get("top5") or [])
    roles = [str(item.get("selection_role") or "") for item in candidates]
    observations = {
        str(item.get("fingerprint_id") or ""): item
        for item in replay.get("successful") or []
    }
    selected_ids = [_fingerprint_id(item) for item in candidates]
    rows: list[dict[str, Any]] = []
    resource_factors: list[float] = []
    for candidate in candidates:
        identifier = _fingerprint_id(candidate)
        observation = observations.get(identifier)
        if observation is None:
            raise RuntimeError(f"XP v44 missing second observation: {identifier}")
        predicted = _finite(candidate["candidate_score"])
        actual = _finite(observation["actual_score"])
        role = str(candidate.get("selection_role") or "")
        rows.append(
            {
                "rank": int(candidate.get("rank") or len(rows) + 1),
                "role": role,
                "fingerprint_id": identifier,
                "predicted_score": predicted,
                "actual_score": actual,
                "score_error_actual_minus_predicted": actual - predicted,
            }
        )
        if role == "trusted_calibrated_rank":
            for name in RESOURCES:
                predicted_resource = _finite(candidate["predicted_resource"][name])
                actual_resource = _finite(observation["actual_resource"][name])
                if predicted_resource <= 0.0 or actual_resource <= 0.0:
                    raise RuntimeError("XP v44 resource evidence must be positive")
                resource_factors.append(
                    max(
                        predicted_resource / actual_resource,
                        actual_resource / predicted_resource,
                    )
                )

    trusted_rows = [row for row in rows if row["role"] == "trusted_calibrated_rank"]
    all_errors = [float(row["score_error_actual_minus_predicted"]) for row in rows]
    trusted_errors = [
        float(row["score_error_actual_minus_predicted"]) for row in trusted_rows
    ]
    actual_best_rank = max(rows, key=lambda row: float(row["actual_score"]))["rank"]
    trusted_spearman = base_gate.get("trusted_spearman")
    maximum_resource_factor = max(resource_factors) if resource_factors else None
    confidence_complete = all(
        all(field in candidate for field in REQUIRED_CANDIDATE_FIELDS)
        and isinstance(candidate.get("confidence"), Mapping)
        and bool(candidate.get("confidence"))
        and all(math.isfinite(float(value)) for value in candidate["confidence"].values())
        for candidate in candidates
    )
    first_observation_sources_sha_bound = True
    for candidate in candidates:
        binding = candidate.get("v39_exact_point_binding") or {}
        for path_field, sha_field in (
            ("source_selection", "source_selection_sha256"),
            ("source_replay", "source_replay_sha256"),
        ):
            source_path = Path(str(binding.get(path_field) or ""))
            expected_sha = str(binding.get(sha_field) or "")
            if (
                not source_path.is_absolute()
                or not source_path.is_file()
                or not expected_sha
                or _sha256(source_path) != expected_sha
            ):
                first_observation_sources_sha_bound = False
                break
        if not first_observation_sources_sha_bound:
            break
    base_checks = dict(base_gate.get("checks") or {})
    checks = {
        "pending_stage_exactly_three_trusted_two_probes": roles == EXPECTED_ROLES,
        "five_unique_noncorner_three_level_fingerprints": bool(
            len(candidates) == 5
            and len(set(selected_ids)) == 5
            and "" not in selected_ids
            and all(
                item.get("boundary_corner") is False
                and item.get("near_corner_excluded_from_noncorner_gate") is not True
                for item in candidates
            )
        ),
        "second_observation_clean5": bool(
            exact_audit.get("validated_trusted_count") == 3
            and int(replay.get("physical_workload_invocation_count", -1)) == 5
            and int(replay.get("successful_real_replay_count", -1)) == 5
            and int(replay.get("technical_failure_count", -1)) == 0
            and set(observations) == set(selected_ids)
        ),
        "pending_points_promoted_only_after_exact_second_observation": bool(
            exact_audit.get("validated_trusted_count") == 3
            and len(exact_audit.get("records") or []) == 3
        ),
        "first_observation_selection_and_replay_sha_bound": (
            first_observation_sources_sha_bound
        ),
        "trusted_only_spearman_positive": bool(
            trusted_spearman is not None and float(trusted_spearman) > 0.0
        ),
        "actual_best_in_predicted_top3": int(actual_best_rank) <= 3,
        "trusted_resource_no_order_of_magnitude_error": bool(
            maximum_resource_factor is not None
            and float(maximum_resource_factor) < 10.0
        ),
        "score_span_exceeds_replay_noise_bound": bool(
            base_checks.get("score_span_exceeds_replay_noise_bound") is True
        ),
        "candidate_diagnostics_and_confidence_complete": confidence_complete,
        "baseline_restored": replay.get("baseline_restore_verified") is True,
        "diagnostic_state_neutral": bool(
            replay.get("diagnostic_only") is True
            and replay.get("accepted_update_applied") is False
            and replay.get("incumbent_updated") is False
            and replay.get("range_updated") is False
            and replay.get("gradient_update_applied") is False
            and replay.get("state_mutations_applied_by_this_bundle") is False
        ),
        "probes_not_promoted_to_trusted": all(
            item.get("formal_trusted_evidence_eligible") is False
            for item in candidates[3:]
        ),
    }
    failed = [name for name, passed in checks.items() if not passed]
    return {
        "contract": CONTRACT,
        "passed": not failed,
        "checks": checks,
        "contract_check_names": list(checks),
        "failed_checks": failed,
        "selection_stage": "exact_point_pending_trust_second_diagnostic_complete",
        "selection_path": str(selection_path),
        "selection_sha256": _sha256(selection_path),
        "replay_path": str(replay_path),
        "replay_sha256": _sha256(replay_path),
        "trusted_only_spearman": trusted_spearman,
        "actual_best_predicted_rank": int(actual_best_rank),
        "all_score_mae": sum(abs(value) for value in all_errors) / len(all_errors),
        "all_score_bias_actual_minus_predicted": sum(all_errors) / len(all_errors),
        "trusted_score_mae": (
            sum(abs(value) for value in trusted_errors) / len(trusted_errors)
        ),
        "trusted_score_bias_actual_minus_predicted": (
            sum(trusted_errors) / len(trusted_errors)
        ),
        "predicted_score_span": base_gate.get("predicted_score_span"),
        "replay_noise_bound_2mae": base_gate.get("replay_noise_bound_2mae"),
        "maximum_trusted_resource_factor_error": maximum_resource_factor,
        "exact_point_promotion_audit": exact_audit,
        "formal_state_mutation": False,
        "new_workload_calls": 0,
        "manual_candidate_injection": False,
        "threshold_relaxation": False,
    }


def install_adapter(adapter: Any, pending_module: Any) -> Any:
    """Install the stage-aware gate and liveness-preserving re-exploration."""

    original_entry_gate = adapter._diagnostic_entry_gate
    original_generate = adapter.generate_selection

    def diagnostic_entry_gate(
        selection_path: Path,
        replay_path: Path,
    ) -> dict[str, Any]:
        base_gate = original_entry_gate(selection_path, replay_path)
        selection = _read_object(selection_path.resolve(), "entry selection")
        if not _is_pending(selection):
            return copy.deepcopy(dict(base_gate))
        return build_post_replay_gate(
            base_gate,
            selection_path,
            replay_path,
            pending_module,
        )

    def generate_selection(
        paths: Any,
        batch_id: str,
        state: Mapping[str, Any],
        batch_dir: Path,
    ) -> Mapping[str, Any]:
        last_batch = state.get("last_batch") or {}
        source_batch_id = str(last_batch.get("batch_id") or "")
        source_selection_path = paths.batch_root / source_batch_id / "selection.json"
        source_replay_path = paths.batch_root / source_batch_id / "replay_summary.json"
        if (
            last_batch.get("mode") == "diagnostic_validation"
            and source_selection_path.is_file()
            and source_replay_path.is_file()
        ):
            source_selection = _read_object(source_selection_path, "source selection")
            if _is_pending(source_selection):
                snapshot_before = formal_snapshot_digest(state)
                gate = diagnostic_entry_gate(source_selection_path, source_replay_path)
                if gate.get("passed") is not True:
                    routed_state = copy.deepcopy(dict(state))
                    routed_last = copy.deepcopy(dict(last_batch))
                    routed_last["mode"] = "diagnostic_quality_failed_reexploration"
                    routed_last["v44_post_replay_gate"] = copy.deepcopy(gate)
                    routed_state["last_batch"] = routed_last
                    audit = {
                        "contract": REEXPLORATION_CONTRACT,
                        "source_batch_id": source_batch_id,
                        "source_selection": str(source_selection_path.resolve()),
                        "source_selection_sha256": _sha256(source_selection_path),
                        "source_replay": str(source_replay_path.resolve()),
                        "source_replay_sha256": _sha256(source_replay_path),
                        "post_replay_gate": gate,
                        "failed_checks": list(gate.get("failed_checks") or []),
                        "next_action": (
                            "existing_deterministic_explain_pipeline_generates_"
                            "five_new_noncorner_fingerprints"
                        ),
                        "legacy_probe5_repeat_reused": False,
                        "formal_admission_allowed": False,
                        "formal_state_sha256_before": snapshot_before,
                        "state_mutation": False,
                        "new_workload_calls": 0,
                        "manual_candidate_injection": False,
                        "threshold_relaxation": False,
                    }
                    batch_dir.mkdir(parents=True, exist_ok=True)
                    audit_path = batch_dir / "v44_pending_post_replay_reroute.json"
                    audit_path.write_text(
                        json.dumps(audit, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8",
                    )
                    selected = copy.deepcopy(
                        dict(original_generate(paths, batch_id, routed_state, batch_dir))
                    )
                    if _is_pending(selected):
                        raise RuntimeError(
                            "XP v44 re-exploration retained stale pending stage"
                        )
                    if (
                        (selected.get("calibration_source") or {}).get("contract")
                        in {
                            "clean_probe5_exact_plan_first_observation_repeat_v1",
                            "clean_actual_best_rank_miss_score_span_repeat_v1",
                            "clean_nonpositive_spearman_score_span_repeat_v1",
                        }
                    ):
                        raise RuntimeError(
                            "XP v44 failed gate re-entered a legacy exact-repeat branch"
                        )
                    top5 = list(selected.get("top5") or [])
                    identifiers = [_fingerprint_id(item) for item in top5]
                    replayed = {str(value) for value in state.get("replayed_fingerprints") or []}
                    if not (
                        len(top5) == 5
                        and len(set(identifiers)) == 5
                        and "" not in identifiers
                        and not (set(identifiers) & replayed)
                        and all(
                            item.get("boundary_corner") is False
                            and item.get("near_corner_excluded_from_noncorner_gate") is not True
                            for item in top5
                        )
                        and formal_snapshot_digest(state) == snapshot_before
                    ):
                        raise RuntimeError(
                            "XP v44 deterministic re-exploration did not yield five new noncorner fingerprints"
                        )
                    selected["v44_pending_post_replay_reroute"] = {
                        **audit,
                        "audit_path": str(audit_path.resolve()),
                        "audit_sha256": _sha256(audit_path),
                        "generated_fingerprint_ids": identifiers,
                    }
                    return selected
        return original_generate(paths, batch_id, state, batch_dir)

    adapter._diagnostic_entry_gate = diagnostic_entry_gate
    adapter.generate_selection = generate_selection
    adapter.XP_V44_PENDING_POST_REPLAY_CONTRACT = CONTRACT
    return adapter


def install_phase_gate(phase_module: Any) -> Any:
    """Admit a v39-promoted formal batch without reapplying its pending gate."""

    original = phase_module._selection_gate

    def selection_gate(
        selection: Mapping[str, Any],
        replayed_fingerprints: Any = (),
    ) -> dict[str, Any]:
        pending = selection.get("v39_pending_trust") or {}
        promotion = selection.get("v39_exact_point_promotion") or {}
        if not (
            selection.get("batch_mode") == "formal"
            and pending.get("contract") == PENDING_CONTRACT
            and pending.get("formal_admission_allowed") is True
            and int(pending.get("formal_trusted_evidence_count", -1)) == 3
            and promotion.get("contract")
            == "xp_v39_exact_point_two_clean_observation_promotion_v1"
        ):
            return original(selection, replayed_fingerprints)

        top5 = list(selection.get("top5") or [])
        roles = [str(item.get("selection_role") or "") for item in top5]
        identifiers = [_fingerprint_id(item) for item in top5]
        replayed = {str(value) for value in replayed_fingerprints or []}
        trusted = top5[:3]
        probes = top5[3:]
        checks = {
            "exactly_five": len(top5) == 5,
            "unique_novel_three_level_fingerprints": bool(
                len(set(identifiers)) == 5
                and "" not in identifiers
                and not (set(identifiers) & replayed)
            ),
            "all_noncorner": all(
                item.get("boundary_corner") is False
                and item.get("near_corner_excluded_from_noncorner_gate") is not True
                for item in top5
            ),
            "formal_trusted3_probe2_roles": roles == EXPECTED_ROLES,
            "three_exact_points_promoted": bool(
                len(trusted) == 3
                and all(
                    item.get("pending_trusted") is False
                    and item.get("exact_plan_regime_validated") is True
                    and item.get("formal_trusted_evidence_eligible") is True
                    for item in trusted
                )
            ),
            "probes_not_promoted": bool(
                len(probes) == 2
                and all(
                    item.get("pending_trusted") is False
                    and item.get("formal_trusted_evidence_eligible") is False
                    and item.get("used_for_gradient") is False
                    and item.get("used_for_inner_incumbent_update") is False
                    for item in probes
                )
            ),
            "promotion_has_three_sha_bound_records": len(promotion.get("records") or [])
            == 3,
            "fixed_entry_gate_passed": bool(
                ((selection.get("formal_admission") or {}).get(
                    "diagnostic_entry_recheck"
                )
                or {}).get("passed")
                is True
            ),
            "replay_authorized": selection.get("replay_authorized") is True,
        }
        return {
            "contract": "xp_v44_promoted_pending3_formal_preworkload_gate_v1",
            "checks": checks,
            "passed": all(checks.values()),
            "roles": roles,
            "selection_stage": "promoted_exact_point_trusted3_formal_confirmation",
            "fingerprint_ids": identifiers,
            "manual_candidate_injection": False,
            "maximum_physical_calls": 5,
            "pending_stage_gate_reapplied": False,
        }

    phase_module._selection_gate = selection_gate
    phase_module.XP_V44_PROMOTED_FORMAL_GATE_CONTRACT = (
        "xp_v44_promoted_pending3_formal_preworkload_gate_v1"
    )
    return phase_module

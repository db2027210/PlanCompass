#!/usr/bin/env python3
"""XP v51: preserve the sealed formal object after pending-trust promotion.

The v39 adapter correctly proves the second exact-point observation, but its
formal branch returned a promoted copy of the *diagnostic* selection.  That
discarded the formal three-level fingerprints and the v37 preformal handoff.
The residual pending lineage was then routed back through the diagnostic-only
gate.  This patch merges the exact-point trust certificate into the already
sealed formal selection and installs a stage-specific formal gate.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


CONTRACT = "xp_v51_preserve_sealed_formal_after_pending_promotion_v1"
FORMAL_GATE_CONTRACT = "xp_v51_promoted_formal_stage_gate_v1"
PENDING_CONTRACT = "xp_v39_exact_point_pending_trust_second_diagnostic_v1"
PROMOTION_CONTRACT = "xp_v39_exact_point_two_clean_observation_promotion_v1"
FORMAL_PROMOTION_CONTRACT = (
    "sealed_clean5_top5_two_observation_formal_confirmation_v2"
)
EXPECTED_ROLES = [
    "trusted_calibrated_rank",
    "trusted_calibrated_rank",
    "trusted_calibrated_rank",
    "maximin_uncertain_probe",
    "maximin_uncertain_probe",
]
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


def _closure_value(function: Any, name: str) -> Any:
    closure = function.__closure__ or ()
    values = dict(zip(function.__code__.co_freevars, closure))
    if name not in values:
        raise RuntimeError(f"XP v51 missing sealed adapter closure: {name}")
    return values[name].cell_contents


def _validated_fingerprint(candidate: Mapping[str, Any], label: str) -> dict[str, Any]:
    fingerprint = candidate.get("three_level_fingerprint") or {}
    feature = fingerprint.get("feature_ratio_level") or {}
    resource = fingerprint.get("predicted_resource_level") or {}
    plan = str(fingerprint.get("plan_level") or "")
    if fingerprint.get("prediction_source") in {
        "robust_exact_point_history_median_rescored_v3",
        "two_independent_exact_point_resource_median_rescored_v2",
    }:
        payload = {
            "contract": "plan_feature_resource_three_level_v1",
            "plan_level": plan,
            "feature_ratio_level": feature,
            "predicted_resource_level": resource,
            "prediction_source": fingerprint["prediction_source"],
        }
    else:
        payload = {
            "plan": plan,
            "feature_ratio_q005": feature,
            "resource_utilization_q005": resource,
        }
    expected_id = "three-level-sha256:" + _digest(payload)
    if not (
        fingerprint.get("contract") == "plan_feature_resource_three_level_v1"
        and fingerprint.get("valid") is True
        and fingerprint.get("parameter_fallback_used") is False
        and not (fingerprint.get("validation_errors") or [])
        and plan.startswith("ratio-plan-sha256:")
        and set(feature) == {"cpu", "io", "memory"}
        and set(resource) == {"cpu", "io", "memory"}
        and all(isinstance(value, int) for value in feature.values())
        and all(isinstance(value, int) for value in resource.values())
        and str(fingerprint.get("id") or "") == expected_id
    ):
        raise RuntimeError(f"XP v51 invalid {label} three-level fingerprint")
    return copy.deepcopy(dict(fingerprint))


def _binding_rows(
    formal: Mapping[str, Any],
    prior: Mapping[str, Any],
    robust_module: Any,
) -> list[dict[str, Any]]:
    prior_by_params = {
        robust_module._params_key(item.get("candidate_params")): item
        for item in prior.get("top5") or []
    }
    rows = []
    for candidate in formal.get("top5") or []:
        params_sha = robust_module._params_key(candidate.get("candidate_params"))
        source = prior_by_params.get(params_sha)
        if source is None:
            raise RuntimeError("XP v51 formal point lacks exact diagnostic params")
        formal_fp = _validated_fingerprint(candidate, "formal")
        source_fp = _validated_fingerprint(source, "source")
        if not (
            formal_fp.get("source_fingerprint_id") == source_fp["id"]
            and formal_fp["plan_level"] == source_fp["plan_level"]
            and formal_fp["feature_ratio_level"] == source_fp["feature_ratio_level"]
            and str(candidate.get("selection_role") or "")
            == str(source.get("selection_role") or "")
        ):
            raise RuntimeError("XP v51 formal/source fingerprint lineage mismatch")
        rows.append(
            {
                "rank": int(candidate.get("rank") or 0),
                "selection_role": str(candidate.get("selection_role") or ""),
                "candidate_params_sha256": params_sha,
                "formal_fingerprint_id": formal_fp["id"],
                "formal_fingerprint_sha256": _digest(formal_fp),
                "source_fingerprint_id": source_fp["id"],
                "source_fingerprint_sha256": _digest(source_fp),
            }
        )
    if len(rows) != 5 or [row["rank"] for row in rows] != [1, 2, 3, 4, 5]:
        raise RuntimeError("XP v51 fingerprint binding is not an ordered Top-5")
    return rows


def merge_exact_trust_into_formal(
    prepared_formal: Mapping[str, Any],
    prior_diagnostic: Mapping[str, Any],
    replay_path: Path,
    pending_module: Any,
    robust_module: Any,
) -> dict[str, Any]:
    """Merge trust evidence without changing formal candidates or ordering."""

    selected = copy.deepcopy(dict(prepared_formal))
    prior = copy.deepcopy(dict(prior_diagnostic))
    promoted_prior = pending_module.promote_after_second_clean(prior, replay_path)
    promotion = promoted_prior.get("v39_exact_point_promotion") or {}
    if (
        selected.get("batch_mode") != "formal"
        or promotion.get("contract") != PROMOTION_CONTRACT
        or int(promotion.get("validated_trusted_count", -1)) != 3
        or (promoted_prior.get("v39_pending_trust") or {}).get("contract")
        != PENDING_CONTRACT
    ):
        raise RuntimeError("XP v51 exact-point promotion certificate mismatch")

    handoff = robust_module.validate_preformal_handoff(selected)
    formal_promotion = selected.get("promoted_from_diagnostic_validation") or {}
    pending_source = (prior.get("v39_pending_trust") or {}).get("source") or {}
    sources = handoff.get("source_files") or {}
    if not (
        str(formal_promotion.get("selection_path") or "")
        == str(sources.get("second_selection_path") or "")
        and str(formal_promotion.get("selection_sha256") or "")
        == str(sources.get("second_selection_sha256") or "")
        and str(formal_promotion.get("replay_path") or "")
        == str(sources.get("second_replay_path") or "")
        and str(formal_promotion.get("replay_sha256") or "")
        == str(sources.get("second_replay_sha256") or "")
        and str(pending_source.get("source_selection") or "")
        == str(sources.get("first_selection_path") or "")
        and str(pending_source.get("source_selection_sha256") or "")
        == str(sources.get("first_selection_sha256") or "")
        and str(pending_source.get("source_replay") or "")
        == str(sources.get("first_replay_path") or "")
        and str(pending_source.get("source_replay_sha256") or "")
        == str(sources.get("first_replay_sha256") or "")
    ):
        raise RuntimeError("XP v51 promotion and handoff evidence are cross-wired")

    prior_by_params = {
        robust_module._params_key(item.get("candidate_params")): item
        for item in promoted_prior.get("top5") or []
    }
    formal_top5 = list(selected.get("top5") or [])
    formal_ids_before = [
        str((item.get("three_level_fingerprint") or {}).get("id") or "")
        for item in formal_top5
    ]
    roles_before = [str(item.get("selection_role") or "") for item in formal_top5]
    params_before = [copy.deepcopy(item.get("candidate_params")) for item in formal_top5]
    if (
        len(formal_top5) != 5
        or len(prior_by_params) != 5
        or roles_before != EXPECTED_ROLES
        or len(set(formal_ids_before)) != 5
        or "" in formal_ids_before
    ):
        raise RuntimeError("XP v51 prepared formal Top-5 mismatch")

    binding_rows = _binding_rows(selected, prior, robust_module)
    promotion_records = {
        str(item.get("repeat_fingerprint_id") or ""): item
        for item in promotion.get("records") or []
    }
    if len(promotion_records) != 3:
        raise RuntimeError("XP v51 promotion must contain exactly three records")
    source_ids = []
    for candidate in formal_top5:
        params_key = robust_module._params_key(candidate.get("candidate_params"))
        source = prior_by_params.get(params_key)
        source_id = str(
            (candidate.get("three_level_fingerprint") or {}).get(
                "source_fingerprint_id"
            )
            or ""
        )
        if (
            source is None
            or source_id
            != str((source.get("three_level_fingerprint") or {}).get("id") or "")
            or str(candidate.get("selection_role") or "")
            != str(source.get("selection_role") or "")
        ):
            raise RuntimeError("XP v51 formal/source exact-parameter binding mismatch")
        source_ids.append(source_id)
        for field in (
            "pending_trusted",
            "exact_plan_regime_validated",
            "formal_trusted_evidence_eligible",
            "v39_validated_trust",
            "plan_regime_calibration",
        ):
            if field in source:
                candidate[field] = copy.deepcopy(source[field])
            else:
                candidate.pop(field, None)
        if str(candidate.get("selection_role") or "").startswith("trusted"):
            record = promotion_records.get(source_id)
            if not (
                record is not None
                and str(record.get("exact_params_sha256") or "") == params_key
                and str(record.get("first_replay_sha256") or "")
                == str(sources.get("first_replay_sha256") or "")
                and str(record.get("second_replay_sha256") or "")
                == str(sources.get("second_replay_sha256") or "")
                and candidate.get("v39_validated_trust") == record
                and candidate.get("pending_trusted") is False
            ):
                raise RuntimeError("XP v51 trusted promotion record binding mismatch")
        elif not (
            candidate.get("formal_trusted_evidence_eligible") is False
            and candidate.get("used_for_gradient") is False
            and candidate.get("used_for_inner_incumbent_update") is False
        ):
            raise RuntimeError("XP v51 probe was upgraded during formal promotion")

    selected["v39_pending_trust"] = copy.deepcopy(
        promoted_prior["v39_pending_trust"]
    )
    selected["v39_exact_point_promotion"] = copy.deepcopy(promotion)
    selected["v51_formal_promotion_routing"] = {
        "contract": CONTRACT,
        "diagnostic_source_fingerprint_ids": source_ids,
        "formal_fingerprint_ids": formal_ids_before,
        "binding_rows": binding_rows,
        "binding_sha256": _digest(binding_rows),
        "formal_candidate_order_changed": False,
        "formal_candidate_values_changed": False,
        "formal_candidate_fingerprints_changed": False,
        "preformal_handoff_preserved": True,
        "pending_diagnostic_gate_reapplied_to_formal": False,
        "manual_candidate_injection": False,
        "formal_state_mutation": False,
        "workload_calls_added": 0,
        "threshold_relaxation": False,
    }
    if (
        [str(item.get("selection_role") or "") for item in formal_top5]
        != roles_before
        or [item.get("candidate_params") for item in formal_top5] != params_before
        or [
            str((item.get("three_level_fingerprint") or {}).get("id") or "")
            for item in formal_top5
        ]
        != formal_ids_before
    ):
        raise RuntimeError("XP v51 formal candidate identity changed during merge")
    robust_module.validate_preformal_handoff(selected)
    return selected


def install_adapter(adapter: Any, pending_module: Any, robust_module: Any) -> Any:
    """Replace only v39's lossy formal-return branch."""

    pending_generate = adapter.generate_selection
    robust_generate = _closure_value(pending_generate, "original_generate")
    if pending_generate.__name__ != "generate_selection":
        raise RuntimeError("XP v51 unexpected pending adapter wrapper")

    def generate_selection(*args: Any, **kwargs: Any) -> Mapping[str, Any]:
        prepared = copy.deepcopy(dict(robust_generate(*args, **kwargs)))
        promotion = prepared.get("promoted_from_diagnostic_validation") or {}
        selection_path = Path(str(promotion.get("selection_path") or ""))
        replay_path = Path(str(promotion.get("replay_path") or ""))
        if (
            prepared.get("batch_mode") == "formal"
            and promotion.get("contract") == FORMAL_PROMOTION_CONTRACT
            and selection_path.is_absolute()
            and selection_path.is_file()
            and replay_path.is_absolute()
            and replay_path.is_file()
            and _sha256(selection_path) == str(promotion.get("selection_sha256") or "")
            and _sha256(replay_path) == str(promotion.get("replay_sha256") or "")
        ):
            prior = json.loads(selection_path.read_text(encoding="utf-8"))
            if (prior.get("v39_pending_trust") or {}).get("contract") == PENDING_CONTRACT:
                return merge_exact_trust_into_formal(
                    prepared,
                    prior,
                    replay_path,
                    pending_module,
                    robust_module,
                )
        source = prepared.get("calibration_source") or {}
        if source.get("contract") == "clean_probe5_exact_plan_first_observation_repeat_v1":
            return pending_module.sanitize_pending_selection(prepared)
        return prepared

    adapter.generate_selection = generate_selection
    adapter.XP_V51_FORMAL_PROMOTION_ROUTING_CONTRACT = CONTRACT
    return adapter


def promoted_formal_gate(
    selection: Mapping[str, Any],
    replayed_fingerprints: Any,
    robust_module: Any,
) -> dict[str, Any]:
    """Validate a promoted formal selection without diagnostic-stage routing."""

    top5 = list(selection.get("top5") or [])
    roles = [str(item.get("selection_role") or "") for item in top5]
    identifiers = [
        str((item.get("three_level_fingerprint") or {}).get("id") or "")
        for item in top5
    ]
    replayed = {str(value) for value in replayed_fingerprints or []}
    handoff = robust_module.validate_preformal_handoff(selection)
    promotion = selection.get("v39_exact_point_promotion") or {}
    pending = selection.get("v39_pending_trust") or {}
    admission = selection.get("formal_admission") or {}
    routing = selection.get("v51_formal_promotion_routing") or {}
    formal_promotion = selection.get("promoted_from_diagnostic_validation") or {}
    source_path = Path(str(formal_promotion.get("selection_path") or ""))
    if (
        not source_path.is_absolute()
        or not source_path.is_file()
        or _sha256(source_path) != str(formal_promotion.get("selection_sha256") or "")
    ):
        raise RuntimeError("XP v51 formal gate source selection SHA mismatch")
    prior = json.loads(source_path.read_text(encoding="utf-8"))
    binding_rows = _binding_rows(selection, prior, robust_module)
    checks = {
        "formal_stage": selection.get("batch_mode") == "formal",
        "exactly_five_unique_novel_noncorner": bool(
            len(top5) == 5
            and len(set(identifiers)) == 5
            and "" not in identifiers
            and not (set(identifiers) & replayed)
            and all(
                item.get("boundary_corner") is False
                and item.get("near_corner_excluded_from_noncorner_gate") is not True
                for item in top5
            )
        ),
        "trusted3_probe2": roles == EXPECTED_ROLES,
        "three_exact_points_promoted": bool(
            promotion.get("contract") == PROMOTION_CONTRACT
            and int(promotion.get("validated_trusted_count", -1)) == 3
            and pending.get("formal_admission_allowed") is True
            and int(pending.get("formal_trusted_evidence_count", -1)) == 3
            and pending.get("second_diagnostic_required") is False
            and all(
                item.get("exact_plan_regime_validated") is True
                and item.get("formal_trusted_evidence_eligible") is True
                for item in top5[:3]
            )
            and all(
                item.get("formal_trusted_evidence_eligible") is False
                and item.get("used_for_gradient") is False
                and item.get("used_for_inner_incumbent_update") is False
                for item in top5[3:]
            )
            and all(item.get("pending_trusted") is False for item in top5)
        ),
        "formal_admission_passed": bool(
            admission.get("passed") is True
            and (admission.get("diagnostic_entry_recheck") or {}).get("passed") is True
            and (admission.get("formal_acceptance_feasibility_gate") or {}).get("passed")
            is True
        ),
        "sealed_handoff_valid": bool(
            handoff.get("status") == "sealed_before_formal_workload"
            and int(handoff.get("observation_count_per_candidate", -1)) == 2
        ),
        "required_candidate_audit_complete": all(
            all(field in item for field in REQUIRED_CANDIDATE_FIELDS)
            for item in top5
        ),
        "replay_authorized": selection.get("replay_authorized") is True,
        "affinity_exact": bool(
            (selection.get("affinity") or {}).get("cpuset_cpus")
            == "32-51,288-307"
            and (selection.get("affinity") or {}).get("excluded_cpus")
            == "184-199,440-455"
        ),
        "routing_patch_is_state_neutral": bool(
            routing.get("contract") == CONTRACT
            and routing.get("preformal_handoff_preserved") is True
            and routing.get("pending_diagnostic_gate_reapplied_to_formal") is False
            and routing.get("manual_candidate_injection") is False
            and routing.get("formal_state_mutation") is False
            and int(routing.get("workload_calls_added", -1)) == 0
            and routing.get("threshold_relaxation") is False
            and routing.get("binding_rows") == binding_rows
            and routing.get("binding_sha256") == _digest(binding_rows)
        ),
    }
    return {
        "contract": FORMAL_GATE_CONTRACT,
        "checks": checks,
        "passed": all(bool(value) for value in checks.values()),
        "roles": roles,
        "fingerprint_ids": identifiers,
        "selection_stage": "two_clean_diagnostics_to_independent_formal",
        "manual_candidate_injection": False,
        "maximum_physical_calls": 5,
    }


def install_phase_gate(phase_module: Any, robust_module: Any) -> Any:
    """Route only a v51-sealed formal object around diagnostic-only gates."""

    original = phase_module._selection_gate

    def selection_gate(
        selection: Mapping[str, Any],
        replayed_fingerprints: Any = (),
    ) -> dict[str, Any]:
        routing = selection.get("v51_formal_promotion_routing") or {}
        if (
            selection.get("batch_mode") == "formal"
            and routing.get("contract") == CONTRACT
        ):
            return promoted_formal_gate(selection, replayed_fingerprints, robust_module)
        return original(selection, replayed_fingerprints)

    phase_module._selection_gate = selection_gate
    phase_module.XP_V51_FORMAL_PROMOTION_GATE_CONTRACT = FORMAL_GATE_CONTRACT
    return phase_module

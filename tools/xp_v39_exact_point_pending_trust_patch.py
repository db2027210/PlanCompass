#!/usr/bin/env python3
"""XP v39 exact-point pending-trust and second-observation promotion.

This patch keeps the v38 phase state machine, but makes trust an exact-point
property.  A clean probe5 discovery may schedule the same five points for a
second diagnostic observation.  The selected three are pending validation;
they are not formal trusted evidence until that second replay is clean.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping


PENDING_CONTRACT = "xp_v39_exact_point_pending_trust_second_diagnostic_v1"
PROMOTION_CONTRACT = "xp_v39_exact_point_two_clean_observation_promotion_v1"
CALIBRATION_CONTRACT = "xp_v39_exact_point_only_calibration_v1"
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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def formal_snapshot_digest(state: Mapping[str, Any]) -> str:
    """Return the digest used to prove accepted/incumbent/range neutrality."""

    snapshot = {key: copy.deepcopy(state.get(key)) for key in FORMAL_KEYS}
    return _digest(snapshot)


def _read_object(path: Path, label: str) -> dict[str, Any]:
    if not path.is_absolute() or not path.is_file():
        raise RuntimeError(f"XP v39 missing absolute {label}: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"XP v39 {label} is not an object")
    return value


def _params(candidate: Mapping[str, Any]) -> dict[str, Any]:
    value = candidate.get("candidate_params") or candidate.get("optimized_params")
    if not isinstance(value, dict) or not value:
        raise RuntimeError("XP v39 candidate lacks exact parameters")
    return copy.deepcopy(value)


def _fingerprint_id(candidate: Mapping[str, Any]) -> str:
    return str((candidate.get("three_level_fingerprint") or {}).get("id") or "")


def _plan(candidate: Mapping[str, Any]) -> str:
    return str(
        (candidate.get("three_level_fingerprint") or {}).get("plan_level")
        or candidate.get("workload_plan_fingerprint")
        or ""
    )


def _clean_diagnostic_replay(replay: Mapping[str, Any]) -> bool:
    return bool(
        replay.get("batch_mode") == "diagnostic_validation"
        and int(replay.get("physical_workload_invocation_count", -1)) == 5
        and int(replay.get("successful_real_replay_count", -1)) == 5
        and int(replay.get("technical_failure_count", -1)) == 0
        and len(replay.get("successful") or []) == 5
        and replay.get("baseline_restore_verified") is True
        and replay.get("accepted_update_applied") is False
        and replay.get("gradient_update_applied") is False
        and replay.get("incumbent_updated") is False
        and replay.get("range_updated") is False
        and replay.get("state_mutations_applied_by_this_bundle") is False
        and not (replay.get("rejected") or [])
    )


def _source_files(selection: Mapping[str, Any]) -> tuple[Path, Path]:
    source = selection.get("calibration_source") or {}
    if source.get("contract") != "clean_probe5_exact_plan_first_observation_repeat_v1":
        raise RuntimeError("XP v39 selection is not a probe5 exact-point repeat")
    selection_path = Path(str(source.get("selection_path") or ""))
    replay_path = Path(str(source.get("replay_path") or ""))
    return selection_path, replay_path


def _first_observation_bindings(
    selection: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source_selection_path, source_replay_path = _source_files(selection)
    source_selection = _read_object(source_selection_path, "source selection")
    source_replay = _read_object(source_replay_path, "source replay")
    if not _clean_diagnostic_replay(source_replay):
        raise RuntimeError("XP v39 first-observation replay is not clean5")
    source_top5 = list(source_selection.get("top5") or [])
    if len(source_top5) != 5 or any(
        item.get("selection_role") != "maximin_uncertain_probe"
        for item in source_top5
    ):
        raise RuntimeError("XP v39 source is not a probe5 discovery selection")
    source_by_id = {_fingerprint_id(item): item for item in source_top5}
    replay_by_id = {
        str(item.get("fingerprint_id") or ""): item
        for item in source_replay.get("successful") or []
    }
    if len(source_by_id) != 5 or set(source_by_id) != set(replay_by_id):
        raise RuntimeError("XP v39 source selection/replay fingerprints differ")

    source_selection_sha = _sha256(source_selection_path)
    source_replay_sha = _sha256(source_replay_path)
    bindings = []
    seen_source_ids: set[str] = set()
    for candidate in selection.get("top5") or []:
        calibration = candidate.get("exact_plan_regime_calibration") or {}
        source_id = str(calibration.get("source_fingerprint_id") or "")
        source_candidate = source_by_id.get(source_id)
        source_observation = replay_by_id.get(source_id)
        history = list(candidate.get("exact_plan_regime_observation_history") or [])
        if source_candidate is None or source_observation is None or len(history) != 1:
            raise RuntimeError("XP v39 exact-point first observation is incomplete")
        prior = history[0]
        if (
            source_id in seen_source_ids
            or _params(candidate) != _params(source_candidate)
            or _plan(candidate) != _plan(source_candidate)
            or str(prior.get("source_replay") or "") != str(source_replay_path)
            or str(prior.get("source_replay_sha256") or "") != source_replay_sha
            or prior.get("actual_resource") != source_observation.get("actual_resource")
            or not math.isclose(
                float(prior.get("actual_score")),
                float(source_observation.get("actual_score")),
                rel_tol=0.0,
                abs_tol=1e-12,
            )
            or source_observation.get("technical_failure") is True
            or source_observation.get("baseline_restore_verified") is not True
        ):
            raise RuntimeError("XP v39 same-plan/different-params evidence rejected")
        seen_source_ids.add(source_id)
        bindings.append(
            {
                "source_fingerprint_id": source_id,
                "repeat_fingerprint_id": _fingerprint_id(candidate),
                "plan_level": _plan(candidate),
                "exact_params_sha256": _digest(_params(candidate)),
                "source_selection": str(source_selection_path),
                "source_selection_sha256": source_selection_sha,
                "source_replay": str(source_replay_path),
                "source_replay_sha256": source_replay_sha,
                "first_observation_id": str(prior.get("observation_id") or ""),
            }
        )
    if len(bindings) != 5 or len(seen_source_ids) != 5:
        raise RuntimeError("XP v39 exact-point binding is not five-way unique")
    audit = {
        "source_selection": str(source_selection_path),
        "source_selection_sha256": source_selection_sha,
        "source_replay": str(source_replay_path),
        "source_replay_sha256": source_replay_sha,
        "source_physical_calls": 5,
        "source_valid_replays": 5,
        "source_technical_failures": 0,
    }
    return bindings, audit


def sanitize_pending_selection(selection: Mapping[str, Any]) -> dict[str, Any]:
    """Seal a probe5 same-point repeat without promoting formal trust."""

    selected = copy.deepcopy(dict(selection))
    top5 = list(selected.get("top5") or [])
    roles = [str(item.get("selection_role") or "") for item in top5]
    if (
        selected.get("batch_mode") != "diagnostic_validation"
        or roles != EXPECTED_ROLES
        or len({_fingerprint_id(item) for item in top5}) != 5
    ):
        raise RuntimeError("XP v39 pending selection must be diagnostic trusted3+probe2")
    bindings, source_audit = _first_observation_bindings(selected)
    by_repeat = {item["repeat_fingerprint_id"]: item for item in bindings}
    for candidate in top5:
        binding = by_repeat[_fingerprint_id(candidate)]
        pending = str(candidate.get("selection_role") or "").startswith("trusted")
        candidate["exact_plan_regime_validated"] = False
        candidate["pending_trusted"] = pending
        candidate["formal_trusted_evidence_eligible"] = False
        candidate["v39_exact_point_binding"] = copy.deepcopy(binding)
        candidate["plan_regime_calibration"] = {
            "contract": CALIBRATION_CONTRACT,
            "scope": "exact_parameters_plus_plan_plus_source_three_level_fingerprint",
            "plan": _plan(candidate),
            "observation_count": 1,
            "trusted_eligible": False,
            "pending_second_diagnostic": pending,
            "unknown_plan_probe_only": not pending,
            "same_plan_different_params_history_used": False,
        }
        candidate["used_for_gradient"] = False
        candidate["used_for_inner_incumbent_update"] = False
    selected["top5"] = top5
    selected["v39_pending_trust"] = {
        "contract": PENDING_CONTRACT,
        "source": source_audit,
        "pending_trusted_count": 3,
        "uncertain_probe_count": 2,
        "formal_trusted_evidence_count": 0,
        "second_diagnostic_required": True,
        "formal_admission_allowed": False,
        "same_plan_different_params_history_forbidden": True,
        "state_mutation": False,
        "new_workload_calls": 0,
    }
    return selected


def pending_selection_gate(
    original: Any,
    selection: Mapping[str, Any],
    replayed_fingerprints: Any = (),
) -> dict[str, Any]:
    """Allow only a fully sealed pending-trust diagnostic through v38 gate."""

    pending = selection.get("v39_pending_trust") or {}
    if pending.get("contract") != PENDING_CONTRACT:
        return original(selection, replayed_fingerprints)
    selected = sanitize_pending_selection(selection)
    top5 = list(selected.get("top5") or [])
    identifiers = [_fingerprint_id(item) for item in top5]
    replayed = {str(value) for value in replayed_fingerprints or []}
    checks = {
        "exactly_five": len(top5) == 5,
        "unique_three_level_fingerprints": len(set(identifiers)) == 5 and "" not in identifiers,
        "all_repeat_fingerprints_not_previously_replayed": not bool(set(identifiers) & replayed),
        "all_noncorner": all(
            item.get("boundary_corner") is False
            and item.get("near_corner_excluded_from_noncorner_gate") is not True
            for item in top5
        ),
        "diagnostic_only": selected.get("batch_mode") == "diagnostic_validation",
        "trusted3_probe2_pending_roles": [
            str(item.get("selection_role") or "") for item in top5
        ] == EXPECTED_ROLES,
        "three_pending_not_formal_trusted": sum(
            item.get("pending_trusted") is True
            and item.get("formal_trusted_evidence_eligible") is False
            for item in top5
        ) == 3,
        "unknown_regimes_remain_probes": all(
            not (
                (item.get("plan_regime_calibration") or {}).get(
                    "unknown_plan_probe_only"
                )
                is True
                and str(item.get("selection_role") or "").startswith("trusted")
            )
            for item in top5
        ),
        "replay_authorized": selected.get("replay_authorized") is True,
        "exact_point_first_observation_sealed": True,
        "same_plan_different_params_history_forbidden": True,
    }
    return {
        "contract": PENDING_CONTRACT,
        "checks": checks,
        "passed": all(checks.values()),
        "roles": EXPECTED_ROLES,
        "selection_stage": "exact_point_pending_trust_second_diagnostic",
        "fingerprint_ids": identifiers,
        "manual_candidate_injection": False,
        "maximum_physical_calls": 5,
        "formal_trusted_evidence_count": 0,
    }


def _second_observation_audit(
    selection: Mapping[str, Any],
    replay: Mapping[str, Any],
    replay_path: Path,
) -> dict[str, Any]:
    if not _clean_diagnostic_replay(replay):
        raise RuntimeError("XP v39 second exact-point observation is not clean5")
    current = {
        str(item.get("fingerprint_id") or ""): item
        for item in replay.get("successful") or []
    }
    trusted = [
        item
        for item in selection.get("top5") or []
        if str(item.get("selection_role") or "").startswith("trusted")
    ]
    if len(trusted) != 3:
        raise RuntimeError("XP v39 second replay does not have three pending points")
    records = []
    for candidate in trusted:
        identifier = _fingerprint_id(candidate)
        observation = current.get(identifier)
        binding = candidate.get("v39_exact_point_binding") or {}
        if (
            candidate.get("pending_trusted") is not True
            or candidate.get("formal_trusted_evidence_eligible") is not False
            or _digest(_params(candidate)) != binding.get("exact_params_sha256")
            or _plan(candidate) != binding.get("plan_level")
            or observation is None
            or observation.get("technical_failure") is True
            or observation.get("baseline_restore_verified") is not True
        ):
            raise RuntimeError("XP v39 pending trusted exact point changed")
        records.append(
            {
                "repeat_fingerprint_id": identifier,
                "exact_params_sha256": binding["exact_params_sha256"],
                "first_source_fingerprint_id": binding["source_fingerprint_id"],
                "first_replay_sha256": binding["source_replay_sha256"],
                "second_replay_sha256": _sha256(replay_path),
                "second_actual_score": float(observation["actual_score"]),
            }
        )
    return {
        "contract": PROMOTION_CONTRACT,
        "validated_trusted_count": 3,
        "records": records,
        "second_replay": str(replay_path),
        "second_replay_sha256": _sha256(replay_path),
        "same_plan_different_params_history_used": False,
        "formal_state_mutation": False,
    }


def promote_after_second_clean(
    selection: Mapping[str, Any],
    replay_path: Path,
) -> dict[str, Any]:
    """Promote pending points only after their exact second clean replay."""

    promoted = copy.deepcopy(dict(selection))
    replay = _read_object(replay_path, "second replay")
    audit = _second_observation_audit(promoted, replay, replay_path)
    by_repeat = {item["repeat_fingerprint_id"]: item for item in audit["records"]}
    for candidate in promoted.get("top5") or []:
        identifier = _fingerprint_id(candidate)
        if identifier not in by_repeat:
            candidate["pending_trusted"] = False
            candidate["formal_trusted_evidence_eligible"] = False
            continue
        candidate["pending_trusted"] = False
        candidate["exact_plan_regime_validated"] = True
        candidate["formal_trusted_evidence_eligible"] = True
        candidate["v39_validated_trust"] = copy.deepcopy(by_repeat[identifier])
        candidate["plan_regime_calibration"] = {
            "contract": CALIBRATION_CONTRACT,
            "scope": "exact_parameters_plus_plan_plus_source_three_level_fingerprint",
            "plan": _plan(candidate),
            "observation_count": 2,
            "trusted_eligible": True,
            "pending_second_diagnostic": False,
            "unknown_plan_probe_only": False,
            "same_plan_different_params_history_used": False,
        }
    promoted["v39_pending_trust"]["formal_admission_allowed"] = True
    promoted["v39_pending_trust"]["formal_trusted_evidence_count"] = 3
    promoted["v39_pending_trust"]["second_diagnostic_required"] = False
    promoted["v39_exact_point_promotion"] = audit
    return promoted


def install_bundle(bundle_module: Any) -> Any:
    """Prevent plan-level calibration from granting cross-parameter trust."""

    original_build = bundle_module.build_calibration
    original_candidates = bundle_module._calibrated_candidates

    def build_calibration(
        selection: Mapping[str, Any],
        replay: Mapping[str, Any],
    ) -> dict[str, Any]:
        calibration = copy.deepcopy(original_build(selection, replay))
        validated = []
        current = {
            str(item.get("fingerprint_id") or ""): item
            for item in replay.get("successful") or []
        }
        for candidate in selection.get("top5") or []:
            identifier = _fingerprint_id(candidate)
            history = list(candidate.get("exact_plan_regime_observation_history") or [])
            if len(history) == 1 and identifier in current:
                validated.append(
                    {
                        "plan": _plan(candidate),
                        "exact_params_sha256": _digest(_params(candidate)),
                        "fingerprint_id": identifier,
                    }
                )
        calibration["v39_validated_exact_points"] = validated
        calibration["contract"] = CALIBRATION_CONTRACT
        return calibration

    def calibrated_candidates(
        raw_candidates: Any,
        calibration: Mapping[str, Any],
        excluded_ids: set[str],
    ) -> Any:
        active, corners, excluded = original_candidates(
            raw_candidates,
            calibration,
            excluded_ids,
        )
        allowed = {
            (str(item["plan"]), str(item["exact_params_sha256"]))
            for item in calibration.get("v39_validated_exact_points") or []
        }
        for group in (active, corners, excluded):
            for candidate in group:
                exact_match = (_plan(candidate), _digest(_params(candidate))) in allowed
                if not exact_match:
                    candidate["exact_plan_regime_validated"] = False
                    plan_calibration = dict(candidate.get("plan_regime_calibration") or {})
                    plan_calibration.update(
                        {
                            "contract": CALIBRATION_CONTRACT,
                            "scope": "prediction_only_for_same_plan_different_params",
                            "trusted_eligible": False,
                            "unknown_plan_probe_only": True,
                            "same_plan_different_params_history_used": False,
                        }
                    )
                    candidate["plan_regime_calibration"] = plan_calibration
        return active, corners, excluded

    bundle_module.build_calibration = build_calibration
    bundle_module._calibrated_candidates = calibrated_candidates
    return bundle_module


def install_phase_gate(phase_module: Any) -> Any:
    original = phase_module._selection_gate

    def selection_gate(
        selection: Mapping[str, Any],
        replayed_fingerprints: Any = (),
    ) -> dict[str, Any]:
        return pending_selection_gate(original, selection, replayed_fingerprints)

    phase_module._selection_gate = selection_gate
    return phase_module


def install_adapter(adapter: Any) -> Any:
    """Sanitize pending selections and enforce exact-point formal promotion."""

    original_gate = adapter._diagnostic_entry_gate
    original_generate = adapter.generate_selection

    def diagnostic_entry_gate(
        selection_path: Path,
        replay_path: Path,
    ) -> dict[str, Any]:
        gate = copy.deepcopy(original_gate(selection_path, replay_path))
        selection = _read_object(selection_path.resolve(), "entry selection")
        if (selection.get("v39_pending_trust") or {}).get("contract") != PENDING_CONTRACT:
            return gate
        replay = _read_object(replay_path.resolve(), "entry replay")
        exact_audit = _second_observation_audit(selection, replay, replay_path.resolve())
        checks = dict(gate.get("checks") or {})
        checks["three_trusted_have_two_exact_point_clean_observations"] = (
            exact_audit["validated_trusted_count"] == 3
        )
        checks["same_plan_different_params_history_forbidden"] = True
        gate["checks"] = checks
        gate["passed"] = all(bool(value) for value in checks.values())
        gate["v39_exact_point_promotion"] = exact_audit
        return gate

    def generate_selection(*args: Any, **kwargs: Any) -> Mapping[str, Any]:
        selected = copy.deepcopy(dict(original_generate(*args, **kwargs)))
        promotion = selected.get("promoted_from_diagnostic_validation") or {}
        selection_path = Path(str(promotion.get("selection_path") or ""))
        replay_path = Path(str(promotion.get("replay_path") or ""))
        if (
            selected.get("batch_mode") == "formal"
            and selection_path.is_absolute()
            and selection_path.is_file()
            and replay_path.is_absolute()
            and replay_path.is_file()
        ):
            prior = _read_object(selection_path, "promoted selection")
            if (prior.get("v39_pending_trust") or {}).get("contract") == PENDING_CONTRACT:
                promoted = promote_after_second_clean(prior, replay_path)
                promoted["batch_mode"] = "formal"
                promoted["formal_admission"] = copy.deepcopy(
                    selected.get("formal_admission") or {}
                )
                promoted["promoted_from_diagnostic_validation"] = copy.deepcopy(promotion)
                promoted["source_batch_id"] = selected.get("source_batch_id")
                promoted["affinity"] = copy.deepcopy(selected.get("affinity") or {})
                return promoted
        source = selected.get("calibration_source") or {}
        if source.get("contract") == "clean_probe5_exact_plan_first_observation_repeat_v1":
            return sanitize_pending_selection(selected)
        return selected

    adapter._diagnostic_entry_gate = diagnostic_entry_gate
    adapter.generate_selection = generate_selection
    return adapter

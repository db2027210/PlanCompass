#!/usr/bin/env python3
"""TPC-H v55 compatibility with a permanently bound 03be validator.

Only the v51 safe-unknown recovery contract is translated. The selection is
first validated independently, then a deep copy is presented to the exact
deployed v52 compatibility authority as its stability-acquisition contract.
The on-disk selection, roles, fingerprints, and optimizer-state flags are
never changed.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import math
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Tuple


REMOTE_V45 = Path(
    "/tmp/run_tpch_round29_diagnostic_exact_plan_regime_batch_v45_none_return.py"
)
LOCAL_V45 = Path(__file__).with_name(
    "tpch_v52_deployed_diagnostic_authority_03be.py"
)
EXPECTED_V45_SHA256 = (
    "03be882bd4c78ebac37f18b1c3f689305050057d3b73a9964b3d3d1f7dc14c5f"
)
SAFE_UNKNOWN_CONTRACT = (
    "exact_structural_plan_regime_trusted3_stability_"
    "safe_unknown_maximin_probe2_diagnostic_v1"
)
SAFE_UNKNOWN_AUDIT_CONTRACT = (
    "safe_unknown_uncertainty_three_level_maximin_probe2_v1"
)
RESOURCES = ("cpu", "io", "memory")
MAX_UNKNOWN_UCB_BONUS = 0.05


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def finite(value: Any, label: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"v55 {label} is not finite")
    return number


def load_v45() -> Any:
    path = REMOTE_V45 if REMOTE_V45.is_file() else LOCAL_V45
    if not path.is_file() or sha256(path) != EXPECTED_V45_SHA256:
        raise RuntimeError("TPC-H v55 deployed runner authority SHA mismatch")
    specification = importlib.util.spec_from_file_location(
        "tpch_v55_pinned_deployed_runner", path
    )
    if specification is None or specification.loader is None:
        raise RuntimeError(f"cannot import sealed v45 runner: {path}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def fingerprint_id(candidate: Mapping[str, Any]) -> str:
    fingerprint = candidate.get("three_level_fingerprint") or {}
    return str(fingerprint.get("id") or "")


def validate_three_level(candidate: Mapping[str, Any], rank: int) -> None:
    fingerprint = candidate.get("three_level_fingerprint") or {}
    plan = str(fingerprint.get("plan_level") or "")
    feature_level = fingerprint.get("feature_ratio_level") or {}
    resource_level = fingerprint.get("predicted_resource_level") or {}
    payload = {
        "plan": plan,
        "feature_ratio_q005": feature_level,
        "resource_utilization_q005": resource_level,
    }
    expected = "three-level-sha256:" + canonical_sha256(payload)
    if not (
        fingerprint.get("contract") == "plan_feature_resource_three_level_v1"
        and fingerprint.get("valid") is True
        and fingerprint.get("parameter_fallback_used") is False
        and plan
        and isinstance(feature_level, Mapping)
        and isinstance(resource_level, Mapping)
        and set(feature_level) == set(RESOURCES)
        and set(resource_level) == set(RESOURCES)
        and all(
            isinstance(feature_level[name], int)
            and isinstance(resource_level[name], int)
            for name in RESOURCES
        )
        and fingerprint_id(candidate) == expected
    ):
        raise ValueError(f"v55 rank {rank} three-level fingerprint mismatch")


def _state_neutral(candidate: Mapping[str, Any]) -> bool:
    return bool(
        candidate.get("diagnostic_only") is True
        and candidate.get("accepted_update_allowed") is False
        and candidate.get("incumbent_update_allowed") is False
        and candidate.get("range_update_allowed") is False
        and candidate.get("gradient_update_allowed") is False
        and candidate.get("incumbent_update_eligible") is False
        and candidate.get("eligible_for_best_candidate") is False
        and candidate.get("used_for_gradient") is False
        and candidate.get("used_for_inner_incumbent_update") is False
        and candidate.get("qps_used_for_inner_scoring") is False
    )


def _resource_safe(candidate: Mapping[str, Any], rank: int) -> bool:
    predicted = candidate.get("predicted_resource") or {}
    utilization = candidate.get("utilization") or {}
    try:
        return bool(
            set(predicted) == set(RESOURCES)
            and set(utilization) == set(RESOURCES)
            and all(
                finite(predicted[name], f"rank {rank} predicted {name}") > 0.0
                for name in RESOURCES
            )
            and all(
                finite(utilization[name], f"rank {rank} utilization {name}")
                >= 0.0
                for name in RESOURCES
            )
        )
    except (TypeError, ValueError):
        return False


def validate_safe_unknown_selection(selection: Mapping[str, Any]) -> None:
    """Fail closed unless this is exactly the v51 safe-unknown contract."""

    candidates = selection.get("top5") or []
    roles = [item.get("selection_role") for item in candidates]
    audit = selection.get("safe_unknown_probe_audit") or {}
    selection_audit = selection.get("selection_audit") or {}
    no_update_fields = (
        "accepted_update_allowed",
        "incumbent_update_allowed",
        "range_update_allowed",
        "gradient_update_allowed",
    )
    if not (
        selection.get("selection_contract") == SAFE_UNKNOWN_CONTRACT
        and isinstance(candidates, list)
        and len(candidates) == 5
        and roles == ["trusted", "trusted", "trusted", "probe", "probe"]
        and selection.get("safe_unknown_probe_recovery_mode") is True
        and selection.get("stability_acquisition_mode") is True
        and selection.get("trusted_evidence_acquisition_mode") is False
        and selection.get("stable_trusted_ready_before_diagnostic") is False
        and selection.get("automatic_recovery_mode") is True
        and selection.get("automatic_recovery_reason")
        == "exact_regime_trusted_stability_acquisition"
        and selection.get("diagnostic_only") is True
        and selection.get("formal_continuation") is False
        and selection.get("formal_admission_from_this_selection_allowed") is False
        and selection.get("formal_admission_requires_exact_and_robust_repeat") is True
        and selection.get("unknown_plan_regime_trusted_allowed") is False
        and selection.get("probe_expected_score_must_clear_target") is False
        and selection.get("selected_safe_unknown_probes_are_credible") is False
        and selection.get("safe_unknown_probe_score_floor_scope")
        == "diagnostic_only_not_formal_or_gradient"
        and selection.get("evidence_acquisition_slots_are_formal_candidates") is False
        and int(selection.get("trusted_slots", -1)) == 3
        and int(selection.get("probe_slots", -1)) == 2
        and int(selection.get("maximum_real_replay_invocations", -1)) == 5
        and selection.get("serial_real_replay_required") is True
        and selection.get("technical_failures_count_toward_physical_cap") is True
        and selection.get("technical_failures_count_as_valid_or_accepted") is False
        and selection.get("qps_used_for_inner_scoring") is False
        and all(selection.get(field) is False for field in no_update_fields)
        and int(selection.get("novel_unique_fingerprint_count", -1)) == 5
        and int(selection_audit.get("selected_unique_fingerprint_count", -1)) == 5
        and int(selection_audit.get("trusted_count", -1)) == 3
        and int(selection_audit.get("probe_count", -1)) == 2
        and int(selection_audit.get("credible_probe_count", -1)) == 0
        and int(selection_audit.get("safe_unknown_probe_count", -1)) == 2
        and audit.get("contract") == SAFE_UNKNOWN_AUDIT_CONTRACT
        and int(audit.get("selected_count", -1)) == 2
        and int(audit.get("safe_pool_count", -1)) >= 2
        and audit.get("formal_score_floor_satisfied") is False
        and audit.get("formal_candidate_eligible") is False
        and audit.get("optimizer_state_updated") is False
        and audit.get("manual_candidate_injection") is False
    ):
        raise ValueError("v55 safe-unknown selection contract mismatch")

    identifiers = [fingerprint_id(item) for item in candidates]
    if len(set(identifiers)) != 5 or any(not value for value in identifiers):
        raise ValueError("v55 safe-unknown fingerprints are missing or duplicated")

    for rank, candidate in enumerate(candidates, 1):
        validate_three_level(candidate, rank)
        if not _state_neutral(candidate) or not _resource_safe(candidate, rank):
            raise ValueError(f"v55 rank {rank} is not state-neutral and resource-safe")

    for rank, candidate in enumerate(candidates[:3], 1):
        confidence = candidate.get("confidence") or {}
        calibration = candidate.get("regime_calibration") or {}
        if not (
            candidate.get("trusted") is True
            and candidate.get("uncertain_probe") is False
            and candidate.get("safe_unknown_stability_probe") is False
            and candidate.get("trusted_stability_gate_passed") is False
            and candidate.get("formal_trust_requires_independent_diagnostic") is True
            and candidate.get("formal_score_floor_satisfied") is True
            and candidate.get("exact_plan_regime_validated") is True
            and int(confidence.get("measurement_count") or 0) >= 2
            and calibration.get("trusted_prediction_eligible") is True
            and int(calibration.get("evidence_count") or 0) >= 2
            and calibration.get("global_scalar_forbidden") is True
            and calibration.get("unknown_regime_calibration_forbidden") is True
        ):
            raise ValueError(f"v55 trusted rank {rank} evidence mismatch")

    probe_ids = identifiers[3:]
    for rank, candidate in enumerate(candidates[3:], 4):
        confidence = candidate.get("confidence") or {}
        if not (
            candidate.get("trusted") is False
            and candidate.get("uncertain_probe") is True
            and candidate.get("safe_unknown_stability_probe") is True
            and candidate.get("trusted_stability_gate_passed") is False
            and candidate.get("formal_trust_requires_independent_diagnostic") is False
            and candidate.get("formal_score_floor_satisfied") is False
            and candidate.get("exact_plan_regime_validated") is False
            and confidence.get("unknown_regime") is True
            and int(confidence.get("measurement_count", -1)) == 0
            and candidate.get("exact_regime_stability_failure_irrecoverable")
            is not True
            and candidate.get("boundary_corner") is False
            and candidate.get("boundary_plateau") is False
        ):
            raise ValueError(f"v55 safe-unknown probe rank {rank} mismatch")
        bonus = finite(
            candidate.get("probe_acquisition_score"),
            f"rank {rank} probe acquisition score",
        ) - finite(candidate.get("candidate_score"), f"rank {rank} score")
        if not -1e-12 <= bonus <= MAX_UNKNOWN_UCB_BONUS + 1e-12:
            raise ValueError(f"v55 safe-unknown probe rank {rank} bonus mismatch")

    bindings = audit.get("selected_bindings") or []
    if not (
        isinstance(bindings, list)
        and len(bindings) == 2
        and [str(item.get("fingerprint_id") or "") for item in bindings]
        == probe_ids
        and all(
            finite(item.get("minimum_three_level_distance"), "maximin distance")
            >= 0.0
            and 0.0
            <= finite(item.get("uncertainty_bonus"), "uncertainty bonus")
            <= MAX_UNKNOWN_UCB_BONUS + 1e-12
            for item in bindings
        )
    ):
        raise ValueError("v55 safe-unknown maximin audit binding mismatch")


v45: Any = None
v45_validator: Callable[[Mapping[str, Any]], List[Dict[str, Any]]] | None = None


def bound_v45() -> Tuple[
    Any,
    Callable[[Mapping[str, Any]], List[Dict[str, Any]]],
]:
    """Return the base module and its import-time validator function object."""

    global v45, v45_validator
    if v45 is None:
        v45 = load_v45()
        candidate = v45.validate_selection
        if candidate is validate_selection:
            raise RuntimeError("TPC-H v55 base validator resolved to outer wrapper")
        v45_validator = candidate
    if v45_validator is None or v45_validator is validate_selection:
        raise RuntimeError("TPC-H v55 base validator identity is not sealed")
    return v45, v45_validator


def base_validator_identity() -> Dict[str, Any]:
    module, validator = bound_v45()
    return {
        "contract": "tpch_v55_bound_03be_validator_identity_v1",
        "base_module_file": str(Path(module.__file__).resolve()),
        "base_module_sha256": sha256(Path(module.__file__)),
        "validator_module": str(getattr(validator, "__module__", "")),
        "validator_qualname": str(getattr(validator, "__qualname__", "")),
        "validator_is_outer_wrapper": validator is validate_selection,
        "validator_is_current_base_attribute": (
            validator is module.validate_selection
        ),
    }


def validate_selection(selection: Mapping[str, Any]) -> List[Dict[str, Any]]:
    module, validator = bound_v45()
    if selection.get("selection_contract") != SAFE_UNKNOWN_CONTRACT:
        return validator(selection)
    validate_safe_unknown_selection(selection)
    translated = copy.deepcopy(dict(selection))
    translated["selection_contract"] = module.STABILITY_ACQUISITION_CONTRACT
    return validator(translated)


def main() -> int:
    module, validator = bound_v45()
    if module.validate_selection is not validator:
        raise RuntimeError("TPC-H v55 base validator changed before binding")
    module.validate_selection = validate_selection
    return int(module.main() or 0)


if __name__ == "__main__":
    raise SystemExit(main())

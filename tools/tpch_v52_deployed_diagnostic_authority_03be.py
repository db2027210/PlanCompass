#!/usr/bin/env python3
"""Strict compatibility wrapper for the v52 safe-unknown TPC-H diagnostic."""

import copy
import hashlib
import importlib.util
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple


SOURCE_PACKAGE = Path(
    "/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/"
    "pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/"
    "experience_sgd/attempt48_global_replay_cap5_20260726/"
    "gradient_debug_round57_probe5_recovery_v18_20260731T2310_package"
)
REMOTE_ORIGINAL = SOURCE_PACKAGE / (
    "run_tpch_round29_diagnostic_exact_plan_regime_batch.py"
)
LOCAL_ORIGINAL = Path(__file__).with_name(
    "run_tpch_round29_diagnostic_exact_plan_regime_batch.py"
)
ORIGINAL_SHA256 = (
    "12b6ade0325541dbe1eadfd40af873fd3144fc1cbf97d9eca52f11a430731872"
)
BOUNDED_CONTRACT = (
    "exact_structural_plan_regime_trusted3_bounded_probe2_top5_"
    "diagnostic_v1"
)
PENDING_RETEST_CONTRACT = (
    "exact_regime_first_observation_retest_trusted3_probe2_v1"
)
STABILITY_ACQUISITION_CONTRACT = (
    "exact_structural_plan_regime_trusted3_stability_acquisition_"
    "bounded_probe2_diagnostic_v1"
)
SAFE_UNKNOWN_CONTRACT = (
    "exact_structural_plan_regime_trusted3_stability_safe_unknown_"
    "maximin_probe2_diagnostic_v1"
)
EXACT_COMPATIBLE_CONTRACTS = frozenset(
    {
        BOUNDED_CONTRACT,
        PENDING_RETEST_CONTRACT,
        STABILITY_ACQUISITION_CONTRACT,
        SAFE_UNKNOWN_CONTRACT,
    }
)
SEALED_EQUIVALENT_CONTRACT = (
    "exact_structural_plan_regime_trusted3_unknown_probe2_top5_"
    "diagnostic_v1"
)
UPDATE_FIELDS = (
    "accepted_update_allowed",
    "incumbent_update_allowed",
    "range_update_allowed",
    "gradient_update_allowed",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_original() -> Any:
    path = REMOTE_ORIGINAL if REMOTE_ORIGINAL.is_file() else LOCAL_ORIGINAL
    if not path.is_file() or sha256(path) != ORIGINAL_SHA256:
        raise RuntimeError("TPC-H v52 sealed diagnostic runner SHA mismatch")
    specification = importlib.util.spec_from_file_location(
        "tpch_v52_sealed_diagnostic_runner",
        path,
    )
    if specification is None or specification.loader is None:
        raise RuntimeError("cannot load sealed TPC-H diagnostic runner")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


original: Any = None
sealed_validate_selection: Any = None


def bound_original() -> Tuple[Any, Any]:
    global original, sealed_validate_selection
    if original is None:
        original = load_original()
        sealed_validate_selection = original.validate_selection
    if sealed_validate_selection is None:
        raise RuntimeError("TPC-H v52 sealed validation binding is missing")
    return original, sealed_validate_selection


def normalize_return_code(result: Any) -> int:
    return 0 if result is None else int(result)


def validate_stability_acquisition(selection: Mapping[str, Any]) -> None:
    candidates = selection.get("top5") or []
    roles = [item.get("selection_role") for item in candidates]
    if not (
        len(candidates) == 5
        and roles == ["trusted", "trusted", "trusted", "probe", "probe"]
        and selection.get("stability_acquisition_mode") is True
        and selection.get("stable_trusted_ready_before_diagnostic") is False
        and selection.get("formal_admission_from_this_selection_allowed") is False
        and selection.get("formal_admission_requires_exact_and_robust_repeat") is True
        and selection.get("automatic_recovery_mode") is True
        and selection.get("automatic_recovery_reason")
        == "exact_regime_trusted_stability_acquisition"
        and selection.get("diagnostic_only") is True
        and all(selection.get(field) is False for field in UPDATE_FIELDS)
        and selection.get("qps_used_for_inner_scoring") is False
        and all(
            item.get("trusted_stability_gate_passed") is False
            and item.get("formal_trust_requires_independent_diagnostic") is True
            for item in candidates[:3]
        )
        and all(
            item.get("formal_trust_requires_independent_diagnostic") is False
            for item in candidates[3:]
        )
    ):
        raise ValueError("v52 stability-acquisition selection contract mismatch")


def validate_safe_unknown(selection: Mapping[str, Any]) -> None:
    candidates = selection.get("top5") or []
    roles = [item.get("selection_role") for item in candidates]
    accepted = int(selection.get("accepted_count_start", -1))
    if not (
        len(candidates) == 5
        and roles == ["trusted", "trusted", "trusted", "probe", "probe"]
        and selection.get("schema_version") == "tpch_autonomous_successor_top5_v1"
        and selection.get("diagnostic_only") is True
        and selection.get("formal_continuation") is False
        and selection.get("serial_real_replay_required") is True
        and int(selection.get("maximum_real_replay_invocations", -1)) == 5
        and selection.get("technical_failures_count_toward_physical_cap") is True
        and selection.get("technical_failures_count_as_valid_or_accepted") is False
        and selection.get("unknown_plan_regime_trusted_allowed") is False
        and selection.get("qps_used_for_inner_scoring") is False
        and all(selection.get(field) is False for field in UPDATE_FIELDS)
        and selection.get("automatic_recovery_mode") is True
        and selection.get("automatic_recovery_reason")
        == "exact_regime_trusted_stability_acquisition"
        and selection.get("safe_unknown_probe_recovery_mode") is True
        and selection.get("safe_unknown_probe_score_floor_scope")
        == "diagnostic_only_not_formal_or_gradient"
        and selection.get("formal_admission_from_this_selection_allowed") is False
        and selection.get("formal_admission_requires_exact_and_robust_repeat") is True
        and int(selection.get("historical_accepted_excluded", -1)) == 0
        and accepted >= 0
        and int(selection.get("inherited_accepted_count", -1)) == accepted
        and int(selection.get("novel_unique_fingerprint_count", -1)) == 5
        and all(
            item.get("trusted") is True
            and item.get("exact_plan_regime_validated") is True
            and item.get("trusted_stability_gate_passed") is False
            and item.get("formal_trust_requires_independent_diagnostic") is True
            and item.get("safe_unknown_stability_probe") is False
            and item.get("uncertain_probe") is False
            and item.get("formal_score_floor_satisfied") is True
            and item.get("eligible_for_best_candidate") is False
            and item.get("qps_used_for_inner_scoring") is False
            and all(item.get(field) is False for field in UPDATE_FIELDS)
            for item in candidates[:3]
        )
        and all(
            item.get("trusted") is False
            and item.get("exact_plan_regime_validated") is False
            and item.get("formal_trust_requires_independent_diagnostic") is False
            and item.get("safe_unknown_stability_probe") is True
            and item.get("uncertain_probe") is True
            and item.get("formal_score_floor_satisfied") is False
            and item.get("eligible_for_best_candidate") is False
            and item.get("qps_used_for_inner_scoring") is False
            and all(item.get(field) is False for field in UPDATE_FIELDS)
            for item in candidates[3:]
        )
    ):
        raise ValueError("v52 safe-unknown selection contract mismatch")


def validate_selection(selection: Mapping[str, Any]) -> List[Dict[str, Any]]:
    _, sealed_validator = bound_original()
    contract = selection.get("selection_contract")
    if contract not in EXACT_COMPATIBLE_CONTRACTS:
        return sealed_validator(selection)
    if contract == STABILITY_ACQUISITION_CONTRACT:
        validate_stability_acquisition(selection)
    elif contract == SAFE_UNKNOWN_CONTRACT:
        validate_safe_unknown(selection)
    translated = copy.deepcopy(dict(selection))
    translated["selection_contract"] = SEALED_EQUIVALENT_CONTRACT
    return sealed_validator(translated)


def main() -> int:
    module, _ = bound_original()
    module.validate_selection = validate_selection
    return normalize_return_code(module.main())


if __name__ == "__main__":
    raise SystemExit(main())

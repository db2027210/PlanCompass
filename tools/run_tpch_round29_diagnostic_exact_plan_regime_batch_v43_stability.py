#!/usr/bin/env python3
"""TPC-H v43 stability-acquisition diagnostic runner compatibility shim.

The sealed round29 runner enforces the complete diagnostic replay contract.
V43 admits exactly three named trusted3+probe2 builder contracts by translating
their names to the equivalent sealed diagnostic contract before delegating
every role, field, fingerprint, and state-neutrality check.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
from pathlib import Path
from typing import Any, Dict, List, Mapping


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
EXACT_COMPATIBLE_CONTRACTS = frozenset(
    {
        BOUNDED_CONTRACT,
        PENDING_RETEST_CONTRACT,
        STABILITY_ACQUISITION_CONTRACT,
    }
)
SEALED_EQUIVALENT_CONTRACT = (
    "exact_structural_plan_regime_trusted3_unknown_probe2_top5_"
    "diagnostic_v1"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_original() -> Any:
    path = REMOTE_ORIGINAL if REMOTE_ORIGINAL.is_file() else LOCAL_ORIGINAL
    if not path.is_file() or sha256(path) != ORIGINAL_SHA256:
        raise RuntimeError("TPC-H v40 sealed diagnostic runner SHA mismatch")
    specification = importlib.util.spec_from_file_location(
        "tpch_v40_sealed_diagnostic_runner",
        path,
    )
    if specification is None or specification.loader is None:
        raise RuntimeError("cannot load sealed TPC-H diagnostic runner")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


original = load_original()
sealed_validate_selection = original.validate_selection


def validate_selection(selection: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Admit exact contracts only, then run all sealed validations."""
    if selection.get("selection_contract") not in EXACT_COMPATIBLE_CONTRACTS:
        return sealed_validate_selection(selection)
    if selection.get("selection_contract") == STABILITY_ACQUISITION_CONTRACT:
        candidates = selection.get("top5") or []
        roles = [item.get("selection_role") for item in candidates]
        if not (
            len(candidates) == 5
            and roles == ["trusted", "trusted", "trusted", "probe", "probe"]
            and selection.get("stability_acquisition_mode") is True
            and selection.get("stable_trusted_ready_before_diagnostic") is False
            and selection.get("formal_admission_from_this_selection_allowed")
            is False
            and selection.get(
                "formal_admission_requires_exact_and_robust_repeat"
            )
            is True
            and selection.get("automatic_recovery_mode") is True
            and selection.get("automatic_recovery_reason")
            == "exact_regime_trusted_stability_acquisition"
            and selection.get("diagnostic_only") is True
            and selection.get("accepted_update_allowed") is False
            and selection.get("incumbent_update_allowed") is False
            and selection.get("range_update_allowed") is False
            and selection.get("gradient_update_allowed") is False
            and selection.get("qps_used_for_inner_scoring") is False
            and all(
                item.get("trusted_stability_gate_passed") is False
                and item.get("formal_trust_requires_independent_diagnostic")
                is True
                for item in candidates[:3]
            )
            and all(
                item.get("formal_trust_requires_independent_diagnostic")
                is False
                for item in candidates[3:]
            )
        ):
            raise ValueError(
                "v43 stability-acquisition selection contract mismatch"
            )
    translated = copy.deepcopy(dict(selection))
    translated["selection_contract"] = SEALED_EQUIVALENT_CONTRACT
    return sealed_validate_selection(translated)


original.validate_selection = validate_selection


def main() -> int:
    return int(original.main())


if __name__ == "__main__":
    raise SystemExit(main())

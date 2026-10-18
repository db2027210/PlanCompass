#!/usr/bin/env python3
"""TPC-C v32 controller with exact-repeat-aware fingerprint admission."""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

try:
    import build_tpcc_exact_plan_repeat_validation_v4 as repeat_v4
    import tpcc_autonomous_target10_controller_v25 as v25
    import tpcc_autonomous_target10_controller_v28_role_aware as v28
    import tpcc_autonomous_target10_controller_v29_prelaunch_fingerprint as v29
    import tpcc_autonomous_target10_controller_v31_cross_run_stability as v31
except ModuleNotFoundError:
    from tools import build_tpcc_exact_plan_repeat_validation_v4 as repeat_v4
    from tools import tpcc_autonomous_target10_controller_v25 as v25
    from tools import tpcc_autonomous_target10_controller_v28_role_aware as v28
    from tools import tpcc_autonomous_target10_controller_v29_prelaunch_fingerprint as v29
    from tools import tpcc_autonomous_target10_controller_v31_cross_run_stability as v31


def _verified_latest_repeat_source(
    controller_state: Mapping[str, Any],
) -> tuple[Mapping[str, Any], Dict[str, Any]]:
    audits = list(controller_state.get("audits") or [])
    if not audits:
        raise RuntimeError("validation repeat has no prior sealed audit")
    latest = audits[-1]
    if (
        latest.get("mode") not in {"diagnostic", "diagnostic_validation"}
        or latest.get("clean") is not True
        or int(latest.get("physical_calls", -1)) != 5
        or int(latest.get("valid_replays", -1)) != 5
        or int(latest.get("technical_failures", -1)) != 0
        or latest.get("baseline_restored") is not True
    ):
        raise RuntimeError("validation repeat source audit is not clean 5/5/0")
    selection_path = Path(str(latest.get("selection_path") or "")).resolve()
    selection_sha256 = str(latest.get("selection_sha256") or "")
    ledger_path = Path(str(latest.get("ledger_path") or "")).resolve()
    ledger_sha256 = str(latest.get("ledger_sha256") or "")
    if (
        not selection_path.is_file()
        or v25.sha256(selection_path) != selection_sha256
        or not ledger_path.is_file()
        or v25.sha256(ledger_path) != ledger_sha256
    ):
        raise RuntimeError("validation repeat source SHA mismatch")
    return latest, v25.load_json(selection_path)


def validation_repeat_prelaunch_fingerprint_gate(
    selection: Mapping[str, Any],
    mode: str,
    controller_state: Mapping[str, Any],
) -> Dict[str, Any]:
    """Apply novelty to new/formal points and provenance to exact repeats."""
    global_gate = v29.global_prelaunch_fingerprint_gate(
        selection,
        mode,
        controller_state,
    )
    if mode != "diagnostic_validation":
        return global_gate

    latest, source_selection = _verified_latest_repeat_source(controller_state)
    contract = selection.get("validation_repeat_contract")
    if not isinstance(contract, Mapping) or contract.get("contract") != repeat_v4.REPEAT_CONTRACT:
        raise RuntimeError("diagnostic_validation lacks sealed repeat contract")
    required_pairs = (
        ("source_selection_path", latest["selection_path"]),
        ("source_selection_sha256", latest["selection_sha256"]),
        ("source_ledger_path", latest["ledger_path"]),
        ("source_ledger_sha256", latest["ledger_sha256"]),
    )
    for field, expected in required_pairs:
        observed = str(contract.get(field) or "")
        if field.endswith("_path"):
            observed = str(Path(observed).resolve())
            expected = str(Path(str(expected)).resolve())
        else:
            expected = str(expected)
        if observed != expected:
            raise RuntimeError(f"validation repeat {field} mismatch")

    source_identity = repeat_v4.exact_top5_identity(source_selection)
    repeat_identity = repeat_v4.exact_top5_identity(selection)
    identity_values = {
        source_identity["sha256"],
        repeat_identity["sha256"],
        str(contract.get("source_top5_identity_sha256") or ""),
        str(contract.get("repeat_top5_identity_sha256") or ""),
    }
    if len(identity_values) != 1:
        raise RuntimeError("validation repeat changed parameters or plan")

    prior_count = int(
        latest.get("maximum_total_exact_observation_count")
        or contract.get("prior_complete_run_count")
        or 0
    )
    if (
        prior_count < 1
        or prior_count >= v31.MAX_COMPLETE_RUN_REPEATS
        or int(contract.get("prior_complete_run_count", -1)) != prior_count
        or int(contract.get("independent_run_ordinal", -1)) != prior_count + 1
        or int(contract.get("novel_fingerprint_credit", -1)) != 0
        or contract.get("same_exact_parameter_plan_set") is not True
        or contract.get("source_selection_sha_verified") is not True
        or int(controller_state.get("next_ordinal", 0)) < 1
    ):
        raise RuntimeError("validation repeat ordinal/provenance mismatch")

    result = copy.deepcopy(global_gate)
    result.update(
        {
            "contract": "tpcc_validation_repeat_fingerprint_admission_v32",
            "passed": True,
            "collision_ids": [],
            "allowed_exact_repeat_collision_ids": copy.deepcopy(
                global_gate.get("collision_ids") or []
            ),
            "exact_repeat_identity_sha256": repeat_identity["sha256"],
            "source_selection_sha256": latest["selection_sha256"],
            "independent_run_ordinal": prior_count + 1,
            "novel_fingerprint_credit": 0,
            "novelty_gate_role": "not_applicable_sealed_exact_repeat",
        }
    )
    return result


def audit_completed_run_v32(
    run: Mapping[str, Any],
    formal_state: Mapping[str, Any],
    *,
    verify_current_state_sha: bool = True,
) -> Dict[str, Any]:
    audit = v31.audit_completed_run_v31(
        run,
        formal_state,
        verify_current_state_sha=verify_current_state_sha,
    )
    if audit.get("mode") == "diagnostic_validation":
        audit["validation_repeat"] = True
        audit["novel_fingerprint_credit"] = 0
        audit["novel_fingerprint_ids"] = []
        audit["repeat_fingerprint_ids"] = copy.deepcopy(
            audit.get("physical_fingerprint_ids") or []
        )
    return audit


def main() -> int:
    manifest_path = Path(sys.argv[1]).resolve()
    manifest = v25.load_json(manifest_path)
    repeat_builder = Path(manifest["exact_repeat_builder_path"]).resolve()
    if repeat_builder.name != "build_tpcc_exact_plan_repeat_validation_v4.py":
        raise RuntimeError("v32 requires the v4 sealed exact-repeat builder")
    v31._configure(manifest_path)
    v29._configure_from_argv(sys.argv)
    v25.prelaunch_fingerprint_gate = validation_repeat_prelaunch_fingerprint_gate
    v25.accumulated_observation_formal_selection = (
        v28.accumulated_observation_formal_selection_v28
    )
    v25.audit_completed_run = audit_completed_run_v32
    v25.quality_gate = v31.quality_gate_v31
    v25.build_exact_calibration = v31.build_exact_calibration_v31
    v25.choose_next_mode = v31.choose_next_mode_v31
    return v25.main()


if __name__ == "__main__":
    raise SystemExit(main())

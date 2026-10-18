#!/usr/bin/env python3
"""TPC-C v34 controller with same-group repeat accounting."""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any, Dict, Mapping, MutableMapping, Sequence

try:
    import build_tpcc_exact_plan_repeat_validation_v5 as repeat_v5
    import tpcc_autonomous_target10_controller_v25 as v25
    import tpcc_autonomous_target10_controller_v28_role_aware as v28
    import tpcc_autonomous_target10_controller_v29_prelaunch_fingerprint as v29
    import tpcc_autonomous_target10_controller_v31_cross_run_stability as v31
    import tpcc_autonomous_target10_controller_v32_validation_repeat as v32
except ModuleNotFoundError:
    from tools import build_tpcc_exact_plan_repeat_validation_v5 as repeat_v5
    from tools import tpcc_autonomous_target10_controller_v25 as v25
    from tools import tpcc_autonomous_target10_controller_v28_role_aware as v28
    from tools import tpcc_autonomous_target10_controller_v29_prelaunch_fingerprint as v29
    from tools import tpcc_autonomous_target10_controller_v31_cross_run_stability as v31
    from tools import tpcc_autonomous_target10_controller_v32_validation_repeat as v32


def _audit_identity(audit: Mapping[str, Any]) -> str:
    cached = str(audit.get("exact_top5_group_identity_sha256") or "")
    if cached:
        return cached
    path = Path(str(audit.get("selection_path") or "")).resolve()
    expected = str(audit.get("selection_sha256") or "")
    if not path.is_file() or v25.sha256(path) != expected:
        raise RuntimeError("same-group audit selection SHA mismatch")
    return repeat_v5.v4.exact_top5_identity(v25.load_json(path))["sha256"]


def annotate_same_group_batch_count(
    audit: Mapping[str, Any],
    prior_audits: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    result = copy.deepcopy(dict(audit))
    identity = _audit_identity(result)
    matching = []
    for prior in prior_audits:
        if prior.get("clean") is True and _audit_identity(prior) == identity:
            matching.append(prior)
    count = len(matching) + 1
    assessments = list(result.get("score_stability_assessments") or [])
    vector = [
        {
            "rank": int(item["rank"]),
            "point_key": str(item["point_key"]),
            "total_exact_observation_count": int(
                item["repeatability"]["observation_count"]
            ),
        }
        for item in assessments
    ]
    vector.sort(key=lambda item: item["rank"])
    counts = [item["total_exact_observation_count"] for item in vector]
    if len(counts) != 5:
        raise RuntimeError("same-group audit requires five valid point assessments")
    origin_novel = bool(
        result.get("group_origin_novel_fingerprint_gate_passed") is True
        or any(
            prior.get("group_origin_novel_fingerprint_gate_passed") is True
            for prior in matching
        )
    )
    result.update(
        {
            "exact_top5_group_identity_sha256": identity,
            "complete_same_group_batch_count": count,
            "same_group_batch_count_contract": (
                "clean_complete_exact_top5_batches_v34"
            ),
            "per_point_total_exact_observation_counts": vector,
            "minimum_total_exact_observation_count": min(counts),
            "maximum_total_exact_observation_count": max(counts),
            "formal_gate_minimum_per_point_observations": 3,
            "repeat_cap_basis": "complete_same_group_batch_count",
            "group_origin_novel_fingerprint_gate_passed": origin_novel,
        }
    )
    return result


def validation_repeat_prelaunch_fingerprint_gate_v34(
    selection: Mapping[str, Any],
    mode: str,
    controller_state: Mapping[str, Any],
) -> Dict[str, Any]:
    global_gate = v29.global_prelaunch_fingerprint_gate(
        selection, mode, controller_state
    )
    if mode != "diagnostic_validation":
        return global_gate

    latest, source_selection = v32._verified_latest_repeat_source(controller_state)
    contract = selection.get("validation_repeat_contract")
    if not isinstance(contract, Mapping) or contract.get("contract") != repeat_v5.REPEAT_CONTRACT:
        raise RuntimeError("diagnostic_validation lacks v34 repeat contract")
    for field, expected in (
        ("source_selection_path", latest["selection_path"]),
        ("source_selection_sha256", latest["selection_sha256"]),
        ("source_ledger_path", latest["ledger_path"]),
        ("source_ledger_sha256", latest["ledger_sha256"]),
    ):
        observed = str(contract.get(field) or "")
        if field.endswith("_path"):
            observed = str(Path(observed).resolve())
            expected = str(Path(str(expected)).resolve())
        else:
            expected = str(expected)
        if observed != expected:
            raise RuntimeError(f"validation repeat {field} mismatch")

    source_identity = repeat_v5.v4.exact_top5_identity(source_selection)
    repeat_identity = repeat_v5.v4.exact_top5_identity(selection)
    if len(
        {
            source_identity["sha256"],
            repeat_identity["sha256"],
            str(contract.get("source_top5_identity_sha256") or ""),
            str(contract.get("repeat_top5_identity_sha256") or ""),
            _audit_identity(latest),
        }
    ) != 1:
        raise RuntimeError("validation repeat changed parameters or plan")

    completed = int(latest.get("complete_same_group_batch_count", 0))
    vector = list(contract.get("per_point_total_exact_observation_counts") or [])
    observed_counts = [
        int((item.get("confidence") or {}).get("measurement_count", -1))
        for item in selection.get("top5") or []
    ]
    contract_counts = [
        int(item.get("total_exact_observation_count", -1)) for item in vector
    ]
    if (
        completed < 1
        or completed >= v31.MAX_COMPLETE_RUN_REPEATS
        or int(contract.get("prior_complete_same_group_batch_count", -1)) != completed
        or int(contract.get("independent_same_group_batch_ordinal", -1)) != completed + 1
        or contract.get("repeat_cap_basis") != "complete_same_group_batch_count"
        or contract.get("heterogeneous_point_observation_counts_allowed") is not True
        or len(vector) != 5
        or observed_counts != contract_counts
        or min(contract_counts) < 1
        or int(contract.get("minimum_total_exact_observation_count", -1)) != min(contract_counts)
        or int(contract.get("maximum_total_exact_observation_count", -1)) != max(contract_counts)
        or int(contract.get("novel_fingerprint_credit", -1)) != 0
        or contract.get("same_exact_parameter_plan_set") is not True
        or contract.get("source_selection_sha_verified") is not True
    ):
        raise RuntimeError("validation repeat v34 ordinal/provenance mismatch")

    result = copy.deepcopy(global_gate)
    result.update(
        {
            "contract": "tpcc_validation_repeat_fingerprint_admission_v34",
            "passed": True,
            "collision_ids": [],
            "allowed_exact_repeat_collision_ids": copy.deepcopy(
                global_gate.get("collision_ids") or []
            ),
            "exact_repeat_identity_sha256": repeat_identity["sha256"],
            "source_selection_sha256": latest["selection_sha256"],
            "independent_same_group_batch_ordinal": completed + 1,
            "per_point_total_exact_observation_counts": contract_counts,
            "novel_fingerprint_credit": 0,
            "novelty_gate_role": "inherited_from_novel_group_origin",
        }
    )
    return result


def audit_completed_run_v34(
    run: Mapping[str, Any],
    formal_state: Mapping[str, Any],
    *,
    verify_current_state_sha: bool = True,
) -> Dict[str, Any]:
    audit = v32.audit_completed_run_v32(
        run,
        formal_state,
        verify_current_state_sha=verify_current_state_sha,
    )
    if audit.get("mode") == "formal":
        return audit
    if v31._CONTROLLER_STATE_PATH is None:
        raise RuntimeError("v34 controller state path is not configured")
    prior = v25.load_json(v31._CONTROLLER_STATE_PATH).get("audits") or []
    return annotate_same_group_batch_count(audit, prior)


def quality_gate_v34(audits: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    gate = v31.quality_gate_v31(audits)
    latest = next(
        (
            item
            for item in reversed(audits)
            if item.get("mode") == "diagnostic_validation"
        ),
        None,
    )
    checks = gate.setdefault("checks", {})
    checks["five_novel_noncorner_fingerprints"] = bool(
        latest is not None
        and latest.get("group_origin_novel_fingerprint_gate_passed") is True
        and len(set(latest.get("fingerprint_ids") or [])) == v25.CAP
    )
    checks["at_least_three_exact_observations_per_candidate"] = bool(
        latest is not None
        and int(latest.get("minimum_total_exact_observation_count", 0))
        >= v31.MIN_COMPLETE_RUN_REPEATS
    )
    checks["same_group_repeat_cap_not_exhausted"] = bool(
        latest is not None
        and int(latest.get("complete_same_group_batch_count", 0))
        <= v31.MAX_COMPLETE_RUN_REPEATS
    )
    gate["passed"] = all(checks.values())
    gate["formal_start_authorized"] = bool(gate["passed"])
    gate["complete_same_group_batch_count"] = int(
        (latest or {}).get("complete_same_group_batch_count", 0)
    )
    gate["repeat_cap_basis"] = "complete_same_group_batch_count"
    return gate


def choose_next_mode_v34(
    audits: Sequence[Mapping[str, Any]], gate: Mapping[str, Any]
) -> str:
    latest = audits[-1] if audits else None
    if latest is not None and latest.get("mode") == "diagnostic_validation":
        completed = int(latest.get("complete_same_group_batch_count", 0))
        if (
            latest.get("clean") is True
            and gate.get("passed") is not True
            and completed < v31.MAX_COMPLETE_RUN_REPEATS
            and (
                int(latest.get("minimum_total_exact_observation_count", 0))
                < v31.MIN_COMPLETE_RUN_REPEATS
                or latest.get("all_trusted_complete_run_repeatable") is not True
            )
        ):
            if isinstance(gate, MutableMapping):
                gate["complete_run_repeatability_reroute"] = True
                gate["complete_run_repeatability_next_mode"] = (
                    "diagnostic_validation"
                )
                gate["repeat_cap_basis"] = "complete_same_group_batch_count"
            return "diagnostic_validation"
    return v28.choose_next_mode_v28(audits, gate)


def main() -> int:
    manifest_path = Path(sys.argv[1]).resolve()
    manifest = v25.load_json(manifest_path)
    repeat_builder = Path(manifest["exact_repeat_builder_path"]).resolve()
    if repeat_builder.name != "build_tpcc_exact_plan_repeat_validation_v5.py":
        raise RuntimeError("v34 requires the v5 heterogeneous repeat builder")
    v31._configure(manifest_path)
    v29._configure_from_argv(sys.argv)
    v25.prelaunch_fingerprint_gate = validation_repeat_prelaunch_fingerprint_gate_v34
    v25.accumulated_observation_formal_selection = (
        v28.accumulated_observation_formal_selection_v28
    )
    v25.audit_completed_run = audit_completed_run_v34
    v25.quality_gate = quality_gate_v34
    v25.build_exact_calibration = v31.build_exact_calibration_v31
    v25.choose_next_mode = choose_next_mode_v34
    return v25.main()


if __name__ == "__main__":
    raise SystemExit(main())

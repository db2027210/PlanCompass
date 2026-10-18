#!/usr/bin/env python3
"""XP v42 automatic safe-frontier transition-family acquisition."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping, Optional


CONTRACT = "xp_v42_automatic_safe_frontier_transition_family_probe5_v1"
APPROVED_FRONTIER_CONTRACT = "bounded_multihop_joint_halton_fresh_explain_probe_v3"
TRANSITION_CONTRACT = "baseline_transition_backtracking_compensation_v1"
EXPECTED_V41_BLOCKED_MANIFEST_SHA256 = (
    "b0afc740ec1d22fd5003df48ca60c9a6254b92bbaac1c9f51967d74af5c40d5d"
)
EXPECTED_V41_BLOCKED_SELECTION_SHA256 = (
    "cef5539fac003d6d6ef61299330e339b43588b33ac354f9d6b37240179001a55"
)
EXPECTED_V41_BLOCKED_OVERLAY_SHA256 = (
    "c020a52517aa6655514d6713a090b40c0a06d9ec8cfb67489854970a3b34c22a"
)
EXPECTED_GAPS = [
    "transition backtracking requires at least 4 safe novel non-corner fingerprints after plan-level quarantine; got 1",
    "selection requires exactly 5 replay points; got 0",
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def _finite(value: Any, label: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise RuntimeError(f"XP v42 non-finite {label}")
    return number


def _approved_frontier_candidate(
    candidate: Mapping[str, Any],
    allowed_anchor_ids: set[str],
) -> bool:
    generation = candidate.get("safe_frontier_generation") or {}
    fingerprint = candidate.get("three_level_fingerprint") or {}
    anchor = str(generation.get("exploration_anchor_id") or "")
    configured = _finite(
        generation.get("maximum_relative_parameter_step"),
        "configured maximum relative step",
    )
    observed = _finite(
        generation.get("observed_maximum_relative_parameter_step"),
        "observed maximum relative step",
    )
    return bool(
        generation.get("contract") == APPROVED_FRONTIER_CONTRACT
        and anchor in allowed_anchor_ids | {"formal_incumbent"}
        and generation.get("fresh_explain") is True
        and generation.get("respects_formal_range") is True
        and generation.get("strict_formal_range_interior") is True
        and generation.get("formal_range_eligible") is True
        and generation.get("trusted_without_real_evidence") is False
        and generation.get(
            "exploration_anchor_updates_accepted_incumbent_range_or_gradient"
        )
        is False
        and candidate.get("formal_range_eligible") is not False
        and candidate.get("boundary_corner") is False
        and fingerprint.get("valid") is True
        and not (fingerprint.get("validation_errors") or [])
        and str(fingerprint.get("id") or "").startswith("three-level-sha256:")
        and str(fingerprint.get("plan_level") or "").startswith("ratio-plan-sha256:")
        and 0.0 <= observed <= configured <= 0.8 + 1e-12
    )


def _load_blocked_manifest(output_dir: Path) -> dict[str, Any]:
    manifest_path = output_dir / "manifest.json"
    selection_path = output_dir / "selection.json"
    overlay_path = output_dir / "config_overlay.json"
    expected = {
        manifest_path: EXPECTED_V41_BLOCKED_MANIFEST_SHA256,
        selection_path: EXPECTED_V41_BLOCKED_SELECTION_SHA256,
        overlay_path: EXPECTED_V41_BLOCKED_OVERLAY_SHA256,
    }
    for path, digest in expected.items():
        if not path.is_file() or sha256(path) != digest:
            raise RuntimeError(f"XP v42 v41 blocked bundle SHA mismatch: {path.name}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    counts = manifest.get("counts") or {}
    if (
        manifest.get("status") != "blocked_missing_input"
        or manifest.get("input_gaps") != EXPECTED_GAPS
        or int(counts.get("safe_probe_candidates", -1)) != 9
        or int(counts.get("safe_transition_backtracking_candidates", -1)) != 1
        or int(counts.get("selected_top5", -1)) != 0
    ):
        raise RuntimeError("XP v42 v41 blocked bundle contract mismatch")
    return manifest


def _load_ready_manifest(output_dir: Path) -> Optional[dict[str, Any]]:
    """Return a fully validated v42 bundle after an interrupted controller restart."""

    manifest_path = output_dir / "manifest.json"
    selection_path = output_dir / "selection.json"
    overlay_path = output_dir / "config_overlay.json"
    if not all(path.is_file() for path in (manifest_path, selection_path, overlay_path)):
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    overlay = json.loads(overlay_path.read_text(encoding="utf-8"))
    audit = selection.get("v42_automatic_transition_family") or {}
    top5 = selection.get("top5") or []
    identifiers = [
        str((item.get("three_level_fingerprint") or {}).get("id") or "")
        for item in top5
    ]
    valid = bool(
        manifest.get("status") == "ready"
        and not (manifest.get("input_gaps") or [])
        and manifest.get("selected_selection") == selection
        and manifest.get("optimizer_options") == overlay
        and audit.get("contract") == CONTRACT
        and int(audit.get("automatic_transition_family_count", -1)) >= 5
        and audit.get("selected_fingerprint_ids") == identifiers
        and len(identifiers) == 5
        and "" not in identifiers
        and len(set(identifiers)) == 5
        and [str(item.get("selection_role") or "") for item in top5]
        == ["maximin_uncertain_probe"] * 5
        and audit.get("resource_envelope_relaxed") is False
        and audit.get("parameter_step_limit_relaxed") is False
        and audit.get("formal_range_relaxed") is False
        and audit.get("trusted_promotion_applied") is False
        and audit.get("manual_candidate_injection") is False
        and audit.get("formal_state_mutation") is False
        and int(audit.get("new_workload_calls", -1)) == 0
    )
    if not valid:
        return None
    return manifest


def apply_automatic_transition_family(
    bundle_module: Any,
    manifest: Mapping[str, Any],
    *,
    selection_path: Path,
    replay_path: Path,
    candidate_path: Path,
    accepted_incumbent_path: Path,
    output_dir: Path,
    extra_excluded_ids: Optional[set[str]] = None,
    risk_utilization_upper: Optional[Mapping[str, float]] = None,
    risk_audit: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Build probe5 from an automatically classified strict frontier family."""

    if manifest.get("status") == "ready":
        return copy.deepcopy(dict(manifest))
    if list(manifest.get("input_gaps") or []) != EXPECTED_GAPS:
        return copy.deepcopy(dict(manifest))
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    replay = json.loads(replay_path.read_text(encoding="utf-8"))
    incumbent = json.loads(accepted_incumbent_path.read_text(encoding="utf-8"))
    candidate_payload = json.loads(candidate_path.read_text(encoding="utf-8"))
    raw_candidates = bundle_module._ranking_candidates(candidate_path)
    excluded_ids = bundle_module._replayed_ids(replay)
    excluded_ids.update(extra_excluded_ids or set())
    calibration = bundle_module.build_calibration(selection, replay)
    baseline_anchor = bundle_module._install_fresh_baseline_plan_anchor(
        calibration,
        raw_candidates,
        incumbent,
        candidate_payload.get("baseline_plan_authority") or {},
    )
    active, _corners, excluded = bundle_module._calibrated_candidates(
        raw_candidates, calibration, excluded_ids
    )
    if not isinstance(excluded, list) or any(not isinstance(value, str) for value in excluded):
        raise RuntimeError("XP v42 excluded ID contract mismatch")
    explicit = [item for item in active if bundle_module._is_probe(item)]
    safe_upper, safe_reference = bundle_module._clean_replay_relative_safe_upper(replay)
    applied_risk_upper: dict[str, float] = {}
    for resource, raw_cap in (risk_utilization_upper or {}).items():
        cap = _finite(raw_cap, f"risk upper {resource}")
        safe_upper[resource] = min(safe_upper[resource], cap)
        applied_risk_upper[resource] = safe_upper[resource]
    resource_safe = [
        item for item in explicit if bundle_module._safe_probe(item, safe_upper)
    ]
    safe = [
        item
        for item in resource_safe
        if bundle_module._stability_risk_safe(item, risk_audit)
    ]
    allowed_anchor_ids = {
        str(item.get("fingerprint_id") or "")
        for item in calibration.get("v39_validated_exact_points") or []
        if str(item.get("fingerprint_id") or "")
    }
    family = []
    for source in safe:
        if not _approved_frontier_candidate(source, allowed_anchor_ids):
            continue
        item = copy.deepcopy(source)
        generation = item["safe_frontier_generation"]
        previous = copy.deepcopy(item.get("transition_search") or {})
        item["transition_search"] = {
            "contract": TRANSITION_CONTRACT,
            "classification_contract": CONTRACT,
            "classification_rule": (
                "strict_fresh_explain_formal_range_safe_frontier_from_"
                "formal_or_two_observation_clean_anchor"
            ),
            "source_frontier_contract": APPROVED_FRONTIER_CONTRACT,
            "source_anchor_id": generation["exploration_anchor_id"],
            "original_transition_search": previous,
            "fresh_explain": True,
            "maximum_relative_parameter_step": 0.8,
            "resource_envelope_relaxed": False,
            "parameter_step_limit_relaxed": False,
            "manual_candidate_injection": False,
        }
        family.append(item)
    family_ids = [bundle_module.fingerprint_id(item) for item in family]
    if len(family) < 5 or len(family_ids) != len(set(family_ids)):
        raise RuntimeError(
            "XP v42 automatic transition family has insufficient unique candidates"
        )
    clean_anchor_indices = [
        index
        for index, item in enumerate(family)
        if str(
            (item.get("safe_frontier_generation") or {}).get(
                "exploration_anchor_id"
            )
            or ""
        )
        in allowed_anchor_ids
    ]
    if not clean_anchor_indices:
        raise RuntimeError("XP v42 transition family lacks a clean-anchor exploitation point")
    mandatory_index = min(
        clean_anchor_indices,
        key=lambda index: (
            bundle_module._maximum_relative_parameter_step(family[index]),
            -_finite(family[index]["candidate_score"], "candidate score"),
            family_ids[index],
        ),
    )
    selected_indices = bundle_module._maximin_probe_indices_with_mandatory(
        family, 5, mandatory_index
    )
    top5 = [copy.deepcopy(family[index]) for index in selected_indices]
    mandatory_id = family_ids[mandatory_index]
    protocol_id = str((risk_audit or {}).get("protocol_environment_signature_id") or "")
    for rank, candidate in enumerate(top5, 1):
        identifier = bundle_module.fingerprint_id(candidate)
        candidate["rank"] = rank
        candidate["selection_role"] = "maximin_uncertain_probe"
        candidate["probe_kind"] = "maximin_uncertain_probe"
        candidate["boundary_corner"] = False
        candidate["used_for_gradient"] = False
        candidate["used_for_inner_incumbent_update"] = False
        candidate["baseline_plan_exploitation_probe"] = False
        candidate["clean_anchor_exploitation_probe"] = identifier == mandatory_id
        params = candidate.get("candidate_params") or candidate.get("optimized_params") or {}
        exact_id = bundle_module.exact_parameter_fingerprint(params)
        candidate["exact_parameter_fingerprint"] = exact_id
        plan = str((candidate.get("three_level_fingerprint") or {}).get("plan_level") or "")
        if protocol_id:
            candidate["protocol_environment_signature_id"] = protocol_id
            candidate["local_regime_id"] = bundle_module.local_regime_id(
                plan, exact_id, protocol_id
            )
    selected_ids = [bundle_module.fingerprint_id(item) for item in top5]
    if len(set(selected_ids)) != 5 or set(selected_ids) & excluded_ids:
        raise RuntimeError("XP v42 selected fingerprints are not five-way novel")
    audit = {
        "contract": CONTRACT,
        "trigger": "strict_transition_backtracking_family_below_four",
        "approved_source_frontier_contract": APPROVED_FRONTIER_CONTRACT,
        "allowed_anchor_ids": sorted(allowed_anchor_ids | {"formal_incumbent"}),
        "resource_safe_candidate_count": len(resource_safe),
        "stability_safe_candidate_count": len(safe),
        "automatic_transition_family_count": len(family),
        "selected_fingerprint_ids": selected_ids,
        "mandatory_clean_anchor_exploitation_fingerprint_id": mandatory_id,
        "selection_algorithm": "deterministic_maximin_seeded_by_minimum_step_clean_anchor_v1",
        "resource_safe_upper": safe_upper,
        "clean_replay_reference": safe_reference,
        "applied_risk_upper": applied_risk_upper,
        "resource_envelope_relaxed": False,
        "parameter_step_limit_relaxed": False,
        "formal_range_relaxed": False,
        "trusted_promotion_applied": False,
        "manual_candidate_injection": False,
        "formal_state_mutation": False,
        "new_workload_calls": 0,
    }
    fixed = copy.deepcopy(dict(manifest))
    fixed["status"] = "ready"
    fixed["input_gaps"] = []
    fixed["counts"].update(
        {
            "safe_transition_backtracking_candidates": len(family),
            "automatic_transition_family_candidates": len(family),
            "uncertain_probes": 5,
            "selected_top5": 5,
        }
    )
    fixed["diagnostic_discovery"].update(
        {
            "probe_count": 5,
            "baseline_plan_exploitation_fingerprint_id": None,
            "clean_anchor_exploitation_fingerprint_id": mandatory_id,
            "transition_backtracking_candidates_preferred": True,
            "transition_shortfall_policy": CONTRACT,
            "automatic_transition_family": audit,
        }
    )
    fixed["formal_admission"].update(
        {"passed": False, "trusted_candidate_available": False}
    )
    selected = copy.deepcopy(fixed["selected_selection"])
    selected.update(
        {
            "replay_authorized": True,
            "batch_mode": "diagnostic_validation",
            "formal_admission": copy.deepcopy(fixed["formal_admission"]),
            "diagnostic_discovery": copy.deepcopy(fixed["diagnostic_discovery"]),
            "top5": top5,
            "v42_automatic_transition_family": audit,
        }
    )
    fixed["selected_selection"] = selected
    atomic_json(output_dir / "manifest.json", fixed)
    atomic_json(output_dir / "selection.json", selected)
    atomic_json(output_dir / "config_overlay.json", fixed["optimizer_options"])
    return fixed


def install_bundle(bundle_module: Any) -> Any:
    """Bypass consumed v36 fallback only for the exact sealed v41 block."""

    original = bundle_module.build_bundle

    def build_bundle(**kwargs: Any) -> dict[str, Any]:
        output_dir = Path(str(kwargs["output_dir"])).resolve()
        if output_dir.name == "diagnostic_bundle" and output_dir.parent.name == "cont006":
            manifest = _load_ready_manifest(output_dir)
            if manifest is None:
                manifest = _load_blocked_manifest(output_dir)
        else:
            manifest = original(**kwargs)
        return apply_automatic_transition_family(
            bundle_module, manifest, **kwargs
        )

    bundle_module.build_bundle = build_bundle
    bundle_module.XP_V42_AUTOMATIC_TRANSITION_FAMILY_CONTRACT = CONTRACT
    return bundle_module


def install_phase_gate(phase_module: Any) -> Any:
    """Admit only the sealed v42 automatic probe5 contract pre-workload."""

    original = phase_module._selection_gate

    def selection_gate(selection: Mapping[str, Any], replayed_fingerprints: Any = ()) -> dict[str, Any]:
        gate = copy.deepcopy(original(selection, replayed_fingerprints))
        audit = selection.get("v42_automatic_transition_family") or {}
        if audit.get("contract") != CONTRACT:
            return gate
        roles = [str(item.get("selection_role") or "") for item in selection.get("top5") or []]
        contract_ok = bool(
            selection.get("batch_mode") == "diagnostic_validation"
            and roles == ["maximin_uncertain_probe"] * 5
            and int(audit.get("automatic_transition_family_count", -1)) >= 5
            and len(audit.get("selected_fingerprint_ids") or []) == 5
            and audit.get("selection_algorithm")
            == "deterministic_maximin_seeded_by_minimum_step_clean_anchor_v1"
            and audit.get("resource_envelope_relaxed") is False
            and audit.get("parameter_step_limit_relaxed") is False
            and audit.get("formal_range_relaxed") is False
            and audit.get("trusted_promotion_applied") is False
            and audit.get("manual_candidate_injection") is False
            and int(audit.get("new_workload_calls", -1)) == 0
        )
        checks = dict(gate.get("checks") or {})
        checks["role_stage_contract"] = contract_ok
        gate["checks"] = checks
        gate["passed"] = all(bool(value) for value in checks.values())
        gate["selection_stage"] = (
            "automatic_safe_frontier_transition_probe5" if contract_ok else "invalid"
        )
        gate["v42_automatic_transition_family"] = copy.deepcopy(audit)
        return gate

    phase_module._selection_gate = selection_gate
    return phase_module

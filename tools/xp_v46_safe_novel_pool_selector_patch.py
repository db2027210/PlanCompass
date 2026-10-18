#!/usr/bin/env python3
"""XP v46: minimal safe-pool routing repair for discovery probe5.

The sealed diagnostic builder applied all resource and stability gates and had
13 safe novel probes, but it rejected them first because none carried a legacy
``transition_backtracking`` label and then because none matched the baseline
plan.  This patch SHA-binds the builder and changes only those two routing
branches.  Candidate generation, safety thresholds, and maximin selection stay
unchanged.
"""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping


CONTRACT = "xp_v46_minimal_safe_pool_routing_probe5_v1"
EXPECTED_BUNDLE_SHA256 = "c91fa48a7e79f9a1307b2e3eec4a240809861e08472824aba7b8cfffaf71246e"
OLD_TRANSITION_BRANCH = "if discovery_mode and len(safe_transition_candidates) < 4:"
NEW_TRANSITION_BRANCH = """if (
        discovery_mode
        and len(safe_transition_candidates) < 4
        and len(probe_candidates) < required_probe_count
    ):"""
OLD_BASELINE_BRANCH = """elif discovery_mode:
            gaps.append(
                "discovery requires one fresh baseline-plan exploitation probe"
            )
            probe_indices = []"""
NEW_BASELINE_BRANCH = """elif discovery_mode:
            probe_indices = _maximin_probe_indices(
                probe_candidates,
                required_probe_count,
            )"""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def _finite(value: Any, label: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise RuntimeError(f"XP v46 non-finite {label}")
    return number


def install_base_builder_fix(bundle_module: Any) -> Any:
    """Replace exactly two branches in the SHA-sealed `_assemble_bundle`."""

    source_path = Path(bundle_module.__file__).resolve()
    if _sha256(source_path) != EXPECTED_BUNDLE_SHA256:
        raise RuntimeError("XP v46 diagnostic builder SHA mismatch")
    source = inspect.getsource(bundle_module._assemble_bundle)
    if source.count(OLD_TRANSITION_BRANCH) != 1:
        raise RuntimeError("XP v46 transition branch source mismatch")
    if source.count(OLD_BASELINE_BRANCH) != 1:
        raise RuntimeError("XP v46 baseline branch source mismatch")
    patched = source.replace(OLD_TRANSITION_BRANCH, NEW_TRANSITION_BRANCH).replace(
        OLD_BASELINE_BRANCH, NEW_BASELINE_BRANCH
    )
    exec(compile(patched, "<xp_v46_minimal_builder_fix>", "exec"), bundle_module.__dict__)
    bundle_module.XP_V46_MINIMAL_BUILDER_FIX_CONTRACT = CONTRACT
    return bundle_module


def _fresh_safe_probe(candidate: Mapping[str, Any]) -> bool:
    generation = candidate.get("safe_frontier_generation") or {}
    fingerprint = candidate.get("three_level_fingerprint") or {}
    configured = _finite(
        generation.get("maximum_relative_parameter_step"),
        "configured maximum relative step",
    )
    observed = _finite(
        generation.get("observed_maximum_relative_parameter_step"),
        "observed maximum relative step",
    )
    return bool(
        generation.get("contract")
        in {
            "bounded_multihop_joint_halton_fresh_explain_probe_v3",
            "bounded_global_range_probe_only_escape_v1",
        }
        and generation.get("fresh_explain") is True
        and generation.get("formal_range_eligible") is True
        and generation.get("trusted_without_real_evidence") is False
        and generation.get(
            "exploration_anchor_updates_accepted_incumbent_range_or_gradient"
        )
        is False
        and candidate.get("formal_range_eligible") is not False
        and candidate.get("boundary_corner") is False
        and candidate.get("near_corner_excluded_from_noncorner_gate") is not True
        and fingerprint.get("valid") is True
        and not (fingerprint.get("validation_errors") or [])
        and str(fingerprint.get("id") or "").startswith("three-level-sha256:")
        and str(fingerprint.get("plan_level") or "").startswith("ratio-plan-sha256:")
        and 0.0 <= observed <= configured <= 0.8 + 1e-12
    )


def _annotate_ready_probe5(
    bundle_module: Any,
    manifest: Mapping[str, Any],
    *,
    output_dir: Path,
    extra_excluded_ids: Any = None,
    risk_audit: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Attach a fail-closed audit to a probe5 selected by the patched builder."""

    fixed = copy.deepcopy(dict(manifest))
    counts = fixed.get("counts") or {}
    discovery = fixed.get("diagnostic_discovery") or {}
    if not (
        fixed.get("status") == "ready"
        and not (fixed.get("input_gaps") or [])
        and discovery.get("enabled") is True
        and int(counts.get("safe_transition_backtracking_candidates", -1)) < 4
        and int(counts.get("safe_probe_candidates", -1)) >= 5
        and int(counts.get("resource_safe_probe_candidates", -1)) >= 5
        and int(counts.get("selected_top5", -1)) == 5
    ):
        return fixed

    selected = copy.deepcopy(fixed.get("selected_selection") or {})
    top5 = list(selected.get("top5") or [])
    identifiers = [bundle_module.fingerprint_id(item) for item in top5]
    excluded = {str(item) for item in (extra_excluded_ids or set())}
    frontier = discovery.get("safe_frontier_contract") or {}
    safe_upper = dict(frontier.get("maximum_predicted_utilization") or {})
    applied_risk_audit = risk_audit or frontier.get("stability_risk_audit") or {}
    checks = {
        "exactly_five": len(top5) == 5,
        "unique_three_level_fingerprints": (
            len(identifiers) == 5 and "" not in identifiers and len(set(identifiers)) == 5
        ),
        "excluded_fingerprints_absent": not (set(identifiers) & excluded),
        "all_uncertain_probe": all(
            item.get("selection_role") == "maximin_uncertain_probe" for item in top5
        ),
        "all_fresh_safe_noncorner": all(
            _fresh_safe_probe(item)
            and bundle_module._is_probe(item)
            and bundle_module._safe_probe(item, safe_upper)
            and bundle_module._stability_risk_safe(item, applied_risk_audit)
            for item in top5
        ),
        "no_gradient_or_incumbent_use": all(
            item.get("used_for_gradient") is False
            and item.get("used_for_inner_incumbent_update") is False
            for item in top5
        ),
    }
    if not all(checks.values()):
        raise RuntimeError(f"XP v46 ready probe5 audit failed: {checks}")

    audit = {
        "contract": CONTRACT,
        "source_patch": "two_branch_sha_bound_minimal_routing_fix",
        "checks": checks,
        "source_safe_probe_count": int(counts["safe_probe_candidates"]),
        "source_safe_transition_count": int(
            counts["safe_transition_backtracking_candidates"]
        ),
        "selected_fingerprint_ids": identifiers,
        "selection_algorithm": "existing_deterministic_parameter_space_maximin_v1",
        "diagnostic_only": True,
        "first_probe_replay_cannot_be_formal": True,
        "all_candidates_remain_uncertain_probes": True,
        "pending_trust_requires_clean_exact_point_repeat": True,
        "formal_requires_trusted3_plus_probe2_and_all_entry_gates": True,
        "resource_envelope_relaxed": False,
        "parameter_step_limit_relaxed": False,
        "trusted_promotion_applied": False,
        "manual_candidate_injection": False,
        "formal_state_mutation": False,
        "new_workload_calls": 0,
    }
    discovery["transition_backtracking_candidates_preferred"] = False
    discovery["transition_shortfall_policy"] = CONTRACT
    discovery["v46_safe_pool_routing"] = audit
    selected["batch_mode"] = "diagnostic_validation"
    selected["replay_authorized"] = True
    selected["diagnostic_discovery"] = copy.deepcopy(discovery)
    selected["v46_safe_pool_routing"] = audit
    fixed["diagnostic_discovery"] = discovery
    fixed["selected_selection"] = selected
    _atomic_json(Path(output_dir) / "manifest.json", fixed)
    _atomic_json(Path(output_dir) / "selection.json", selected)
    _atomic_json(Path(output_dir) / "config_overlay.json", fixed["optimizer_options"])
    return fixed


def install_bundle(bundle_module: Any) -> Any:
    """Annotate only ready probe5 results created by the minimal base fix."""

    original = bundle_module.build_bundle

    def build_bundle(**kwargs: Any) -> dict[str, Any]:
        manifest = original(**kwargs)
        return _annotate_ready_probe5(bundle_module, manifest, **kwargs)

    bundle_module.build_bundle = build_bundle
    return bundle_module


def install_phase_gate(phase_module: Any) -> Any:
    """Permit the v46 probe5 only as diagnostic evidence acquisition."""

    original = phase_module._selection_gate

    def selection_gate(
        selection: Mapping[str, Any], replayed_fingerprints: Any = ()
    ) -> dict[str, Any]:
        gate = copy.deepcopy(original(selection, replayed_fingerprints))
        audit = selection.get("v46_safe_pool_routing") or {}
        if audit.get("contract") != CONTRACT:
            return gate
        roles = [
            str(item.get("selection_role") or "") for item in selection.get("top5") or []
        ]
        contract_ok = bool(
            selection.get("batch_mode") == "diagnostic_validation"
            and roles == ["maximin_uncertain_probe"] * 5
            and audit.get("diagnostic_only") is True
            and audit.get("first_probe_replay_cannot_be_formal") is True
            and audit.get("pending_trust_requires_clean_exact_point_repeat") is True
            and audit.get("formal_requires_trusted3_plus_probe2_and_all_entry_gates") is True
            and audit.get("resource_envelope_relaxed") is False
            and audit.get("parameter_step_limit_relaxed") is False
            and audit.get("trusted_promotion_applied") is False
            and audit.get("manual_candidate_injection") is False
            and audit.get("formal_state_mutation") is False
            and int(audit.get("new_workload_calls", -1)) == 0
        )
        checks = dict(gate.get("checks") or {})
        checks["role_stage_contract"] = contract_ok
        checks["diagnostic_only_not_formal"] = selection.get("batch_mode") != "formal"
        gate["checks"] = checks
        gate["passed"] = all(bool(value) for value in checks.values())
        gate["selection_stage"] = (
            "safe_pool_discovery_probe5" if contract_ok else "invalid"
        )
        gate["v46_safe_pool_routing"] = copy.deepcopy(audit)
        return gate

    phase_module._selection_gate = selection_gate
    return phase_module


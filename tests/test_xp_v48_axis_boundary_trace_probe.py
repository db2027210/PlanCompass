#!/usr/bin/env python3
"""Offline regression tests for XP v48 cont010 zero-call compatibility."""

from __future__ import annotations

import copy
import importlib
import json
from pathlib import Path
import sys

import pytest


PROJECT = Path(__file__).resolve().parents[1]
BUNDLE = Path(
    "/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v33_plan_quarantine"
)
CAMPAIGN = PROJECT / "history/xp_final_algorithm_fresh10_v38_20260803"
MANIFEST = CAMPAIGN / "batches/cont010/diagnostic_bundle/manifest.json"
SELECTION = CAMPAIGN / "batches/cont010/diagnostic_bundle/selection.json"

for path in (PROJECT, PROJECT / "tools", BUNDLE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from tools import recover_xp_v48_cont010_axis_probe_zero_call as recovery  # noqa: E402
from tools import xp_v38_phase_state_patch as phase_state  # noqa: E402
from tools import xp_v46_safe_novel_pool_selector_patch as v46  # noqa: E402
from tools import xp_v48_axis_boundary_trace_probe_patch as v48  # noqa: E402


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def axis_candidates() -> list[dict]:
    return [
        item for item in load(SELECTION)["top5"]
        if item["safe_frontier_generation"]["contract"] == v48.AXIS_CONTRACT
    ]


def set_path(value: dict, path: str, replacement: object) -> None:
    target = value
    keys = path.split(".")
    for key in keys[:-1]:
        target = target[key]
    target[keys[-1]] = replacement


def test_real_axis_candidates_cover_both_sealed_source_eta_semantics() -> None:
    candidates = axis_candidates()
    assert [item["rank"] for item in candidates] == [1, 5]
    assert [item["eta_semantics"] for item in candidates] == [
        "adaptive_plan_change_gradient_direction_multiplier",
        "incumbent_to_corner_ray_fraction",
    ]
    assert all(v48.axis_boundary_trace_safe(item) for item in candidates)


@pytest.mark.parametrize("candidate_index", [0, 1])
def test_each_real_eta_semantics_fails_closed_when_tampered(candidate_index: int) -> None:
    candidate = copy.deepcopy(axis_candidates()[candidate_index])
    candidate["eta_semantics"] = "unsealed_or_unknown_eta_semantics"
    assert v48.axis_boundary_trace_safe(candidate) is False


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        ("safe_frontier_generation.contract", "some_other_axis_contract"),
        ("safe_frontier_generation.fresh_explain", False),
        ("safe_frontier_generation.formal_range_eligible", False),
        ("safe_frontier_generation.respects_formal_range", False),
        ("safe_frontier_generation.strict_formal_range_interior", False),
        ("safe_frontier_generation.vertices_included", False),
        ("safe_frontier_generation.trusted_without_real_evidence", True),
        (
            "safe_frontier_generation.exploration_anchor_updates_accepted_incumbent_range_or_gradient",
            True,
        ),
        ("safe_frontier_generation.step_limit_semantics", "global_total_step"),
        ("safe_frontier_generation.maximum_relative_parameter_step", 0.81),
        ("safe_frontier_generation.observed_maximum_relative_parameter_step", 0.81),
        ("safe_frontier_generation.sealed_source_eta", 0.0),
        ("candidate_origin", "manual_axis_probe"),
        ("selection_role", "trusted_calibrated_rank"),
        ("formal_range_eligible", False),
        ("boundary_corner", True),
        ("near_corner_excluded_from_noncorner_gate", True),
        ("used_for_gradient", True),
        ("used_for_inner_incumbent_update", True),
        ("eta", 0.1),
        ("three_level_fingerprint.valid", False),
        ("three_level_fingerprint.parameter_fallback_used", True),
        ("three_level_fingerprint.validation_errors", ["tampered"]),
        ("three_level_fingerprint.resource_formula_audit.cpu.absolute_error", 1.0),
        ("utilization.cpu", float("nan")),
    ],
)
def test_axis_contract_field_tamper_fails_closed(path: str, replacement: object) -> None:
    candidate = copy.deepcopy(axis_candidates()[0])
    set_path(candidate, path, replacement)
    assert v48.axis_boundary_trace_safe(candidate) is False


def test_axis_delta_must_be_single_axis_and_match_sealed_anchor() -> None:
    candidate = copy.deepcopy(axis_candidates()[0])
    candidate["delta_param"]["random_page_cost"]["relative"] = 0.1
    assert v48.axis_boundary_trace_safe(candidate) is False

    candidate = copy.deepcopy(axis_candidates()[1])
    candidate["initial_params"]["seq_page_cost"] += 0.01
    assert v48.axis_boundary_trace_safe(candidate) is False


def test_v46_rejects_axis_before_v48_and_accepts_only_after_strict_patch() -> None:
    module = importlib.reload(v46)
    top5 = load(SELECTION)["top5"]
    assert [module._fresh_safe_probe(item) for item in top5] == [
        False, True, True, True, False
    ]
    v48.install_v46_probe_acceptance(module)
    assert all(module._fresh_safe_probe(item) for item in top5)


def test_real_probe5_seals_resource_safe_gradient_neutral_diagnostic_gate() -> None:
    module = importlib.reload(v46)
    bundle_module = importlib.reload(importlib.import_module("xp_round10_diagnostic_bundle"))
    manifest = load(MANIFEST)
    source = manifest["source"]
    sealed, checks = v48.seal_ready_probe5(
        module,
        bundle_module,
        manifest,
        load(SELECTION),
        source["round9_excluded_fingerprints"],
    )
    assert all(checks.values())
    assert sealed["batch_mode"] == "diagnostic_validation"
    assert sealed["replay_authorized"] is True
    assert [item["selection_role"] for item in sealed["top5"]] == [
        "maximin_uncertain_probe"
    ] * 5
    assert all(item["used_for_gradient"] is False for item in sealed["top5"])
    assert all(
        item["used_for_inner_incumbent_update"] is False
        for item in sealed["top5"]
    )
    gate_module = importlib.reload(phase_state)
    module.install_phase_gate(gate_module)
    gate = gate_module._selection_gate(
        sealed, source["round9_excluded_fingerprints"]
    )
    assert gate["passed"] is True
    assert gate["selection_stage"] == "safe_pool_discovery_probe5"


def test_real_zero_call_recovery_preserves_formal_snapshot() -> None:
    before, selection, checks = recovery.validate_before()
    after = recovery.recovered_state(before, selection, checks)
    assert recovery.formal_snapshot(after) == recovery.formal_snapshot(before)
    assert after["accepted_count"] == 0
    assert after["next_batch_index"] == 10
    assert after["status"] == "diagnostic_ready_preworkload"
    assert after["active_phase"]["physical_workload_calls"] == 0
    assert after["active_phase"]["maximum_physical_calls"] == 5
    assert after["v48_zero_call_recovery"]["candidate_regeneration"] is False
    assert after["v48_zero_call_recovery"]["manual_candidate_injection"] is False
    assert after["v48_zero_call_recovery"]["resource_gate_relaxed"] is False
    assert after["v48_zero_call_recovery"]["formal_state_mutation"] is False


def test_recovery_outputs_are_deterministic_and_preworkload_only() -> None:
    selection_sha, state_sha = recovery.expected_after_sha256()
    assert selection_sha == "fb2e4e728e2c839d730c038aeca184c11374000a0017561a6cb07f93ee78e63e"
    assert state_sha == "a467fc7420e98ae106d9754e47ce9d9583c0f9b23b773fed34b9c37d55d38b01"
    assert recovery.BATCH_SELECTION.exists() is False
    assert (recovery.CONT010 / "replay_summary.json").exists() is False
    assert (recovery.CONT010 / "serial_replay").exists() is False


def test_recovery_artifact_sha_tamper_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(recovery.EXPECTED, recovery.MANIFEST, "0" * 64)
    with pytest.raises(RuntimeError, match="authority SHA mismatch"):
        recovery.validate_before()


def test_controller_installs_v48_before_v46_builder_wrapper() -> None:
    source = (
        PROJECT / "tools/resume_xp_fresh0_fixed_contract_target10_v48.py"
    ).read_text(encoding="utf-8")
    assert source.index("v48.install_v46_probe_acceptance(v46)") < source.index(
        "v46.install_bundle(bundle_module)"
    )
    assert source.index("v46.install_phase_gate(phase_state)") < source.index(
        "v47.install_phase_gate(phase_state, pending_trust)"
    )

#!/usr/bin/env python3
"""Offline tests for the XP v46 minimal routing fix and trust lifecycle."""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
from pathlib import Path
import sys


PROJECT = Path("/home/anonymous/program/resource_adaptive")
BUNDLE = Path("/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v33_plan_quarantine")
V45_OUTPUT = Path("/tmp/xp_v45_main_validation_20260803/campaign/batches/cont008")

for path in (PROJECT, PROJECT / "tools", BUNDLE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import xp_v38_phase_state_patch as phase_state  # noqa: E402
import xp_v39_exact_point_pending_trust_patch as pending_trust  # noqa: E402
import xp_v44_pending_post_replay_patch as post_replay  # noqa: E402
import xp_v46_safe_novel_pool_selector_patch as v46  # noqa: E402


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_manifest() -> dict:
    return json.loads(
        (V45_OUTPUT / "diagnostic_bundle/manifest.json").read_text(encoding="utf-8")
    )


def _build_v46(output_dir: Path) -> dict:
    source_manifest = _source_manifest()
    frontier = source_manifest["diagnostic_discovery"]["safe_frontier_contract"]
    bundle_module = importlib.reload(importlib.import_module("xp_round10_diagnostic_bundle"))
    v46.install_base_builder_fix(bundle_module)
    v46.install_bundle(bundle_module)
    return bundle_module.build_bundle(
        selection_path=Path(source_manifest["source"]["selection"]),
        replay_path=Path(source_manifest["source"]["replay"]),
        candidate_path=Path(source_manifest["source"]["candidate_pool"]),
        accepted_incumbent_path=Path(source_manifest["source"]["accepted_incumbent"]),
        output_dir=output_dir,
        extra_excluded_ids=set(
            source_manifest["source"]["round9_excluded_fingerprints"]
        ),
        risk_utilization_upper={},
        risk_audit=frontier["stability_risk_audit"],
    )


def _clean_replay(top5: list[dict], *, scores: list[float]) -> dict:
    return {
        "batch_mode": "diagnostic_validation",
        "physical_workload_invocation_count": 5,
        "successful_real_replay_count": 5,
        "technical_failure_count": 0,
        "successful": [
            {
                "fingerprint_id": item["three_level_fingerprint"]["id"],
                "actual_resource": {
                    "cpu": 0.7 + index * 0.01,
                    "io": 0.6 + index * 0.01,
                    "memory": 0.5 + index * 0.01,
                },
                "actual_score": scores[index],
                "technical_failure": False,
                "baseline_restore_verified": True,
            }
            for index, item in enumerate(top5)
        ],
        "baseline_restore_verified": True,
        "accepted_update_applied": False,
        "gradient_update_applied": False,
        "incumbent_updated": False,
        "range_updated": False,
        "state_mutations_applied_by_this_bundle": False,
        "rejected": [],
    }


def test_v46_two_branch_fix_uses_existing_safe_maximin_pool(tmp_path: Path) -> None:
    fixed = _build_v46(tmp_path / "diagnostic_bundle")
    source_manifest = _source_manifest()
    selection = fixed["selected_selection"]
    top5 = selection["top5"]
    audit = selection["v46_safe_pool_routing"]
    identifiers = [item["three_level_fingerprint"]["id"] for item in top5]

    assert fixed["status"] == "ready"
    assert fixed["input_gaps"] == []
    assert len(top5) == 5
    assert len(set(identifiers)) == 5
    assert [item["selection_role"] for item in top5] == [
        "maximin_uncertain_probe"
    ] * 5
    assert all(item["boundary_corner"] is False for item in top5)
    assert all(item["used_for_gradient"] is False for item in top5)
    assert all(item["used_for_inner_incumbent_update"] is False for item in top5)
    assert audit["source_safe_probe_count"] == 13
    assert audit["source_safe_transition_count"] == 0
    assert audit["diagnostic_only"] is True
    assert audit["first_probe_replay_cannot_be_formal"] is True
    assert audit["pending_trust_requires_clean_exact_point_repeat"] is True
    assert audit["resource_envelope_relaxed"] is False
    assert audit["parameter_step_limit_relaxed"] is False
    assert audit["manual_candidate_injection"] is False
    assert audit["formal_state_mutation"] is False
    assert audit["new_workload_calls"] == 0

    gate_module = importlib.reload(phase_state)
    v46.install_phase_gate(gate_module)
    gate = gate_module._selection_gate(
        selection, source_manifest["source"]["round9_excluded_fingerprints"]
    )
    assert gate["passed"] is True
    assert gate["selection_stage"] == "safe_pool_discovery_probe5"

    tampered = copy.deepcopy(selection)
    tampered["batch_mode"] = "formal"
    formal_gate = gate_module._selection_gate(tampered, ())
    assert formal_gate["passed"] is False
    assert formal_gate["checks"]["diagnostic_only_not_formal"] is False


def test_v46_clean_probe5_requires_pending_repeat_before_formal(tmp_path: Path) -> None:
    fixed = _build_v46(tmp_path / "first_bundle")
    source_selection = fixed["selected_selection"]
    source_top5 = source_selection["top5"]
    source_selection_path = tmp_path / "first_selection.json"
    source_replay_path = tmp_path / "first_replay.json"
    source_selection_path.write_text(
        json.dumps(source_selection, sort_keys=True) + "\n", encoding="utf-8"
    )
    first_replay = _clean_replay(source_top5, scores=[5.0, 4.0, 3.0, 2.0, 1.0])
    source_replay_path.write_text(
        json.dumps(first_replay, sort_keys=True) + "\n", encoding="utf-8"
    )
    first_replay_sha = _sha256(source_replay_path)
    by_source = {
        item["fingerprint_id"]: item for item in first_replay["successful"]
    }

    repeat = copy.deepcopy(source_selection)
    repeat["calibration_source"] = {
        "contract": "clean_probe5_exact_plan_first_observation_repeat_v1",
        "selection_path": str(source_selection_path),
        "replay_path": str(source_replay_path),
    }
    for index, candidate in enumerate(repeat["top5"]):
        source_id = source_top5[index]["three_level_fingerprint"]["id"]
        repeat_id = "three-level-sha256:" + hashlib.sha256(
            f"v46-repeat-{index}".encode("utf-8")
        ).hexdigest()
        candidate["three_level_fingerprint"]["id"] = repeat_id
        candidate["selection_role"] = (
            "trusted_calibrated_rank" if index < 3 else "maximin_uncertain_probe"
        )
        candidate["exact_plan_regime_calibration"] = {
            "source_fingerprint_id": source_id
        }
        observation = by_source[source_id]
        candidate["exact_plan_regime_observation_history"] = [
            {
                "observation_id": f"first-{index}",
                "source_replay": str(source_replay_path),
                "source_replay_sha256": first_replay_sha,
                "actual_resource": observation["actual_resource"],
                "actual_score": observation["actual_score"],
            }
        ]

    pending = pending_trust.sanitize_pending_selection(repeat)
    pending_audit = pending["v39_pending_trust"]
    assert pending["batch_mode"] == "diagnostic_validation"
    assert pending_audit["pending_trusted_count"] == 3
    assert pending_audit["formal_trusted_evidence_count"] == 0
    assert pending_audit["second_diagnostic_required"] is True
    assert pending_audit["formal_admission_allowed"] is False
    assert sum(item["pending_trusted"] is True for item in pending["top5"]) == 3
    assert all(
        item["formal_trusted_evidence_eligible"] is False
        for item in pending["top5"]
    )
    pending_gate = pending_trust.pending_selection_gate(
        importlib.reload(phase_state)._selection_gate,
        pending,
        [item["three_level_fingerprint"]["id"] for item in source_top5],
    )
    assert pending_gate["passed"] is True
    assert pending_gate["maximum_physical_calls"] == 5
    assert pending_gate["formal_trusted_evidence_count"] == 0

    second_replay_path = tmp_path / "second_replay.json"
    second_replay = _clean_replay(
        pending["top5"], scores=[5.1, 4.1, 3.1, 2.1, 1.1]
    )
    second_replay_path.write_text(
        json.dumps(second_replay, sort_keys=True) + "\n", encoding="utf-8"
    )
    promoted = pending_trust.promote_after_second_clean(pending, second_replay_path)
    assert promoted["v39_pending_trust"]["formal_trusted_evidence_count"] == 3
    assert promoted["v39_pending_trust"]["second_diagnostic_required"] is False
    assert sum(
        item["formal_trusted_evidence_eligible"] is True
        for item in promoted["top5"]
    ) == 3

    promoted["batch_mode"] = "formal"
    promoted["replay_authorized"] = True
    promoted["formal_admission"] = {
        "diagnostic_entry_recheck": {"passed": False}
    }
    formal_gate_module = importlib.reload(phase_state)
    post_replay.install_phase_gate(formal_gate_module)
    blocked = formal_gate_module._selection_gate(promoted, ())
    assert blocked["passed"] is False
    assert blocked["checks"]["fixed_entry_gate_passed"] is False


def test_v46_source_sha_and_routing_fragments_are_sealed() -> None:
    bundle_module = importlib.reload(importlib.import_module("xp_round10_diagnostic_bundle"))
    assert _sha256(Path(bundle_module.__file__)) == v46.EXPECTED_BUNDLE_SHA256
    source = __import__("inspect").getsource(bundle_module._assemble_bundle)
    assert source.count(v46.OLD_TRANSITION_BRANCH) == 1
    assert source.count(v46.OLD_BASELINE_BRANCH) == 1


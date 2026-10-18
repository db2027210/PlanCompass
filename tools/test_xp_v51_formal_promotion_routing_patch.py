#!/usr/bin/env python3
"""Focused live-fixture tests for XP v51's zero-call routing repair."""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from tools import xp_v37_robust_state_patch as robust
from tools import xp_v39_exact_point_pending_trust_patch as pending
from tools import xp_v51_formal_promotion_routing_patch as patch


CAMPAIGN = Path(
    "/home/anonymous/program/resource_adaptive/"
    "history/xp_final_algorithm_fresh10_v38_20260803"
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _prepared_formal() -> tuple[dict, dict, Path]:
    selection_path = (
        CAMPAIGN / "batches/cont012/score_metadata_normalization/selection.json"
    )
    replay_path = CAMPAIGN / "batches/cont011/replay_summary.json"
    handoff_path = CAMPAIGN / "batches/cont012/preformal_robust_handoff.json"
    prior = _load(selection_path)
    replay = _load(replay_path)
    handoff = _load(handoff_path)
    bundle = Path("/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v33_plan_quarantine")
    if str(bundle) not in sys.path:
        sys.path.insert(0, str(bundle))
    adapter = importlib.import_module("xp_unattended_adapters")
    state = _load(CAMPAIGN / "state.json")
    formal = adapter._two_observation_formal_selection(
        prior,
        replay,
        handoff["capability"],
        float(state["incumbent_score"]),
    )
    formal["batch_mode"] = "formal"
    formal["promoted_from_diagnostic_validation"] = {
        "contract": patch.FORMAL_PROMOTION_CONTRACT,
        "selection_path": str(selection_path),
        "selection_sha256": hashlib.sha256(selection_path.read_bytes()).hexdigest(),
        "replay_path": str(replay_path),
        "replay_sha256": hashlib.sha256(replay_path.read_bytes()).hexdigest(),
    }
    formal["formal_admission"] = {
        "passed": True,
        "diagnostic_entry_recheck": {"passed": True},
        "formal_acceptance_feasibility_gate": {"passed": True},
    }
    formal["affinity"] = {
        "cpuset_cpus": "32-51,288-307",
        "excluded_cpus": "184-199,440-455",
    }
    formal["preformal_robust_handoff"] = {
        "contract": robust.HANDOFF_CONTRACT,
        "path": str(handoff_path),
        "sha256": hashlib.sha256(handoff_path.read_bytes()).hexdigest(),
        "formal_candidate_binding_sha256": handoff[
            "formal_candidate_binding_sha256"
        ],
        "observation_count_per_candidate": 2,
        "sealed_before_formal_workload": True,
    }
    assert robust._candidate_binding(formal) == handoff["formal_candidate_binding"]
    return formal, prior, replay_path


def test_live_formal_object_keeps_handoff_and_bypasses_pending_gate() -> None:
    formal, prior, replay_path = _prepared_formal()
    ids_before = [item["three_level_fingerprint"]["id"] for item in formal["top5"]]
    merged = patch.merge_exact_trust_into_formal(
        formal, prior, replay_path, pending, robust
    )
    assert [item["three_level_fingerprint"]["id"] for item in merged["top5"]] == ids_before
    assert merged["preformal_robust_handoff"] == formal["preformal_robust_handoff"]
    assert [item["formal_trusted_evidence_eligible"] for item in merged["top5"]] == [
        True,
        True,
        True,
        False,
        False,
    ]
    gate = patch.promoted_formal_gate(merged, (), robust)
    assert gate["passed"] is True
    assert all(gate["checks"].values())


def test_source_fingerprint_mismatch_fails_closed() -> None:
    formal, prior, replay_path = _prepared_formal()
    formal["top5"][0]["three_level_fingerprint"]["source_fingerprint_id"] = (
        "three-level-sha256:wrong"
    )
    with pytest.raises(RuntimeError, match="handoff binding mismatch|fingerprint lineage mismatch"):
        patch.merge_exact_trust_into_formal(
            formal, prior, replay_path, pending, robust
        )


def test_install_chain_bypasses_only_v39_lossy_return() -> None:
    formal, _prior, _replay_path = _prepared_formal()

    def robust_generate(*_args, **_kwargs):
        return copy.deepcopy(formal)

    def make_pending_wrapper(original_generate):
        def generate_selection(*args, **kwargs):
            _ = original_generate(*args, **kwargs)
            return {"lossy_v39_return": True}

        return generate_selection

    adapter = SimpleNamespace(generate_selection=make_pending_wrapper(robust_generate))
    patch.install_adapter(adapter, pending, robust)
    result = adapter.generate_selection()
    assert result.get("lossy_v39_return") is None
    assert result["v51_formal_promotion_routing"]["preformal_handoff_preserved"] is True
    assert result["preformal_robust_handoff"] == formal["preformal_robust_handoff"]

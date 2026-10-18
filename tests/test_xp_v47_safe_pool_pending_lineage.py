from __future__ import annotations

import copy
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import recover_xp_v47_cont009_pending_zero_call as recovery
from tools import xp_v38_phase_state_patch as phase_state
from tools import xp_v39_exact_point_pending_trust_patch as pending
from tools import xp_v42_automatic_transition_family_patch as v42
from tools import xp_v43_pending_lineage_gate_patch as v43
from tools import xp_v44_pending_post_replay_patch as v44
from tools import xp_v46_safe_novel_pool_selector_patch as v46
from tools import xp_v47_safe_pool_pending_lineage_patch as v47


PROJECT = Path(__file__).resolve().parents[1]
CAMPAIGN = PROJECT / "history/xp_final_algorithm_fresh10_v38_20260803"
EXACT = CAMPAIGN / "batches/cont009/exact_plan_repeat/selection.json"
STATE = CAMPAIGN / "state.json"


def load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_old_install_order_reproduces_exact_two_false_schema_checks() -> None:
    selection = pending.sanitize_pending_selection(load(EXACT))
    replayed = load(STATE)["replayed_fingerprints"]
    module = SimpleNamespace(_selection_gate=phase_state._selection_gate)
    pending.install_phase_gate(module)
    v42.install_phase_gate(module)
    v43.install_phase_gate(module, pending)
    v44.install_phase_gate(module)
    v46.install_phase_gate(module)
    old_gate = module._selection_gate(selection, replayed)
    failed = {name for name, value in old_gate["checks"].items() if not value}
    assert failed == {
        "role_stage_contract",
        "v42_transition_audit_retained_as_lineage_only",
    }


def test_v47_outer_gate_routes_safe_pool_pending_without_relaxation() -> None:
    selection = pending.sanitize_pending_selection(load(EXACT))
    replayed = load(STATE)["replayed_fingerprints"]
    module = SimpleNamespace(_selection_gate=lambda *_args: {"passed": False})
    v47.install_phase_gate(module, pending)
    gate = module._selection_gate(selection, replayed)
    assert gate["passed"] is True
    assert gate["contract"] == v47.CONTRACT
    assert all(gate["checks"].values())
    assert gate["checks"]["role_stage_contract"] is True
    assert gate["checks"]["v46_safe_pool_audit_retained_as_lineage_only"] is True
    assert gate["checks"]["v42_transition_lineage_not_required_for_v46_source"] is True
    assert gate["checks"]["all_repeat_fingerprints_not_previously_replayed"] is True
    assert gate["checks"]["all_noncorner"] is True


def test_pending_three_and_probes_remain_nonformal_state_neutral() -> None:
    selection = v47.seal_safe_pool_pending_selection(pending, load(EXACT))
    top5 = selection["top5"]
    assert [item["selection_role"] for item in top5] == v47.EXPECTED_ROLES
    assert [item["pending_trusted"] for item in top5] == [True, True, True, False, False]
    assert all(item["formal_trusted_evidence_eligible"] is False for item in top5)
    assert all(item["exact_plan_regime_validated"] is False for item in top5)
    assert all(item["used_for_gradient"] is False for item in top5)
    assert all(item["used_for_inner_incumbent_update"] is False for item in top5)
    assert selection["batch_mode"] == "diagnostic_validation"


def test_v46_lineage_tamper_fails_closed() -> None:
    selection = load(EXACT)
    tampered = copy.deepcopy(selection)
    tampered["v46_safe_pool_routing"]["selected_fingerprint_ids"][0] = "tampered"
    with pytest.raises(RuntimeError, match="safe-pool lineage failed"):
        v47.seal_safe_pool_pending_selection(pending, tampered)


def test_ambiguous_v42_and_v46_lineage_fails_closed() -> None:
    selection = load(EXACT)
    selection["v42_automatic_transition_family"] = {"contract": v42.CONTRACT}
    with pytest.raises(RuntimeError, match="unambiguous v46 pending lineage"):
        v47.seal_safe_pool_pending_selection(pending, selection)


def test_repeat_fingerprint_replay_is_still_forbidden() -> None:
    selection = v47.seal_safe_pool_pending_selection(pending, load(EXACT))
    replayed = list(load(STATE)["replayed_fingerprints"])
    replayed.append(selection["top5"][0]["three_level_fingerprint"]["id"])
    gate = v47.safe_pool_pending_gate(pending, selection, replayed)
    assert gate["passed"] is False
    assert gate["checks"]["all_repeat_fingerprints_not_previously_replayed"] is False


def test_real_zero_call_recovery_builds_only_cont009_ready_state() -> None:
    before, selection, gate = recovery.validate_before()
    recovered = recovery.recovered_state(before, selection, gate)
    assert recovery.formal_snapshot(recovered) == recovery.formal_snapshot(before)
    assert recovered["accepted_count"] == 0
    assert recovered["status"] == "diagnostic_ready_preworkload"
    assert recovered["next_batch_index"] == 9
    assert recovered["active_phase"]["batch_id"] == "cont009"
    assert recovered["active_phase"]["physical_workload_calls"] == 0
    audit = recovered["v47_zero_call_recovery"]
    assert audit["cont008_rerun"] is False
    assert audit["cont009_only"] is True
    assert audit["top3_gate_relaxed"] is False
    assert audit["resource_gate_relaxed"] is False
    assert audit["novel_fingerprint_gate_relaxed"] is False
    assert audit["manual_candidate_injection"] is False


def test_recovery_outputs_are_deterministic_and_no_batch_root_selection_exists() -> None:
    selection_sha, state_sha = recovery.expected_after_sha256()
    assert selection_sha == "3cdebf753dc073e41cc005b92979b6d04400dde3759a56f0561d9ca16454b735"
    assert state_sha == "bbd3c5f47c5559e8cbc5718bf724c49bd8641bfc8c0e71a1d6080ae3a37cb015"
    assert recovery.BATCH_SELECTION.exists() is False
    assert (recovery.CONT009 / "replay_summary.json").exists() is False
    assert (recovery.CONT009 / "serial_replay").exists() is False


def test_controller_installs_v47_after_v46() -> None:
    source = (PROJECT / "tools/resume_xp_fresh0_fixed_contract_target10_v47.py").read_text(
        encoding="utf-8"
    )
    assert source.index("v46.install_phase_gate(phase_state)") < source.index(
        "v47.install_phase_gate(phase_state, pending_trust)"
    )

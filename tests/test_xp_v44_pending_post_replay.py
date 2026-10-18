#!/usr/bin/env python3
"""Offline tests for XP v44 pending-stage post-replay liveness."""

from __future__ import annotations

import copy
import importlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace


PROJECT = Path("/home/anonymous/program/resource_adaptive")
TOOLS = PROJECT / "tools"
BUNDLE = Path("/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v33_plan_quarantine")
ROOT = PROJECT / "history/xp_final_algorithm_fresh10_v38_20260803"
for path in (TOOLS, BUNDLE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import xp_unattended_adapters as adapter
import xp_v37_robust_state_patch as robust
import xp_v38_phase_state_patch as phase
import xp_v39_exact_point_pending_trust_patch as pending
import xp_v42_automatic_transition_family_patch as v42
import xp_v43_pending_lineage_gate_patch as v43
import xp_v44_pending_post_replay_patch as v44


SELECTION = ROOT / "batches/cont007/selection.json"
REPLAY = ROOT / "batches/cont007/replay_summary.json"
STATE = ROOT / "state.json"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def actual_adapter() -> object:
    module = importlib.reload(adapter)
    robust.install(module)
    phase.install_adapter(module)
    pending.install_adapter(module)
    v44.install_adapter(module, pending)
    return module


def actual_base_adapter_without_v44() -> object:
    module = importlib.reload(adapter)
    robust.install(module)
    phase.install_adapter(module)
    pending.install_adapter(module)
    return module


def test_actual_clean5_fails_only_actual_best_top3() -> None:
    gate = actual_adapter()._diagnostic_entry_gate(SELECTION, REPLAY)
    assert gate["contract"] == v44.CONTRACT
    assert gate["passed"] is False
    assert gate["failed_checks"] == ["actual_best_in_predicted_top3"]
    assert gate["trusted_only_spearman"] > 0.0
    assert gate["actual_best_predicted_rank"] == 4
    assert gate["maximum_trusted_resource_factor_error"] < 10.0
    assert gate["checks"]["candidate_diagnostics_and_confidence_complete"] is True
    assert gate["checks"]["diagnostic_state_neutral"] is True
    assert gate["checks"]["probes_not_promoted_to_trusted"] is True


def test_gate_pass_promotes_only_after_all_fixed_checks(tmp_path: Path) -> None:
    replay = load(REPLAY)
    replay["successful"][3]["actual_score"] = -49.2115
    replay_path = tmp_path / "replay.json"
    replay_path.write_text(json.dumps(replay, sort_keys=True), encoding="utf-8")
    gate = actual_adapter()._diagnostic_entry_gate(SELECTION, replay_path)
    assert gate["passed"] is True
    assert gate["failed_checks"] == []
    assert gate["actual_best_predicted_rank"] == 1


def test_source_sha_or_candidate_diagnostics_tamper_fails(tmp_path: Path) -> None:
    for mutation in ("sha", "diagnostics"):
        selection = load(SELECTION)
        if mutation == "sha":
            selection["top5"][0]["v39_exact_point_binding"][
                "source_replay_sha256"
            ] = "0" * 64
        else:
            del selection["top5"][0]["confidence"]
        selection_path = tmp_path / f"selection_{mutation}.json"
        selection_path.write_text(json.dumps(selection, sort_keys=True), encoding="utf-8")
        gate = actual_adapter()._diagnostic_entry_gate(selection_path, REPLAY)
        assert gate["passed"] is False
        expected = (
            "first_observation_selection_and_replay_sha_bound"
            if mutation == "sha"
            else "candidate_diagnostics_and_confidence_complete"
        )
        assert expected in gate["failed_checks"]


def _novel_selection() -> dict:
    top5 = []
    for rank in range(1, 6):
        top5.append(
            {
                "rank": rank,
                "boundary_corner": False,
                "near_corner_excluded_from_noncorner_gate": False,
                "three_level_fingerprint": {"id": f"new-fingerprint-{rank}"},
            }
        )
    return {"batch_mode": "diagnostic_validation", "top5": top5}


def test_failed_gate_routes_to_existing_new_fingerprint_pipeline(tmp_path: Path) -> None:
    captured = {}

    class FakeAdapter:
        @staticmethod
        def _diagnostic_entry_gate(selection_path: Path, replay_path: Path) -> dict:
            real = actual_base_adapter_without_v44()
            return real._diagnostic_entry_gate(selection_path, replay_path)

        @staticmethod
        def generate_selection(paths, batch_id, state, batch_dir):
            captured["mode"] = state["last_batch"]["mode"]
            captured["formal_snapshot"] = v44.formal_snapshot_digest(state)
            return _novel_selection()

    v44.install_adapter(FakeAdapter, pending)
    state = load(STATE)
    state["replayed_fingerprints"] = []
    before = v44.formal_snapshot_digest(state)
    paths = SimpleNamespace(batch_root=ROOT / "batches")
    selected = FakeAdapter.generate_selection(paths, "cont008", state, tmp_path)
    assert captured["mode"] == "diagnostic_quality_failed_reexploration"
    assert captured["formal_snapshot"] == before
    assert v44.formal_snapshot_digest(state) == before
    assert selected["v44_pending_post_replay_reroute"][
        "legacy_probe5_repeat_reused"
    ] is False
    assert selected["v44_pending_post_replay_reroute"]["failed_checks"] == [
        "actual_best_in_predicted_top3"
    ]
    assert (tmp_path / "v44_pending_post_replay_reroute.json").is_file()


def test_passed_gate_does_not_take_reexploration_branch(tmp_path: Path) -> None:
    captured = {}
    source_dir = tmp_path / "batches/cont007"
    source_dir.mkdir(parents=True)
    source_selection = load(SELECTION)
    source_replay = load(REPLAY)
    source_replay["successful"][3]["actual_score"] = -49.2115
    (source_dir / "selection.json").write_text(
        json.dumps(source_selection, sort_keys=True), encoding="utf-8"
    )
    (source_dir / "replay_summary.json").write_text(
        json.dumps(source_replay, sort_keys=True), encoding="utf-8"
    )

    class FakeAdapter:
        @staticmethod
        def _diagnostic_entry_gate(selection_path: Path, replay_path: Path) -> dict:
            return actual_base_adapter_without_v44()._diagnostic_entry_gate(
                selection_path, replay_path
            )

        @staticmethod
        def generate_selection(paths, batch_id, state, batch_dir):
            captured["mode"] = state["last_batch"]["mode"]
            return {"batch_mode": "formal", "top5": []}

    v44.install_adapter(FakeAdapter, pending)
    state = load(STATE)
    paths = SimpleNamespace(batch_root=tmp_path / "batches")
    selected = FakeAdapter.generate_selection(paths, "cont008", state, tmp_path)
    assert captured["mode"] == "diagnostic_validation"
    assert selected["batch_mode"] == "formal"
    assert not (tmp_path / "v44_pending_post_replay_reroute.json").exists()


def test_promoted_formal_phase_gate_never_promotes_probes() -> None:
    promoted = pending.promote_after_second_clean(load(SELECTION), REPLAY)
    promoted["batch_mode"] = "formal"
    promoted["formal_admission"] = {
        "diagnostic_entry_recheck": {"passed": True}
    }
    module = importlib.reload(phase)
    pending.install_phase_gate(module)
    v42.install_phase_gate(module)
    v43.install_phase_gate(module, pending)
    v44.install_phase_gate(module)
    gate = module._selection_gate(promoted, [])
    assert gate["passed"] is True
    assert gate["checks"]["three_exact_points_promoted"] is True
    assert gate["checks"]["probes_not_promoted"] is True
    assert gate["pending_stage_gate_reapplied"] is False


def test_actual_state_remains_fresh_zero() -> None:
    state = load(STATE)
    assert state["accepted_count"] == 0
    assert state["gradient_state"]["update_count"] == 0
    assert state["active_phase"]["physical_workload_calls"] == 0


def main() -> int:
    tests = [
        test_actual_clean5_fails_only_actual_best_top3,
        test_gate_pass_promotes_only_after_all_fixed_checks,
        test_source_sha_or_candidate_diagnostics_tamper_fails,
        test_failed_gate_routes_to_existing_new_fingerprint_pipeline,
        test_passed_gate_does_not_take_reexploration_branch,
        test_promoted_formal_phase_gate_never_promotes_probes,
        test_actual_state_remains_fresh_zero,
    ]
    for test in tests:
        if "tmp_path" in test.__code__.co_varnames:
            import tempfile

            with tempfile.TemporaryDirectory(prefix="xp_v44_test_") as directory:
                test(Path(directory))
        else:
            test()
        print(f"PASS {test.__name__}", flush=True)
    print(f"PASS {len(tests)}/{len(tests)} XP v44 tests", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

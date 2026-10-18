#!/usr/bin/env python3
"""Offline XP v39 exact-point pending-trust tests."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace


PROJECT = Path("/home/anonymous/program/resource_adaptive")
if str(PROJECT / "tools") not in sys.path:
    sys.path.insert(0, str(PROJECT / "tools"))

import xp_v39_exact_point_pending_trust_patch as v39


ROOT = PROJECT / "history/xp_final_algorithm_fresh10_v38_20260803"
STATE = ROOT / "state.json"
PENDING_SOURCE = ROOT / "batches/cont004/exact_plan_repeat/selection.json"
FIRST_REPLAY = ROOT / "batches/cont003/replay_summary.json"


def pending_selection() -> dict:
    raw = json.loads(PENDING_SOURCE.read_text(encoding="utf-8"))
    return v39.sanitize_pending_selection(raw)


def simulated_second_replay(selection: dict) -> dict:
    replay = json.loads(FIRST_REPLAY.read_text(encoding="utf-8"))
    source_to_repeat = {
        item["v39_exact_point_binding"]["source_fingerprint_id"]: (
            item["v39_exact_point_binding"]["repeat_fingerprint_id"]
        )
        for item in selection["top5"]
    }
    for item in replay["successful"]:
        item["fingerprint_id"] = source_to_repeat[item["fingerprint_id"]]
    return replay


def write_json(path: Path, value: dict) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_actual_pending_roles_and_gate() -> None:
    before = STATE.read_bytes()
    selection = pending_selection()
    gate = v39.pending_selection_gate(lambda *_: {"passed": False}, selection, [])
    assert gate["passed"] is True
    assert gate["selection_stage"] == "exact_point_pending_trust_second_diagnostic"
    assert sum(item["pending_trusted"] is True for item in selection["top5"]) == 3
    assert all(
        item["formal_trusted_evidence_eligible"] is False
        for item in selection["top5"]
    )
    assert STATE.read_bytes() == before


def test_same_plan_different_params_is_rejected() -> None:
    raw = json.loads(PENDING_SOURCE.read_text(encoding="utf-8"))
    raw["top5"][0]["candidate_params"]["seq_page_cost"] += 0.01
    try:
        v39.sanitize_pending_selection(raw)
    except RuntimeError as exc:
        assert "same-plan/different-params" in str(exc)
    else:
        raise AssertionError("cross-parameter evidence was accepted")


def test_pending_trust_cannot_be_formal() -> None:
    selection = pending_selection()
    selection["batch_mode"] = "formal"
    try:
        v39.pending_selection_gate(lambda *_: {"passed": True}, selection, [])
    except RuntimeError:
        pass
    else:
        raise AssertionError("pending trusted points entered formal replay")


def test_second_clean_promotes_only_three_exact_points() -> None:
    selection = pending_selection()
    with tempfile.TemporaryDirectory(prefix="xp_v39_second_") as raw:
        replay_path = Path(raw) / "replay.json"
        write_json(replay_path, simulated_second_replay(selection))
        promoted = v39.promote_after_second_clean(selection, replay_path.resolve())
    trusted = promoted["top5"][:3]
    probes = promoted["top5"][3:]
    assert all(item["exact_plan_regime_validated"] is True for item in trusted)
    assert all(item["formal_trusted_evidence_eligible"] is True for item in trusted)
    assert all(item["pending_trusted"] is False for item in trusted)
    assert all(item["formal_trusted_evidence_eligible"] is False for item in probes)
    assert promoted["v39_exact_point_promotion"]["validated_trusted_count"] == 3


def test_technical_second_observation_fails_closed() -> None:
    selection = pending_selection()
    replay = simulated_second_replay(selection)
    replay["technical_failure_count"] = 1
    replay["successful_real_replay_count"] = 4
    replay["successful"][0]["technical_failure"] = True
    with tempfile.TemporaryDirectory(prefix="xp_v39_tech_") as raw:
        replay_path = Path(raw) / "replay.json"
        write_json(replay_path, replay)
        try:
            v39.promote_after_second_clean(selection, replay_path.resolve())
        except RuntimeError as exc:
            assert "not clean5" in str(exc)
        else:
            raise AssertionError("technical replay promoted trust")


def test_crash_windows_are_preworkload_and_state_neutral() -> None:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    before_digest = v39.formal_snapshot_digest(state)
    raw = json.loads(PENDING_SOURCE.read_text(encoding="utf-8"))
    raw["top5"][0]["exact_plan_regime_observation_history"][0][
        "source_replay_sha256"
    ] = "0" * 64
    calls = 0
    try:
        v39.sanitize_pending_selection(raw)
        calls += 1
    except RuntimeError:
        pass
    assert calls == 0
    after = json.loads(STATE.read_text(encoding="utf-8"))
    assert v39.formal_snapshot_digest(after) == before_digest
    assert not (ROOT / "batches/cont004/serial_replay").exists()


def test_plan_match_without_parameter_match_cannot_grant_trust() -> None:
    selection = pending_selection()
    trusted = copy.deepcopy(selection["top5"][0])
    replay = simulated_second_replay(selection)

    def base_calibration(*_args):
        return {"plan_regimes": {trusted["workload_plan_fingerprint"]: {}}}

    def base_candidates(raw, *_args):
        active = copy.deepcopy(raw)
        for item in active:
            item["exact_plan_regime_validated"] = True
        return active, [], []

    bundle = SimpleNamespace(
        build_calibration=base_calibration,
        _calibrated_candidates=base_candidates,
    )
    v39.install_bundle(bundle)
    calibration = bundle.build_calibration(selection, replay)
    different = copy.deepcopy(trusted)
    different["candidate_params"]["random_page_cost"] += 0.1
    active, _, _ = bundle._calibrated_candidates([different], calibration, set())
    assert active[0]["exact_plan_regime_validated"] is False
    assert active[0]["plan_regime_calibration"]["trusted_eligible"] is False


def test_adapter_sanitizes_diagnostic_and_promotes_formal() -> None:
    raw_pending = json.loads(PENDING_SOURCE.read_text(encoding="utf-8"))
    adapter = SimpleNamespace(
        _diagnostic_entry_gate=lambda *_args: {"checks": {}, "passed": True},
        generate_selection=lambda *_args, **_kwargs: copy.deepcopy(raw_pending),
    )
    v39.install_adapter(adapter)
    pending = adapter.generate_selection()
    assert pending["v39_pending_trust"]["formal_trusted_evidence_count"] == 0

    with tempfile.TemporaryDirectory(prefix="xp_v39_adapter_") as raw:
        directory = Path(raw)
        selection_path = directory / "selection.json"
        replay_path = directory / "replay.json"
        write_json(selection_path, pending)
        write_json(replay_path, simulated_second_replay(pending))
        formal = copy.deepcopy(pending)
        formal["batch_mode"] = "formal"
        formal["formal_admission"] = {"passed": True}
        formal["promoted_from_diagnostic_validation"] = {
            "selection_path": str(selection_path.resolve()),
            "replay_path": str(replay_path.resolve()),
        }
        adapter.generate_selection = lambda: formal
        v39.install_adapter(adapter)
        promoted = adapter.generate_selection()
    assert promoted["batch_mode"] == "formal"
    assert promoted["v39_exact_point_promotion"]["validated_trusted_count"] == 3
    assert all(
        item["formal_trusted_evidence_eligible"] is True
        for item in promoted["top5"][:3]
    )


def main() -> int:
    tests = [
        test_actual_pending_roles_and_gate,
        test_same_plan_different_params_is_rejected,
        test_pending_trust_cannot_be_formal,
        test_second_clean_promotes_only_three_exact_points,
        test_technical_second_observation_fails_closed,
        test_crash_windows_are_preworkload_and_state_neutral,
        test_plan_match_without_parameter_match_cannot_grant_trust,
        test_adapter_sanitizes_diagnostic_and_promotes_formal,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}", flush=True)
    print(f"PASS {len(tests)}/{len(tests)} XP v39 pending-trust tests", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

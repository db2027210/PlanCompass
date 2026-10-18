from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from tools import audit_tpcc_v44_postrun_gate as auditor
from tools import build_tpcc_v44_plan_regime_score_separation as base_builder
from tools import run_tpcc_v48_autonomous_clean5_successor_loop as controller


PROJECT = Path(__file__).resolve().parents[1]
V47 = PROJECT / "history/tpcc_v47_trusted3_serial_compat_package_20260804"
REPLAY = V47 / "top5_real_replay_round17_v47_trusted3_diagnostic_02"
SELECTION = V47 / "top5.json"
LEDGER = V47 / "strict_top5_real_replay_round17_v47_ledger_v1.json"
EVENTS = REPLAY / "events.jsonl"
SUMMARY = REPLAY / "summary.json"
FORMAL = PROJECT / "history/tpcc_final_algorithm_fresh0_v36_20260803/formal_state.json"
RECOVERY = PROJECT / "history/tpcc_v48_orphan_recovery_package_20260804"
SUPERSESSION = PROJECT / "history/tpcc_v48_late_completion_supersession_package_20260804"
SUCCESSOR = PROJECT / "history/tpcc_v48_clean5_failed_gate_successor_package_20260804"
SUCCESSOR_SELECTION = SUCCESSOR / "top5.json"
RUNTIME = PROJECT / "history/tpcc_v40_clean5_exact_recalibration_package_20260803/runtime"
FORMAL_SHA = "62e3fd40c93c163727367afd20b151c291c3646d63fbe0c5c5e2d32c3160aeb0"


def test_pre_recovery_authority_is_preserved() -> None:
    prepared = json.loads((RECOVERY / "prepared.json").read_text(encoding="utf-8"))
    assert base_builder.v6._sha256(RECOVERY / "ledger.pre_recovery.json") == (
        prepared["source_authority"]["ledger_pre_recovery"]["sha256"]
    )
    assert base_builder.v6._sha256(RECOVERY / "events.pre_recovery.jsonl") == (
        prepared["source_authority"]["events_pre_recovery"]["sha256"]
    )
    assert prepared["workload_calls_added"] == 0


def test_late_completion_supersession_yields_effective_clean5_failed_gate() -> None:
    completed = json.loads((SUPERSESSION / "completed.json").read_text(encoding="utf-8"))
    assert completed["physical_calls"] == 5
    assert completed["valid_replays"] == 5
    assert completed["effective_technical_failures"] == 0
    assert completed["workload_calls_added"] == 0
    gate = auditor.audit_v44(SELECTION, LEDGER, EVENTS, SUMMARY, FORMAL, FORMAL_SHA)
    accounting = gate["accounting"]
    assert accounting["physical_calls"] == 5
    assert accounting["valid_replays"] == 5
    assert accounting["technical_failures"] == 0
    assert accounting["raw_technical_failures"] == 1
    assert accounting["superseded_technical_bindings"] == [(5, 1)]
    assert gate["state_isolation"]["passed"] is True
    assert gate["passed"] is False
    assert {
        name
        for name, passed in gate["quality_gate"]["checks"].items()
        if passed is not True
    } == {"actual_best_in_predicted_top3"}
    assert gate["quality_gate"]["trusted_spearman"] > 0
    assert gate["quality_gate"]["actual_best_predicted_rank"] == 5


def test_successor_is_automatic_fresh_3t2p_with_v47_evidence() -> None:
    source = json.loads(SELECTION.read_text(encoding="utf-8"))
    successor = json.loads(SUCCESSOR_SELECTION.read_text(encoding="utf-8"))
    top5 = successor["top5"]
    assert [row["selection_role"] for row in top5] == [
        "trusted", "trusted", "trusted", "uncertain_probe", "uncertain_probe"
    ]
    old_ids = {row["three_level_fingerprint"]["id"] for row in source["top5"]}
    new_ids = {row["three_level_fingerprint"]["id"] for row in top5}
    assert len(new_ids) == 5
    assert not old_ids.intersection(new_ids)
    assert successor["state_mutation_contract"] == {
        "accepted_update": False,
        "gradient_update": False,
        "incumbent_update": False,
        "range_update": False,
    }
    assert [row["trusted"] for row in top5] == [True, True, True, False, False]
    assert all(row["incumbent_update_eligible"] is False for row in top5)
    assert all(row["used_for_gradient"] is False for row in top5)
    descriptors = successor["source_batches"]
    assert any(
        row["selection_path"] == str(SELECTION)
        and row["ledger_path"] == str(LEDGER)
        for row in descriptors
    )
    assert all(
        (row["plan_regime_calibration"]["nonstationarity_model"])["contract"]
        == "tpcc_v43_distance_weighted_exact_plan_nonstationarity_v1"
        for row in top5[:3]
    )
    assert all(
        row["plan_regime_calibration"]["trusted_eligible"] is False
        for row in top5[3:]
    )


def test_full_v48_controller_verify_only_adds_no_replay() -> None:
    before = sorted(path.relative_to(SUCCESSOR) for path in SUCCESSOR.rglob("*"))
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = os.pathsep.join(
        (str(RUNTIME), str(PROJECT / "tools"), str(PROJECT))
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-u",
            str(Path(controller.__file__).resolve()),
            "--selection",
            str(SUCCESSOR_SELECTION),
            "--campaign-root",
            str(SUCCESSOR),
            "--expected-formal-state-sha256",
            FORMAL_SHA,
            "--verify-only",
        ],
        cwd=PROJECT,
        env=env,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout
    assert "tpcc_v48_autonomous_clean5_successor_preflight_passed" in completed.stdout
    assert '"workload_calls_added": 0' in completed.stdout
    after = sorted(path.relative_to(SUCCESSOR) for path in SUCCESSOR.rglob("*"))
    assert after == before


def test_apply_and_launch_are_separate_zero_call_boundaries() -> None:
    apply_result = subprocess.run(
        [str(PROJECT / "tools/apply_tpcc_v48_clean5_successor.sh")],
        cwd=PROJECT,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=30,
    )
    assert apply_result.returncode == 0, apply_result.stdout
    assert '"launch_started":false' in apply_result.stdout
    assert '"workload_calls_added":0' in apply_result.stdout
    launch_result = subprocess.run(
        [
            str(PROJECT / "tools/launch_tpcc_v48_clean5_successor.sh"),
            "verify-only",
        ],
        cwd=PROJECT,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=30,
    )
    assert launch_result.returncode == 0, launch_result.stdout
    assert "tpcc_v48_autonomous_clean5_successor_preflight_passed" in (
        launch_result.stdout
    )

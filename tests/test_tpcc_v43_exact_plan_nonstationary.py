from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess

import audit_tpcc_v43_postrun_gate as gate_module
import build_tpcc_v43_exact_plan_nonstationary_recalibration as builder
import prepare_tpcc_v43_trusted3_sha_bound_handoff as handoff
import run_tpcc_formal_target10_trusted3_v43_sha_bound as formal_runner
import run_tpcc_v43_exact_plan_nonstationary_strict_serial as runner
import run_tpcc_v43_trusted3_formal_substrate as formal_substrate


PROJECT = Path("/home/anonymous/program/resource_adaptive")
V42 = PROJECT / "history/tpcc_v42_exact_plan_io_recalibration_package_20260803"
PACKAGE = PROJECT / "history/tpcc_v43_exact_plan_nonstationary_package_20260803"
FORMAL = PROJECT / "history/tpcc_final_algorithm_fresh0_v36_20260803/formal_state.json"
SELECTION = V42 / "top5.json"
LEDGER = V42 / "strict_top5_real_replay_round16_ledger_v1.json"
EVENTS = V42 / "top5_real_replay_round16_v42_exact_plan_io_diagnostic_01/events.jsonl"
SUMMARY = V42 / "top5_real_replay_round16_v42_exact_plan_io_diagnostic_01/summary.json"
GATE = PACKAGE / "v42_postrun_gate.json"
TOP5 = PACKAGE / "top5.json"
FORMAL_SHA = "62e3fd40c93c163727367afd20b151c291c3646d63fbe0c5c5e2d32c3160aeb0"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _build() -> dict:
    return builder.build(
        selection_path=SELECTION,
        selection_sha256=builder.v6._sha256(SELECTION),
        ledger_path=LEDGER,
        ledger_sha256=builder.v6._sha256(LEDGER),
        events_path=EVENTS,
        events_sha256=builder.v6._sha256(EVENTS),
        summary_path=SUMMARY,
        summary_sha256=builder.v6._sha256(SUMMARY),
        gate_path=GATE,
        gate_sha256=builder.v6._sha256(GATE),
        formal_state_path=FORMAL,
        formal_state_sha256=FORMAL_SHA,
    )


def test_v42_is_strictly_rejected_for_only_the_observed_prediction_failures() -> None:
    gate = gate_module.audit_v43(
        SELECTION, LEDGER, EVENTS, SUMMARY, FORMAL, FORMAL_SHA
    )
    failed = {
        name
        for name, passed in gate["quality_gate"]["checks"].items()
        if passed is not True
    }
    assert gate["passed"] is False
    assert failed == {
        "actual_best_in_predicted_top3",
        "trusted_score_mae_within_gate",
    }
    assert gate["quality_gate"]["actual_best_predicted_rank"] == 4
    assert gate["quality_gate"]["trusted_spearman"] > 0
    assert gate["quality_gate"]["trusted_score_mae"] > 0.05
    assert gate["accounting"]["physical_calls"] == 5
    assert gate["accounting"]["valid_replays"] == 5
    assert gate["state_isolation"]["passed"] is True


def test_v43_build_is_deterministic_and_matches_seal() -> None:
    assert _build() == _load(TOP5)


def test_v43_has_five_new_noncorner_3t2p_and_excludes_current_probes() -> None:
    selection = _load(TOP5)
    top5 = selection["top5"]
    assert [item["selection_role"] for item in top5] == [
        "trusted",
        "trusted",
        "trusted",
        "uncertain_probe",
        "uncertain_probe",
    ]
    identifiers = {item["three_level_fingerprint"]["id"] for item in top5}
    historical = set()
    for descriptor in selection["source_batches"]:
        source = _load(Path(descriptor["selection_path"]))
        historical.update(
            item["three_level_fingerprint"]["id"] for item in source["top5"]
        )
    assert len(identifiers) == 5
    assert not identifiers.intersection(historical)
    assert all(item["boundary_corner"] is False for item in top5)
    old_probe_keys = {
        item["exact_point_key"] for item in _load(SELECTION)["top5"][3:]
    }
    assert not old_probe_keys.intersection(
        item["exact_point_key"] for item in top5
    )


def test_probe_evidence_cannot_update_trusted_or_formal_state() -> None:
    selection = _load(TOP5)
    for rank, candidate in enumerate(selection["top5"], 1):
        calibration = candidate["plan_regime_calibration"]
        provenance = candidate["source_provenance"]
        assert calibration["probe_rows_used_for_trusted_calibration"] == 0
        assert calibration["probe_evidence_affects_formal_or_gradient"] is False
        assert provenance["v42_probe_formal_state_update_rows"] == 0
        assert provenance["v42_probe_gradient_update_rows"] == 0
        assert provenance["v42_probe_incumbent_update_rows"] == 0
        assert candidate["incumbent_update_eligible"] is False
        assert candidate["used_for_gradient"] is False
        assert candidate["used_for_inner_incumbent_update"] is False
        assert candidate["trusted_eligible_v43"] is (rank <= 3)


def test_runner_accepts_seal_and_rejects_probe_role_pollution() -> None:
    selection = _load(TOP5)
    runner._validate_v43(selection, selection["top5"])
    tampered = copy.deepcopy(selection)
    tampered["top5"][3]["used_for_gradient"] = True
    try:
        runner._validate_v43(tampered, tampered["top5"])
    except RuntimeError:
        pass
    else:
        raise AssertionError("v43 runner accepted probe gradient pollution")


def test_launcher_preflight_is_zero_call() -> None:
    completed = subprocess.run(
        [str(PACKAGE / "launch_diagnostic_only.sh"), "verify-only"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    payload = json.loads(completed.stdout)
    assert payload["workload_calls_added"] == 0
    assert payload["diagnostic_gate_automatic"] is True
    assert payload["gate_fail_starts_new5_automatic"] is True
    assert payload["gate_pass_starts_formal_target10_automatic"] is True
    assert payload["probe_formal_call_count"] == 0
    archive = _load(PACKAGE / "zero_call_failure_archive_01.json")
    assert archive["physical_workload_calls"] == 0
    assert archive["failed_replay_root_exists_at_archive"] is False
    assert archive["failed_ledger_exists_at_archive"] is False
    assert archive["recovery_reuses_failed_replay_root"] is False
    assert not Path(archive["failed_replay_root"]).exists()
    assert not Path(archive["recovery_replay_root"]).exists()


def test_trusted3_handoff_excludes_probes_and_formal_runners_compile() -> None:
    selection = _load(TOP5)
    observations = []
    for candidate in selection["top5"]:
        observations.append(
            {
                "rank": candidate["rank"],
                "workload_plan_fingerprint": candidate["workload_plan_fingerprint"],
                "three_level_fingerprint": copy.deepcopy(
                    candidate["three_level_fingerprint"]
                ),
                "actual_score": candidate["predicted_score"],
                "actual_resource": copy.deepcopy(candidate["predicted_resource"]),
            }
        )
    promoted = handoff._promote_trusted3(
        selection,
        {
            "real_workload_invocation_count": 5,
            "successful": observations,
            "technical_failures": [],
        },
        {
            "passed": True,
            "formal_start_authorized": True,
            "selection_sha256": builder.v6._sha256(TOP5),
        },
        TOP5,
        4.416709205648302,
    )
    assert [item["rank"] for item in promoted["top5"]] == [1, 2, 3]
    assert all(item["selection_role"] == "trusted" for item in promoted["top5"])
    contract = promoted["v43_formal_trusted3_contract"]
    assert contract["probe_source_ranks_excluded"] == [4, 5]
    assert contract["probe_formal_call_count"] == 0
    formal_substrate._validate_formal_trusted3(promoted, promoted["top5"])
    assert formal_substrate.load_runtime()["MAX_REAL_WORKLOAD_INVOCATIONS"] == 3
    assert formal_runner.load_runtime()["TARGET"] == 10

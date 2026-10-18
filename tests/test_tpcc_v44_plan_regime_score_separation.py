from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tools import audit_tpcc_v44_postrun_gate as auditor
from tools import build_tpcc_v43_exact_plan_nonstationary_recalibration as v43
from tools import build_tpcc_v44_plan_regime_score_separation as builder
from tools import run_tpcc_v44_autonomous_score_separation_loop as controller
from tools import run_tpcc_v44_plan_regime_score_separation_strict_serial as runner


PROJECT = Path(__file__).resolve().parents[1]
PACKAGE = PROJECT / "history/tpcc_v43_exact_plan_nonstationary_package_20260803"
SOURCE = PACKAGE / "automatic_retry_nonstationary_01/top5.json"
LEDGER = PACKAGE / "automatic_retry_nonstationary_01/strict_top5_real_replay_round16_ledger_v1.json"
EVENTS = PACKAGE / "automatic_retry_nonstationary_01/top5_real_replay_round16_v43_nonstationary_diagnostic_01/events.jsonl"
SUMMARY = PACKAGE / "automatic_retry_nonstationary_01/top5_real_replay_round16_v43_nonstationary_diagnostic_01/summary.json"
GATE = PACKAGE / "postrun_gate_diagnostic_02/postrun_gate.json"
FORMAL = PROJECT / "history/tpcc_final_algorithm_fresh0_v36_20260803/formal_state.json"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build() -> dict:
    return builder.build(
        selection_path=SOURCE,
        selection_sha256=builder.v6._sha256(SOURCE),
        ledger_path=LEDGER,
        ledger_sha256=builder.v6._sha256(LEDGER),
        events_path=EVENTS,
        events_sha256=builder.v6._sha256(EVENTS),
        summary_path=SUMMARY,
        summary_sha256=builder.v6._sha256(SUMMARY),
        gate_path=GATE,
        gate_sha256=builder.v6._sha256(GATE),
        formal_state_path=FORMAL,
        formal_state_sha256=builder.v6._sha256(FORMAL),
    )


@pytest.fixture(scope="module")
def acquisition() -> dict:
    return build()


def test_v43_reproduces_exact_trusted_shortfall() -> None:
    with pytest.raises(RuntimeError, match="has only 1 trusted candidates"):
        v43.build(
            selection_path=SOURCE,
            selection_sha256=builder.v6._sha256(SOURCE),
            ledger_path=LEDGER,
            ledger_sha256=builder.v6._sha256(LEDGER),
            events_path=EVENTS,
            events_sha256=builder.v6._sha256(EVENTS),
            summary_path=SUMMARY,
            summary_sha256=builder.v6._sha256(SUMMARY),
            gate_path=GATE,
            gate_sha256=builder.v6._sha256(GATE),
            formal_state_path=FORMAL,
            formal_state_sha256=builder.v6._sha256(FORMAL),
        )


def test_v44_shortfall_becomes_automatic_state_neutral_probe5(
    acquisition: dict,
) -> None:
    contract = acquisition["v44_build_contract"]
    top5 = acquisition["top5"]
    assert contract["mode"] == "score_separation_acquisition"
    assert contract["trusted_candidate_count_before_acquisition"] == 2
    assert contract["selection_algorithm"] == (
        "score_extrema_then_score_parameter_maximin_v1"
    )
    assert contract["manual_candidate_selection"] is False
    assert contract["threshold_changed"] is False
    assert contract["maximum_physical_calls"] == 5
    assert contract["physical_workload_calls_added"] == 0
    assert acquisition["maximum_real_workload_invocations"] == 5
    assert acquisition["state_mutation_contract"] == {
        "accepted_update": False,
        "gradient_update": False,
        "incumbent_update": False,
        "range_update": False,
    }
    assert [item["selection_role"] for item in top5] == [
        "uncertain_probe"
    ] * 5
    assert len({item["exact_point_key"] for item in top5}) == 5
    assert len({item["three_level_fingerprint"]["id"] for item in top5}) == 5
    assert len({item["workload_plan_fingerprint"] for item in top5}) == 1
    assert all(item["boundary_corner"] is False for item in top5)
    assert all(item["used_for_gradient"] is False for item in top5)
    assert all(item["used_for_inner_incumbent_update"] is False for item in top5)


def test_uncertainty_witness_is_joint_bounded_and_not_trust(
    acquisition: dict,
) -> None:
    for candidate in acquisition["top5"]:
        witness = candidate["acquisition_uncertainty_witness"]
        assert witness["contract"] == "tpcc_v44_joint_resource_lcb_witness_v1"
        assert 0.8 <= witness["joint_resource_scale"] <= 1.2
        assert witness["deterministic_interval_side"] in {-1, 1}
        assert witness["io_only_scalar_calibration"] is False
        assert witness["used_to_establish_trust"] is False
        assert candidate["trusted"] is False
        assert candidate["trusted_eligible_v43"] is False
        assert candidate["trusted_eligible_v44"] is False
        assert candidate["plan_regime_calibration"]["trusted_eligible"] is False


def test_builder_is_deterministic(acquisition: dict) -> None:
    assert build() == acquisition


def test_runner_accepts_acquisition_and_rejects_role_pollution(
    acquisition: dict,
) -> None:
    runner._validate_v44(acquisition, acquisition["top5"])
    tampered = copy.deepcopy(acquisition)
    tampered["top5"][0]["selection_role"] = "trusted"
    with pytest.raises(RuntimeError, match="acquisition mismatch"):
        runner._validate_v44(tampered, tampered["top5"])


def test_runner_rejects_threshold_and_gradient_tamper(acquisition: dict) -> None:
    tampered = copy.deepcopy(acquisition)
    tampered["v44_build_contract"]["score_mae_max"] = 0.06
    with pytest.raises(RuntimeError, match="common diagnostic"):
        runner._validate_v44(tampered, tampered["top5"])
    tampered = copy.deepcopy(acquisition)
    tampered["top5"][3]["used_for_gradient"] = True
    with pytest.raises(RuntimeError, match="common diagnostic"):
        runner._validate_v44(tampered, tampered["top5"])


def test_clean_acquisition_audit_routes_to_rebuild_not_formal(
    acquisition: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observations = []
    for candidate in acquisition["top5"]:
        observations.append(
            {
                "rank": candidate["rank"],
                "workload_plan_fingerprint": candidate[
                    "workload_plan_fingerprint"
                ],
                "three_level_fingerprint": copy.deepcopy(
                    candidate["three_level_fingerprint"]
                ),
                "actual_score": candidate["acquisition_score_center"],
                "actual_resource": copy.deepcopy(
                    candidate["acquisition_center_resource"]
                ),
                "measurement_protocol": {
                    "stable": True,
                    "correctness_passed": True,
                    "technical_failure": False,
                },
            }
        )
    objects = {
        "selection": acquisition,
        "ledger": {
            "successful": observations,
        },
        "summary": {
            "real_workload_invocation_count": 5,
            "successful_real_replay_count": 5,
            "technical_failure_count": 0,
            "preworkload_failure_count": 0,
            "invoked_ranks": [1, 2, 3, 4, 5],
            "fatal_error": None,
        },
        "formal": {
            "accepted_count": 0,
            "formal_physical_call_count": 0,
            "formal_valid_replay_count": 0,
            "formal_technical_failure_count": 0,
        },
    }
    fake_paths = {
        Path("selection"): objects["selection"],
        Path("ledger"): objects["ledger"],
        Path("summary"): objects["summary"],
        Path("formal"): objects["formal"],
    }
    monkeypatch.setattr(
        auditor.v43.durable,
        "audit",
        lambda *_args: {
            "accounting": {
                "passed": True,
                "physical_calls": 5,
                "valid_replays": 5,
                "technical_failures": 0,
                "preworkload_failures": 0,
            },
            "state_isolation": {"passed": True},
        },
    )
    monkeypatch.setattr(
        auditor.v43.durable,
        "_load",
        lambda path: copy.deepcopy(fake_paths[Path(path)]),
    )
    monkeypatch.setattr(auditor.v43.durable, "_sha256", lambda _path: "f" * 64)
    gate = auditor._acquisition_audit(
        Path("selection"),
        Path("ledger"),
        Path("events"),
        Path("summary"),
        Path("formal"),
        "f" * 64,
    )
    assert gate["passed"] is False
    assert gate["formal_start_authorized"] is False
    assert gate["acquisition_complete"] is True
    assert gate["acquisition_gate"]["next_action"] == (
        "automatic_rebuild_then_formal_gate_or_reexplore"
    )


def test_controller_has_automatic_acquisition_and_formal_branches() -> None:
    source = Path(controller.__file__).read_text(encoding="utf-8")
    assert "gate.get(\"acquisition_complete\") is not True" in source
    assert "builder.build(" in source
    assert "if gate.get(\"passed\") is True:" in source
    assert "_prepare_and_launch_formal(" in source
    assert "MAX_DIAGNOSTIC_BATCHES = 64" in source


def test_two_independent_clean_probe_batches_promote_calibration_trust(
    tmp_path: Path,
) -> None:
    candidate = {
        "rank": 1,
        "exact_point_key": "exact-key",
        "candidate_params": {"seq_page_cost": 1.0},
        "workload_plan_fingerprint": "ratio-plan-sha256:" + "1" * 64,
        "three_level_fingerprint": {"id": "three-level-sha256:" + "2" * 64},
        "selection_role": "uncertain_probe",
        "diagnostic_only": True,
        "incumbent_update_eligible": False,
        "used_for_gradient": False,
        "formal_improvement_eligible": False,
    }
    rows = []
    for ordinal in (1, 2):
        selection_path = tmp_path / f"selection_{ordinal}.json"
        ledger_path = tmp_path / f"ledger_{ordinal}.json"
        selection = {
            "top5": [candidate],
            "batch_nonce": ordinal,
            "state_mutation_contract": {
                "accepted_update": False,
                "gradient_update": False,
                "incumbent_update": False,
                "range_update": False,
            },
        }
        observation = copy.deepcopy(candidate)
        observation.update(
            {
                "workload_returncode": 0,
                "technical_failure": False,
                "actual_score": 4.5,
                "actual_resource": {"cpu": 2.0, "io": 30.0, "memory": 2.5},
                "measurement_protocol": {
                    "stable": True,
                    "correctness_passed": True,
                    "technical_failure": False,
                },
            }
        )
        ledger = {
            "successful": [observation],
            "technical_failures": [],
            "batch_nonce": ordinal,
        }
        selection_path.write_text(json.dumps(selection), encoding="utf-8")
        ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
        rows.append(
            {
                "selection_path": str(selection_path),
                "selection_sha256": builder.v6._sha256(selection_path),
                "ledger_path": str(ledger_path),
                "ledger_sha256": builder.v6._sha256(ledger_path),
                "rank": 1,
                "trusted_training_eligible": False,
            }
        )
    promoted = builder._promote_repeated_clean_probes({"exact-key": rows})
    promoted_rows = promoted["exact-key"]
    assert all(row["trusted_training_eligible"] is False for row in promoted_rows)
    assert all(row["calibration_trusted_eligible"] is True for row in promoted_rows)
    certificate = promoted_rows[0]["calibration_trust_certificate"]
    assert certificate["contract"] == builder.PROBE_PROMOTION_CONTRACT
    assert certificate["independent_clean_probe_batch_count"] == 2
    assert certificate["source_selection_role_preserved"] == "uncertain_probe"
    assert certificate["formal_state_update_eligible"] is False
    assert certificate["gradient_update_eligible"] is False
    assert certificate["incumbent_update_eligible"] is False
    assert certificate["range_update_eligible"] is False


def test_one_probe_or_technical_probe_never_promotes(tmp_path: Path) -> None:
    selection_path = tmp_path / "selection.json"
    ledger_path = tmp_path / "ledger.json"
    candidate = {
        "rank": 1,
        "exact_point_key": "exact-key",
        "candidate_params": {"seq_page_cost": 1.0},
        "workload_plan_fingerprint": "ratio-plan-sha256:" + "1" * 64,
        "three_level_fingerprint": {"id": "three-level-sha256:" + "2" * 64},
        "selection_role": "uncertain_probe",
        "diagnostic_only": True,
        "incumbent_update_eligible": False,
        "used_for_gradient": False,
        "formal_improvement_eligible": False,
    }
    selection_path.write_text(
        json.dumps(
            {
                "top5": [candidate],
                "state_mutation_contract": {
                    "accepted_update": False,
                    "gradient_update": False,
                    "incumbent_update": False,
                    "range_update": False,
                },
            }
        ),
        encoding="utf-8",
    )
    observation = copy.deepcopy(candidate)
    observation.update(
        {
            "workload_returncode": 1,
            "technical_failure": True,
            "measurement_protocol": {
                "stable": False,
                "correctness_passed": False,
                "technical_failure": True,
            },
        }
    )
    ledger_path.write_text(
        json.dumps({"successful": [observation]}), encoding="utf-8"
    )
    row = {
        "selection_path": str(selection_path),
        "selection_sha256": builder.v6._sha256(selection_path),
        "ledger_path": str(ledger_path),
        "ledger_sha256": builder.v6._sha256(ledger_path),
        "rank": 1,
        "trusted_training_eligible": False,
    }
    promoted = builder._promote_repeated_clean_probes(
        {"exact-key": [row, copy.deepcopy(row)]}
    )
    assert all(
        item.get("calibration_trusted_eligible") is not True
        for item in promoted["exact-key"]
    )


def test_clean_acquisition_rebuild_exits_probe_only_loop(
    acquisition: dict,
    tmp_path: Path,
) -> None:
    selection_path = tmp_path / "top5.json"
    ledger_path = tmp_path / "strict_top5_real_replay_round16_ledger_v1.json"
    events_path = tmp_path / "events.jsonl"
    summary_path = tmp_path / "summary.json"
    gate_path = tmp_path / "postrun_gate.json"
    selection_path.write_text(json.dumps(acquisition), encoding="utf-8")

    templates = {int(row["rank"]): row for row in load(LEDGER)["successful"]}
    observations = []
    for candidate in acquisition["top5"]:
        observation = copy.deepcopy(templates[int(candidate["rank"])])
        observation.update(
            {
                "rank": candidate["rank"],
                "candidate_params": copy.deepcopy(
                    candidate.get("candidate_params") or candidate["params"]
                ),
                "workload_plan_fingerprint": candidate[
                    "workload_plan_fingerprint"
                ],
                "three_level_fingerprint": copy.deepcopy(
                    candidate["three_level_fingerprint"]
                ),
                "actual_score": candidate["acquisition_score_center"],
                "actual_resource": copy.deepcopy(
                    candidate["acquisition_center_resource"]
                ),
                "workload_returncode": 0,
                "technical_failure": False,
            }
        )
        observation["measurement_protocol"].update(
            {
                "stable": True,
                "correctness_passed": True,
                "technical_failure": False,
            }
        )
        observations.append(observation)
    ledger_path.write_text(
        json.dumps(
            {
                "real_workload_invocation_count": 5,
                "successful": observations,
                "technical_failures": [],
            }
        ),
        encoding="utf-8",
    )
    events_path.write_text("{}\n", encoding="utf-8")
    summary_path.write_text(
        json.dumps(
            {
                "successful_real_replay_count": 5,
                "baseline_params_restored": True,
                "baseline_snapshot_restored": True,
                "accepted_count_delta": 0,
                "gradient_update_applied": False,
                "incumbent_updated": False,
                "range_updated": False,
            }
        ),
        encoding="utf-8",
    )
    gate_path.write_text(
        json.dumps(
            {
                "schema_version": builder.ACQUISITION_GATE_SCHEMA,
                "passed": False,
                "formal_start_authorized": False,
                "acquisition_complete": True,
                "quality_gate": {
                    "checks": {
                        "durable_accounting_clean5": True,
                        "baseline_restored_and_state_neutral": True,
                        "selection_algorithm_gate": True,
                        "trusted_score_mae_within_gate": False,
                        "trusted_spearman_positive": False,
                    }
                },
                "acquisition_gate": {"checks": {"clean5": True}},
            }
        ),
        encoding="utf-8",
    )
    rebuilt = builder.build(
        selection_path=selection_path,
        selection_sha256=builder.v6._sha256(selection_path),
        ledger_path=ledger_path,
        ledger_sha256=builder.v6._sha256(ledger_path),
        events_path=events_path,
        events_sha256=builder.v6._sha256(events_path),
        summary_path=summary_path,
        summary_sha256=builder.v6._sha256(summary_path),
        gate_path=gate_path,
        gate_sha256=builder.v6._sha256(gate_path),
        formal_state_path=FORMAL,
        formal_state_sha256=builder.v6._sha256(FORMAL),
    )
    assert rebuilt["v44_build_contract"]["mode"] == "trusted3_probe2"
    assert [item["selection_role"] for item in rebuilt["top5"]] == [
        "trusted",
        "trusted",
        "trusted",
        "uncertain_probe",
        "uncertain_probe",
    ]
    assert rebuilt["v44_build_contract"][
        "acquisition_probe_rows_used_to_establish_trust"
    ] >= 2
    assert rebuilt["state_mutation_contract"] == {
        "accepted_update": False,
        "gradient_update": False,
        "incumbent_update": False,
        "range_update": False,
    }

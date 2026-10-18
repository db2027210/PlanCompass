from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from shutil import copytree
from types import SimpleNamespace

import pytest


PROJECT = Path(__file__).resolve().parents[1]
CONTROLLER = PROJECT / "tools/tpch_v47_resealed_realized_evidence.py"
REMOTE_FIXTURE = Path("/tmp/tpch_v46_remote_reseal_a_20260803")
FORMAL_SHA = "fc262cead29c831ca04c09a7c4d1b2ebf1481636f82e692cd0dcf3b2d3539bc8"


def import_controller():
    spec = importlib.util.spec_from_file_location("tpch_v47_remote_fixture", CONTROLLER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def copied_fixture(tmp_path: Path) -> Path:
    target = tmp_path / "resealed"
    copytree(REMOTE_FIXTURE, target)
    return target


def test_real_remote_reseal_validates_by_rank_params_and_realized_identity(tmp_path):
    v47 = import_controller()
    fixture = copied_fixture(tmp_path)
    selection = fixture / "selection.json"
    evidence = v47.resealed_clean5_evidence(selection, fixture, 1, FORMAL_SHA)
    assert evidence["contract"] == v47.RESEALED_EVIDENCE_CONTRACT
    assert evidence["rank_parameter_binding"] is True
    assert evidence["predicted_roles"] == [
        "trusted",
        "trusted",
        "trusted",
        "probe",
        "probe",
    ]
    assert evidence["realized_roles"] == [
        "trusted",
        "probe",
        "trusted",
        "probe",
        "trusted",
    ]
    assert len(set(evidence["realized_fingerprint_ids"])) == 5

    cycle = tmp_path / "cycle_004"
    cycle.mkdir()
    realized_path = v47.realized_reexploration_selection(selection, fixture, cycle)
    realized = v47.load(realized_path)
    summary = v47.load(fixture / "diagnostic_summary.json")
    assert realized["schema_version"] == v47.REALIZED_SELECTION_CONTRACT
    assert [item["selection_role"] for item in realized["top5"]] == [
        "trusted",
        "probe",
        "trusted",
        "probe",
        "trusted",
    ]
    assert [item["three_level_fingerprint"]["id"] for item in realized["top5"]] == [
        item["three_level_fingerprint"]["id"] for item in summary["successful"]
    ]
    assert realized["manual_candidate_injection"] is False
    assert realized["thresholds_relaxed"] is False
    assert realized["formal_state_mutated"] is False
    assert realized["new_workload_calls"] == 0


def test_real_remote_reseal_rank4_absorption_uses_realized_fingerprint(tmp_path):
    v47 = import_controller()
    fixture = copied_fixture(tmp_path)
    selection = fixture / "selection.json"
    predicted = v47.load(selection)
    summary = v47.load(fixture / "diagnostic_summary.json")
    predicted_rank4 = predicted["top5"][3]["three_level_fingerprint"]["id"]
    realized_rank4 = summary["successful"][3]["three_level_fingerprint"]["id"]
    assert predicted_rank4 != realized_rank4

    gate = tmp_path / "entry_gate.json"
    checks = {
        "actual_best_in_predicted_top3": False,
        "candidate_audit_fields_complete": True,
        "five_unique_three_level_fingerprints": True,
        "top5_no_technical_failure": True,
        "trusted3_probe2": True,
        "trusted_only_spearman_positive": True,
        "trusted_resource_predictions_no_order_of_magnitude_error": True,
        "trusted_acceptance_feasible": True,
    }
    write_json(
        gate,
        {
            "passed": False,
            "checks": checks,
            "actual_best_predicted_rank": 4,
            "trusted_only_spearman": 0.5,
            "maximum_trusted_resource_factor_error": 1.1,
        },
    )
    formal = tmp_path / "formal_state.json"
    write_json(formal, {"accepted_count": 1})

    def fixture_aware_sha(path: Path) -> str:
        if Path(path) == formal:
            return FORMAL_SHA
        return v47.sha256(Path(path))

    fake_v26 = SimpleNamespace(
        sha256=fixture_aware_sha,
        load=v47.load,
        atomic_json=v47.atomic_json,
        accepted_counter=lambda: 1,
    )
    cycle = tmp_path / "cycle_004"
    cycle.mkdir()
    payload, effective_selection, effective_run = v47.absorb_actual_best_probe_rank4(
        fake_v26,
        cycle_root=cycle,
        selection=selection,
        validation_run=fixture,
        analysis_path=gate,
        accepted=1,
        resume_state=formal,
        expected_formal_sha256=FORMAL_SHA,
    )
    assert payload["actual_best_probe_fingerprint"] == realized_rank4
    assert payload["predicted_rank4_fingerprint"] == predicted_rank4
    assert payload["realized_rank4_fingerprint"] == realized_rank4
    assert payload["resealed_realized_evidence"] is True
    assert payload["top3_threshold_relaxed"] is False
    assert payload["manual_candidate_injection"] is False
    assert effective_selection == cycle / "v47_realized_reseal_evidence.json"
    assert effective_run == fixture


@pytest.mark.parametrize("tamper", ["params", "duplicate", "roles", "fingerprint"])
def test_real_remote_reseal_tamper_fails_closed(tmp_path, tamper):
    v47 = import_controller()
    fixture = copied_fixture(tmp_path)
    summary_path = fixture / "diagnostic_summary.json"
    summary = v47.load(summary_path)
    if tamper == "params":
        summary["successful"][1]["candidate_params"]["seq_page_cost"] = 1.41
    elif tamper == "duplicate":
        summary["successful"][1]["three_level_fingerprint"] = dict(
            summary["successful"][0]["three_level_fingerprint"]
        )
    elif tamper == "roles":
        summary["successful"][1]["selection_role"] = "trusted"
    else:
        summary["successful"][0]["three_level_fingerprint"]["valid"] = False
    write_json(summary_path, summary)
    with pytest.raises(RuntimeError, match="parameter-bound state-neutral 5/5/0"):
        v47.resealed_clean5_evidence(
            fixture / "selection.json",
            fixture,
            1,
            FORMAL_SHA,
        )


def test_ordinary_clean5_does_not_accept_resealed_schema(tmp_path):
    v47 = import_controller()
    fixture = copied_fixture(tmp_path)
    with pytest.raises(RuntimeError, match="clean state-neutral 5/5/0"):
        v47.clean5_evidence(fixture / "selection.json", fixture, 1, FORMAL_SHA)

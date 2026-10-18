from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile

import pytest

from tools import build_tpcc_v37_exact_drift_validation as v37
from tools import build_tpcc_v42_exact_plan_io_recalibration as builder
from tools import run_tpcc_v42_exact_plan_io_strict_serial as runner


ROOT = Path(__file__).resolve().parents[1]
RUN = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/"
    "formal10_final_algorithm_fresh0_v36_20260803/autonomous_runs_v40/"
    "autonomous_diagnostic_validation_01"
)
PATHS = {
    "selection_path": RUN / "top5.json",
    "ledger_path": RUN / "strict_top5_real_replay_round16_ledger_v1.json",
    "events_path": RUN / "top5_real_replay_round16_diagnostic_v1/events.jsonl",
    "summary_path": RUN / "top5_real_replay_round16_diagnostic_v1/summary.json",
    "gate_path": RUN / "postrun_gate_and_formal_handoff/postrun_gate.json",
    "formal_state_path": ROOT / "history/tpcc_final_algorithm_fresh0_v36_20260803/formal_state.json",
}
SHAS = {
    "selection_sha256": "72e0debe1b55271173cbe42a45cf697bb5f67334396876ed0b1e5d8d40dbb1ef",
    "ledger_sha256": "a1f668a2e8b8433344f136c5890331d2a2f49913b3ab37be2ada98f6ecb6f227",
    "events_sha256": "0fedf74816387132cf6878789a9a134098e3c70ee97bae197b160ef31872e239",
    "summary_sha256": "524868c69e7f8fafed44fac4c28e162a1ce9c49cf58b50b0827a0fd52098428a",
    "gate_sha256": "8ed3f1cd81910f9677982065d0228922ebde6a97abcff0640896c81805687eca",
    "formal_state_sha256": "62e3fd40c93c163727367afd20b151c291c3646d63fbe0c5c5e2d32c3160aeb0",
}


def build() -> dict:
    return builder.build(**PATHS, **SHAS)


def test_v42_is_deterministic_fresh_noncorner_3t2p() -> None:
    first = build()
    assert first == build()
    assert [item["selection_role"] for item in first["top5"]] == [
        "trusted",
        "trusted",
        "trusted",
        "uncertain_probe",
        "uncertain_probe",
    ]
    identifiers = {item["three_level_fingerprint"]["id"] for item in first["top5"]}
    historical = v37._historical_fingerprint_ids(first["source_batches"])
    assert len(identifiers) == 5
    assert not identifiers.intersection(historical)
    assert all(item["boundary_corner"] is False for item in first["top5"])


def test_rank4_probe_is_probe_only_evidence_and_not_replayed() -> None:
    result = build()
    contract = result["v42_build_contract"]
    source = json.loads(PATHS["selection_path"].read_text(encoding="utf-8"))
    rank4_key = source["top5"][3]["exact_point_key"]
    assert contract["current_probe_exact_points_replayed"] is False
    assert rank4_key not in {item["exact_point_key"] for item in result["top5"]}
    assert contract["trusted_plan_probe_rows_used"] == 0
    assert contract["rank4_probe_formal_state_update_rows"] == 0
    assert contract["rank4_probe_gradient_update_rows"] == 0
    assert contract["rank4_probe_incumbent_update_rows"] == 0
    for item in result["top5"]:
        calibration = item["plan_regime_calibration"]
        assert calibration["probe_rows_used_for_trusted_calibration"] == 0
        assert calibration["rank4_probe_used_for_formal_state"] is False
        assert calibration["rank4_probe_used_for_gradient"] is False
        assert calibration["rank4_probe_used_for_incumbent"] is False
        assert item["diagnostic_only"] is True
        assert item["incumbent_update_eligible"] is False
        assert item["used_for_gradient"] is False


def test_thresholds_are_fixed_and_tamper_fails_closed() -> None:
    result = build()
    contract = result["v42_build_contract"]
    assert contract["threshold_changed"] is False
    assert contract["score_mae_max"] == 0.05
    assert contract["resource_mae_pp_max"] == {
        "cpu": 5.0,
        "io": 5.0,
        "memory": 2.0,
    }
    gate = json.loads(PATHS["gate_path"].read_text(encoding="utf-8"))
    gate["quality_gate"]["thresholds"]["score_mae_max"] = 0.051
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "gate.json"
        path.write_text(json.dumps(gate, sort_keys=True) + "\n", encoding="utf-8")
        kwargs = {**PATHS, **SHAS}
        kwargs["gate_path"] = path
        kwargs["gate_sha256"] = builder.v6._sha256(path)
        with pytest.raises(RuntimeError, match="requires sealed state-neutral v40"):
            builder.build(**kwargs)


def test_runner_accepts_sealed_v42_and_rejects_role_pollution() -> None:
    runtime = runner.load_runtime()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        selection = root / "top5.json"
        selection.write_text(json.dumps(build(), sort_keys=True) + "\n", encoding="utf-8")
        _analysis, _generation, candidates, _provenance = runtime[
            "validate_generation_and_top5"
        ](selection, root / "preflight")
        assert len(candidates) == 5
        tampered = build()
        tampered["top5"][0]["plan_regime_calibration"][
            "rank4_probe_used_for_formal_state"
        ] = True
        selection.write_text(json.dumps(tampered, sort_keys=True) + "\n", encoding="utf-8")
        with pytest.raises(RuntimeError, match="candidate role isolation mismatch"):
            runtime["validate_generation_and_top5"](selection, root / "tampered")


def test_state_neutral_max5_no_retry_contract() -> None:
    result = build()
    assert result["maximum_real_workload_invocations"] == 5
    assert result["state_mutation_contract"] == {
        "accepted_update": False,
        "gradient_update": False,
        "incumbent_update": False,
        "range_update": False,
    }
    contract = result["v42_build_contract"]
    assert contract["same_rank_retry_allowed"] is False
    assert contract["technical_call_advances_to_next_unique_rank"] is True
    assert contract["manual_candidate_selection"] is False
    assert contract["physical_workload_calls_added"] == 0

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))
import build_tpcc_v39_probe_regime_validation as v39  # noqa: E402


SOURCE_SELECTION = ROOT / "history/tpcc_v38_fresh_unique_package_20260803/top5.json"
SOURCE_ROOT = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/"
    "formal10_final_algorithm_fresh0_v36_20260803/autonomous_runs_v38/"
    "autonomous_diagnostic_validation_01"
)
SOURCE_LEDGER = SOURCE_ROOT / "strict_top5_real_replay_round16_ledger_v1.json"
SOURCE_SUMMARY = SOURCE_ROOT / "top5_real_replay_round16_diagnostic_v1/summary.json"
SOURCE_GATE = SOURCE_ROOT / "postrun_gate_and_formal_handoff/postrun_gate.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build() -> dict:
    return v39.build(
        v39._load(SOURCE_SELECTION),
        source_selection_path=SOURCE_SELECTION,
        source_ledger_path=SOURCE_LEDGER,
        source_summary_path=SOURCE_SUMMARY,
        source_gate_path=SOURCE_GATE,
    )


def test_real_v38_clean5_build_is_deterministic_and_novel() -> None:
    source_shas = {
        path: sha(path)
        for path in (SOURCE_SELECTION, SOURCE_LEDGER, SOURCE_SUMMARY, SOURCE_GATE)
    }
    first = build()
    second = build()
    assert first == second
    top5 = first["top5"]
    assert [item["selection_role"] for item in top5] == [
        "trusted",
        "trusted",
        "trusted",
        "uncertain_probe",
        "uncertain_probe",
    ]
    identifiers = [item["three_level_fingerprint"]["id"] for item in top5]
    assert len(set(identifiers)) == 5
    assert len({item["exact_point_key"] for item in top5}) == 5
    assert all(item.get("boundary_corner") is not True for item in top5)
    historical = v39.v37._historical_fingerprint_ids(first["source_batches"])
    assert not set(identifiers).intersection(historical)
    assert all(sha(path) == value for path, value in source_shas.items())


def test_rank5_probe_becomes_algorithmic_regime_stability_anchor() -> None:
    result = build()
    contract = result["v39_build_contract"]
    rank1 = result["top5"][0]
    calibration = contract["probe_regime_calibration"]
    assert contract["v38_empirical_best_rank"] == 5
    assert rank1["stability_acquisition"] is True
    assert rank1["exact_point_key"] == contract["probe_discovered_exact_point_key"]
    assert rank1["workload_plan_fingerprint"] == contract["probe_discovered_plan_regime"]
    assert calibration["current_anchor_count"] == 2
    assert calibration["current_trusted_anchor_count"] == 1
    assert calibration["current_probe_anchor_count"] == 1
    assert calibration["validated_for_trusted_transfer"] is True
    assert calibration["probe_rows_used_for_gradient_incumbent_accepted"] == 0
    assert contract["probe_rows_used_for_gradient_incumbent_accepted"] == 0


def test_candidate_logging_and_state_neutral_contract_are_complete() -> None:
    result = build()
    required = {
        "feature_ratio",
        "predicted_resource",
        "utilization",
        "dScore/dU",
        "T",
        "confidence",
        "g_j",
        "eta",
        "delta_param",
        "candidate_score",
    }
    assert all(not (required - set(item)) for item in result["top5"])
    assert result["maximum_real_workload_invocations"] == 5
    assert result["same_rank_retry_allowed"] is False
    assert result["technical_call_advances_to_next_unique_rank"] is True
    assert result["state_mutation_contract"] == {
        "accepted_update": False,
        "gradient_update": False,
        "incumbent_update": False,
        "range_update": False,
    }
    assert result["qps_role"] == "outer_audit_only_not_inner_score"
    assert result["v39_build_contract"]["physical_workload_calls_added"] == 0


def test_any_additional_v38_gate_failure_fails_closed() -> None:
    selection = v39._load(SOURCE_SELECTION)
    ledger = v39._load(SOURCE_LEDGER)
    summary = v39._load(SOURCE_SUMMARY)
    gate = v39._load(SOURCE_GATE)
    bad = copy.deepcopy(gate)
    bad["quality_gate"]["checks"]["trusted_spearman_positive"] = False
    try:
        v39._validate_failed_gate(bad, SOURCE_SELECTION, SOURCE_LEDGER, SOURCE_SUMMARY)
    except RuntimeError as error:
        assert "rank5-only miss" in str(error)
    else:
        raise AssertionError("v39 accepted a multi-cause failed source gate")
    assert len(v39._current_pairs(selection, ledger)) == 5
    assert summary["technical_failure_count"] == 0


if __name__ == "__main__":
    test_real_v38_clean5_build_is_deterministic_and_novel()
    test_rank5_probe_becomes_algorithmic_regime_stability_anchor()
    test_candidate_logging_and_state_neutral_contract_are_complete()
    test_any_additional_v38_gate_failure_fails_closed()
    print("TPC-C v39 probe-regime tests passed: 4", flush=True)

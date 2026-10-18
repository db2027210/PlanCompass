from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build_tpcc_v40_clean5_exact_recalibration as v40  # noqa: E402
import build_tpcc_v37_exact_drift_validation as v37  # noqa: E402
import tpcc_v40_postrun_gate_and_formal_handoff as handoff  # noqa: E402
import run_tpcc_v40_clean5_exact_strict_serial as runner  # noqa: E402


RUN = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/"
    "formal10_final_algorithm_fresh0_v36_20260803/autonomous_runs_v39/"
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
    "selection_sha256": "a9d81278b751b1bb10675c9b6727aade1442b6ff386456b9f84466a8b293fff1",
    "ledger_sha256": "bd6aa58a3d5cd9e9505cc2efbf32c307d906c06a258f6f15eeb855a299f46e27",
    "events_sha256": "d382f4069efc20fcc0369ce2f1b3de5599063e5358d2cfcd20d41dcb25e48200",
    "summary_sha256": "cb93606cb866e0b3d9a5611ad3f0848dc35d7ae682bedf749be5222ac0424a6d",
    "gate_sha256": "543c3f9ff6c705f74cf570da4d484870dc3f74044b4d6366725baad000306ba2",
    "formal_state_sha256": "62e3fd40c93c163727367afd20b151c291c3646d63fbe0c5c5e2d32c3160aeb0",
}


def build() -> dict:
    return v40.build(**PATHS, **SHAS)


def test_final_clean5_authority_and_output_are_deterministic() -> None:
    first = build()
    assert first == build()
    contract = first["v40_build_contract"]
    assert contract["source_gate_only_failure"] == "trusted_score_mae_within_gate"
    assert contract["threshold_changed"] is False
    assert contract["current_clean_trusted_rows_absorbed"] == 3
    assert contract["direct_formal_promotion"] is False
    assert [item["selection_role"] for item in first["top5"]] == [
        "trusted", "trusted", "trusted", "uncertain_probe", "uncertain_probe"
    ]
    assert [item["exact_point_key"][:8] for item in first["top5"][:3]] == [
        "34608a48", "26dfa06b", "acaa9f22"
    ]
    historical = v37._historical_fingerprint_ids(first["source_batches"])
    identifiers = {item["three_level_fingerprint"]["id"] for item in first["top5"]}
    assert len(identifiers) == 5
    assert not identifiers.intersection(historical)
    assert all(item["boundary_corner"] is False for item in first["top5"])


def test_partial_or_mutated_summary_fails_closed() -> None:
    summary = json.loads(PATHS["summary_path"].read_text(encoding="utf-8"))
    summary["successful_real_replay_count"] = 3
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "summary.json"
        path.write_text(json.dumps(summary, sort_keys=True) + "\n", encoding="utf-8")
        kwargs = {**PATHS, **SHAS}
        kwargs["summary_path"] = path
        kwargs["summary_sha256"] = v40.v6._sha256(path)
        try:
            v40.build(**kwargs)
        except RuntimeError as error:
            assert "requires sealed v39 clean5" in str(error)
        else:
            raise AssertionError("v40 accepted a partial batch")


def test_rank3_residual_failure_is_automatically_demoted_to_probe() -> None:
    result = build()
    by_key = {item["exact_point_key"][:8]: item for item in result["top5"]}
    demoted = by_key["bf7db93f"]
    calibration = demoted["plan_regime_calibration"]
    assert demoted["selection_role"] == "uncertain_probe"
    assert calibration["current_clean_trusted"] is True
    assert calibration["current_score_residual"] > 0.05
    assert calibration["current_clean_residual_gate"] is False


def test_formal_handoff_preserves_historical_roles_and_uses_zero_update_rows() -> None:
    selection = build()
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "top5.json"
        path.write_text(json.dumps(selection, sort_keys=True) + "\n", encoding="utf-8")
        successful = [
            {
                "rank": item["rank"],
                "actual_score": item["predicted_score"],
                "actual_resource": item["predicted_resource"],
                "workload_plan_fingerprint": item["workload_plan_fingerprint"],
                "three_level_fingerprint": item["three_level_fingerprint"],
            }
            for item in selection["top5"]
        ]
        promoted = handoff.promote_v40(
            selection,
            {"real_workload_invocation_count": 5, "successful": successful, "technical_failures": []},
            {
                "passed": True,
                "formal_start_authorized": True,
                "selection_sha256": v40.v6._sha256(path),
            },
            path,
            3.9894795578792417,
        )
    assert promoted["v40_historical_evidence_policy"]["formal_state_update_rows"] == 0
    assert promoted["probe_observations_affect_formal_acceptance"] is False
    promoted_certificate = next(
        item for item in promoted["top5"] if item["exact_point_key"].startswith("26dfa06b")
    )
    assert len(promoted_certificate["source_provenance"]["trusted_observations"]) == 1
    assert len(promoted_certificate["source_provenance"]["probe_observations_excluded_from_trusted"]) == 3
    assert promoted_certificate["confidence"]["historical_roles_rewritten"] is False


def test_runner_accepts_sealed_v40_and_rejects_threshold_change() -> None:
    runtime = runner.load_runtime()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        path = root / "top5.json"
        path.write_text(json.dumps(build(), sort_keys=True) + "\n", encoding="utf-8")
        _analysis, _generation, candidates, _provenance = runtime[
            "validate_generation_and_top5"
        ](path, root / "preflight")
        assert len(candidates) == 5
        tampered = build()
        tampered["v40_build_contract"]["threshold_changed"] = True
        path.write_text(json.dumps(tampered, sort_keys=True) + "\n", encoding="utf-8")
        try:
            runtime["validate_generation_and_top5"](path, root / "tampered")
        except RuntimeError as error:
            assert "v40 clean5 recalibration runner contract mismatch" in str(error)
        else:
            raise AssertionError("v40 runner accepted a threshold change")


if __name__ == "__main__":
    test_final_clean5_authority_and_output_are_deterministic()
    test_partial_or_mutated_summary_fails_closed()
    test_rank3_residual_failure_is_automatically_demoted_to_probe()
    test_formal_handoff_preserves_historical_roles_and_uses_zero_update_rows()
    test_runner_accepts_sealed_v40_and_rejects_threshold_change()
    print("TPC-C v40 clean5 recalibration tests passed: 5", flush=True)

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
RUNTIME = ROOT / "history/tpcc_v38_fresh_unique_package_20260803/runtime"
sys.path.insert(0, str(TOOLS))
import build_tpcc_unique_exact_validation_v6 as v6  # noqa: E402
import build_tpcc_v37_exact_drift_validation as v37  # noqa: E402
import build_tpcc_v39_plan_regime_absorption as absorption  # noqa: E402
import tpcc_v39_postrun_gate_and_formal_handoff as handoff  # noqa: E402


SOURCE_SELECTION = ROOT / "history/tpcc_v38_fresh_unique_package_20260803/top5.json"
SOURCE_ROOT = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/"
    "formal10_final_algorithm_fresh0_v36_20260803/autonomous_runs_v38/"
    "autonomous_diagnostic_validation_01"
)
SOURCE_LEDGER = SOURCE_ROOT / "strict_top5_real_replay_round16_ledger_v1.json"
SOURCE_EVENTS = SOURCE_ROOT / "top5_real_replay_round16_diagnostic_v1/events.jsonl"
SOURCE_SUMMARY = SOURCE_ROOT / "top5_real_replay_round16_diagnostic_v1/summary.json"
SOURCE_GATE = SOURCE_ROOT / "postrun_gate_and_formal_handoff/postrun_gate.json"
FORMAL_STATE = ROOT / "history/tpcc_final_algorithm_fresh0_v36_20260803/formal_state.json"


def build() -> dict:
    return absorption.build(
        selection_path=SOURCE_SELECTION,
        ledger_path=SOURCE_LEDGER,
        events_path=SOURCE_EVENTS,
        summary_path=SOURCE_SUMMARY,
        gate_path=SOURCE_GATE,
        formal_state_path=FORMAL_STATE,
    )


def test_absorption_builds_deterministic_novel_3t2p_with_certificate_promotion() -> None:
    first = build()
    second = build()
    assert first == second
    top5 = first["top5"]
    contract = first["v39_plan_regime_absorption_contract"]
    assert [item["selection_role"] for item in top5] == [
        "trusted",
        "trusted",
        "trusted",
        "uncertain_probe",
        "uncertain_probe",
    ]
    assert len({item["three_level_fingerprint"]["id"] for item in top5}) == 5
    assert all(item.get("boundary_corner") is False for item in top5)
    assert contract["absorbed_probe_rank"] == 5
    assert contract["absorbed_probe_updates_plan_prediction"] is True
    assert contract["historical_probe_roles_rewritten"] is False
    assert contract["promotion_certificate_updates_current_selection_role"] is True
    assert contract["absorbed_probe_updates_formal_acceptance"] is False
    assert contract["absorbed_exact_point_promoted_by_certificate"] is True
    assert contract["promotion_evaluation_rows"] == 3
    assert contract["formal_state_update_rows"] == 0
    promoted = [
        item for item in top5
        if item["exact_point_key"] == contract["absorbed_probe_exact_point_key"]
    ]
    assert len(promoted) == 1
    assert promoted[0]["rank"] == 1
    certificate = promoted[0]["accumulated_probe_promotion_certificate"]
    assert certificate["passed"] is True
    assert certificate["promotion_evaluation_rows"] == 3
    assert certificate["formal_state_update_rows"] == 0
    assert certificate["actual_score_span"] <= certificate["empirical_noise_bound"]
    assert all(
        row["selection_role"] == "uncertain_probe"
        and row["trusted_training_eligible"] is False
        for row in certificate["source_rows"]
    )


def test_sealed_recent_intervals_make_a_probe_best_miss_unlikely() -> None:
    top5 = build()["top5"]
    trusted_lower = max(
        float(item["predicted_score_interval"]["lower"]) for item in top5[:3]
    )
    probe_upper = max(
        float(item["predicted_score_interval"]["upper"]) for item in top5[3:]
    )
    assert trusted_lower == 4.718949318367642
    assert probe_upper == 4.687673497736383
    assert trusted_lower > probe_upper


def _promotion_metrics(rank: int) -> tuple[list[dict], dict]:
    source = v6._load(SOURCE_SELECTION)
    pool, descriptors = v6.reconstruct_exact_pool(
        source,
        source_selection_path=SOURCE_SELECTION,
        source_ledger_path=SOURCE_LEDGER,
    )
    _ = pool
    evidence = v37._role_evidence(descriptors, v6._sha256(SOURCE_LEDGER))
    candidate = source["top5"][rank - 1]
    key = v6._point_key(v6._params(candidate), candidate["workload_plan_fingerprint"])
    rows = evidence[key]
    return rows, v37._loo_metrics(rows)


def test_independent_accumulated_probe_promotion_gate_distinguishes_rank5() -> None:
    rank5_rows, rank5 = _promotion_metrics(5)
    rank4_rows, rank4 = _promotion_metrics(4)
    assert len(rank5_rows) == 3
    assert len(rank4_rows) == 3
    assert all(row["trusted_training_eligible"] is False for row in rank5_rows)
    assert all(row["trusted_training_eligible"] is False for row in rank4_rows)
    assert rank5["score_mae"] == 0.024475404721084892
    assert rank4["score_mae"] == 0.09985969921010647
    rank5_promotable = bool(
        rank5["score_mae"] <= 0.05
        and rank5["resource_mae_pp"]["cpu"] <= 5.0
        and rank5["resource_mae_pp"]["io"] <= 5.0
        and rank5["resource_mae_pp"]["memory"] <= 2.0
    )
    rank4_promotable = bool(
        rank4["score_mae"] <= 0.05
        and rank4["resource_mae_pp"]["cpu"] <= 5.0
        and rank4["resource_mae_pp"]["io"] <= 5.0
        and rank4["resource_mae_pp"]["memory"] <= 2.0
    )
    assert rank5_promotable is True
    assert rank4_promotable is False


def test_sealed_v38_runner_rejects_new_absorption_candidate_contracts() -> None:
    if str(RUNTIME) not in sys.path:
        sys.path.insert(0, str(RUNTIME))
    runner_path = RUNTIME / "run_tpcc_v38_unique_no_retry_strict_serial.py"
    spec = importlib.util.spec_from_file_location("sealed_tpcc_v38_runner_review", runner_path)
    assert spec is not None and spec.loader is not None
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    with tempfile.TemporaryDirectory() as directory:
        selection_path = Path(directory) / "top5.json"
        selection_path.write_text(
            json.dumps(build(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        try:
            runner.validate_generation_and_top5(
                selection_path,
                Path(directory) / "preflight_only",
            )
        except RuntimeError as error:
            assert "calibration/LOO contract is invalid" in str(error)
        else:
            raise AssertionError("sealed v38 runner unexpectedly accepted v39 contracts")


def test_v39_runner_accepts_certificate_and_rejects_role_rewrite() -> None:
    if str(RUNTIME) not in sys.path:
        sys.path.insert(0, str(RUNTIME))
    import run_tpcc_v39_plan_regime_strict_serial as wrapper

    runtime = wrapper.load_runtime()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        selection_path = root / "top5.json"
        selection_path.write_text(
            json.dumps(build(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        _analysis, _generation, candidates, _provenance = runtime[
            "validate_generation_and_top5"
        ](selection_path, root / "preflight")
        assert len(candidates) == 5
        tampered = build()
        tampered["v39_plan_regime_absorption_contract"][
            "historical_probe_roles_rewritten"
        ] = True
        selection_path.write_text(
            json.dumps(tampered, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        try:
            runtime["validate_generation_and_top5"](
                selection_path, root / "preflight_tampered"
            )
        except RuntimeError as error:
            assert "v39 absorption/promotion runner contract mismatch" in str(error)
        else:
            raise AssertionError("v39 runner accepted a historical-role rewrite")


def test_handoff_uses_three_promotion_rows_but_zero_formal_update_rows() -> None:
    selection = build()
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "top5.json"
        path.write_text(
            json.dumps(selection, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
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
        promoted = handoff.promote_v39(
            selection,
            {
                "real_workload_invocation_count": 5,
                "successful": successful,
                "technical_failures": [],
            },
            {
                "passed": True,
                "formal_start_authorized": True,
                "selection_sha256": v6._sha256(path),
            },
            path,
            4.416709205648302,
        )
    rank1 = promoted["top5"][0]
    assert rank1["confidence"]["historical_probe_evaluation_rows_used"] == 3
    assert rank1["confidence"]["formal_state_update_rows_from_historical_probe_evidence"] == 0
    assert "probe_rows_used_for_trusted_calibration" not in rank1["confidence"]
    assert all(
        row["selection_role"] == "uncertain_probe"
        for row in rank1["source_provenance"]["probe_observations_excluded_from_trusted"]
    )
    assert rank1["source_provenance"]["trusted_observations"] == []
    assert promoted["v39_historical_probe_evidence_policy"]["formal_state_update_rows"] == 0


if __name__ == "__main__":
    test_absorption_builds_deterministic_novel_3t2p_with_certificate_promotion()
    test_sealed_recent_intervals_make_a_probe_best_miss_unlikely()
    test_independent_accumulated_probe_promotion_gate_distinguishes_rank5()
    test_sealed_v38_runner_rejects_new_absorption_candidate_contracts()
    test_v39_runner_accepts_certificate_and_rejects_role_rewrite()
    test_handoff_uses_three_promotion_rows_but_zero_formal_update_rows()
    print("TPC-C v39 absorption review tests passed: 6", flush=True)

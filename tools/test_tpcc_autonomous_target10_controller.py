#!/usr/bin/env python3
"""Mock full-path tests for the TPC-C autonomous controller (zero workload)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tools.tpcc_autonomous_target10_controller as target
import tools.build_tpcc_round14_robust_regime_top5 as builder


def candidate(rank: int, role: str) -> dict:
    plan = f"ratio-plan-sha256:{rank:064x}"
    calibration = {
        "mode": "exact_regime_componentwise_median_with_interval",
        "trusted_eligible": role == "trusted",
        "regime": {
            "plan": plan,
            "observation_count": 2 if role == "trusted" else 1,
        },
    }
    return {
        "rank": rank,
        "selection_role": role,
        "trusted": role == "trusted",
        "uncertain_probe": role != "trusted",
        "exact_plan_regime_validated": True,
        "diagnostic_only": True,
        "incumbent_update_eligible": False,
        "boundary_corner": False,
        "feature_ratio": {"cpu": 1.0, "io": 1.0, "memory": 1.0},
        "predicted_resource": {"cpu": 2.0, "io": 12.0, "memory": 2.2},
        "uncalibrated_predicted_resource": {
            "cpu": 2.0,
            "io": 12.0,
            "memory": 2.2,
        },
        "utilization": {"cpu": 0.5, "io": 0.2, "memory": 0.36},
        "dScore/dU": {},
        "T": {},
        "confidence": {},
        "g_j": {},
        "eta": 0.1,
        "delta_param": {},
        "candidate_score": 5.0 - rank * 0.1,
        "predicted_score": 5.0 - rank * 0.1,
        "predicted_score_interval": {
            "lower": 4.8 - rank * 0.1,
            "median": 5.0 - rank * 0.1,
            "upper": 5.1 - rank * 0.1,
        },
        "formal_lcb_score": 4.8 - rank * 0.1,
        "candidate_params": {"seq_page_cost": 1.0 + rank * 0.01},
        "optimized_params": {"seq_page_cost": 1.0 + rank * 0.01},
        "workload_plan_fingerprint": plan,
        "plan_regime_calibration": calibration,
        "three_level_fingerprint": {
            "id": f"three-level-sha256:{rank:064x}",
            "valid": True,
        },
    }


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="tpcc_autonomous_mock_") as raw:
        root = Path(raw)
        state = root / "formal_state.json"
        target.atomic_json(
            state,
            {
                "accepted_count": 3,
                "accepted_target": 10,
                "qps_used_for_inner_scoring": False,
            },
        )
        roles = ["trusted", "trusted", "trusted", "uncertain_probe", "uncertain_probe"]
        selection = {
            "selection_contract": "tpcc_robust_exact_plan_regime_trusted3_probe2_v1",
            "maximum_real_workload_invocations": 5,
            "qps_role": "outer_audit_only_not_inner_score",
            "formal_expected_improvement_required": True,
            "formal_admission_score": "predicted_score_interval.lower",
            "minimum_predicted_improvement": 1e-6,
            "top5": [candidate(rank, roles[rank - 1]) for rank in range(1, 6)],
        }
        for item in selection["top5"]:
            item["predicted_improvement_over_incumbent"] = (
                0.1 if item["selection_role"] == "trusted" else None
            )
            item["predicted_improvement_lower_bound"] = (
                0.05 if item["selection_role"] == "trusted" else None
            )
            item["formal_improvement_eligible"] = (
                item["selection_role"] == "trusted"
            )
        selection_path = root / "top5.json"
        target.atomic_json(selection_path, selection)
        successful = []
        for rank in range(1, 6):
            evidence = root / f"summary_{rank}.json"
            target.atomic_json(evidence, {"rank": rank})
            item = selection["top5"][rank - 1]
            successful.append(
                {
                    "rank": rank,
                    "predicted_score": item["predicted_score"],
                    "actual_score": 5.0 - rank * 0.08,
                    "predicted_resource": item["predicted_resource"],
                    "actual_resource": {
                        "cpu": 2.0 + rank * 0.01,
                        "io": 12.0 + rank * 0.02,
                        "memory": 2.2 + rank * 0.01,
                    },
                    "workload_plan_fingerprint": item[
                        "workload_plan_fingerprint"
                    ],
                    "three_level_fingerprint": item[
                        "three_level_fingerprint"
                    ],
                    "measurement_protocol": {
                        "stable": True,
                        "correctness_passed": True,
                        "technical_failure": False,
                        "validated_summary_path": str(evidence),
                        "validated_summary_sha256": target.sha256(evidence),
                    },
                }
            )
        ledger_path = root / "ledger.json"
        target.atomic_json(
            ledger_path,
            {
                "real_workload_invocation_count": 5,
                "successful": successful,
                "technical_failures": [],
            },
        )
        summary_path = root / "formal_batch_summary.json"
        target.atomic_json(
            summary_path,
            {
                "baseline_restored": True,
                "formal_state_sha256": target.sha256(state),
                "incumbent_update_applied": True,
                "range_update_applied": True,
                "accepted_before": 3,
                "accepted_after": 4,
                "batch_added_accepted": 1,
            },
        )
        run = {
            "mode": "formal",
            "root": str(root),
            "selection_path": str(selection_path),
            "selection_sha256": target.sha256(selection_path),
            "ledger_path": str(ledger_path),
            "ledger_sha256": target.sha256(ledger_path),
            "summary_path": str(summary_path),
            "summary_sha256": target.sha256(summary_path),
        }
        audit = target.audit_completed_run(run, state)
        assert audit["clean"] is True
        assert audit["top3_hit"] is True
        assert audit["trusted_spearman"] > 0.0
        assert audit["trusted_resource_within_factor_two"] is True
        assert audit["formal_prediction_miss"] is False
        validation_audit = copy.deepcopy(audit)
        validation_audit["mode"] = "diagnostic_validation"
        gate = target.quality_gate([validation_audit])
        assert gate["passed"] is True
        reversed_audit = copy.deepcopy(validation_audit)
        reversed_audit["trusted_pairs"] = [
            (4.9, 4.1),
            (4.8, 4.5),
            (4.7, 4.9),
        ]
        reversed_audit["trusted_spearman"] = -1.0
        reversed_audit["gradient_reversal_detected"] = True
        assert target.quality_gate([reversed_audit])["passed"] is False
        one_batch_gate = target.quality_gate([validation_audit])
        assert one_batch_gate["passed"] is True
        assert target.choose_next_mode([validation_audit], one_batch_gate) == "formal"
        missed_formal = copy.deepcopy(audit)
        missed_formal["batch_added_accepted"] = 0
        missed_formal["formal_prediction_miss"] = True
        assert (
            target.choose_next_mode(
                [validation_audit, missed_formal], one_batch_gate
            )
            == "diagnostic"
        )
        calibration = target.build_exact_calibration([audit])
        assert len(calibration["regimes"]) == 5

        interval = builder.conservative_score_interval(
            {
                "cpu": {"lower": 2.8, "median": 3.3, "upper": 3.6},
                "io": {"lower": 12.0, "median": 20.0, "upper": 30.0},
                "memory": {"lower": 1.5, "median": 2.4, "upper": 3.6},
            }
        )
        assert interval["lower"] <= interval["median"] <= interval["upper"]
        assert interval["evaluated_point_count"] >= 8

        optimistic_only = copy.deepcopy(selection)
        optimistic_only["top5"][0]["formal_lcb_score"] = 4.0
        optimistic_only["top5"][0]["predicted_score_interval"]["lower"] = 4.0
        optimistic_only["top5"][0]["predicted_improvement_lower_bound"] = -0.1
        optimistic_only["top5"][0]["formal_improvement_eligible"] = False
        try:
            target.validate_selection(optimistic_only, mode="formal")
        except RuntimeError:
            pass
        else:
            raise AssertionError("optimistic point estimate entered formal mode")

        diagnostic = copy.deepcopy(selection)
        diagnostic["selection_contract"] = (
            "tpcc_round12_current_plan_discovery_probe5_v1"
        )
        for item in diagnostic["top5"]:
            item["selection_role"] = "uncertain_probe"
            item["trusted"] = False
        diagnostic_path = root / "diagnostic_top5.json"
        target.atomic_json(diagnostic_path, diagnostic)
        target.validate_selection(diagnostic, mode="diagnostic")

        dummy = root / "dummy.py"
        target.atomic_text(dummy, "# mock only\n")
        baseline = root / "baseline.json"
        target.atomic_json(baseline, {"mock": True})
        manifest = {
            "python": sys.executable,
            "batch_parent": str(root / "runs"),
            "baseline_path": str(baseline),
            "diagnostic_runner_path": str(dummy),
            "formal_runner_path": str(dummy),
            "formal_state_path": str(state),
            "pinned_files": [
                {"path": str(dummy), "sha256": target.sha256(dummy)},
                {"path": str(baseline), "sha256": target.sha256(baseline)},
            ],
        }
        seal = target.seal_next_run(
            manifest,
            root / "prepare",
            selection_path,
            "formal",
            7,
            gate,
        )
        assert Path(seal["launcher_path"]).is_file()
        assert target.sha256(Path(seal["launcher_path"])) == seal["launcher_sha256"]
        print(
            json.dumps(
                {
                    "status": "passed",
                    "workload_calls": 0,
                    "clean_formal_audit": True,
                    "fixed_entry_gate_pass": True,
                    "gradient_reversal_routes_to_diagnostic": True,
                    "formal_miss_routes_to_diagnostic": True,
                    "confidence_lower_bound_gate": True,
                    "exact_regime_count": len(calibration["regimes"]),
                    "sealed_launcher_created_not_executed": True,
                },
                sort_keys=True,
            ),
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

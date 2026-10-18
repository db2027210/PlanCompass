from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
SELECTION = PROJECT / "history/tpcc_final_algorithm_fresh0_v36_20260803/autonomous_controller/prepare_01/sealed_run/top5.json"
LEDGER = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/"
    "formal10_final_algorithm_fresh0_v36_20260803/autonomous_runs_v36/"
    "autonomous_diagnostic_validation_01/strict_top5_real_replay_round14_ledger_v1.json"
)
RUN_ROOT = LEDGER.parent / "top5_real_replay_round14_diagnostic_v1"
FORMAL = PROJECT / "history/tpcc_final_algorithm_fresh0_v36_20260803/formal_state.json"
FORMAL_SHA = "62e3fd40c93c163727367afd20b151c291c3646d63fbe0c5c5e2d32c3160aeb0"
REMOTE = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/"
    "gradient_debug_round14_robust_regime_interval_v2_20260729T1824"
)
sys.path.insert(0, str(REMOTE))
sys.path.insert(0, str(PROJECT))

from tools import audit_tpcc_v36_postrun_events as audit_module
from tools import audit_tpcc_v37_postrun_gate as strict_audit
from tools import build_tpcc_v37_exact_drift_validation as builder
from tools import run_tpcc_v36_unique_exact_strict_serial as runner
from tools import run_tpcc_formal_target10_batch_v3 as formal_runner
from tools import tpcc_v37_postrun_gate_and_formal_handoff as handoff


class TpccV37ExactDriftTests(unittest.TestCase):
    def build(self):
        return builder.build(
            builder.v6._load(SELECTION),
            source_selection_path=SELECTION,
            source_ledger_path=LEDGER,
        )

    def test_v37_build_is_deterministic_and_role_separated(self) -> None:
        first = self.build()
        second = self.build()
        self.assertEqual(first, second)
        candidates = first["top5"]
        self.assertEqual(
            [item["selection_role"] for item in candidates],
            ["trusted", "trusted", "trusted", "uncertain_probe", "uncertain_probe"],
        )
        self.assertTrue(all(item["trusted_loo_gate_passed"] for item in candidates[:3]))
        self.assertTrue(
            all(
                item["plan_regime_calibration"]["probe_rows_used_for_trusted_calibration"] == 0
                for item in candidates
            )
        )
        self.assertTrue(all(item["exact_trusted_observation_count"] == 0 for item in candidates[3:]))
        lower_bounds = [item["formal_lcb_score"] for item in candidates[:3]]
        self.assertEqual(lower_bounds, sorted(lower_bounds, reverse=True))

    def test_v37_fingerprints_are_new_unique_and_noncorner(self) -> None:
        result = self.build()
        historical = builder._historical_fingerprint_ids(result["source_batches"])
        fingerprints = [item["three_level_fingerprint"]["id"] for item in result["top5"]]
        self.assertEqual(len(set(fingerprints)), 5)
        self.assertFalse(set(fingerprints).intersection(historical))
        self.assertTrue(all(item["boundary_corner"] is False for item in result["top5"]))

    def test_probe_rows_do_not_affect_drift_calibration(self) -> None:
        selection = builder.v6._load(SELECTION)
        ledger = builder.v6._load(LEDGER)
        expected = builder._drift_calibration(selection, ledger)
        modified = copy.deepcopy(ledger)
        for item in modified["successful"]:
            if int(item["rank"]) > 3:
                item["actual_score"] = 1_000_000.0
                item["actual_resource"] = {name: 1_000_000.0 for name in builder.RESOURCES}
        self.assertEqual(builder._drift_calibration(selection, modified), expected)

    def test_actual_postrun_audit_quarantines_old_summary_parser_defect(self) -> None:
        result = audit_module.audit(
            SELECTION,
            LEDGER,
            RUN_ROOT / "events.jsonl",
            RUN_ROOT / "summary.json",
            FORMAL,
            FORMAL_SHA,
        )
        self.assertTrue(result["accounting"]["passed"])
        self.assertEqual(result["accounting"]["physical_calls"], 5)
        self.assertEqual(result["accounting"]["valid_replays"], 5)
        self.assertEqual(result["accounting"]["technical_failures"], 0)
        self.assertEqual(result["accounting"]["canonical_invoked_ranks"], [1, 2, 3, 4, 5])
        self.assertTrue(result["accounting"]["runner_summary_invoked_ranks_mismatch"])
        self.assertFalse(result["formal_entry_eligible"])
        self.assertLess(result["quality"]["trusted_spearman"], 0.0)
        self.assertEqual(result["quality"]["actual_best_predicted_rank"], 4)

    def test_runner_accepts_v37_selection_in_zero_call_validation(self) -> None:
        result = self.build()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            selection_path = root / "top5.json"
            selection_path.write_text(json.dumps(result, sort_keys=True) + "\n", encoding="utf-8")
            analysis, generation_root, candidates, provenance = runner.validate_generation_and_top5(
                selection_path,
                root / "top5_real_replay_round15_zero_call_preflight",
            )
            self.assertEqual(generation_root, root)
            self.assertEqual(analysis["selection_contract"], builder.SELECTION_CONTRACT)
            self.assertEqual(len(candidates), 5)
            self.assertEqual(provenance["selection_contract"], builder.SELECTION_CONTRACT)

    def _synthetic_postrun(self, root: Path) -> tuple[Path, Path, Path, Path, Path, str]:
        selection = self.build()
        selection_path = root / "top5.json"
        ledger_path = root / "ledger.json"
        events_path = root / "events.jsonl"
        summary_path = root / "summary.json"
        formal_path = root / "formal.json"
        successful = []
        events = []
        for candidate in selection["top5"]:
            rank = int(candidate["rank"])
            actual_score = float(candidate["predicted_score"]) - rank * 0.001
            result = {
                "rank": rank,
                "attempt": 1,
                "actual_score": actual_score,
                "actual_resource": copy.deepcopy(candidate["predicted_resource"]),
                "workload_plan_fingerprint": candidate["workload_plan_fingerprint"],
                "three_level_fingerprint": copy.deepcopy(candidate["three_level_fingerprint"]),
                "measurement_protocol": {
                    "stable": True,
                    "correctness_passed": True,
                    "technical_failure": False,
                },
            }
            successful.append(result)
            for name in (
                "tpcc_gradient_real_replay_started",
                "tpcc_gradient_real_replay_workload_invocation_reserved",
                "tpcc_gradient_real_replay_complete",
            ):
                events.append({"event": name, "rank": rank, "attempt": 1})
        events.append(
            {
                "event": "tpcc_gradient_baseline_restore_finally",
                "baseline_params_restored": True,
                "baseline_snapshot_restored": True,
            }
        )
        ledger = {
            "real_workload_invocation_count": 5,
            "successful": successful,
            "technical_failures": [],
            "preworkload_failures": [],
        }
        summary = {
            "successful_real_replay_count": 5,
            "technical_failure_count": 0,
            "preworkload_failure_count": 0,
            "real_workload_invocation_count": 5,
            "invoked_ranks": [1, 2, 3, 4, 5],
            "baseline_params_restored": True,
            "baseline_snapshot_restored": True,
            "accepted_count_delta": 0,
            "incumbent_updated": False,
            "range_updated": False,
            "gradient_update_applied": False,
            "state_commit_eligible": False,
            "fatal_error": None,
        }
        formal = {
            "accepted_count": 0,
            "formal_physical_call_count": 0,
            "formal_valid_replay_count": 0,
            "formal_technical_failure_count": 0,
            "incumbent_score": 3.9894795578792417,
        }
        selection_path.write_text(json.dumps(selection) + "\n", encoding="utf-8")
        ledger_path.write_text(json.dumps(ledger) + "\n", encoding="utf-8")
        events_path.write_text(
            "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
        )
        summary_path.write_text(json.dumps(summary) + "\n", encoding="utf-8")
        formal_path.write_text(json.dumps(formal) + "\n", encoding="utf-8")
        return (
            selection_path,
            ledger_path,
            events_path,
            summary_path,
            formal_path,
            strict_audit.durable._sha256(formal_path),
        )

    def test_strict_v37_gate_passes_only_complete_stable_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._synthetic_postrun(Path(temporary))
            result = strict_audit.audit_v37(*paths)
            self.assertTrue(result["passed"])
            self.assertTrue(result["formal_start_authorized"])
            self.assertTrue(all(result["quality_gate"]["checks"].values()))

            ledger = json.loads(paths[1].read_text(encoding="utf-8"))
            ledger["successful"][3]["actual_score"] = 100.0
            paths[1].write_text(json.dumps(ledger) + "\n", encoding="utf-8")
            failed = strict_audit.audit_v37(*paths)
            self.assertFalse(failed["passed"])
            self.assertFalse(
                failed["quality_gate"]["checks"]["actual_best_in_predicted_top3"]
            )

    def test_strict_gate_accepts_v38_contract_without_weakening_checks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._synthetic_postrun(Path(temporary))
            selection = json.loads(paths[0].read_text(encoding="utf-8"))
            selection["selection_contract"] = (
                "tpcc_v38_fresh_unique_no_retry_trusted3_probe2_v1"
            )
            selection["v38_build_contract"] = {
                "five_new_noncorner_fingerprints": True,
                "same_rank_retry_allowed": False,
                "technical_call_advances_to_next_unique_rank": True,
            }
            paths[0].write_text(json.dumps(selection) + "\n", encoding="utf-8")
            result = strict_audit.audit_v37(*paths)
            self.assertTrue(result["passed"])
            self.assertEqual(
                result["schema_version"],
                "tpcc_v38_strict_postrun_entry_gate_v1",
            )

    def test_formal_promotion_keeps_probe_evidence_out_of_acceptance(self) -> None:
        selection = self.build()
        successful = []
        for candidate in selection["top5"]:
            successful.append(
                {
                    "rank": candidate["rank"],
                    "actual_score": candidate["predicted_score"],
                    "actual_resource": copy.deepcopy(candidate["predicted_resource"]),
                    "workload_plan_fingerprint": candidate["workload_plan_fingerprint"],
                    "three_level_fingerprint": copy.deepcopy(candidate["three_level_fingerprint"]),
                }
            )
        ledger = {
            "real_workload_invocation_count": 5,
            "successful": successful,
            "technical_failures": [],
        }
        gate = {
            "passed": True,
            "formal_start_authorized": True,
            "selection_sha256": builder.v6._sha256(
                PROJECT
                / "history/tpcc_v37_exact_drift_package_20260803/top5.json"
            ),
        }
        promoted = handoff.promote(
            selection,
            ledger,
            gate,
            PROJECT / "history/tpcc_v37_exact_drift_package_20260803/top5.json",
            3.9894795578792417,
        )
        self.assertEqual(
            promoted["selection_contract"],
            "tpcc_v37_promoted_formal_exact_trusted3_probe2_v1",
        )
        self.assertFalse(promoted["probe_observations_affect_formal_acceptance"])
        rows = promoted["formal_acceptance_feasibility_gate"]["candidate_bounds"]
        self.assertTrue(all(row["feasible"] for row in rows[:3]))
        self.assertTrue(all(not row["feasible"] for row in rows[3:]))
        self.assertTrue(
            all(
                item["confidence"]["probe_rows_used_for_trusted_calibration"] == 0
                for item in promoted["top5"][:3]
            )
        )
        self.assertEqual(
            formal_runner.validate_formal_feasibility_gate(
                promoted,
                promoted["formal_acceptance_feasibility_gate"],
                3.9894795578792417,
            ),
            [1, 2, 3],
        )


if __name__ == "__main__":
    unittest.main()

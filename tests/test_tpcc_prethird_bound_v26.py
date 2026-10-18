from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import tpcc_autonomous_target10_controller_v25 as v25
from tools import tpcc_autonomous_target10_controller_v26_prethird_bound as v26


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def candidate(rank: int, score: float) -> dict:
    role = "trusted" if rank <= 3 else "uncertain_probe"
    fingerprint = f"three-level-sha256:{rank:064x}"
    plan = f"ratio-plan-sha256:{rank:064x}"
    return {
        "rank": rank,
        "selection_role": role,
        "independent_actual_scores": [score],
        "workload_plan_fingerprint": plan,
        "three_level_fingerprint": {"id": fingerprint},
    }


def build_fixture(
    root: Path,
    scores: list[tuple[float, float]],
) -> tuple[dict, Path]:
    selection_path = root / "selection.json"
    ledger_path = root / "ledger.json"
    formal_state_path = root / "formal_state.json"
    selection = {
        "maximum_real_workload_invocations": 5,
        "qps_role": "outer_audit_only_not_inner_score",
        "top5": [
            candidate(rank, pair[0])
            for rank, pair in enumerate(scores, 1)
        ],
    }
    ledger = {
        "real_workload_invocation_count": 5,
        "technical_failures": [],
        "successful": [],
    }
    for rank, pair in enumerate(scores, 1):
        item = selection["top5"][rank - 1]
        ledger["successful"].append(
            {
                "rank": rank,
                "actual_score": pair[1],
                "workload_plan_fingerprint": item[
                    "workload_plan_fingerprint"
                ],
                "three_level_fingerprint": copy.deepcopy(
                    item["three_level_fingerprint"]
                ),
                "measurement_protocol": {
                    "stable": True,
                    "correctness_passed": True,
                    "technical_failure": False,
                },
            }
        )
    formal = {
        "schema_version": "tpcc_formal_target10_state_v1",
        "status": "ready",
        "accepted_count": 2,
        "incumbent_score": 4.7,
    }
    write_json(selection_path, selection)
    write_json(ledger_path, ledger)
    write_json(formal_state_path, formal)
    audit = {
        "mode": "diagnostic_validation",
        "clean": True,
        "physical_calls": 5,
        "valid_replays": 5,
        "technical_failures": 0,
        "minimum_total_exact_observation_count": 2,
        "maximum_total_exact_observation_count": 2,
        "selection_path": str(selection_path),
        "selection_sha256": v25.sha256(selection_path),
        "ledger_path": str(ledger_path),
        "ledger_sha256": v25.sha256(ledger_path),
    }
    return audit, formal_state_path


class TpccPrethirdBoundV26Test(unittest.TestCase):
    def test_infeasible_clean_two_observation_batch_reroutes_without_third(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audit, formal_state = build_fixture(
                Path(directory),
                [
                    (4.60, 4.65),
                    (4.55, 4.61),
                    (4.52, 4.62),
                    (4.50, 4.66),
                    (4.51, 4.63),
                ],
            )
            gate: dict = {"passed": False}
            with mock.patch.object(v25, "validate_selection"), mock.patch.object(
                v26,
                "_FORMAL_STATE_PATH",
                formal_state,
            ):
                mode = v26.choose_next_mode_v26([audit], gate)

            self.assertEqual(mode, "diagnostic")
            proof = gate["prethird_acceptance_feasibility_gate"]
            self.assertFalse(proof["passed"])
            self.assertEqual(proof["physical_workload_calls_added"], 0)
            self.assertFalse(proof["formal_state_mutated"])
            self.assertFalse(proof["manual_candidate_injection"])
            self.assertTrue(gate["prethird_zero_call_reroute"])

    def test_feasible_candidate_keeps_mandatory_third_observation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audit, formal_state = build_fixture(
                Path(directory),
                [
                    (4.60, 4.75),
                    (4.55, 4.61),
                    (4.52, 4.62),
                    (4.50, 4.66),
                    (4.51, 4.63),
                ],
            )
            gate: dict = {"passed": False}
            with mock.patch.object(v25, "validate_selection"), mock.patch.object(
                v26,
                "_FORMAL_STATE_PATH",
                formal_state,
            ), mock.patch.object(
                v26,
                "_ORIGINAL_CHOOSE_NEXT_MODE",
                return_value="diagnostic_validation",
            ):
                mode = v26.choose_next_mode_v26([audit], gate)

            self.assertEqual(mode, "diagnostic_validation")
            proof = gate["prethird_acceptance_feasibility_gate"]
            self.assertTrue(proof["passed"])
            self.assertEqual(proof["feasible_ranks"], [1])

    def test_non_boundary_audit_defers_to_existing_controller(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audit, formal_state = build_fixture(
                Path(directory),
                [(4.6, 4.65)] * 5,
            )
            audit["maximum_total_exact_observation_count"] = 3
            with mock.patch.object(
                v26,
                "_FORMAL_STATE_PATH",
                formal_state,
            ), mock.patch.object(
                v26,
                "_ORIGINAL_CHOOSE_NEXT_MODE",
                return_value="formal",
            ):
                mode = v26.choose_next_mode_v26(
                    [audit],
                    {"passed": True},
                )
            self.assertEqual(mode, "formal")

    def test_sealed_evidence_sha_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audit, formal_state = build_fixture(
                Path(directory),
                [(4.6, 4.65)] * 5,
            )
            audit["selection_sha256"] = "0" * 64
            with mock.patch.object(v25, "validate_selection"):
                with self.assertRaisesRegex(RuntimeError, "SHA mismatch"):
                    v26.build_prethird_feasibility_gate(
                        audit,
                        formal_state,
                    )

    def test_candidate_identity_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audit, formal_state = build_fixture(
                Path(directory),
                [(4.6, 4.65)] * 5,
            )
            ledger_path = Path(audit["ledger_path"])
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            ledger["successful"][0]["workload_plan_fingerprint"] = "wrong"
            write_json(ledger_path, ledger)
            audit["ledger_sha256"] = v25.sha256(ledger_path)
            with mock.patch.object(v25, "validate_selection"):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "candidate evidence mismatch",
                ):
                    v26.build_prethird_feasibility_gate(
                        audit,
                        formal_state,
                    )


if __name__ == "__main__":
    unittest.main()

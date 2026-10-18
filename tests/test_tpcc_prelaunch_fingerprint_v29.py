from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools import tpcc_autonomous_target10_controller_v29_prelaunch_fingerprint as v29


class GlobalPrelaunchFingerprintGateTest(unittest.TestCase):
    def test_collects_every_structured_history_source(self) -> None:
        selection = {
            "top5": [
                {"three_level_fingerprint": {"id": "id:a"}},
                {"three_level_fingerprint": {"id": "id:b"}},
            ]
        }
        state = {
            "audits": [
                {
                    "fingerprint_ids": ["id:a"],
                    "physical_fingerprint_ids": ["id:c"],
                }
            ],
            "quarantined_fingerprints": ["id:b"],
        }
        gate = v29.global_prelaunch_fingerprint_gate(
            selection,
            "diagnostic_validation",
            state,
        )
        self.assertFalse(gate["passed"])
        self.assertEqual(gate["collision_ids"], ["id:a", "id:b"])
        self.assertEqual(
            gate["historical_fingerprint_ids"],
            ["id:a", "id:b", "id:c"],
        )
        self.assertEqual(gate["workload_calls_added"], 0)
        self.assertFalse(gate["manual_candidate_injection"])

    def test_passes_a_fully_novel_selection(self) -> None:
        selection = {
            "top5": [
                {"three_level_fingerprint": {"id": f"new:{rank}"}}
                for rank in range(1, 6)
            ]
        }
        state = {"audits": [{"fingerprint_ids": ["old:1"]}]}
        gate = v29.global_prelaunch_fingerprint_gate(
            selection,
            "diagnostic",
            state,
        )
        self.assertTrue(gate["passed"])
        self.assertEqual(gate["collision_ids"], [])

    def test_reads_and_sha_verifies_historical_source_selections(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source_path = Path(temporary) / "top5.json"
            source_path.write_text(
                json.dumps(
                    {
                        "top5": [
                            {
                                "three_level_fingerprint": {
                                    "id": "source:id"
                                }
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            source_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()
            state = {
                "sources": [
                    {
                        "selection_path": str(source_path),
                        "selection_sha256": source_sha256,
                    }
                ]
            }
            selection = {
                "top5": [
                    {
                        "three_level_fingerprint": {
                            "id": "source:id"
                        }
                    }
                ]
            }
            gate = v29.global_prelaunch_fingerprint_gate(
                selection,
                "diagnostic_validation",
                state,
            )
            self.assertEqual(gate["collision_ids"], ["source:id"])

            state["sources"][0]["selection_sha256"] = "0" * 64
            with self.assertRaisesRegex(RuntimeError, "SHA check"):
                v29.global_prelaunch_fingerprint_gate(
                    selection,
                    "diagnostic_validation",
                    state,
                )

    def test_detects_the_sealed_validation151_collision(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        selection_path = Path(
            "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
            "tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/"
            "formal10_final_algorithm_fresh10_v29_20260731T2005/"
            "autonomous_runs_v32/autonomous_diagnostic_validation_151/"
            "top5.json"
        )
        state_path = project_root / (
            "history/tpcc_final_algorithm_fresh10_v29_20260731T2005/"
            "autonomous_controller_v32/controller_state.json"
        )
        if not selection_path.is_file() or not state_path.is_file():
            self.skipTest("sealed validation151 fixture is unavailable")
        selection = json.loads(selection_path.read_text(encoding="utf-8"))
        state = json.loads(state_path.read_text(encoding="utf-8"))
        gate = v29.global_prelaunch_fingerprint_gate(
            selection,
            "diagnostic_validation",
            state,
        )
        self.assertIn(
            "three-level-sha256:3318bd683824b11c5d2312795b648cad19c42e8191006f4384ed9a3da4bbaa96",
            gate["collision_ids"],
        )


if __name__ == "__main__":
    unittest.main()

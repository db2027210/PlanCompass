from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from tools import handover_tpch_v33_to_v34_after_cycle14 as handover


def write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class TpchV34HandoverTests(unittest.TestCase):
    def test_boundary_requires_exact_completed_cycle(self) -> None:
        state = {
            "schema_version": "tpch_fresh10_target10_loop_v33",
            "controller_version": 33,
            "status": "reexplore_prethird_acceptance_impossible",
            "accepted_count": 0,
            "next_cycle": 15,
            "cycles": [
                {
                    "cycle": 14,
                    "status": "reexplore_prethird_acceptance_impossible",
                    "accepted_before": 0,
                    "accepted_after": 0,
                    "formal_started": False,
                    "formal_state_mutated": False,
                }
            ],
        }
        self.assertTrue(handover.boundary_matches(state, 14, 0))
        changed = dict(state)
        changed["next_cycle"] = 16
        self.assertFalse(handover.boundary_matches(changed, 14, 0))

    def test_clean_diagnostic_checks_summary_neutrality(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = {
                "accepted_count": 0,
                "status": "complete",
                "physical_call_count": 5,
                "valid_replay_count": 5,
                "technical_failure_count": 0,
                "baseline_restored": True,
            }
            summary = {
                **state,
                "accepted_update_applied": False,
                "incumbent_update_applied": False,
                "range_update_applied": False,
                "gradient_update_applied": False,
            }
            write(root / "diagnostic_state.json", state)
            write(root / "diagnostic_summary.json", summary)
            self.assertTrue(handover.clean_diagnostic_state(root, 0))
            summary["accepted_update_applied"] = True
            write(root / "diagnostic_summary.json", summary)
            self.assertFalse(handover.clean_diagnostic_state(root, 0))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from tools.xp_v34_process_guard import active_matches


PATTERNS = (
    r"resume_xp_fresh0_fixed_contract_target10_v3[34]\.py",
    r"run_xp_incumbent_revalidation_once\.py",
)


class ProcessGuardTests(unittest.TestCase):
    def test_zombie_never_blocks(self) -> None:
        processes = [
            {
                "pid": 3150748,
                "state": "Z",
                "command": "python resume_xp_fresh0_fixed_contract_target10_v34.py",
            }
        ]
        self.assertEqual(active_matches(processes, PATTERNS), [])

    def test_live_states_block(self) -> None:
        for state in ("S", "R", "D", "T"):
            with self.subTest(state=state):
                matches = active_matches(
                    [
                        {
                            "pid": 10,
                            "state": state,
                            "command": (
                                "python resume_xp_fresh0_fixed_contract_"
                                "target10_v34.py"
                            ),
                        }
                    ],
                    PATTERNS,
                )
                self.assertEqual(len(matches), 1)
                self.assertEqual(matches[0]["state"], state)

    def test_unrelated_live_process_does_not_block(self) -> None:
        processes = [
            {"pid": 11, "state": "R", "command": "postgres: checkpointer"}
        ]
        self.assertEqual(active_matches(processes, PATTERNS), [])


if __name__ == "__main__":
    unittest.main()

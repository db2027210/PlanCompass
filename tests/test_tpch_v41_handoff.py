from __future__ import annotations

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from tools import tpch_v41_preformal_handoff as handoff
from tests.test_tpch_v38_bounded_retest import candidate


def write_clean_diagnostic(root: Path, selection: Path) -> None:
    selection_sha = handoff.sha256(selection)
    state = {
        "status": "complete",
        "diagnostic_only": True,
        "accepted_count": 0,
        "physical_call_count": 5,
        "valid_replay_count": 5,
        "technical_failure_count": 0,
        "baseline_restored": True,
        "accepted_update_allowed": False,
        "incumbent_update_allowed": False,
        "range_update_allowed": False,
        "gradient_update_allowed": False,
        "selection_sha256": selection_sha,
    }
    summary = {
        "accepted_count": 0,
        "physical_call_count": 5,
        "valid_replay_count": 5,
        "technical_failure_count": 0,
        "baseline_restored": True,
        "accepted_update_applied": False,
        "incumbent_update_applied": False,
        "range_update_applied": False,
        "gradient_update_applied": False,
        "selection_sha256": selection_sha,
    }
    root.mkdir(parents=True)
    (root / "diagnostic_state.json").write_text(
        json.dumps(state), encoding="utf-8"
    )
    (root / "diagnostic_summary.json").write_text(
        json.dumps(summary), encoding="utf-8"
    )


class TpchV41HandoffTests(unittest.TestCase):
    def test_roots_and_acceptance_algorithm_are_distinct(self) -> None:
        self.assertNotEqual(handoff.V40_ROOT, handoff.V41_ROOT)
        self.assertNotEqual(handoff.V40_COUNTER, handoff.V41_COUNTER)
        self.assertEqual(
            handoff.EXPECTED[handoff.FORMAL_RUNNER],
            "2e8daea587b6d158269dbbe8767b086c0882aa0cd92650a3860c39f38e649eeb",
        )

    def test_clean_diagnostic_requires_exact_state_neutral_5_5_0(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selection = root / "top5.json"
            selection.write_text(
                json.dumps({"top5": [candidate(i) for i in range(1, 6)]}),
                encoding="utf-8",
            )
            run = root / "run"
            write_clean_diagnostic(run, selection)
            result = handoff.clean_diagnostic(selection, run)
            self.assertEqual(result["state"]["physical_call_count"], 5)

            summary = json.loads(
                (run / "diagnostic_summary.json").read_text(encoding="utf-8")
            )
            summary["incumbent_update_applied"] = True
            (run / "diagnostic_summary.json").write_text(
                json.dumps(summary), encoding="utf-8"
            )
            with self.assertRaisesRegex(RuntimeError, "state-neutral 5/5/0"):
                handoff.clean_diagnostic(selection, run)

    def test_selection_history_includes_only_explicit_adopted_and_v41(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            v40 = root / "v40.json"
            v39 = root / "v39.json"
            loop = root / "target10_loop_v41"
            cycle = loop / "cycle_004"
            cycle.mkdir(parents=True)
            current = cycle / "probe_top5.a.json"
            payload = {"top5": [candidate(i) for i in range(1, 6)]}
            for path in (v40, v39, current):
                path.write_text(json.dumps(payload), encoding="utf-8")
            paths = list(
                handoff.selection_paths(
                    lambda _root: iter(()), root, loop, [v40]
                )
            )
        self.assertEqual(paths, [v40.resolve(), current.resolve()])
        self.assertNotIn(v39.resolve(), paths)

    def test_zero_call_stage_rejects_every_replay_runner(self) -> None:
        called = []
        common = SimpleNamespace(
            WORKLOAD=Path("/tmp/workload"),
            DIAGNOSTIC_RUNNER=Path("/tmp/diagnostic"),
            FORMAL_RUNNER=Path("/tmp/formal"),
            run=lambda argv, log, environment: called.append(argv),
        )
        with self.assertRaisesRegex(RuntimeError, "attempted a replay runner"):
            handoff.run_zero_call(
                common,
                ["python", "/tmp/diagnostic"],
                Path("log"),
                {},
            )
        handoff.run_zero_call(
            common,
            ["python", "/tmp/analyzer"],
            Path("log"),
            {},
        )
        self.assertEqual(called, [["python", "/tmp/analyzer"]])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from unittest import mock
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace

from tools import tpch_exact_regime_neighbor_expand_v5_bounded as expander
from tools import tpch_fresh0_target10_v38_bounded_retest as controller


PARAMS = {
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "cpu_tuple_cost": 0.01,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
    "random_page_cost": 4.0,
    "seq_page_cost": 1.0,
}
RANGES = {
    "cpu_index_tuple_cost": {"min_value": 0.0005, "max_value": 0.05},
    "cpu_operator_cost": {"min_value": 0.00025, "max_value": 0.025},
    "cpu_tuple_cost": {"min_value": 0.001, "max_value": 0.1},
    "parallel_setup_cost": {"min_value": 0.0, "max_value": 10000.0},
    "parallel_tuple_cost": {"min_value": 0.001, "max_value": 1.0},
    "random_page_cost": {"min_value": 0.1, "max_value": 100.0},
    "seq_page_cost": {"min_value": 0.1, "max_value": 100.0},
}


class TpchV38BoundedExpansionTests(unittest.TestCase):
    def test_first_retest_bypasses_explain_with_empty_finite_pool(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = root / "evidence.json"
            resume = root / "resume.json"
            evidence.write_text("{}", encoding="utf-8")
            resume.write_text("{}", encoding="utf-8")

            def atomic_json(path: Path, value: dict) -> None:
                path.write_text(json.dumps(value), encoding="utf-8")

            module = SimpleNamespace(
                sha256=controller.sha256,
            )
            output = controller.write_first_retest_bypass(
                module,
                SimpleNamespace(atomic_json=atomic_json),
                root / "cycle_004",
                evidence,
                resume,
            )
            payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(payload["new_explain_calls"], 0)
        self.assertEqual(payload["ranked_candidates"], [])

    def test_outer_expansion_process_timeout_is_hard(self) -> None:
        logs = []
        with mock.patch.object(
            controller.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired("expander", 1860),
        ) as run:
            with self.assertRaisesRegex(RuntimeError, "1860-second"):
                controller.run_with_expansion_timeout(
                    lambda *_args: self.fail("unbounded runner was used"),
                    lambda path, value: logs.append((path, value)),
                    Path("/tmp/expander.py"),
                    ["python", "/tmp/expander.py"],
                    Path("expand.log"),
                    {},
                )
        self.assertEqual(run.call_args.kwargs["timeout"], 1860)
        self.assertEqual(logs, [(Path("expand.log"), "")])

    def test_default_budgets_are_valid_and_cannot_be_relaxed(self) -> None:
        expander.validate_budgets(
            expander.DEFAULT_MAXIMUM_WALL_SECONDS,
            expander.DEFAULT_MAXIMUM_EXPLAIN_INVOCATIONS,
            expander.DEFAULT_MAXIMUM_CANDIDATE_ESTIMATIONS,
            expander.DEFAULT_EXPLAIN_TIMEOUT_SECONDS,
        )
        with self.assertRaisesRegex(RuntimeError, "may not be relaxed"):
            expander.validate_budgets(
                expander.DEFAULT_MAXIMUM_WALL_SECONDS + 1,
                expander.DEFAULT_MAXIMUM_EXPLAIN_INVOCATIONS,
                expander.DEFAULT_MAXIMUM_CANDIDATE_ESTIMATIONS,
                expander.DEFAULT_EXPLAIN_TIMEOUT_SECONDS,
            )
        with self.assertRaisesRegex(RuntimeError, "may not be relaxed"):
            expander.validate_budgets(
                expander.DEFAULT_MAXIMUM_WALL_SECONDS,
                expander.DEFAULT_MAXIMUM_EXPLAIN_INVOCATIONS,
                expander.DEFAULT_MAXIMUM_CANDIDATE_ESTIMATIONS + 1,
                expander.DEFAULT_EXPLAIN_TIMEOUT_SECONDS,
            )

    def test_coordinate_generation_is_finite_and_has_top5_capacity(self) -> None:
        first = expander.incumbent_coordinate_params(PARAMS, RANGES)
        second = expander.incumbent_coordinate_params(PARAMS, RANGES)
        self.assertEqual(first, second)
        self.assertGreaterEqual(len(first), 5)
        self.assertLessEqual(
            expander.DEFAULT_MAXIMUM_CANDIDATE_ESTIMATIONS,
            len(first),
        )
        parameter_keys = {
            expander.canonical_sha256(candidate_params)
            for _, _, candidate_params in first
        }
        self.assertEqual(len(parameter_keys), len(first))


if __name__ == "__main__":
    unittest.main()

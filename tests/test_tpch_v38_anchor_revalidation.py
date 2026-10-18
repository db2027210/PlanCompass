from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

from tools import run_tpch_current_anchor_revalidation_v38 as anchor
from tools import reseal_tpch_v38_anchor_state_v2 as resealer
from tools import run_tpch_formal_exact_plan_regime_batch as formal_runner


PARAMS = {
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "cpu_tuple_cost": 0.01,
    "parallel_setup_cost": 1000,
    "parallel_tuple_cost": 0.1,
    "random_page_cost": 4,
    "seq_page_cost": 1,
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TpchV38AnchorTests(unittest.TestCase):
    def test_build_anchor_state_is_fresh0_and_state_neutral(self) -> None:
        source = {
            "status": "complete",
            "accepted_count": 0,
            "range_state": {name: {"min_value": 0, "max_value": 1} for name in PARAMS},
        }
        observations = [
            {
                "ordinal": 1,
                "actual_score": 4.0,
                "actual_resource": {"cpu": 0.4, "io": 16.0, "memory": 1.2},
                "baseline_restored_after_observation": True,
            },
            {
                "ordinal": 2,
                "actual_score": 4.02,
                "actual_resource": {"cpu": 0.42, "io": 16.1, "memory": 1.22},
                "baseline_restored_after_observation": True,
            },
        ]
        result = anchor.build_anchor_state(
            source, PARAMS, observations, {}, 0.05, 1.5
        )
        self.assertEqual(result["accepted_count"], 0)
        self.assertEqual(result["physical_call_count"], 0)
        self.assertAlmostEqual(result["incumbent_score"], 4.0)
        self.assertEqual(
            result["incumbent_resource"], observations[0]["actual_resource"]
        )
        audit = result["current_epoch_anchor_revalidation"]
        self.assertEqual(audit["diagnostic_physical_call_count"], 2)
        self.assertTrue(audit["state_neutral"])
        self.assertFalse(audit["formal_source_mutated"])
        self.assertEqual(
            audit["anchor_score_policy"],
            "lower_observed_score_exact_resource_v1",
        )

    def test_unstable_anchor_fails_closed(self) -> None:
        observations = [
            {
                "ordinal": 1,
                "actual_score": 4.0,
                "actual_resource": {"cpu": 0.4, "io": 16.0, "memory": 1.2},
                "baseline_restored_after_observation": True,
            },
            {
                "ordinal": 2,
                "actual_score": 4.2,
                "actual_resource": {"cpu": 0.4, "io": 16.0, "memory": 1.2},
                "baseline_restored_after_observation": True,
            },
        ]
        with self.assertRaisesRegex(RuntimeError, "score span is unstable"):
            anchor.build_anchor_state(
                {"range_state": {}}, PARAMS, observations, {}, 0.05, 1.5
            )

    def test_runner_makes_two_calls_and_preserves_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "formal.json"
            runner = root / "runner.py"
            workload = root / "workload.sh"
            workload_driver = root / "workload_driver.py"
            dependencies = [root / f"dependency_{index}.py" for index in range(3)]
            config = root / "config.json"
            counter = root / "accepted.txt"
            output = root / "output"
            source.write_text(
                json.dumps(
                    {
                        "status": "complete",
                        "accepted_count": 0,
                        "baseline_restored": True,
                        "incumbent_params": PARAMS,
                        "range_state": {
                            name: {"min_value": 0, "max_value": 1}
                            for name in PARAMS
                        },
                    }
                ),
                encoding="utf-8",
            )
            runner.write_text(
                "BASELINE_PARAMS = " + repr(PARAMS) + "\n"
                "calls = 0\n"
                "def apply_params(container, database, params):\n"
                "    return {'ok': True}\n"
                "def run_workload(workload, root, timeout):\n"
                "    global calls\n"
                "    calls += 1\n"
                "    return 0, str(calls)\n"
                "def extract_payload(output):\n"
                "    return int(output)\n"
                "def classify_payload(payload, returncode, capability):\n"
                "    score = 4.596539792387544\n"
                "    resource = {'cpu': .4, 'io': 16.0, 'memory': 1.2}\n"
                "    utilization = {'cpu': .4, 'io': .4, 'memory': .8}\n"
                "    return resource, utilization, score\n",
                encoding="utf-8",
            )
            workload.write_text("offline fixture\n", encoding="utf-8")
            workload_driver.write_text("# fixture\n", encoding="utf-8")
            for dependency in dependencies:
                dependency.write_text("# fixture\n", encoding="utf-8")
            config.write_text(
                json.dumps(
                    {"capability": {"cpu": 1.0, "io": 40.0, "memory": 1.5}}
                ),
                encoding="utf-8",
            )
            counter.write_text("0\n", encoding="utf-8")
            source_before = digest(source)
            counter_before = digest(counter)
            old_argv = sys.argv
            try:
                sys.argv = [
                    "anchor",
                    "--source-formal-state",
                    str(source),
                    "--source-formal-state-sha256",
                    digest(source),
                    "--accepted-counter",
                    str(counter),
                    "--runner-module",
                    str(runner),
                    "--runner-module-sha256",
                    digest(runner),
                    "--workload",
                    str(workload),
                    "--workload-sha256",
                    digest(workload),
                    "--workload-driver",
                    str(workload_driver),
                    "--workload-driver-sha256",
                    digest(workload_driver),
                    *[
                        value
                        for dependency in dependencies
                        for value in (
                            "--runtime-dependency",
                            f"{dependency}::{digest(dependency)}",
                        )
                    ],
                    "--config",
                    str(config),
                    "--config-sha256",
                    digest(config),
                    "--container",
                    "pg-test",
                    "--database",
                    "tpch",
                    "--output",
                    str(output),
                    "--timeout-seconds",
                    "60",
                    "--armed",
                ]
                self.assertEqual(anchor.main(), 0)
            finally:
                sys.argv = old_argv
            state = json.loads(
                (output / "diagnostic_state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(state["physical_call_count"], 2)
            self.assertEqual(state["valid_replay_count"], 2)
            self.assertEqual(state["technical_failure_count"], 0)
            self.assertTrue(state["baseline_restored"])
            self.assertEqual(digest(source), source_before)
            self.assertEqual(digest(counter), counter_before)
            raw_anchor = output / "current_anchor_state.json"
            corrected_anchor = output / "current_anchor_state.v2.json"
            old_argv = sys.argv
            try:
                sys.argv = [
                    "reseal",
                    "--source-formal-state",
                    str(source),
                    "--source-formal-state-sha256",
                    digest(source),
                    "--diagnostic-state",
                    str(output / "diagnostic_state.json"),
                    "--diagnostic-state-sha256",
                    digest(output / "diagnostic_state.json"),
                    "--raw-anchor-state",
                    str(raw_anchor),
                    "--raw-anchor-state-sha256",
                    digest(raw_anchor),
                    "--anchor-builder",
                    str(Path(anchor.__file__)),
                    "--anchor-builder-sha256",
                    digest(Path(anchor.__file__)),
                    "--output",
                    str(corrected_anchor),
                ]
                self.assertEqual(resealer.main(), 0)
            finally:
                sys.argv = old_argv
            corrected = json.loads(corrected_anchor.read_text(encoding="utf-8"))
            recomputed, _ = formal_runner.score_resource(
                corrected["incumbent_resource"],
                {"cpu": 1.0, "io": 40.0, "memory": 1.5},
            )
            self.assertAlmostEqual(recomputed, corrected["incumbent_score"], places=9)
            self.assertEqual(
                corrected["current_epoch_anchor_revalidation"][
                    "new_physical_calls"
                ],
                0,
            )


if __name__ == "__main__":
    unittest.main()

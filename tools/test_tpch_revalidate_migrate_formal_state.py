from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).with_name(
    "tpch_revalidate_migrate_formal_state.py"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TpchProtocolMigrationTest(unittest.TestCase):
    def write_json(self, path: Path, value: object) -> None:
        path.write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def make_fixture(
        self, root: Path, *, technical_failure: bool
    ) -> tuple[list[str], Path]:
        formal_state = root / "formal_state.json"
        config = root / "config.json"
        workload = root / "workload.sh"
        driver = root / "driver.py"
        runner = root / "runner.py"
        output = root / "output"
        self.write_json(
            formal_state,
            {
                "schema_version": "tpch_exact_plan_regime_formal_state_v1",
                "status": "complete",
                "baseline_restored": True,
                "accepted_count": 1,
                "accepted_shrink_target": 10,
                "incumbent_score": 4.7,
                "incumbent_resource": {
                    "cpu": 1.0,
                    "io": 2.0,
                    "memory": 1.0,
                },
                "incumbent_params": {
                    "cpu_tuple_cost": 0.01,
                },
                "range_state": {
                    "cpu_tuple_cost": {
                        "min_value": 0.001,
                        "max_value": 0.1,
                        "transform": "log",
                    }
                },
            },
        )
        self.write_json(
            config,
            {
                "capability": {
                    "cpu": 22,
                    "io": 36,
                    "memory": 1.6111880540847778,
                }
            },
        )
        workload.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        driver.write_text("# pinned fake driver\n", encoding="utf-8")
        runner.write_text(
            "\n".join(
                [
                    "BASELINE_PARAMS = {'cpu_tuple_cost': 0.01}",
                    "def apply_params(container, database, params):",
                    "    return 'cpu_tuple_cost=0.01'",
                    "def run_workload(workload, point_root, timeout_seconds):",
                    "    point_root.mkdir(parents=True, exist_ok=True)",
                    "    (point_root / 'workload.log').write_text('ok')",
                    "    return (1, 'failed')"
                    if technical_failure
                    else "    return (0, 'ok')",
                    "def extract_payload(output):",
                    "    return {'status': 'complete'}",
                    "def classify_payload(payload, returncode, capability):",
                    "    if returncode != 0:",
                    "        raise RuntimeError('technical failure')",
                    (
                        "    return "
                        "({'cpu': 8.0, 'io': 15.0, 'memory': 1.4}, "
                        "{'cpu': 8.0/22, 'io': 15.0/36, "
                        "'memory': 1.4/capability['memory']}, 4.81)"
                    ),
                    "",
                ]
            ),
            encoding="utf-8",
        )
        command = [
            sys.executable,
            str(SCRIPT),
            "--formal-state",
            str(formal_state),
            "--formal-state-sha256",
            sha256(formal_state),
            "--runner-module",
            str(runner),
            "--runner-module-sha256",
            sha256(runner),
            "--workload",
            str(workload),
            "--workload-sha256",
            sha256(workload),
            "--workload-driver",
            str(driver),
            "--workload-driver-sha256",
            sha256(driver),
            "--config",
            str(config),
            "--config-sha256",
            sha256(config),
            "--container",
            "fake",
            "--database",
            "fake",
            "--output",
            str(output),
            "--armed",
        ]
        return command, formal_state

    def test_success_preserves_formal_optimizer_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            command, formal_state = self.make_fixture(
                root, technical_failure=False
            )
            before = json.loads(formal_state.read_text(encoding="utf-8"))
            completed = subprocess.run(
                command,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout)
            after = json.loads(formal_state.read_text(encoding="utf-8"))
            self.assertEqual(after["accepted_count"], before["accepted_count"])
            self.assertEqual(
                after["incumbent_params"], before["incumbent_params"]
            )
            self.assertEqual(after["range_state"], before["range_state"])
            self.assertEqual(after["incumbent_score"], 4.81)
            self.assertEqual(
                after["incumbent_resource"],
                {"cpu": 8.0, "io": 15.0, "memory": 1.4},
            )
            self.assertTrue(
                after["workload_protocol_migration"]["baseline_restored"]
            )
            backups = list(
                root.glob(
                    "formal_state.json.pre_sealed_v4_migration_*.bak"
                )
            )
            self.assertEqual(len(backups), 1)
            self.assertEqual(
                json.loads(backups[0].read_text(encoding="utf-8")),
                before,
            )

    def test_technical_failure_does_not_mutate_formal_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            command, formal_state = self.make_fixture(
                root, technical_failure=True
            )
            before_bytes = formal_state.read_bytes()
            completed = subprocess.run(
                command,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            self.assertEqual(completed.returncode, 2, completed.stdout)
            self.assertEqual(formal_state.read_bytes(), before_bytes)
            failure = json.loads(
                (root / "output" / "migration_failure_audit.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertFalse(failure["formal_state_mutated"])
            self.assertTrue(failure["baseline_restored"])
            self.assertEqual(failure["physical_call_count"], 1)
            self.assertEqual(failure["technical_failure_count"], 1)


if __name__ == "__main__":
    unittest.main()

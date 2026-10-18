#!/usr/bin/env python3
"""Tests immutable VM replay worker input snapshots."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "shell") not in sys.path:
    sys.path.insert(0, str(ROOT / "shell"))

from freeze_vm_replay_worker_config import freeze_worker  # noqa: E402


class FreezeVmReplayWorkerConfigTest(unittest.TestCase):
    def test_freezes_and_rewrites_all_effective_workload_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_text:
            project = Path(temp_text) / "project"
            source = project / "source"
            source.mkdir(parents=True)
            props = source / "base.props"
            props.write_text("xpRunMins=3\n", encoding="utf-8")
            statements = source / "base.toml"
            statements.write_text('[AP-6]\nsql = """select 6"""\n', encoding="utf-8")
            scenario = source / "scenario.json"
            scenario.write_text(
                json.dumps({"benchmark": {"workload_cmd": "unused"}}),
                encoding="utf-8",
            )
            worker = source / "worker.json"
            worker.write_text(
                json.dumps(
                    {
                        "scenario_configs": {"none": "source/scenario.json"},
                        "scenario_overrides": {
                            "none": {
                                "benchmark": {
                                    "workload_cmd": (
                                        "cd /tmp && bash hybench -t runxp "
                                        "-c source/base.props -f source/base.toml"
                                    )
                                }
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            output = Path(temp_text) / "snapshot" / "none"

            metadata = freeze_worker(
                project_root=project,
                source_worker=worker,
                target="none",
                output_dir=output,
            )

            frozen = json.loads((output / "worker_config.json").read_text(encoding="utf-8"))
            command = frozen["scenario_overrides"]["none"]["benchmark"]["workload_cmd"]
            self.assertEqual(str(output / "scenario_config.json"), frozen["scenario_configs"]["none"])
            self.assertIn(str(output / "source.props"), command)
            self.assertIn(str(output / "source_statements.toml"), command)
            self.assertEqual(str(props.resolve()), metadata["source_props"])
            self.assertEqual("xpRunMins=3\n", (output / "source.props").read_text(encoding="utf-8"))
            self.assertIn("select 6", (output / "source_statements.toml").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

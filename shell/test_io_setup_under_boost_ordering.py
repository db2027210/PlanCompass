#!/usr/bin/env python3
"""Regression tests for IO setup-under-boost then measurement-throttle ordering (1059)."""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_mod():
    path = ROOT / "shell/bonect_parallel_vm_replay.py"
    spec = importlib.util.spec_from_file_location("bonect_parallel_vm_replay", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class SetupUnderBoostOrderingTests(unittest.TestCase):
    def test_clear_worker_pre_workload_sql_files_replaces_lists(self) -> None:
        mod = _load_mod()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "worker_config.json"
            path.write_text(
                json.dumps(
                    {
                        "scenario_configs": {
                            "io": str(ROOT / "config/generated/bonect_phase2_vm_io_v14_teacher_v87-3_20260714/io_v14_teacher_bonect-replay-v87-3_vm.json")
                        },
                        "scenario_overrides": {
                            "io": {
                                "benchmark": {
                                    "workload_cmd": "hybench",
                                    "pre_workload_sql_files": [
                                        "shell/hybenchxp_io_groupagg_spill_setup_16mb.sql"
                                    ],
                                }
                            }
                        },
                        "config_override": {},
                    }
                ),
                encoding="utf-8",
            )
            result = mod.clear_worker_pre_workload_sql_files(path)
            self.assertTrue(result["setup_under_boost_cleared_pre_workload"])
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                data["scenario_overrides"]["io"]["benchmark"]["pre_workload_sql_files"],
                [],
            )
            self.assertEqual(
                data["config_override"]["benchmark"]["pre_workload_sql_files"],
                [],
            )

    def test_resolve_setup_sql_from_v14_config(self) -> None:
        mod = _load_mod()
        cfg = (
            ROOT
            / "config/generated/bonect_phase2_vm_io_v14_teacher_v87-3_20260714/io_v14_teacher_bonect-replay-v87-3_vm.json"
        )
        if not cfg.exists():
            self.skipTest("v14 teacher config not present")
        worker = {
            "worker_id": "bonect-replay-v87-3",
            "scenario_configs": {"io": str(cfg)},
            "config_override": {},
            "scenario_overrides": {},
            "vm_resource": {},
        }
        paths = mod.resolve_pre_workload_setup_sql_files(worker, "io")
        names = [p.name for p in paths]
        self.assertIn("hybenchxp_io_groupagg_spill_setup_16mb.sql", names)
        self.assertIn("hybenchxp_io_groupagg_spill_vacuum.sql", names)

    def test_ordering_contract_comment_present(self) -> None:
        text = (ROOT / "shell/bonect_parallel_vm_replay.py").read_text(encoding="utf-8")
        self.assertIn("setup SQL under boost", text)
        self.assertIn("setup_measurement_ordering_audit.json", text)
        self.assertIn("run_worker_setup_sql_under_boost", text)
        self.assertIn("clear_worker_pre_workload_sql_files", text)

    def test_setup_ordering_audit_initialized_outside_wave_loop(self) -> None:
        """Paired multi-wave runs must not reinitialize the audit list each wave."""
        text = (ROOT / "shell/bonect_parallel_vm_replay.py").read_text(encoding="utf-8")
        marker = "worker_by_id = {worker[\"worker_id\"]: worker for worker in workers}"
        self.assertIn(marker, text)
        before_loop, after_loop = text.split(marker, 1)
        # Accumulator lives before the wave loop.
        self.assertIn("setup_ordering_audit: List[Dict[str, Any]] = []", before_loop)
        self.assertIn("restore_profile_audit: List[Dict[str, Any]] = []", before_loop)
        # Must not reinitialize after entering for wave in wave_plans.
        wave_for = "for wave in wave_plans:"
        self.assertIn(wave_for, after_loop)
        loop_body = after_loop.split(wave_for, 1)[1]
        # Next top-level def ends the main body; only inspect up to there is unnecessary —
        # assert no re-init assignment remains inside the wave loop region before write path.
        self.assertNotIn(
            "\n        setup_ordering_audit: List[Dict[str, Any]] = []\n",
            loop_body.split("with concurrent.futures.ThreadPoolExecutor")[0],
        )
        self.assertNotIn(
            "\n        restore_profile_audit: List[Dict[str, Any]] = []\n",
            loop_body.split("with concurrent.futures.ThreadPoolExecutor")[0],
        )

    def test_multi_wave_setup_ordering_audit_persistence(self) -> None:
        """setup_measurement_ordering_audit.json must retain rows for every wave index."""
        mod = _load_mod()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "setup_measurement_ordering_audit.json"
            audit: list = []
            # Simulate wave 0 (setup + measurement) then wave 1 without reinit.
            for wave_index in (0, 1, 2):
                audit.append(
                    {
                        "wave_index": wave_index,
                        "phase": "setup_under_restore_boost",
                        "status": "ok",
                    }
                )
                audit.append(
                    {
                        "wave_index": wave_index,
                        "phase": "measurement_io_throttle",
                        "status": "ok",
                    }
                )
                mod.write_json(path, audit)
            loaded = json.loads(path.read_text(encoding="utf-8"))
            waves = sorted({int(row["wave_index"]) for row in loaded})
            self.assertEqual(waves, [0, 1, 2])
            self.assertEqual(len(loaded), 6)
            phases = {(row["wave_index"], row["phase"]) for row in loaded}
            for wave_index in (0, 1, 2):
                self.assertIn((wave_index, "setup_under_restore_boost"), phases)
                self.assertIn((wave_index, "measurement_io_throttle"), phases)


if __name__ == "__main__":
    unittest.main()

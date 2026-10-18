#!/usr/bin/env python3
"""Offline launch-chain regression checks for staged Algorithm C v94."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from scripts import recover_mysql_algorithm_c_staged_v94_v2 as recovery
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v5 as controller
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v2 as runtime


class StagedV94Test(unittest.TestCase):
    def test_frozen_contract_and_bound_sources(self) -> None:
        document = controller.validate_contract_v94()["successor"]
        self.assertEqual(document["release_version"], controller.RELEASE)
        self.assertTrue(document["mixed_memory_scene_batch_supported"])

    def test_memory_quarantine_projection_passes_exact_runtime_validator(self) -> None:
        source = recovery.QUARANTINE
        value = json.loads(source.read_text(encoding="utf-8"))
        for key in tuple(value):
            if key.endswith("_by_scene") and isinstance(value[key], dict):
                value[key] = {
                    scene: value[key][scene] for scene in recovery.MEMORY_SCENES
                }
        value.update({
            "schema_version": "mysql_algorithm_c_replay_quarantine_v6",
            "memory_pair_projection": True,
            "source_quarantine_path": str(source),
            "source_quarantine_sha256": recovery.sha(source),
            "active_scenes": list(recovery.MEMORY_SCENES),
            "formal_state_mutation": False,
        })
        checkpoint = runtime.alg.load(recovery.CHECKPOINT)
        original_scenes = runtime.SCENES
        try:
            runtime.SCENES = recovery.MEMORY_SCENES
            with tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "memory_pair_quarantine.json"
                path.write_text(json.dumps(value), encoding="utf-8")
                result = runtime._validate_replay_quarantine(path, checkpoint)
        finally:
            runtime.SCENES = original_scenes
        self.assertEqual(set(result["combined"]), set(recovery.MEMORY_SCENES))

    def test_recovery_declares_independent_release_without_external_control(self) -> None:
        source = Path(recovery.__file__).read_text(encoding="utf-8")
        self.assertIn('"four_scene_wave_barrier": False', source)
        self.assertIn('"sibling_scene_wait": False', source)
        self.assertIn('"external_process_control_or_stop_performed": False', source)
        self.assertIn('"postgresql_process_control_or_stop_performed": False', source)
        self.assertIn('"schema_version": "mysql_algorithm_c_replay_quarantine_v6"', source)


if __name__ == "__main__":
    unittest.main()

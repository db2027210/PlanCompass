from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import mysql_cross_project_wave_coordination_v30 as gate
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v40 as controller
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v48 as runtime


class ResourceBindingTests(unittest.TestCase):
    def test_kernel_gate_uses_current_authority_freeze(self) -> None:
        freeze = gate.ROOT / (
            "config/formal/frozen_mysql_four_scene_two_worker_20260920_v22/"
            "four_scene_effective_resource_successor.json"
        )
        previous = gate.kernel.FREEZE
        with tempfile.TemporaryDirectory(dir=gate.HISTORY) as directory:
            root = Path(directory)
            prepared = root / "prepared_binding.json"
            document = {
                "freeze_path": str(freeze),
                "freeze_sha256": hashlib.sha256(freeze.read_bytes()).hexdigest(),
            }
            prepared.write_text(json.dumps(document))
            try:
                with patch.dict(os.environ, {"MYSQL_TWO_WORKER_AUTHORITY_ROOT": str(root)}):
                    self.assertEqual(gate.bind_current_authority(), prepared)
                    self.assertEqual(gate.kernel.FREEZE, freeze)
                    prepared.write_text(json.dumps({**document, "freeze_sha256": "0" * 64}))
                    with self.assertRaisesRegex(RuntimeError, "resource freeze differs"):
                        gate.bind_current_authority()
            finally:
                gate.kernel.FREEZE = previous

    def test_compiled_runtime_receives_paper_gate(self) -> None:
        controller.validate_contract()
        original = getattr(runtime, "paper_baseline_gate", None)
        had_binding = hasattr(runtime, "paper_baseline_gate")

        def inspect_binding() -> int:
            self.assertIs(runtime.paper_baseline_gate, controller.paper_baseline_gate)
            return 0

        with patch.object(controller.predecessor, "validate_contract"), patch.object(
            controller.predecessor, "main", side_effect=inspect_binding,
        ):
            self.assertEqual(controller.main(), 0)
        self.assertEqual(hasattr(runtime, "paper_baseline_gate"), had_binding)
        if had_binding:
            self.assertIs(runtime.paper_baseline_gate, original)


if __name__ == "__main__":
    unittest.main()

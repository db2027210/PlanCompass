#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from watch_hybenchxp_four_vm_recall_completion import (
    TARGETS,
    converged_final_artifacts,
    guard_complete,
    invalidation_markers,
)


class CompletionAuditWatcherTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.run_root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @staticmethod
    def write_json(path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    def test_missing_final_artifacts_are_not_converged(self) -> None:
        self.assertEqual(
            converged_final_artifacts(self.run_root),
            {target: False for target in TARGETS},
        )
        self.assertFalse(guard_complete(self.run_root))
        self.assertEqual(invalidation_markers(self.run_root), [])

    def test_all_finals_and_guard_must_report_epsilon_completion(self) -> None:
        for target in TARGETS:
            self.write_json(
                self.run_root / target / f"final_for_{target}.json",
                {"converged": True, "stop_reason": "epsilon_converged"},
            )
        self.write_json(
            self.run_root / "host_disk_isolation_status.json",
            {
                "event": "host_disk_guard_complete",
                "status": "ok",
                "all_converged": True,
            },
        )
        self.assertTrue(all(converged_final_artifacts(self.run_root).values()))
        self.assertTrue(guard_complete(self.run_root))

        self.write_json(
            self.run_root / "io" / "final_for_io.json",
            {"converged": False, "stop_reason": "max_rounds"},
        )
        self.assertFalse(converged_final_artifacts(self.run_root)["io"])

    def test_root_invalidation_markers_are_detected(self) -> None:
        marker = self.run_root / "INVALIDATED_RUNTIME_HOST_DISK_CONFLICT.md"
        marker.write_text("invalid\n", encoding="utf-8")
        self.assertEqual(invalidation_markers(self.run_root), [marker.name])


if __name__ == "__main__":
    unittest.main()

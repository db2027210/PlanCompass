from __future__ import annotations

import json
import math
from pathlib import Path
import tempfile
import unittest

from formal_random_walk_artifacts import (
    ArtifactError,
    append_jsonl,
    atomic_write_json,
    fsync_artifact_tree,
    validate_checksum_manifest,
    write_checksum_manifest,
)


class ArtifactTests(unittest.TestCase):
    def test_finite_json_jsonl_and_complete_checksum_set(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "round_000_baseline/attempt_01").mkdir(parents=True)
            atomic_write_json(root / "run_summary.json", {"status": "complete"})
            events = root / "round_000_baseline/attempt_01/events.jsonl"
            append_jsonl(events, {"event": "round_start"})
            append_jsonl(events, {"event": "round_end"})
            result = write_checksum_manifest(root)
            self.assertEqual(result["entry_count"], 2)
            self.assertEqual(validate_checksum_manifest(root)["status"], "passed")
            rows = [json.loads(row) for row in events.read_text().splitlines()]
            self.assertEqual(len(rows), 2)

    def test_nan_and_overwrite_without_permission_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "value.json"
            with self.assertRaises(ArtifactError):
                atomic_write_json(path, {"bad": math.nan})
            atomic_write_json(path, {"value": 1}, replace=False)
            with self.assertRaises(ArtifactError):
                atomic_write_json(path, {"value": 2}, replace=False)

    def test_symlink_is_rejected_at_finalization(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = root / "target"
            target.write_text("x", encoding="utf-8")
            (root / "alias").symlink_to(target)
            with self.assertRaises(ArtifactError):
                write_checksum_manifest(root)

    def test_fsync_tree_covers_regular_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "nested").mkdir()
            (root / "a.json").write_text("{}\n", encoding="utf-8")
            (root / "nested" / "b.log").write_text("ok\n", encoding="utf-8")
            self.assertEqual(
                fsync_artifact_tree(root), {"file_count": 2, "directory_count": 2}
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)

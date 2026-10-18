#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from summarize_bonect_phase2_none_delta_calibration import (
    comparison_baseline,
    complete_records,
    optional_delta,
)


class DeltaSummaryTest(unittest.TestCase):
    def test_adjacent_current_anchor_repeat_is_paired(self) -> None:
        records = [
            {
                "event": "calibration_point_complete",
                "campaign_id": "campaign",
                "timestamp": "2026-07-11T11:42:30",
                "point_index": 14,
                "kind": "current_feature_baseline",
                "run_id": "default",
                "db_qps": 400.0,
                "resource_normalized": {"cpu": 20.0, "memory": 5.0, "io": 10.0},
            },
            {
                "event": "calibration_point_complete",
                "campaign_id": "campaign",
                "timestamp": "2026-07-11T11:58:30",
                "point_index": 15,
                "kind": "current_anchor_repeat",
                "run_id": "candidate",
                "db_qps": 424.0,
                "resource_normalized": {"cpu": 21.0, "memory": 5.1, "io": 10.5},
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.jsonl"
            path.write_text(
                "".join(json.dumps(record) + "\n" for record in records),
                encoding="utf-8",
            )
            completed = complete_records(path)
        candidate = completed[-1]
        self.assertTrue(candidate["paired_baseline_valid"])
        self.assertEqual(candidate["paired_baseline_position"], "before")
        self.assertEqual(candidate["paired_baseline"]["run_id"], "default")

    def test_unpaired_history_has_no_comparable_delta(self) -> None:
        baseline, valid, source = comparison_baseline(
            {
                "kind": "historical_anchor_revalidation",
                "db_qps": 500.0,
                "resource_normalized": {"cpu": 22.0, "memory": 5.0, "io": 11.0},
            },
            fallback_resource={"cpu": 20.0, "memory": 4.0, "io": 10.0},
            fallback_db_qps=400.0,
        )
        self.assertFalse(valid)
        self.assertEqual(source, "unpaired_cross_time_diagnostic")
        self.assertIsNone(optional_delta(500.0, baseline["db_qps"], valid))

    def test_current_baseline_is_self_comparable(self) -> None:
        baseline, valid, source = comparison_baseline(
            {
                "kind": "current_feature_baseline",
                "db_qps": 400.0,
                "resource_normalized": {"cpu": 20.0, "memory": 4.0, "io": 10.0},
            },
            fallback_resource={},
            fallback_db_qps=0.0,
        )
        self.assertTrue(valid)
        self.assertEqual(source, "self_baseline")
        self.assertEqual(optional_delta(400.0, baseline["db_qps"], valid), 0.0)


if __name__ == "__main__":
    unittest.main()

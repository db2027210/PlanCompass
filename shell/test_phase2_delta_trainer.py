#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell.train_bonect_phase2_none_delta_calibrator import (
    completed_records,
    labels_for_record,
    record_is_eligible,
    select_plan_columns,
)


class Phase2DeltaTrainerTests(unittest.TestCase):
    def test_unpaired_nonbaseline_record_is_not_eligible(self) -> None:
        record = {
            "kind": "single_parameter_endpoint",
            "db_qps": 120.0,
            "resource_normalized": {"cpu": 11.0, "memory": 21.0, "io": 31.0},
            "plan_features": {"plan_gather_count_share": 0.2},
        }
        self.assertFalse(record_is_eligible(record))
        record["paired_baseline_valid"] = True
        self.assertTrue(record_is_eligible(record))

        historical = dict(record)
        historical["kind"] = "historical_anchor_revalidation"
        historical["paired_baseline_valid"] = False
        self.assertFalse(record_is_eligible(historical))

    def test_current_baseline_is_eligible_but_unpaired_anchor_is_not(self) -> None:
        common = {
            "db_qps": 120.0,
            "resource_normalized": {"cpu": 11.0, "memory": 21.0, "io": 31.0},
            "plan_features": {"plan_gather_count_share": 0.2},
        }
        self.assertTrue(
            record_is_eligible({**common, "kind": "current_feature_baseline"})
        )
        self.assertFalse(record_is_eligible({**common, "kind": "current_anchor_repeat"}))

    def test_adjacent_anchor_repeat_is_paired_from_state_order(self) -> None:
        baseline = {
            "event": "calibration_point_complete",
            "campaign_id": "campaign",
            "timestamp": "2026-07-11T11:42:30",
            "point_index": 14,
            "kind": "current_feature_baseline",
            "run_id": "default-run",
            "db_qps": 100.0,
            "resource_normalized": {"cpu": 10.0, "memory": 20.0, "io": 30.0},
            "plan_features": {"plan_gather_count_share": 0.0},
        }
        anchor = {
            "event": "calibration_point_complete",
            "campaign_id": "campaign",
            "timestamp": "2026-07-11T11:58:34",
            "point_index": 15,
            "kind": "current_anchor_repeat",
            "run_id": "anchor-run",
            "db_qps": 110.0,
            "resource_normalized": {"cpu": 11.0, "memory": 21.0, "io": 31.0},
            "plan_features": {"plan_gather_count_share": 0.2},
        }
        with tempfile.TemporaryDirectory(prefix="phase2-delta-trainer-") as temp_dir:
            state = Path(temp_dir) / "state.jsonl"
            state.write_text(
                json.dumps(baseline) + "\n" + json.dumps(anchor) + "\n",
                encoding="utf-8",
            )
            records = completed_records(state)
        paired_anchor = next(record for record in records if record["point_index"] == 15)
        self.assertTrue(paired_anchor["paired_baseline_valid"])
        self.assertEqual(paired_anchor["paired_baseline"]["run_id"], "default-run")
        self.assertTrue(record_is_eligible(paired_anchor))

    def test_labels_use_paired_baseline(self) -> None:
        labels, run_id = labels_for_record(
            {
                "paired_baseline_valid": True,
                "paired_baseline": {
                    "run_id": "paired-default",
                    "db_qps": 200.0,
                    "resource_normalized": {
                        "cpu": 20.0,
                        "memory": 40.0,
                        "io": 60.0,
                    },
                },
                "db_qps": 220.0,
                "resource_normalized": {
                    "cpu": 22.0,
                    "memory": 36.0,
                    "io": 63.0,
                },
            },
            fallback_db_qps=100.0,
            fallback_resource={"cpu": 10.0, "memory": 20.0, "io": 30.0},
            fallback_run_id="global-default",
        )
        self.assertEqual(run_id, "paired-default")
        self.assertEqual(labels, [10.0, 10.0, -10.0, 5.0])

    def test_plan_feature_selection_is_semantic_and_target_independent(self) -> None:
        records = [
            {
                "plan_features": {
                    "plan_gather_count_share": 0.0,
                    "plan_seq_scan_count_share": 0.4,
                    "arbitrary_target_proxy": 1000.0,
                }
            },
            {
                "plan_features": {
                    "plan_gather_count_share": 0.2,
                    "plan_seq_scan_count_share": 0.2,
                    "arbitrary_target_proxy": -1000.0,
                }
            },
        ]
        selected = select_plan_columns(
            records,
            {
                "plan_gather_count_share": 0.0,
                "plan_seq_scan_count_share": 0.4,
                "arbitrary_target_proxy": 0.0,
            },
            16,
        )
        names = [name for name, _values, _metadata in selected]
        self.assertEqual(
            names,
            ["plan_gather_count_share", "plan_seq_scan_count_share"],
        )


if __name__ == "__main__":
    unittest.main()

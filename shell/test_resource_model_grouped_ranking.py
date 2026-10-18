#!/usr/bin/env python3
"""Tests for group-heldout resource-model ranking diagnostics."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from resource_model_retrainer import (  # noqa: E402
    _grouped_holdout_indices,
    _pairwise_order_metrics,
)


class ResourceModelGroupedRankingTests(unittest.TestCase):
    def test_group_holdout_has_no_source_overlap(self) -> None:
        groups = np.asarray(["a", "a", "b", "b", "c", "c", "d", "d"])
        train_idx, test_idx, audit = _grouped_holdout_indices(
            groups,
            seed=42,
            np=np,
            test_fraction=0.25,
        )
        self.assertFalse(set(groups[train_idx]).intersection(groups[test_idx]))
        self.assertEqual(audit["split_policy"], "grouped_holdout_v1")
        self.assertEqual(audit["group_count"], 4)

    def test_single_group_fails_closed(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "at least two independent groups"):
            _grouped_holdout_indices(
                np.asarray(["same", "same"]),
                seed=42,
                np=np,
            )

    def test_pairwise_order_excludes_cross_group_pairs(self) -> None:
        metrics = _pairwise_order_metrics(
            np.asarray([1.0, 2.0, 100.0, 90.0]),
            np.asarray([2.0, 1.0, 80.0, 90.0]),
            np.asarray(["a", "a", "b", "b"]),
            np,
        )
        self.assertEqual(metrics["pairwise_pair_count"], 2)
        self.assertEqual(metrics["pairwise_inversion_count"], 2)
        self.assertEqual(metrics["pairwise_inversion_rate"], 1.0)
        self.assertEqual(metrics["pairwise_direction_accuracy"], 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

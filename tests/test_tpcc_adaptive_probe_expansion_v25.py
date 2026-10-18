from __future__ import annotations

import unittest

from tools import build_tpcc_round14_robust_regime_top5_v24 as robust


def probe(identifier: str) -> dict:
    return {
        "three_level_fingerprint": {
            "id": f"three-level-sha256:{identifier:0>64}"
        }
    }


class AdaptiveProbeExpansionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.anchor = {name: 0.5 for name in ("a", "b", "c")}
        self.gradient = {"a": 1.0, "b": -0.5, "c": 0.25}
        self.ranges = {
            name: {"min_value": 0.0, "max_value": 1.0}
            for name in self.anchor
        }

    def test_available_three_expands_deterministically_beyond_five(self) -> None:
        first = robust.adaptive_gradient_boundary_points(
            self.anchor,
            self.gradient,
            self.ranges,
        )
        second = robust.adaptive_gradient_boundary_points(
            self.anchor,
            self.gradient,
            self.ranges,
        )
        self.assertEqual(first, second)
        self.assertGreaterEqual(len(first), 5)
        self.assertEqual(
            {row["eta"] for row in first},
            set(robust.ADAPTIVE_GRADIENT_ETA_LEVELS),
        )
        parameter_ids = [
            robust.parameter_key(row["params"]) for row in first
        ]
        self.assertEqual(len(parameter_ids), len(set(parameter_ids)))
        selected = [probe("1"), probe("2"), probe("3")]
        selected.extend(
            {
                "three_level_fingerprint": {
                    "id": f"three-level-sha256:{identifier}"
                }
            }
            for identifier in parameter_ids[:2]
        )
        robust.require_five_diagnostic_probes(selected)

    def test_fewer_than_five_distinct_fingerprints_fails_closed(self) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "insufficient novel diagnostic probe pool: available=3",
        ):
            robust.require_five_diagnostic_probes(
                [probe("1"), probe("2"), probe("3")]
            )

    def test_zero_gradient_fails_closed(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "no finite direction"):
            robust.adaptive_gradient_boundary_points(
                self.anchor,
                {name: 0.0 for name in self.anchor},
                self.ranges,
            )


if __name__ == "__main__":
    unittest.main()

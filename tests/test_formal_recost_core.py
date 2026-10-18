from __future__ import annotations

from io import StringIO
import json
import math
from pathlib import Path
import unittest

from formal_recost_core import (
    FrozenRecostContractError,
    FrozenRecostProposalEngine,
    MYSQL_COST_NAMES,
    load_frozen_recost_contract,
)


ROOT = Path(__file__).resolve().parents[1]
FROZEN = ROOT / "preflight" / "frozen_formal_config_20260721_v3"


class FrozenRecostCoreTests(unittest.TestCase):
    def build_engine(self) -> FrozenRecostProposalEngine:
        settings, specs = load_frozen_recost_contract(
            FROZEN / "method_protocols.json",
            FROZEN / "search_spaces.json",
        )
        return FrozenRecostProposalEngine(
            settings,
            specs,
            estimator_version=settings.estimator_version,
        )

    def defaults(self) -> dict[str, float]:
        document = json.loads((FROZEN / "search_spaces.json").read_text())
        return {name: float(document["mysql"]["default"][name]) for name in MYSQL_COST_NAMES}

    def test_loads_exact_frozen_values(self) -> None:
        engine = self.build_engine()
        self.assertEqual(engine.settings.inner_iteration_limit_per_outer, 500)
        self.assertEqual(engine.settings.outer_real_candidate_limit, 10)
        self.assertEqual(engine.settings.finite_difference_epsilon_normalized, 0.05)
        self.assertEqual(tuple(spec.name for spec in engine.parameters), MYSQL_COST_NAMES)

    def test_inner_uses_only_supplied_offline_estimator_and_records_label(self) -> None:
        engine = self.build_engine()
        target = {name: 0.75 for name in MYSQL_COST_NAMES}
        calls: list[dict[str, float]] = []

        def estimator(params: dict[str, float]) -> float:
            calls.append(dict(params))
            normalized = engine.normalize(params)
            return -sum((normalized[name] - target[name]) ** 2 for name in MYSQL_COST_NAMES)

        log = StringIO()
        proposal = engine.propose(self.defaults(), estimator, inner_log=log)
        self.assertGreater(len(calls), 1)
        self.assertGreater(proposal.inner_accepted_iterations, 0)
        self.assertFalse(proposal.duplicate_of_incumbent)
        rows = [json.loads(line) for line in log.getvalue().splitlines()]
        self.assertTrue(rows)
        self.assertTrue(all(row["label"] == "inner_step_accepted" for row in rows))
        self.assertTrue(all(row["estimator_access"] == "offline_only_no_live_database" for row in rows))

    def test_proposal_stays_inside_bounds_and_step_cap(self) -> None:
        engine = self.build_engine()
        before = engine.normalize(self.defaults())
        proposal = engine.propose(
            self.defaults(),
            lambda params: sum(engine.normalize(params).values()),
        )
        for spec in engine.parameters:
            self.assertGreaterEqual(proposal.params[spec.name], spec.minimum)
            self.assertLessEqual(proposal.params[spec.name], spec.maximum)
            self.assertLessEqual(
                abs(proposal.normalized_params[spec.name] - before[spec.name]),
                1.0,
            )

    def test_flat_estimator_stops_without_fabricating_change(self) -> None:
        engine = self.build_engine()
        proposal = engine.propose(self.defaults(), lambda params: 1.0)
        self.assertTrue(proposal.duplicate_of_incumbent)
        self.assertEqual(proposal.inner_accepted_iterations, 0)
        self.assertEqual(proposal.stop_reason, "no_update_patience")

    def test_outer_early_stop_requires_six_candidates_and_four_rejections(self) -> None:
        engine = self.build_engine()
        decisions = [True, True, False, False, False, False]
        stops = [engine.record_outer_result(outer_candidate_accepted=value) for value in decisions]
        self.assertEqual(stops, [False, False, False, False, False, True])

    def test_outer_budget_is_hard_limit(self) -> None:
        engine = self.build_engine()
        for _ in range(10):
            engine.record_outer_result(outer_candidate_accepted=False)
        with self.assertRaises(FrozenRecostContractError):
            engine.record_outer_result(outer_candidate_accepted=False)

    def test_normalize_round_trip(self) -> None:
        engine = self.build_engine()
        point = self.defaults()
        round_trip = engine.denormalize(engine.normalize(point))
        for name in MYSQL_COST_NAMES:
            self.assertTrue(math.isclose(point[name], round_trip[name], rel_tol=1e-12))


if __name__ == "__main__":
    unittest.main()

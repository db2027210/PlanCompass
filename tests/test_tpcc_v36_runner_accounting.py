from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


REMOTE = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/"
    "gradient_debug_round14_robust_regime_interval_v2_20260729T1824"
)
sys.path.insert(0, str(REMOTE))

from tools import run_tpcc_v36_unique_exact_strict_serial as runner


def event(path: Path, name: str, rank: int, attempt: int) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {"event": name, "rank": rank, "attempt": attempt},
                sort_keys=True,
            )
            + "\n"
        )


class TpccV36RunnerAccountingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.events = self.root / "events.jsonl"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def ledger(self) -> dict:
        return {
            "successful": [],
            "technical_failures": [],
            "preworkload_failures": [],
            "real_workload_invocation_count": 0,
        }

    def test_started_only_attempt_is_conservatively_reserved_and_technical(self) -> None:
        event(
            self.events,
            "tpcc_gradient_real_replay_started",
            2,
            1,
        )
        technical, physical = runner.reconcile_unsettled_invocations(
            self.ledger(), self.events, self.root
        )
        self.assertEqual(physical, 1)
        self.assertEqual(len(technical), 1)
        self.assertEqual((technical[0]["rank"], technical[0]["attempt"]), (2, 1))
        self.assertTrue(technical[0]["consumes_physical_call_budget"])
        self.assertTrue(technical[0]["consumes_technical_failure_budget"])
        self.assertEqual(runner.load_invoked_ranks(self.events), [2])

    def test_reserved_orphan_is_recovered_once(self) -> None:
        event(self.events, "tpcc_gradient_real_replay_started", 3, 1)
        event(
            self.events,
            "tpcc_gradient_real_replay_workload_invocation_reserved",
            3,
            1,
        )
        ledger = self.ledger()
        technical, physical = runner.reconcile_unsettled_invocations(
            ledger, self.events, self.root
        )
        self.assertEqual((physical, len(technical)), (1, 1))
        technical, physical = runner.reconcile_unsettled_invocations(
            ledger, self.events, self.root
        )
        self.assertEqual((physical, len(technical)), (1, 1))

    def test_accounted_success_is_not_reclassified(self) -> None:
        event(self.events, "tpcc_gradient_real_replay_started", 1, 1)
        event(
            self.events,
            "tpcc_gradient_real_replay_workload_invocation_reserved",
            1,
            1,
        )
        ledger = self.ledger()
        ledger["real_workload_invocation_count"] = 1
        ledger["successful"] = [{"rank": 1, "attempt": 1}]
        technical, physical = runner.reconcile_unsettled_invocations(
            ledger, self.events, self.root
        )
        self.assertEqual((physical, technical), (1, []))

    def test_ledger_ahead_of_durable_events_fails_closed(self) -> None:
        ledger = self.ledger()
        ledger["real_workload_invocation_count"] = 1
        with self.assertRaisesRegex(RuntimeError, "exceeds durable"):
            runner.reconcile_unsettled_invocations(
                ledger, self.events, self.root
            )

    def test_result_artifact_requires_manual_audit(self) -> None:
        event(self.events, "tpcc_gradient_real_replay_started", 4, 1)
        event(
            self.events,
            "tpcc_gradient_real_replay_workload_invocation_reserved",
            4,
            1,
        )
        attempt = self.root / "rank_04/attempt_01"
        attempt.mkdir(parents=True)
        (attempt / "point_result.json").write_text("{}\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "requires audit"):
            runner.reconcile_unsettled_invocations(
                self.ledger(), self.events, self.root
            )


if __name__ == "__main__":
    unittest.main()

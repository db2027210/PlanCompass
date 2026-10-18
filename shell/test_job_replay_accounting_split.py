#!/usr/bin/env python3
"""Tests for immutable global versus formal-search JOB replay accounting."""

from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from contextlib import redirect_stdout


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "launch_packages/pg5_common_outer_bgallowed_20260722_v1/job_node02/"
    "formal_job_outer_replay.py"
)
SPEC = importlib.util.spec_from_file_location("formal_job_outer_replay_tested", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class JobReplayAccountingSplitTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        outer = self.root / "history/job/outer/attempt_1"
        outer.mkdir(parents=True)
        (outer / "replay_counter.txt").write_text("13\n", encoding="utf-8")
        self.lane_patch = mock.patch.object(MODULE, "LANE_ROOT", self.root)
        self.lane_patch.start()

    def tearDown(self) -> None:
        self.lane_patch.stop()
        self.temporary.cleanup()

    def test_fresh_r0_advances_only_global_then_search_has_own_budget(self) -> None:
        outer = self.root / "history/job/outer/attempt_1"
        with mock.patch.dict(
            os.environ,
            {
                "RA_PG_REPLAY_ACCOUNTING_SCOPE": "fresh_default_r0",
                "RA_PG_FORMAL_SEARCH_MAX_REPLAYS": "1",
            },
            clear=False,
        ):
            fresh = MODULE.claim_replay_accounting()
        self.assertEqual(fresh["global_ordinal_before"], 13)
        self.assertEqual(fresh["global_ordinal"], 14)
        self.assertEqual(fresh["formal_search_replay_count"], 0)

        with mock.patch.dict(
            os.environ,
            {
                "RA_PG_REPLAY_ACCOUNTING_SCOPE": "formal_search",
                "RA_PG_FORMAL_SEARCH_MAX_REPLAYS": "1",
                "RA_PG_FORMAL_SEARCH_ATTEMPT_SAFETY_CAP": "3",
                "RA_PG_EXCLUDE_FIRST_FORMAL_REPLAY_AS_STARTUP_BASELINE": "1",
            },
            clear=False,
        ):
            startup = MODULE.claim_replay_accounting()
            self.assertEqual(startup["global_ordinal_before"], 14)
            self.assertEqual(startup["global_ordinal"], 15)
            self.assertEqual(startup["scope"], "formal_startup_baseline")
            self.assertEqual(startup["formal_search_replay_count"], 0)
            search = MODULE.claim_replay_accounting()
            self.assertEqual(search["global_ordinal_before"], 15)
            self.assertEqual(search["global_ordinal"], 16)
            self.assertEqual(search["formal_search_replay_count_before"], 0)
            self.assertEqual(search["formal_search_replay_count"], 0)
            point_result = outer / "point_result.json"
            point_result.write_text('{"status":"complete"}\n', encoding="utf-8")
            committed = MODULE.commit_successful_replay(
                search,
                point_id="test-point",
                point_result_path=point_result,
            )
            self.assertEqual(committed["formal_search_replay_count"], 1)
            with self.assertRaisesRegex(RuntimeError, "budget exhausted"):
                MODULE.claim_replay_accounting()

        self.assertEqual((outer / "replay_counter.txt").read_text().strip(), "16")
        self.assertEqual(
            (outer / "formal_search_replay_counter.txt").read_text().strip(),
            "1",
        )
        self.assertEqual(
            (outer / "formal_startup_baseline_counter.txt").read_text().strip(),
            "1",
        )

    def test_technical_failure_advances_attempt_not_success(self) -> None:
        outer = self.root / "history/job/outer/attempt_1"
        with mock.patch.dict(
            os.environ,
            {
                "RA_PG_REPLAY_ACCOUNTING_SCOPE": "formal_search",
                "RA_PG_FORMAL_SEARCH_MAX_REPLAYS": "1",
                "RA_PG_FORMAL_SEARCH_ATTEMPT_SAFETY_CAP": "2",
                "RA_PG_EXCLUDE_FIRST_FORMAL_REPLAY_AS_STARTUP_BASELINE": "0",
            },
            clear=False,
        ):
            first_failed_attempt = MODULE.claim_replay_accounting()
            self.assertEqual(
                first_failed_attempt["formal_search_replay_count"],
                0,
            )
            second_failed_attempt = MODULE.claim_replay_accounting()
            self.assertEqual(
                second_failed_attempt["formal_search_replay_count"],
                0,
            )
            with self.assertRaisesRegex(RuntimeError, "attempt safety cap"):
                MODULE.claim_replay_accounting()
        self.assertEqual(
            (outer / "formal_search_replay_counter.txt").read_text().strip(),
            "0",
        )
        self.assertEqual(
            (outer / "formal_search_attempt_counter.txt").read_text().strip(),
            "2",
        )
    def test_global_audit_ordinal_regression_fails_closed(self) -> None:
        outer = self.root / "history/job/outer/attempt_1"
        (outer / "replay_counter.txt").write_text("12\n", encoding="utf-8")
        with mock.patch.dict(
            os.environ,
            {"RA_PG_REPLAY_ACCOUNTING_SCOPE": "fresh_default_r0"},
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "regressed below 13"):
                MODULE.claim_replay_accounting()

    def test_explicit_costs_never_read_control_costs(self) -> None:
        explicit = {
            "seq_page_cost": 1.0,
            "random_page_cost": 4.0,
            "cpu_tuple_cost": 0.01,
            "cpu_index_tuple_cost": 0.0005,
            "cpu_operator_cost": 0.0025,
            "parallel_setup_cost": 7000.0,
            "parallel_tuple_cost": 0.001,
        }
        with mock.patch.dict(
            os.environ,
            {"RA_PG_EXPLICIT_COST_PARAMETERS_JSON": json.dumps(explicit)},
            clear=False,
        ), mock.patch.object(
            MODULE,
            "read_control_costs",
            side_effect=AssertionError("control costs must not be read"),
        ):
            observed, source = MODULE.resolve_replay_costs()
        self.assertEqual(observed, explicit)
        self.assertTrue(source["explicit_override"])

    def test_preclaim_failure_never_claims_an_ordinal(self) -> None:
        output = io.StringIO()
        with mock.patch.object(
            MODULE,
            "resolve_restart_baseline",
            return_value=(Path("marker.json"), {"ok": True}, "r0"),
        ), mock.patch.object(MODULE, "enforce_pause_before_next_claim"), mock.patch.object(
            MODULE,
            "verify_control_readiness",
            side_effect=RuntimeError("not stable"),
        ), mock.patch.object(MODULE, "claim_replay_accounting") as claim, redirect_stdout(output):
            self.assertEqual(MODULE.main(), 75)
        claim.assert_not_called()
        marker = json.loads(output.getvalue())
        self.assertTrue(marker["preclaim_no_ordinal_claim"])
        self.assertEqual(marker["status"], "technical_failure_before_ordinal_claim")

    def test_main_source_orders_readiness_and_costs_before_claim(self) -> None:
        source = __import__("inspect").getsource(MODULE.main)
        self.assertLess(source.index("verify_control_readiness()"), source.index("claim_replay_accounting()"))
        self.assertLess(source.index("resolve_replay_costs()"), source.index("claim_replay_accounting()"))

    def test_readiness_uses_bounded_retry_and_three_success_window(self) -> None:
        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def execute(self, _sql):
                return None

            def fetchone(self):
                return (1, False)

        class Connection:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def cursor(self):
                return Cursor()

        side_effect = [
            MODULE.psycopg2.OperationalError("transient close"),
            Connection(),
            Connection(),
            Connection(),
        ]
        with mock.patch.object(
            MODULE.psycopg2,
            "connect",
            side_effect=side_effect,
        ) as connect, mock.patch.object(MODULE.time, "sleep") as sleep:
            audit = MODULE.verify_control_readiness()
        self.assertEqual(connect.call_count, 4)
        self.assertEqual(audit["attempt_count"], 4)
        self.assertEqual(audit["required_consecutive_successes"], 3)
        self.assertEqual(sleep.call_count, 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""Tests for the generic accepted checkpoint v2 builder."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell.build_gradient_accepted_resume_v2 import (  # noqa: E402
    CheckpointBuildError,
    build_checkpoint,
    main,
)


PARAM_NAMES = ("seq_page_cost", "parallel_setup_cost")


def validation(db_qps: float) -> dict:
    return {
        "db_qps": db_qps,
        "utilization_pct": {"cpu": 80.0, "memory": 40.0, "io": 40.0},
        "candidate_measurement_protocol": {
            "stable": True,
            "correctness_passed": True,
        },
    }


def evaluation(params: dict, *, accepted: bool, db_qps: float) -> dict:
    return {
        "accepted_by_actual_score": accepted,
        "candidate_optimized_params": params,
        "candidate_estimated_resource": {
            "cpu": 1.0,
            "memory": 1.0,
            "io": 1.0,
        },
        "candidate_final_validation": validation(db_qps),
    }


def hex_signature(params: dict) -> list:
    return [[name, float(value).hex()] for name, value in sorted(params.items())]


class Fixture:
    def __init__(self, root: Path, *, count: int) -> None:
        self.root = root / "attempt"
        (self.root / "history").mkdir(parents=True)
        (self.root / "config").mkdir()
        self.counter = self.root / "formal_search_accepted_counter.txt"
        self.counter.write_text(f"{count}\n", encoding="utf-8")
        self.config = self.root / "config" / "workload.json"
        self.config.write_text(
            json.dumps(
                {
                    "param_ranges": {
                        "seq_page_cost": {
                            "min_value": 0.1,
                            "max_value": 10.0,
                        },
                        "parallel_setup_cost": {
                            "min_value": 0.0,
                            "max_value": 100.0,
                        },
                    },
                    "optimizer": {
                        "mode": "gradient",
                        "gradient": {"active_params": list(PARAM_NAMES)},
                    },
                    "formal_outer_protocol": {
                        "formal_search_accepted_counter_path": str(self.counter),
                        "formal_search_max_replay_top_k": 10,
                    },
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        self.history = self.root / "history" / "workload_optimizer_history.jsonl"
        self.point = self.root / "accepted_point.json"

    def write_history(self, rows: list) -> None:
        self.history.write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
            encoding="utf-8",
        )

    def write_point(self, params: dict) -> None:
        self.point.write_text(
            json.dumps(
                {
                    "status": "complete",
                    "measurement": {
                        "correctness_passed": True,
                        "resource_stability": {
                            "passed": True,
                            "stable": True,
                        },
                    },
                    "oom_killed": False,
                    "database_restart_count": 0,
                    "cost_parameters": params,
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )


def count1_rows(*, start_round1: bool = False) -> tuple:
    incumbent = {"seq_page_cost": 1.0, "parallel_setup_cost": 25.0}
    accepted = {"seq_page_cost": 0.5, "parallel_setup_cost": 75.0}
    rows = [
        {
            "event": "experience_table_gradient_initial_real_replay_complete",
            "round_index": 0,
            "replay_batch_audit": {
                "scope_incumbent_signature": hex_signature(incumbent),
            },
            "candidate_evaluations": [
                evaluation(accepted, accepted=True, db_qps=110.0),
            ],
        },
        {
            "event": "gradient_parameter_range_update",
            "round_index": 0,
            "accepted_shrink_count": 1,
            "range_state": {
                "seq_page_cost": {
                    "min_value": 0.1,
                    "max_value": 1.0,
                    "transform": "log",
                },
                "parallel_setup_cost": {
                    "min_value": 50.0,
                    "max_value": 100.0,
                    "transform": "linear",
                },
            },
        },
    ]
    if start_round1:
        rows.append({"event": "final_feedback_status", "round_index": 1})
    return rows, accepted


def count2_rows() -> tuple:
    initial = {"seq_page_cost": 1.0, "parallel_setup_cost": 25.0}
    accepted1 = {"seq_page_cost": 0.5, "parallel_setup_cost": 75.0}
    accepted2 = {"seq_page_cost": 0.5, "parallel_setup_cost": 60.0}
    midpoint = math.sqrt(0.1)
    rows = [
        {
            "event": "experience_table_gradient_initial_real_replay_complete",
            "round_index": 0,
            "replay_batch_audit": {
                "scope_incumbent_signature": hex_signature(initial),
            },
            "candidate_evaluations": [
                evaluation(accepted1, accepted=True, db_qps=110.0),
            ],
        },
        {
            "event": "gradient_parameter_range_update",
            "round_index": 0,
            "accepted_shrink_count": 1,
            "range_state": {
                "seq_page_cost": {
                    "min_value": 0.1,
                    "max_value": 1.0,
                    "transform": "log",
                },
                "parallel_setup_cost": {
                    "min_value": 50.0,
                    "max_value": 100.0,
                    "transform": "linear",
                },
            },
        },
        {
            "event": "final_feedback_round_complete",
            "round_index": 1,
            "accepted_by_actual_score": False,
            "round_start_params": accepted1,
            "candidate_evaluations": [
                evaluation(
                    {"seq_page_cost": 0.8, "parallel_setup_cost": 80.0},
                    accepted=False,
                    db_qps=100.0,
                )
            ],
        },
        {
            "event": "gradient_parameter_range_update",
            "round_index": 2,
            "accepted_shrink_count": 2,
            "range_state": {
                "seq_page_cost": {
                    "min_value": midpoint,
                    "max_value": 1.0,
                    "transform": "log",
                },
                "parallel_setup_cost": {
                    "min_value": 50.0,
                    "max_value": 75.0,
                    "transform": "linear",
                },
            },
        },
        {
            "event": "final_feedback_round_complete",
            "round_index": 2,
            "accepted_by_actual_score": True,
            "round_start_params": accepted1,
            "candidate_evaluations": [
                evaluation(accepted2, accepted=True, db_qps=120.0),
            ],
        },
    ]
    return rows, accepted2


def count1_with_round1_complete_and_round2_tail(*, unsafe_event: str = "") -> tuple:
    rows, accepted = count1_rows()
    rows.extend(
        [
            {
                "event": "final_feedback_round_complete",
                "round_index": 1,
                "accepted_by_actual_score": False,
                "round_start_params": accepted,
                "candidate_evaluations": [
                    evaluation(
                        {"seq_page_cost": 0.8, "parallel_setup_cost": 80.0},
                        accepted=False,
                        db_qps=90.0,
                    )
                ],
            },
            {"event": "final_feedback_status", "round_index": 2},
            {"event": "iteration_plan_changes", "round_index": None},
            {"event": "plan_resource_estimation", "round_index": None},
            {"event": "gradient_optimization_round_start", "round_index": 2},
            {
                "event": "experience_table_all_subset_ranking",
                "round_index": 2,
            },
            {
                "event": "gradient_round_param_delta_clamp_skipped",
                "round_index": 2,
            },
        ]
    )
    if unsafe_event:
        rows.append({"event": unsafe_event, "round_index": 2})
    return rows, accepted


class BuilderTests(unittest.TestCase):
    def test_count1_clean_boundary_and_dry_run_do_not_write(self) -> None:
        with tempfile.TemporaryDirectory(prefix="accepted_builder_v2_") as temp:
            fixture = Fixture(Path(temp), count=1)
            rows, accepted = count1_rows()
            fixture.write_history(rows)
            fixture.write_point(accepted)
            checkpoint, audit = build_checkpoint(
                fixture.root,
                fixture.point,
                accepted_count=1,
                next_feedback_round=1,
            )
            self.assertEqual(checkpoint["counters"]["accepted_shrink_count"], 1)
            self.assertEqual(checkpoint["next_feedback_round"], 1)
            self.assertTrue(
                checkpoint["builder_provenance"][
                    "range_and_counter_are_source_derived"
                ]
            )
            self.assertEqual(audit["status"], "ready")

            output = fixture.root / "must_not_exist.json"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                status = main(
                    [
                        str(fixture.root),
                        "--accepted-point",
                        str(fixture.point),
                        "--accepted-count",
                        "1",
                        "--next-feedback-round",
                        "1",
                        "--output",
                        str(output),
                        "--dry-run",
                    ]
                )
            self.assertEqual(status, 0)
            self.assertFalse(output.exists())
            self.assertFalse(json.loads(stdout.getvalue())["checkpoint_written"])

    def test_writes_sha_pinned_checkpoint_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory(prefix="accepted_builder_v2_") as temp:
            fixture = Fixture(Path(temp), count=1)
            rows, accepted = count1_rows()
            fixture.write_history(rows)
            fixture.write_point(accepted)
            output = fixture.root / "checkpoint" / "accepted_v2.json"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                status = main(
                    [
                        str(fixture.root),
                        "--accepted-point",
                        str(fixture.point),
                        "--accepted-count",
                        "1",
                        "--next-feedback-round",
                        "1",
                        "--output",
                        str(output),
                    ]
                )
            self.assertEqual(status, 0)
            audit = json.loads(stdout.getvalue())
            self.assertTrue(audit["checkpoint_written"])
            self.assertEqual(
                hashlib.sha256(output.read_bytes()).hexdigest(),
                audit["checkpoint_sha256"],
            )
            checkpoint = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(
                hashlib.sha256(fixture.history.read_bytes()).hexdigest(),
                checkpoint["source_history_sha256"],
            )
            self.assertEqual(
                hashlib.sha256(fixture.counter.read_bytes()).hexdigest(),
                checkpoint["source_accepted_counter_sha256"],
            )

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                repeated = main(
                    [
                        str(fixture.root),
                        "--accepted-point",
                        str(fixture.point),
                        "--accepted-count",
                        "1",
                        "--next-feedback-round",
                        "1",
                        "--output",
                        str(output),
                    ]
                )
            self.assertEqual(repeated, 2)
            self.assertIn("refuse to overwrite", stderr.getvalue())

    def test_count2_after_one_rejected_round(self) -> None:
        with tempfile.TemporaryDirectory(prefix="accepted_builder_v2_") as temp:
            fixture = Fixture(Path(temp), count=2)
            rows, accepted = count2_rows()
            fixture.write_history(rows)
            fixture.write_point(accepted)
            checkpoint, _ = build_checkpoint(
                fixture.history,
                fixture.point,
                accepted_count=2,
                next_feedback_round=3,
            )
            self.assertEqual(
                checkpoint["counters"]["completed_outer_explorations"], 3
            )
            self.assertEqual(
                checkpoint["builder_provenance"]["accepted_round_index"], 2
            )
            self.assertEqual(
                checkpoint["expected_range_state_after_accept"][
                    "parallel_setup_cost"
                ]["max_value"],
                75.0,
            )

    def test_refuses_started_next_round(self) -> None:
        with tempfile.TemporaryDirectory(prefix="accepted_builder_v2_") as temp:
            fixture = Fixture(Path(temp), count=1)
            rows, accepted = count1_rows(start_round1=True)
            fixture.write_history(rows)
            fixture.write_point(accepted)
            with self.assertRaisesRegex(
                CheckpointBuildError, "next feedback round 1 already started"
            ):
                build_checkpoint(
                    fixture.root,
                    fixture.point,
                    accepted_count=1,
                    next_feedback_round=1,
                )

    def test_refuses_counter_mismatch(self) -> None:
        with tempfile.TemporaryDirectory(prefix="accepted_builder_v2_") as temp:
            fixture = Fixture(Path(temp), count=0)
            rows, accepted = count1_rows()
            fixture.write_history(rows)
            fixture.write_point(accepted)
            with self.assertRaisesRegex(
                CheckpointBuildError, "source accepted counter mismatch"
            ):
                build_checkpoint(
                    fixture.root,
                    fixture.point,
                    accepted_count=1,
                    next_feedback_round=1,
                )

    def test_discards_only_pre_replay_next_round_tail(self) -> None:
        with tempfile.TemporaryDirectory(prefix="accepted_builder_v2_") as temp:
            fixture = Fixture(Path(temp), count=1)
            rows, accepted = count1_with_round1_complete_and_round2_tail()
            fixture.write_history(rows)
            fixture.write_point(accepted)
            checkpoint, audit = build_checkpoint(
                fixture.root,
                fixture.point,
                accepted_count=1,
                next_feedback_round=2,
                discard_incomplete_next_round=True,
            )
            self.assertEqual(checkpoint["next_feedback_round"], 2)
            self.assertTrue(audit["discarded_incomplete_next_round"])
            self.assertEqual(audit["discarded_round_index"], 2)
            self.assertIn(
                "experience_table_all_subset_ranking",
                audit["discarded_event_names"],
            )
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                status = main(
                    [
                        str(fixture.root),
                        "--accepted-point",
                        str(fixture.point),
                        "--accepted-count",
                        "1",
                        "--next-feedback-round",
                        "2",
                        "--discard-incomplete-next-round",
                        "--dry-run",
                    ]
                )
            self.assertEqual(status, 0)
            self.assertTrue(
                json.loads(stdout.getvalue())[
                    "discarded_incomplete_next_round"
                ]
            )

    def test_discard_refuses_replay_or_real_validation(self) -> None:
        for unsafe_event in (
            "experience_table_gradient_real_replay_plan",
            "feedback_final_validation",
        ):
            with self.subTest(unsafe_event=unsafe_event), tempfile.TemporaryDirectory(
                prefix="accepted_builder_v2_"
            ) as temp:
                fixture = Fixture(Path(temp), count=1)
                rows, accepted = count1_with_round1_complete_and_round2_tail(
                    unsafe_event=unsafe_event
                )
                fixture.write_history(rows)
                fixture.write_point(accepted)
                with self.assertRaisesRegex(
                    CheckpointBuildError,
                    "cannot discard incomplete next round after real replay",
                ):
                    build_checkpoint(
                        fixture.root,
                        fixture.point,
                        accepted_count=1,
                        next_feedback_round=2,
                        discard_incomplete_next_round=True,
                    )

    def test_discards_proven_rejected_tail_with_real_replay(self) -> None:
        with tempfile.TemporaryDirectory(prefix="accepted_builder_v2_") as temp:
            fixture = Fixture(Path(temp), count=1)
            rows, accepted = count1_rows()
            rows.extend(
                [
                    {"event": "final_feedback_status", "round_index": 1},
                    {
                        "event": "experience_table_gradient_real_replay_plan",
                        "round_index": 1,
                    },
                    {
                        "event": "feedback_final_validation",
                        "round_index": 1,
                    },
                    {
                        "event": "final_feedback_round_complete",
                        "round_index": 1,
                        "accepted_by_actual_score": False,
                    },
                    {
                        "event": "experience_table_gradient_candidate_space_exhausted",
                        "round_index": 2,
                    },
                ]
            )
            fixture.write_history(rows)
            fixture.write_point(accepted)
            checkpoint, audit = build_checkpoint(
                fixture.root,
                fixture.point,
                accepted_count=1,
                next_feedback_round=1,
                discard_rejected_tail=True,
            )
            self.assertEqual(checkpoint["next_feedback_round"], 1)
            self.assertTrue(audit["discarded_rejected_tail"])
            self.assertIn(
                "feedback_final_validation",
                audit["discarded_event_names"],
            )

    def test_rejected_tail_discard_refuses_later_accept(self) -> None:
        with tempfile.TemporaryDirectory(prefix="accepted_builder_v2_") as temp:
            fixture = Fixture(Path(temp), count=1)
            rows, accepted = count1_rows()
            rows.extend(
                [
                    {"event": "final_feedback_status", "round_index": 1},
                    {
                        "event": "final_feedback_round_complete",
                        "round_index": 1,
                        "accepted_by_actual_score": True,
                    },
                ]
            )
            fixture.write_history(rows)
            fixture.write_point(accepted)
            with self.assertRaisesRegex(
                CheckpointBuildError,
                "later accepted shrink",
            ):
                build_checkpoint(
                    fixture.root,
                    fixture.point,
                    accepted_count=1,
                    next_feedback_round=1,
                    discard_rejected_tail=True,
                )


if __name__ == "__main__":
    unittest.main()

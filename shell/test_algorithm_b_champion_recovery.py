#!/usr/bin/env python3
"""Regression tests for Algorithm B champion recovery and cohort handoff."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell import run_pg_algorithm_c_cohort_support_supervisor as support
from shell import run_pg_algorithm_c_scene as framework
from shell import repair_algorithm_c_stale_cohort_replays as stale_repair
from shell import handoff_pg_algorithm_c_r75_to_b_r12_20260823 as handoff
from binary_plan_resource_tuner import PgConn, psql_env


class ChampionRecoveryTest(unittest.TestCase):
    @staticmethod
    def _stability_attempt(
        attempt: int,
        *,
        qps: float,
        cpu: float,
        memory: float,
        io: float,
    ) -> dict:
        confirmations = []
        for worker_key in ("w1", "w2"):
            confirmations.append(
                {
                    "worker_key": worker_key,
                    "db_qps": qps,
                    "resources": {"cpu": cpu, "memory": memory, "io": io},
                }
            )
        return {
            "attempt": attempt,
            "passed": True,
            "technical_failure": False,
            "qps_mean": qps,
            "resource_means": {"cpu": cpu, "memory": memory, "io": io},
            "qps_relative_gap_between_workers": 0.0,
            "resource_relative_gaps_between_workers": {
                "cpu": 0.0,
                "memory": 0.0,
                "io": 0.0,
            },
            "confirmation_decisions": [
                {"accepted": True, "acceptance_tier": "fallback"},
                {"accepted": True, "acceptance_tier": "fallback"},
            ],
            "confirmations": confirmations,
        }

    def test_no_mutex_non_target_io_is_diagnostic_only(self) -> None:
        attempts = [
            self._stability_attempt(1, qps=100.0, cpu=2.0, memory=6.0, io=25.0),
            self._stability_attempt(2, qps=105.0, cpu=2.1, memory=6.1, io=50.0),
        ]
        strict_all = framework.assess_stability_attempts(attempts)
        no_mutex_memory = framework.assess_stability_attempts(
            attempts,
            required_resource_names=("cpu", "memory"),
        )
        self.assertFalse(strict_all["passed"])
        self.assertTrue(no_mutex_memory["passed"])
        self.assertEqual(["io"], no_mutex_memory["diagnostic_only_resource_names"])

    def test_io_target_still_requires_io_repeatability(self) -> None:
        attempts = [
            self._stability_attempt(1, qps=100.0, cpu=2.0, memory=6.0, io=25.0),
            self._stability_attempt(2, qps=105.0, cpu=2.1, memory=6.1, io=50.0),
        ]
        assessment = framework.assess_stability_attempts(
            attempts,
            required_resource_names=("cpu", "memory", "io"),
        )
        self.assertFalse(assessment["passed"])
        self.assertEqual([], assessment["diagnostic_only_resource_names"])

    def test_stability_accepts_any_valid_outer_tier(self) -> None:
        attempt = self._stability_attempt(
            1,
            qps=110.0,
            cpu=2.0,
            memory=6.0,
            io=25.0,
        )
        attempt["required_acceptance_tier"] = "strict"
        attempt["confirmation_decisions"] = [
            {"accepted": True, "acceptance_tier": "fallback"},
            {"accepted": True, "acceptance_tier": "secondary"},
        ]
        canonical = framework.canonicalize_stability_attempt_outcomes([attempt])
        self.assertTrue(canonical[0]["passed"])
        self.assertEqual("strict", canonical[0]["original_required_acceptance_tier"])
        self.assertEqual("fallback", canonical[0]["required_acceptance_tier"])

    def test_cross_worker_dispersion_is_diagnostic_but_temporal_io_is_hard(self) -> None:
        first = self._stability_attempt(
            1,
            qps=100.0,
            cpu=2.0,
            memory=6.0,
            io=25.0,
        )
        first["qps_relative_gap_between_workers"] = 0.25
        first["resource_relative_gaps_between_workers"]["io"] = 0.30
        canonical = framework.canonicalize_stability_attempt_outcomes([first])
        self.assertTrue(canonical[0]["passed"])
        self.assertFalse(canonical[0]["within_attempt_worker_dispersion_passed"])
        self.assertTrue(
            canonical[0]["within_attempt_worker_dispersion_is_diagnostic_only"]
        )

        second = self._stability_attempt(
            2,
            qps=105.0,
            cpu=2.1,
            memory=6.1,
            io=50.0,
        )
        assessment = framework.assess_stability_attempts(
            canonical
            + framework.canonicalize_stability_attempt_outcomes([second]),
            required_resource_names=("cpu", "memory", "io"),
        )
        self.assertFalse(assessment["passed"])

    def test_cached_non_target_io_failure_is_redecided_without_replay(self) -> None:
        controller = framework.LaneController.__new__(framework.LaneController)
        champion = {
            "candidate_identity": "stable-except-shared-io",
            "params": {name: 1.0 for name in framework.ACTIVE_PARAMS},
            "decision": {"acceptance_tier": "fallback"},
            "final_decision": {"acceptance_tier": "fallback"},
        }
        attempts = [
            self._stability_attempt(1, qps=100.0, cpu=2.0, memory=6.0, io=25.0),
            self._stability_attempt(2, qps=105.0, cpu=2.1, memory=6.1, io=50.0),
        ]
        controller.state = {
            "accepted": 7,
            "champion_validation_archive": [
                {
                    "champion_rank": 1,
                    "champion": champion,
                    "result": {
                        "status": "semantic_failed",
                        "physical_attempts": 2,
                        "semantic_attempts": 2,
                        "attempts": attempts,
                        "assessment": {"passed": False},
                    },
                }
            ],
        }
        controller.target = "memory"
        controller.stability_confirmation_rounds = 2
        controller.stability_confirmation_max_attempts = 4
        controller._ordered_feasible_champions = lambda: [champion]
        controller._confirm_one_champion = lambda *_args, **_kwargs: self.fail(
            "cached evidence must not run workload"
        )
        controller.save_state = lambda: None
        controller.restore_clean_baseline = lambda **_kwargs: None
        controller.write_authority_manifest = lambda _width: None
        controller.log = lambda *_args, **_kwargs: None
        with patch.dict(
            "os.environ",
            {
                "BONECT_SHARED_SSD_COORDINATION_ENABLED": "0",
                "BONECT_SHARED_SSD_WORKLOAD_LOCK_MODE": "none",
            },
            clear=False,
        ):
            completed = controller.finalize_best_feasible_champion(0.0078125)
        self.assertTrue(completed)
        self.assertEqual("complete", controller.state["phase"])
        self.assertTrue(controller.state["repeatability"]["passed"])
        self.assertEqual(
            ["cpu", "memory"],
            controller.state["repeatability"]["required_resource_names"],
        )
        self.assertEqual(
            0,
            controller.state["champion_validation_archive"][-1]["result"][
                "additional_physical_workload_invocations"
            ],
        )

    def test_final_canonical_decision_controls_champion_order(self) -> None:
        path_strict = {
            "candidate_identity": "path-strict",
            "accepted_index": 1,
            "normalized": {"db_qps": 110.0},
            "decision": {
                "acceptance_tier": "strict",
                "qps_gain_vs_baseline": 0.10,
                "target_drop_vs_baseline": -0.02,
                "target_resource": "memory",
            },
            "final_decision": {
                "acceptance_tier": "fallback",
                "qps_gain_vs_baseline": 0.10,
                "target_drop_vs_baseline": -0.02,
                "target_resource": "memory",
            },
        }
        canonical_strict = {
            "candidate_identity": "canonical-strict",
            "accepted_index": 2,
            "normalized": {"db_qps": 105.0},
            "decision": {
                "acceptance_tier": "strict",
                "qps_gain_vs_baseline": 0.05,
                "target_drop_vs_baseline": 0.03,
                "target_resource": "memory",
            },
            "final_decision": {
                "acceptance_tier": "strict",
                "qps_gain_vs_baseline": 0.05,
                "target_drop_vs_baseline": 0.03,
                "target_resource": "memory",
            },
        }
        self.assertGreater(
            framework.feasible_champion_sort_key(canonical_strict),
            framework.feasible_champion_sort_key(path_strict),
        )

    def test_two_misses_refine_incumbent_interval_without_accepting(self) -> None:
        controller = framework.LaneController.__new__(framework.LaneController)
        controller.accepted_target = 7
        controller.no_improvement_convergence_rounds = 2
        controller.ranges = {
            name: framework.ParameterRange(*framework.DEFAULT_RANGES[name])
            for name in framework.ACTIVE_PARAMS
        }
        incumbent = {
            name: (
                float(controller.ranges[name].min_value)
                + float(controller.ranges[name].max_value)
            )
            / 2.0
            for name in framework.ACTIVE_PARAMS
        }
        validation = {"db_qps": 100.0, "resources": {"cpu": 1.0}}
        controller.state = {
            "accepted": 3,
            "accepted_target": 7,
            "normalized_width": 0.125,
            "incumbent_params": incumbent,
            "incumbent_validation": validation,
            "consecutive_missed_outer_rounds": 1,
            "consecutive_no_improvement_rounds": 1,
        }
        old_widths = {
            name: (
                float(value.max_value) - float(value.min_value)
            )
            for name, value in controller.ranges.items()
        }
        events = []
        controller.save_state = lambda: None
        controller.log = lambda event, **payload: events.append((event, payload))

        terminal = controller.finish_no_improvement_round(
            round_index=5,
            reason="no_higher_qps",
            exhaustive_current_interval=True,
        )

        self.assertFalse(terminal)
        self.assertEqual(3, controller.state["accepted"])
        self.assertEqual(0, controller.state["consecutive_no_improvement_rounds"])
        self.assertEqual(
            1,
            controller.state[
                "incumbent_centered_stagnation_refinement_count"
            ],
        )
        self.assertEqual(incumbent, controller.state["incumbent_params"])
        self.assertEqual(validation, controller.state["incumbent_validation"])
        for name, value in controller.ranges.items():
            self.assertLess(
                float(value.max_value) - float(value.min_value),
                old_widths[name],
            )
        self.assertEqual(
            "algorithm_c_incumbent_centered_stagnation_shrink",
            events[-1][0],
        )

    def test_under_target_incumbent_terminal_reopens_without_replay(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            controller = framework.LaneController.__new__(
                framework.LaneController
            )
            controller.output_dir = Path(temporary)
            controller.accepted_target = 7
            controller.ranges = {
                name: framework.ParameterRange(*framework.DEFAULT_RANGES[name])
                for name in framework.ACTIVE_PARAMS
            }
            incumbent = {
                name: (
                    float(controller.ranges[name].min_value)
                    + float(controller.ranges[name].max_value)
                )
                / 2.0
                for name in framework.ACTIVE_PARAMS
            }
            controller.state = {
                "phase": "converged_on_incumbent_clean",
                "completion_contract": framework.INCUMBENT_CONVERGENCE_CONTRACT,
                "accepted": 3,
                "accepted_target": 7,
                "round_index": 5,
                "valid_replays": 50,
                "physical_workload_invocations": 50,
                "incumbent_params": incumbent,
                "incumbent_validation": {"db_qps": 100.0},
                "incumbent_convergence": {
                    "clean_restore_receipt": {"passed": True}
                },
                "final_champion": {"params": incumbent},
            }
            framework.write_json(
                controller.output_dir / "authority_manifest.json",
                {"accepted": 3, "terminal": True},
            )
            events = []
            controller.save_state = lambda: None
            controller.log = lambda event, **payload: events.append(
                (event, payload)
            )

            reopened = controller.reopen_under_target_incumbent_convergence()

            self.assertTrue(reopened)
            self.assertEqual("formal_search", controller.state["phase"])
            self.assertEqual(3, controller.state["accepted"])
            self.assertEqual(50, controller.state["valid_replays"])
            self.assertEqual(
                50,
                controller.state["physical_workload_invocations"],
            )
            archives = list(
                (controller.output_dir / "superseded_authorities").glob(
                    "*.json"
                )
            )
            self.assertEqual(1, len(archives))
            self.assertEqual(
                "algorithm_c_under_target_incumbent_convergence_reopened",
                events[-1][0],
            )

    def test_confirmation_resumes_existing_three_semantic_attempts(self) -> None:
        controller = framework.LaneController.__new__(framework.LaneController)
        champion = {
            "candidate_identity": "champion-a",
            "params": {name: 1.0 for name in framework.ACTIVE_PARAMS},
            "decision": {"acceptance_tier": "fallback"},
            "final_decision": {"acceptance_tier": "fallback"},
        }
        prior = [
            {"attempt": 1, "physical_attempt": 1, "passed": False},
            {"attempt": 2, "physical_attempt": 2, "passed": True},
            {"attempt": 3, "physical_attempt": 3, "passed": False},
        ]
        controller.state = {
            "phase": "accepted_target_pending_champion_stability",
            "final_champion": champion,
            "stability_rounds": prior,
            "consecutive_stable_rounds": 0,
            "round_index": 7,
            "baseline_by_worker": {"w1": {}, "w2": {}},
            "canonical_baseline": {},
            "qps_noise_model": {},
        }
        controller.target = "memory"
        controller.cfg = {}
        controller.capacity = {"cpu": 4.0, "memory": 4.0, "io": 100.0}
        controller.stability_confirmation_rounds = 2
        controller.stability_confirmation_max_attempts = 4
        controller.stop_requested = False
        controller.save_state = lambda: None
        controller.log = lambda *_args, **_kwargs: None
        calls = []

        def replay(candidates, **kwargs):
            calls.append((candidates, kwargs))
            return [
                {
                    "worker_key": "w1",
                    "status": "ok",
                    "db_qps": 101.0,
                    "resources": {"cpu": 1.0, "memory": 1.0, "io": 1.0},
                },
                {
                    "worker_key": "w2",
                    "status": "ok",
                    "db_qps": 102.0,
                    "resources": {"cpu": 1.0, "memory": 1.0, "io": 1.0},
                },
            ]

        controller.replay_batch = replay
        accepted = {
            "accepted": True,
            "acceptance_tier": "fallback",
        }
        with patch.object(framework, "physical_valid", return_value=True), patch.object(
            framework,
            "normalize_to_worker_baseline",
            side_effect=lambda row, *_args: row,
        ), patch.object(
            framework,
            "outer_semantic_acceptance_details",
            return_value=accepted,
        ), patch.object(
            framework,
            "assess_stability_attempts",
            return_value={"passed": False},
        ):
            result = controller._confirm_one_champion(champion, champion_rank=1)

        self.assertEqual("semantic_failed", result["status"])
        self.assertEqual(1, len(calls))
        self.assertEqual(4, calls[0][0][0]["stability_attempt"])
        self.assertEqual(4, len(result["attempts"]))

    def test_signal_stop_uses_next_physical_attempt_without_overwrite(self) -> None:
        controller = framework.LaneController.__new__(framework.LaneController)
        champion = {
            "candidate_identity": "champion-a",
            "params": {name: 1.0 for name in framework.ACTIVE_PARAMS},
            "decision": {"acceptance_tier": "fallback"},
            "final_decision": {"acceptance_tier": "fallback"},
        }
        controller.state = {
            "phase": "stopped_by_signal_clean",
            "final_champion": champion,
            "stability_rounds": [],
            "consecutive_stable_rounds": 0,
            "round_index": 7,
            "baseline_by_worker": {"w1": {}, "w2": {}},
            "canonical_baseline": {},
            "qps_noise_model": {},
            "champion_validation_archive": [
                {
                    "champion": champion,
                    "result": {"status": "signal_stop", "physical_attempts": 1},
                }
            ],
        }
        controller.target = "memory"
        controller.cfg = {}
        controller.capacity = {"cpu": 4.0, "memory": 4.0, "io": 100.0}
        controller.stability_confirmation_rounds = 2
        controller.stability_confirmation_max_attempts = 4
        controller.stop_requested = True
        controller.save_state = lambda: None
        controller.log = lambda *_args, **_kwargs: None
        calls = []
        controller.replay_batch = lambda candidates, **kwargs: (
            calls.append((candidates, kwargs)) or []
        )
        result = controller._confirm_one_champion(champion, champion_rank=1)
        self.assertEqual("signal_stop", result["status"])
        self.assertEqual(2, calls[0][0][0]["stability_attempt"])

    def test_support_derives_no_mutex_environment_from_campaign(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            campaign_root = Path(temporary)
            for scene in support.SCENES:
                lane_root = campaign_root / scene
                lane_root.mkdir()
                (lane_root / "optimizer_config.json").write_text(
                    json.dumps(
                        {
                            "algorithm_c": {
                                "workload_contract": {
                                    "shared_ssd_coordination": {
                                        "contract": "pg_algorithm_c_user_authorized_no_mutex_v1",
                                        "enabled": False,
                                        "physical_workload_mode": "none",
                                    }
                                }
                            }
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
            with patch.dict("os.environ", {}, clear=True):
                receipt = support.configure_shared_ssd_execution_domain(
                    campaign_root
                )
                self.assertTrue(receipt["no_mutex"])
                self.assertEqual("0", receipt["coordination_enabled_env"])
                self.assertEqual("none", receipt["workload_lock_mode_env"])

    def test_support_uses_lane_local_workload_environments(self) -> None:
        campaign_root = Path("/tmp/campaign")
        job = support.support_workload_environment("job_memory", campaign_root)
        tpch = support.support_workload_environment("tpch_memory", campaign_root)
        xp_cpu = support.support_workload_environment("xp_cpu", campaign_root)
        xp_io = support.support_workload_environment("xp_io", campaign_root)
        self.assertEqual("job", job["GENERIC_WORKLOAD"])
        self.assertEqual("2", job["JOB_WORKERS"])
        self.assertEqual("tpch", tpch["GENERIC_WORKLOAD"])
        self.assertEqual("5", tpch["BENCHBASE_SCALEFACTOR"])
        self.assertEqual(
            "/tmp/campaign/benchbase_runtime",
            tpch["BENCHBASE_RUNTIME_ROOT"],
        )
        self.assertEqual("hybenchxp", xp_cpu["GENERIC_WORKLOAD"])
        self.assertEqual(xp_cpu, xp_io)

    def test_validation_environment_override_does_not_mutate_process(self) -> None:
        pg = PgConn(host="127.0.0.1", port="5432", user="postgres", dbname="db")
        with patch.dict("os.environ", {"GENERIC_WORKLOAD": "sentinel"}, clear=True):
            job = psql_env(pg, {"GENERIC_WORKLOAD": "job", "JOB_WORKERS": "2"})
            tpch = psql_env(pg, {"GENERIC_WORKLOAD": "tpch"})
            self.assertEqual("job", job["GENERIC_WORKLOAD"])
            self.assertEqual("tpch", tpch["GENERIC_WORKLOAD"])
            self.assertEqual("sentinel", os.environ["GENERIC_WORKLOAD"])

    def test_legacy_failed_completion_attempt_is_removed_on_startup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            log_path = Path(temporary) / "algorithm_b.jsonl"
            label = "algorithm_c_r007_champion_03_stability_attempt_02_c01_w1"
            validation = {
                "label": label,
                "status": "ok",
                "ssd_qualification": {"passed": True},
                "candidate_cohort_completion_barrier": {
                    "enabled": True,
                    "passed": False,
                },
            }
            log_path.write_text(
                json.dumps(
                    {
                        "event": "algorithm_c_candidate_replay",
                        "validation": validation,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            attempt = {
                "attempt": 1,
                "physical_attempt": 2,
                "passed": False,
                "confirmations": [validation],
            }
            controller = framework.LaneController.__new__(framework.LaneController)
            controller.log_path = log_path
            controller.state = {
                "accepted": 7,
                "valid_replays": 9,
                "technical_failures": 0,
                "stability_rounds": [attempt],
                "consecutive_stable_rounds": 0,
                "active_champion_validation": {
                    "physical_attempt": 3,
                    "attempts": [attempt],
                },
                "valid_replay_identity_memory": {
                    "identities": {
                        "invalid": {"labels": [label]},
                        "valid": {"labels": ["other"]},
                    }
                },
            }
            controller.save_state = lambda: None
            events = []
            controller.log = lambda event, **payload: events.append((event, payload))
            controller.repair_legacy_completion_domain_pollution()
            self.assertEqual([], controller.state["stability_rounds"])
            self.assertEqual(
                [],
                controller.state["active_champion_validation"]["attempts"],
            )
            self.assertEqual(
                3,
                controller.state["active_champion_validation"]["physical_attempt"],
            )
            self.assertEqual(8, controller.state["valid_replays"])
            self.assertEqual(0, controller.state["technical_failures"])
            self.assertNotIn(
                "invalid",
                controller.state["valid_replay_identity_memory"]["identities"],
            )
            self.assertEqual(
                "algorithm_c_completion_domain_pollution_repaired",
                events[0][0],
            )


class CohortAuthorityHandoffTest(unittest.TestCase):
    def test_handoff_waits_for_algorithm_c_support_session(self) -> None:
        self.assertIn("pgc_trainbound_r75_support", handoff.C_SESSIONS)

    def test_stale_cohort_repair_preserves_physical_and_optimizer_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            lane_root = Path(temporary)
            state = {
                "phase": "stopped_by_signal_clean",
                "accepted": 3,
                "valid_replays": 12,
                "physical_workload_invocations": 14,
                "technical_failures": 2,
                "incumbent_params": {"seq_page_cost": 1.2},
                "ranges": {"seq_page_cost": {"min_value": 1.0, "max_value": 1.4}},
                "gradient_state": {"seq_page_cost": 0.1},
                "valid_replay_identity_memory": {
                    "identities": {
                        "identity": {"labels": ["bad-label"], "rounds": [6]}
                    }
                },
            }
            framework.write_json(lane_root / "algorithm_c_state.json", state)
            framework.append_jsonl(
                lane_root / "algorithm_c.jsonl",
                {
                    "event": "algorithm_c_under_target_incumbent_convergence_reopened",
                    "time": 10.0,
                },
            )
            framework.append_jsonl(
                lane_root / "algorithm_c.jsonl",
                {
                    "event": "algorithm_c_candidate_replay",
                    "time": 11.0,
                    "round_index": 6,
                    "validation": {
                        "label": "bad-label",
                        "candidate_cohort_barrier": {"wave_sequence": 29},
                    },
                },
            )
            argv = [
                "repair",
                "--lane-root",
                str(lane_root),
                "--round-index",
                "6",
                "--maximum-stale-wave-sequence",
                "32",
                "--expected-accepted",
                "3",
            ]
            with patch.object(sys, "argv", argv):
                self.assertEqual(0, stale_repair.main())
            repaired = framework.read_json(lane_root / "algorithm_c_state.json")
            self.assertEqual(11, repaired["valid_replays"])
            self.assertEqual(14, repaired["physical_workload_invocations"])
            self.assertEqual(3, repaired["technical_failures"])
            self.assertEqual({"seq_page_cost": 1.2}, repaired["incumbent_params"])
            self.assertEqual({}, repaired["valid_replay_identity_memory"]["identities"])

    def test_candidate_rows_reject_dead_restart_participants(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            stage_root = Path(temporary)
            wave_sequence = 19
            for scene, pid in (
                ("job_memory", os.getpid()),
                ("xp_io", 2_000_000_000),
            ):
                identity = framework.canonical_sha256(
                    {"scene": scene, "wave_sequence": wave_sequence}
                )
                (stage_root / f"{identity}.ready").write_text(
                    json.dumps(
                        {
                            "scene": scene,
                            "wave_sequence": wave_sequence,
                            "pid": pid,
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
            rows, invalid = framework._validated_cohort_rows(
                stage_root,
                suffix=".ready",
                participant_identity_fields={"wave_sequence": wave_sequence},
                expected_fields={"wave_sequence": wave_sequence},
                require_live_pid=True,
            )
            self.assertEqual(["job_memory"], [row["scene"] for row in rows])
            self.assertEqual(1, len(invalid))

    def test_controller_skips_completed_historical_candidate_waves(self) -> None:
        cohort_id = f"unittest-restart-{os.getpid()}-{time.time_ns()}"
        environment = {
            "BONECT_ALGORITHM_C_CANDIDATE_COHORT_ID": cohort_id,
            "BONECT_ALGORITHM_C_CANDIDATE_COHORT_SIZE": "4",
            "BONECT_ALGORITHM_C_CANDIDATE_COHORT_TIMEOUT_S": "2",
        }
        wave_sequence = 25
        stage_key = framework.canonical_sha256(
            {"cohort_id": cohort_id, "wave_sequence": wave_sequence}
        )
        stage_root = Path(
            f"/tmp/resource_adaptive_algorithm_c_candidate_cohort_{stage_key}"
        )
        stage_root.mkdir()
        try:
            for scene in support.SCENES:
                identity = framework.canonical_sha256(
                    {"scene": scene, "wave_sequence": wave_sequence}
                )
                (stage_root / f"{identity}.done").write_text(
                    json.dumps(
                        {
                            "scene": scene,
                            "wave_sequence": wave_sequence,
                            "pid": os.getpid(),
                            "wave_valid": True,
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
            with patch.dict(os.environ, environment, clear=False):
                selection = (
                    framework.next_unfinished_algorithm_c_candidate_wave_sequence(
                        24
                    )
                )
            self.assertEqual(26, selection["wave_sequence"])
            self.assertEqual([25], selection["skipped_completed_waves"])
        finally:
            for path in stage_root.iterdir():
                path.unlink()
            stage_root.rmdir()

    def test_terminal_wait_timeout_is_longer_than_wave_barrier_timeout(self) -> None:
        self.assertGreater(
            support.DEFAULT_TERMINAL_WAIT_TIMEOUT_S,
            support.DEFAULT_BARRIER_TIMEOUT_S,
        )

    def test_complete_lane_waits_for_manifest_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            lane_root = Path(temporary)
            state_path = lane_root / support.STATE_FILE_NAME
            state_path.write_text(
                json.dumps({"phase": "complete"}) + "\n",
                encoding="utf-8",
            )

            def delayed_manifest() -> None:
                time.sleep(0.1)
                (lane_root / "authority_manifest.json").write_text(
                    json.dumps({"accepted": 7}) + "\n",
                    encoding="utf-8",
                )

            thread = threading.Thread(target=delayed_manifest)
            thread.start()
            observed = support.wait_for_terminal_manifest(
                lane_root,
                timeout_s=2.0,
            )
            thread.join()
            self.assertEqual("complete", observed["phase"])
            self.assertTrue((lane_root / "authority_manifest.json").is_file())

    def test_stale_manifest_does_not_make_reopened_lane_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            lane_root = Path(temporary)
            state_path = lane_root / support.STATE_FILE_NAME
            state_path.write_text(
                json.dumps({"phase": "formal_search"}) + "\n",
                encoding="utf-8",
            )
            (lane_root / "authority_manifest.json").write_text(
                json.dumps({"accepted": 3}) + "\n",
                encoding="utf-8",
            )

            def delayed_terminal() -> None:
                time.sleep(0.1)
                state_path.write_text(
                    json.dumps(
                        {"phase": "qps_progress_candidate_space_exhausted_clean"}
                    )
                    + "\n",
                    encoding="utf-8",
                )

            thread = threading.Thread(target=delayed_terminal)
            thread.start()
            observed = support.wait_for_terminal_manifest(
                lane_root,
                timeout_s=2.0,
            )
            thread.join()
            self.assertEqual(
                "qps_progress_candidate_space_exhausted_clean",
                observed["phase"],
            )

    def test_incumbent_convergence_waits_for_clean_restore_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            lane_root = Path(temporary)
            state_path = lane_root / support.STATE_FILE_NAME
            state_path.write_text(
                json.dumps(
                    {
                        "phase": "converged_on_incumbent_clean",
                        "incumbent_convergence": {},
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            def delayed_restore_commit() -> None:
                time.sleep(0.1)
                state_path.write_text(
                    json.dumps(
                        {
                            "phase": "converged_on_incumbent_clean",
                            "incumbent_convergence": {
                                "clean_restore_receipt": {"passed": True}
                            },
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )

            thread = threading.Thread(target=delayed_restore_commit)
            thread.start()
            observed = support.wait_for_terminal_manifest(
                lane_root,
                timeout_s=2.0,
            )
            thread.join()
            receipt = observed["incumbent_convergence"][
                "clean_restore_receipt"
            ]
            self.assertTrue(receipt["passed"])

    def test_support_accepts_manifest_when_stability_is_engineering_only(self) -> None:
        manifest = {
            "accepted": 7,
            "accepted_target": 7,
            "normalized_width": 0.0078125,
            "normalized_width_threshold": 0.01,
            "consecutive_stable_rounds": 0,
            "required_consecutive_stable_rounds": 0,
            "post_convergence_stability_required": False,
            "repeatability": {
                "required": False,
                "performed": False,
                "passed": None,
            },
            "manual_mutation_after_formal_start": False,
        }
        receipt = support.validate_authoritative_completion_manifest(
            manifest,
            "job_memory",
        )
        self.assertTrue(receipt["passed"])
        self.assertEqual(0, receipt["required_consecutive_stable_rounds"])

    def test_support_rejects_missing_required_stability(self) -> None:
        manifest = {
            "accepted": 7,
            "accepted_target": 7,
            "normalized_width": 0.0078125,
            "normalized_width_threshold": 0.01,
            "consecutive_stable_rounds": 1,
            "required_consecutive_stable_rounds": 2,
            "post_convergence_stability_required": True,
            "repeatability": {"required": True, "passed": False},
            "manual_mutation_after_formal_start": False,
        }
        with self.assertRaisesRegex(
            RuntimeError,
            "required_consecutive_stable_rounds_not_reached",
        ):
            support.validate_authoritative_completion_manifest(
                manifest,
                "job_memory",
            )

    def test_qps_progress_exhaustion_is_clean_support_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            lane_root = Path(temporary)
            (lane_root / support.STATE_FILE_NAME).write_text(
                json.dumps(
                    {"phase": "qps_progress_candidate_space_exhausted_clean"}
                )
                + "\n",
                encoding="utf-8",
            )
            observed = support.wait_for_terminal_manifest(
                lane_root,
                timeout_s=0.2,
            )
            self.assertEqual(
                "qps_progress_candidate_space_exhausted_clean",
                observed["phase"],
            )

    def test_support_continues_after_furthest_campaign_wave(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            campaign_root = Path(temporary)
            for scene, sequence in zip(support.SCENES, (25, 25, 24, 25)):
                lane_root = campaign_root / scene
                lane_root.mkdir()
                (lane_root / support.STATE_FILE_NAME).write_text(
                    json.dumps({"formal_candidate_wave_sequence": sequence})
                    + "\n",
                    encoding="utf-8",
                )
            self.assertEqual(
                26,
                support.next_campaign_wave_sequence(campaign_root, "fresh-cohort"),
            )

    def test_support_joins_inflight_furthest_wave(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            campaign_root = Path(temporary) / "campaign"
            stage_root = Path(temporary) / "stage"
            stage_root.mkdir()
            for scene, sequence in zip(support.SCENES, (25, 25, 24, 25)):
                lane_root = campaign_root / scene
                lane_root.mkdir(parents=True)
                (lane_root / support.STATE_FILE_NAME).write_text(
                    json.dumps({"formal_candidate_wave_sequence": sequence})
                    + "\n",
                    encoding="utf-8",
                )
            (stage_root / "peer.ready").write_text(
                json.dumps({"scene": "job_memory", "pid": os.getpid()}) + "\n",
                encoding="utf-8",
            )
            with patch.object(support, "barrier_dir", return_value=stage_root):
                self.assertEqual(
                    25,
                    support.next_campaign_wave_sequence(campaign_root, "cohort"),
                )

    def test_support_skips_stale_inflight_ready_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            campaign_root = Path(temporary) / "campaign"
            stage_root = Path(temporary) / "stage"
            stage_root.mkdir()
            for scene in support.SCENES:
                lane_root = campaign_root / scene
                lane_root.mkdir(parents=True)
                (lane_root / support.STATE_FILE_NAME).write_text(
                    json.dumps({"formal_candidate_wave_sequence": 25}) + "\n",
                    encoding="utf-8",
                )
            (stage_root / "peer.ready").write_text(
                json.dumps({"scene": "job_memory", "pid": 999999999}) + "\n",
                encoding="utf-8",
            )
            with patch.object(support, "barrier_dir", return_value=stage_root):
                self.assertEqual(
                    26,
                    support.next_campaign_wave_sequence(campaign_root, "cohort"),
                )

    def test_support_skips_fully_completed_furthest_wave(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            campaign_root = Path(temporary) / "campaign"
            stage_root = Path(temporary) / "stage"
            stage_root.mkdir()
            for scene in support.SCENES:
                lane_root = campaign_root / scene
                lane_root.mkdir(parents=True)
                (lane_root / support.STATE_FILE_NAME).write_text(
                    json.dumps({"formal_candidate_wave_sequence": 25}) + "\n",
                    encoding="utf-8",
                )
                identity = scene.replace("_", "-")
                (stage_root / f"{identity}.ready").write_text(
                    json.dumps({"scene": scene}) + "\n",
                    encoding="utf-8",
                )
                (stage_root / f"{identity}.done").write_text(
                    json.dumps({"scene": scene}) + "\n",
                    encoding="utf-8",
                )
            with patch.object(support, "barrier_dir", return_value=stage_root):
                self.assertEqual(
                    26,
                    support.next_campaign_wave_sequence(campaign_root, "cohort"),
                )

    def test_support_waits_for_late_formal_physical_peer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            campaign_root = Path(temporary)
            phases = {
                "job_memory": "fallback_plan_change_real_replay",
                "tpch_memory": "base_top5_real_replay",
                "xp_cpu": "candidate_space_exhausted_without_accepted_target_clean",
                "xp_io": "qps_progress_candidate_space_exhausted_clean",
            }
            for scene, phase in phases.items():
                lane_root = campaign_root / scene
                lane_root.mkdir()
                (lane_root / support.STATE_FILE_NAME).write_text(
                    json.dumps({"phase": phase}) + "\n",
                    encoding="utf-8",
                )

            early_rows = [
                {
                    "scene": "tpch_memory",
                    "batch_kind": "base_top5_empty_wave_control",
                    "wave_sequence": 27,
                },
                {
                    "scene": "xp_cpu",
                    "batch_kind": "cohort_support_control",
                    "support_wave_width": 0,
                    "wave_sequence": 27,
                },
            ]
            self.assertIsNone(
                support.select_support_physical_peer(campaign_root, early_rows)
            )

            late_job = {
                "scene": "job_memory",
                "batch_kind": "fallback_plan_change",
                "wave_start": 4,
                "wave_sequence": 27,
            }
            selected = support.select_support_physical_peer(
                campaign_root,
                early_rows + [late_job],
            )
            self.assertEqual(late_job, selected)

    def test_support_final_restore_passes_config_to_worker_connection(self) -> None:
        cfg = {"database": {"host": "127.0.0.1"}}
        workers = [{"name": "w1", "port": 59521}]
        initial_params = {"random_page_cost": 4.0}
        connection = object()
        with tempfile.TemporaryDirectory() as temporary:
            log_path = Path(temporary) / "support.jsonl"
            with (
                patch.object(
                    support,
                    "restore_support_workers",
                    return_value=[{"worker": "w1", "passed": True}],
                ),
                patch.object(
                    support,
                    "pg_conn_from_worker",
                    return_value=connection,
                ) as pg_conn,
                patch.object(
                    support,
                    "apply_pg_params",
                    return_value={"random_page_cost": "4"},
                ) as apply_params,
            ):
                support.restore_clean_baseline(
                    cfg,
                    "memory",
                    workers,
                    initial_params,
                    "tpch_memory",
                    log_path,
                )
        pg_conn.assert_called_once_with(cfg, workers[0])
        apply_params.assert_called_once_with(connection, initial_params)

    def test_support_accepts_all_formal_empty_wave(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            campaign_root = Path(temporary)
            for scene in support.SCENES:
                lane_root = campaign_root / scene
                lane_root.mkdir()
                phase = (
                    "base_top5_real_replay"
                    if scene in {"job_memory", "tpch_memory"}
                    else "qps_progress_candidate_space_exhausted_clean"
                )
                (lane_root / support.STATE_FILE_NAME).write_text(
                    json.dumps({"phase": phase}) + "\n",
                    encoding="utf-8",
                )
            rows = [
                {
                    "scene": scene,
                    "batch_kind": "base_top5_empty_wave_control",
                    "wave_sequence": 28,
                }
                for scene in ("job_memory", "tpch_memory")
            ]
            selected = support.select_support_physical_peer(campaign_root, rows)
            self.assertIsNotNone(selected)
            self.assertEqual("base_top5_empty_wave_control", selected["batch_kind"])

    def test_support_wave_yields_when_own_lane_reopens(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            campaign_root = Path(temporary)
            for scene in support.SCENES:
                lane_root = campaign_root / scene
                lane_root.mkdir()
                phase = (
                    "base_top5_real_replay"
                    if scene == "tpch_memory"
                    else "complete"
                )
                if scene == "xp_io":
                    phase = "current_interval_expansion_real_replay"
                (lane_root / support.STATE_FILE_NAME).write_text(
                    json.dumps({"phase": phase}) + "\n",
                    encoding="utf-8",
                )

            completed = support.run_support_wave(
                cfg={},
                scene="xp_io",
                target="io",
                workers=[],
                params={},
                campaign_root=campaign_root,
                cohort_id="reopened-lane",
                cohort_size=4,
                wave_sequence=95,
                timeout_s=0.2,
                log_path=campaign_root / "support.jsonl",
            )
            self.assertFalse(completed)
            self.assertFalse(
                support.barrier_dir("reopened-lane", 95).exists()
            )


if __name__ == "__main__":
    unittest.main()

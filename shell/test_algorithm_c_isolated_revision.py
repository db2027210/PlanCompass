#!/usr/bin/env python3
"""Offline checks for two-scene ownership, contamination and comparison handoff."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shell import run_pg_algorithm_c_scene as base
from shell import run_pg_algorithm_c_job_io_revision as runner
from shell import pg_algorithm_c_isolated_guard as guards
from shell import pg_algorithm_c_efficiency as efficiency


def algorithm(scene="job_memory"):
    return {"scene": scene, "isolated_search": {
        "contract": guards.CONTRACT, "enabled": True,
        "external_workload_guard_required": True, "post_restore_quiet_required": True,
        "expected_non_search_guc_sha256": "a" * 64,
    }, "workload_contract": {
        "baseline_cohort": {"lane_count": 1},
        "candidate_cohort": {"contract": "algorithm_c_guarded_isolated_candidate_waves_v1", "lane_count": 1},
    }}


class IsolatedRevisionTest(unittest.TestCase):
    def test_efficiency_waits_are_separate_and_failed_spans_count(self):
        with tempfile.TemporaryDirectory() as directory:
            now = [10.0]
            with mock.patch.object(efficiency.time, "perf_counter", side_effect=lambda: now[0]):
                recorder = efficiency.EfficiencyRecorder(Path(directory))
                with recorder.measure("external_quiet_wait", "startup"):
                    now[0] += 120.0
                with self.assertRaisesRegex(RuntimeError, "failed"):
                    with recorder.measure("post_restore_quiet_wait", "wave"):
                        now[0] += 15.0
                        raise RuntimeError("failed")
                now[0] += 30.0
                receipt = recorder.snapshot({}, phase="failed")
            self.assertEqual(receipt["environment_wait_s"], 135.0)
            self.assertEqual(receipt["end_to_end_wall_s"], 165.0)
            self.assertEqual(receipt["wall_excluding_environment_wait_s"], 30.0)
            self.assertFalse(receipt["old_c_and_b_search_efficiency_comparable"])
            self.assertIsNone(receipt["progress"]["accepted_incumbent_gain_pct"])

    def test_efficiency_progress_is_passive_and_counts_failed_attempts(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = efficiency.EfficiencyRecorder(Path(directory))
            state = {"canonical_baseline": {"db_qps": 100}, "incumbent_validation": {"db_qps": 120},
                     "accepted": 1, "round_index": 2, "physical_workload_invocations": 9,
                     "budgeted_trial": {"search_replays": 5, "search_valid_replays": 4,
                                        "search_technical_failures": 1, "planning_s": 10}}
            before = json.dumps(state, sort_keys=True)
            receipt = recorder.snapshot(state)
            recorder.snapshot(state)
            self.assertEqual(before, json.dumps(state, sort_keys=True))
            self.assertAlmostEqual(receipt["progress"]["accepted_incumbent_gain_pct"], 20.0)
            self.assertEqual(receipt["progress"]["search_physical_attempts"], 5)
            self.assertEqual(receipt["progress"]["search_technical_failures"], 1)
            self.assertEqual(len((Path(directory) / "efficiency_progress.jsonl").read_text().splitlines()), 1)

    def test_final_efficiency_receipt_reads_actual_controller_state_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "xp_io").mkdir()
            (root / "xp_io" / "algorithm_c_state.json").write_text(json.dumps({
                "budgeted_trial": {"search_replays": 3, "search_technical_failures": 1},
            }))
            with mock.patch.object(runner, "verify_sealed"), \
                 mock.patch.object(runner, "_run_scene", side_effect=RuntimeError("controller failed")):
                with self.assertRaisesRegex(RuntimeError, "controller failed"):
                    runner.run_scene(root, "xp_io")
            receipt = json.loads((root / "xp_io" / "efficiency.json").read_text())
            self.assertEqual(receipt["phase"], "failed")
            self.assertEqual(receipt["progress"]["search_physical_attempts"], 3)

    def test_only_authorized_scenes_receive_single_lane_exception(self):
        for scene in ("job_memory", "xp_io"):
            self.assertTrue(base.guarded_c_single_lane_authorized(algorithm(scene)))
        for scene in ("tpch_memory", "xp_cpu"):
            self.assertFalse(base.guarded_c_single_lane_authorized(algorithm(scene)))
        cfg = algorithm()
        cfg["isolated_search"]["post_restore_quiet_required"] = False
        self.assertFalse(base.guarded_c_single_lane_authorized(cfg))

    def test_explicit_user_accepted_uncontrolled_mode_is_authorized(self):
        cfg = algorithm()
        cfg["isolated_search"].update({
            "external_workload_guard_required": False,
            "external_workload_interference_user_accepted": True,
            "external_workload_interference_policy": guards.UNCONTROLLED_EXTERNAL_POLICY,
        })
        self.assertTrue(base.guarded_c_single_lane_authorized(cfg))
        cfg["isolated_search"].pop("external_workload_interference_user_accepted")
        self.assertFalse(base.guarded_c_single_lane_authorized(cfg))

    def test_generic_entry_cannot_run_guarded_configuration_without_guard(self):
        controller = base.LaneController.__new__(base.LaneController)
        controller.algorithm_cfg = algorithm()
        with self.assertRaisesRegex(RuntimeError, "guarded entry point"):
            controller.physical_wave_guard_event("before_restore", "wave")
        controller.algorithm_cfg = {}
        controller.physical_wave_guard_event("before_restore", "wave")

    def test_environment_is_single_scene_and_full_workload(self):
        env = guards.isolated_environment("job_memory", Path("/tmp/isolated-test"))
        self.assertEqual(env["BONECT_ALGORITHM_C_BASELINE_COHORT_SIZE"], "1")
        self.assertEqual(env["BONECT_ALGORITHM_C_CANDIDATE_COHORT_SIZE"], "1")
        self.assertEqual(env["JOB_FIXED_SCHEDULE_SHA256"], runner.replay.JOB_FIXED_SCHEDULE_SHA256)
        with self.assertRaises(ValueError):
            guards.isolated_environment("tpch_memory", Path("/tmp/isolated-test"))

    def guard(self, path):
        controller = SimpleNamespace(
            cfg={}, algorithm_cfg=algorithm(), workers=[], target="memory", output_dir=path,
            stop_requested=False,
        )
        return guards.IsolatedWaveGuard(controller)

    def test_contaminated_rows_cannot_enter_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            guard = self.guard(Path(directory))
            guard.receipt = {"passed": False, "contaminated": True}
            rows = [{"status": "ok", "technical_failure": False, "db_qps": 9999.0}]
            with mock.patch.object(guard, "close"), mock.patch.object(guard, "audit"):
                guard.after_replay(rows)
            self.assertTrue(rows[0]["technical_failure"])
            self.assertFalse(base.physical_valid(rows[0]))

    def test_non_search_guc_drift_invalidates_even_clean_workload(self):
        with tempfile.TemporaryDirectory() as directory:
            guard = self.guard(Path(directory))
            guard.receipt = {"passed": True}
            rows = [{"status": "ok", "technical_failure": False}]
            with mock.patch.object(guard, "close"), mock.patch.object(guard, "audit", side_effect=RuntimeError("GUC drift")):
                guard.after_replay(rows)
            self.assertFalse(base.physical_valid(rows[0]))
            self.assertIn("GUC drift", rows[0]["errors"])

    def test_restore_interference_blocks_workload(self):
        with tempfile.TemporaryDirectory() as directory:
            guard = self.guard(Path(directory))
            guard.receipt = {"contaminated": True}
            with mock.patch.object(guards.replay, "wait_for_post_restore_ssd_quiet", return_value={"passed": True}), \
                 mock.patch.object(guards.replay, "scan_external_workload_processes", return_value=([], [])), \
                 mock.patch.object(guard, "close") as close:
                with self.assertRaisesRegex(RuntimeError, "no workload admitted"):
                    guard.after_restore(None)
                close.assert_called_once()

    def test_owned_backend_roots_exclude_only_declared_container_descendants(self):
        ps_output = "\n".join([
            "101 systemd+ 5 postgres: postgres benchbase local EXPLAIN",
            "201 systemd+ 5 postgres: postgres benchbase external UPDATE",
            "301 wangxr 5 java -jar benchbase.jar -b tpcc --execute=true",
        ])
        completed = guards.subprocess.CompletedProcess(["ps"], 0, ps_output, "")
        with mock.patch.object(guards.replay.subprocess, "run", return_value=completed), \
             mock.patch.object(guards.replay, "pid_descends_from", side_effect=lambda pid, roots: pid == 101):
            rows, errors = guards.replay.scan_external_workload_processes(
                ignored_backend_ancestor_pids={10},
            )
        self.assertFalse(errors)
        self.assertEqual({row["pid"] for row in rows}, {201, 301})

    def test_guard_passes_owned_roots_to_monitor(self):
        with tempfile.TemporaryDirectory() as directory:
            guard = self.guard(Path(directory))
            with mock.patch.object(guard, "resolve_owned_backend_roots", return_value={11, 22}), \
                 mock.patch.object(guards.replay, "wait_for_external_quiet", return_value=True) as quiet, \
                 mock.patch.object(guard, "audit"), \
                 mock.patch.object(guards.threading, "Thread") as thread:
                guard.before_restore("wave")
            self.assertEqual(quiet.call_args.kwargs["ignored_backend_ancestor_pids"], {11, 22})
            self.assertEqual(thread.call_args.kwargs["args"][3], {11, 22})

    def test_user_accepted_mode_skips_external_wait_and_monitor(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = SimpleNamespace(
                cfg={}, algorithm_cfg=algorithm(), workers=[], target="memory",
                output_dir=Path(directory), stop_requested=False,
            )
            controller.algorithm_cfg["isolated_search"].update({
                "external_workload_guard_required": False,
                "external_workload_interference_user_accepted": True,
                "external_workload_interference_policy": guards.UNCONTROLLED_EXTERNAL_POLICY,
            })
            guard = guards.IsolatedWaveGuard(controller)
            with mock.patch.object(guard, "resolve_owned_backend_roots", return_value={11, 22}), \
                 mock.patch.object(guards.replay, "wait_for_external_quiet") as quiet, \
                 mock.patch.object(guard, "audit"), \
                 mock.patch.object(guards.threading, "Thread") as thread:
                guard.before_restore("wave")
            quiet.assert_not_called()
            thread.assert_not_called()
            self.assertTrue(guard.receipt["passed"])
            self.assertEqual(
                guard.receipt["external_workload_interference_policy"],
                guards.UNCONTROLLED_EXTERNAL_POLICY,
            )

    def test_incomplete_search_cannot_load_b_champion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "xp_io").mkdir()
            (root / "xp_io" / "budgeted_formal_result.json").write_text(json.dumps({"status": "search_not_completed"}))
            with self.assertRaisesRegex(RuntimeError, "incomplete"):
                runner.comparison_methods(root, "xp_io")

    def test_comparison_handoff_has_seven_stages_and_initial_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            algorithm_cfg = algorithm()
            algorithm_cfg["isolated_search"].update({
                "external_workload_guard_required": False,
                "external_workload_interference_user_accepted": True,
                "external_workload_interference_policy": guards.UNCONTROLLED_EXTERNAL_POLICY,
            })
            contract = {"cfg": {"algorithm_c": algorithm_cfg}, "config_sha256": "x", "workers": [{"name": "w1"}],
                        "defaults": {"seq_page_cost": 1.0}}
            methods = {name: {"params": {"seq_page_cost": value}}
                       for name, value in [("algorithm_b", 2.0), ("algorithm_c_previous", 3.0), ("algorithm_c", 4.0)]}
            def stage(contracts, output, label, attempt, params, **kwargs):
                return {"all_valid": True, "scene_results": {"xp_io": {
                    "physical_invocations": 1, "technical_failures": 0,
                    "aggregate": {"db_qps": 100.0, "params": params["xp_io"]},
                }}}
            with mock.patch.object(runner, "scene_contract", return_value=contract), \
                 mock.patch.object(runner, "comparison_methods", return_value=methods), \
                 mock.patch.object(runner.replay, "wait_for_external_quiet", return_value=True), \
                 mock.patch.object(runner.replay, "audit_all_scenes"), \
                 mock.patch.object(runner.replay, "audit_container_resource_domain"), \
                 mock.patch.object(runner.replay, "run_stage_attempt", side_effect=stage) as run, \
                 mock.patch.object(runner.replay, "restore_defaults") as restore:
                result = runner.compare(root, "xp_io", 1, lambda: False)
                self.assertEqual(run.call_count, 7)
                self.assertTrue(all(
                    call.kwargs["external_workload_guard_enabled"] is False
                    for call in run.call_args_list
                ))
                restore.assert_called_once_with({"xp_io": contract}, root / "comparison/xp_io/repetition_01")
                self.assertEqual(set(result["methods"]), set(methods))
                self.assertTrue(all(row["qps_reference_stage"] == "common_baseline_00" for row in result["methods"].values()))


if __name__ == "__main__":
    unittest.main()

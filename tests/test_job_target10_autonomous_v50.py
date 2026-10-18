from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
MODULE_PATH = TOOLS / "job_fresh10_target10_autonomous_v50.py"
SELECTION_FIXTURE = Path("/tmp/job_diag003_top5.json")
SUMMARY_FIXTURE = Path("/tmp/job_diag003_summary.json")


def import_module():
    sys.path.insert(0, str(TOOLS))
    try:
        spec = importlib.util.spec_from_file_location("job_v50_test", MODULE_PATH)
        if spec is None or spec.loader is None:
            raise RuntimeError("cannot import JOB v50")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if sys.path and sys.path[0] == str(TOOLS):
            sys.path.pop(0)


job = import_module()


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("fixture is not an object")
    return value


def dump(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class JobTarget10AutonomousV50Test(unittest.TestCase):
    def test_historical_generation_is_rebased_to_frozen_fresh0_baseline(self) -> None:
        with tempfile.TemporaryDirectory(prefix="job_v50_rebase_") as raw:
            state_path = Path(raw) / "fresh_state.json"
            dump(
                state_path,
                {
                    "accepted_count": 0,
                    "incumbent_score": job.BASELINE_SCORE,
                    "incumbent_params": job.BASELINE_PARAMS,
                    "range_by_parameter": {
                        name: {"min": value, "max": value}
                        for name, value in job.BASELINE_PARAMS.items()
                    },
                },
            )
            with mock.patch.object(job, "FRESH_STATE", state_path):
                result = job.rebase_training_discovery_to_fresh0(
                    load(SELECTION_FIXTURE)
                )
            self.assertEqual(result["authoritative_formal_state"]["accepted_count"], 0)
            self.assertFalse(result["historical_incumbent_or_range_inherited"])
            for candidate in result["top5"]:
                self.assertEqual(candidate["initial_params"], job.BASELINE_PARAMS)
                self.assertTrue(candidate["fresh0_baseline_rebased"])
                for name, baseline in job.BASELINE_PARAMS.items():
                    delta = candidate["delta_param"][name]
                    self.assertEqual(delta["before"], baseline)
                    self.assertEqual(
                        delta["absolute"],
                        float(candidate["candidate_params"][name]) - baseline,
                    )

    def test_fresh_point_range_has_five_distinct_reachable_parameter_points(self) -> None:
        with tempfile.TemporaryDirectory(prefix="job_v50_reachable_") as raw:
            root = Path(raw)
            state_path = root / "fresh_state.json"
            dump(
                state_path,
                {
                    "accepted_count": 0,
                    "incumbent_score": job.BASELINE_SCORE,
                    "incumbent_params": job.BASELINE_PARAMS,
                    "range_by_parameter": {
                        name: {"min": value, "max": value}
                        for name, value in job.BASELINE_PARAMS.items()
                    },
                },
            )
            with mock.patch.object(job, "FRESH_STATE", state_path):
                rebased = job.rebase_training_discovery_to_fresh0(
                    load(SELECTION_FIXTURE)
                )
                selection = root / "top5.json"
                dump(selection, rebased)
                audit = job.validate_candidate_reachability(selection)
                self.assertEqual(audit["distinct_parameter_points"], 5)
                self.assertTrue(audit["formal_range_is_point"])
                self.assertGreaterEqual(len(audit["changed_dimensions"]), 2)

                duplicate = copy.deepcopy(rebased)
                duplicate["top5"][1]["candidate_params"] = copy.deepcopy(
                    duplicate["top5"][0]["candidate_params"]
                )
                duplicate["top5"][1]["delta_param"] = copy.deepcopy(
                    duplicate["top5"][0]["delta_param"]
                )
                dump(selection, duplicate)
                with self.assertRaises(job.v49.FailClosed):
                    job.validate_candidate_reachability(selection)

    def test_gate_is_recomputed_and_bound_to_exact_inputs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="job_v50_gate_") as raw:
            root = Path(raw)
            selection = root / "top5.json"
            summary = root / "replay/summary.json"
            selection.parent.mkdir(parents=True, exist_ok=True)
            summary.parent.mkdir(parents=True, exist_ok=True)
            selection.write_bytes(SELECTION_FIXTURE.read_bytes())
            summary.write_bytes(SUMMARY_FIXTURE.read_bytes())
            gate = {
                "passed": True,
                "checks": {"all_contract_checks": True},
            }
            output = root / "entry_gate_raw.json"
            dump(output, gate)

            def fake_run_logged(command, _log, **_kwargs):
                target = Path(command[command.index("--output") + 1])
                dump(target, gate)
                return 0

            with mock.patch.object(
                job.v49, "evaluate_gate", return_value=(True, output)
            ), mock.patch.object(job.v49, "run_logged", side_effect=fake_run_logged):
                passed, observed = job.evaluate_gate_v50(root)
            self.assertTrue(passed)
            self.assertEqual(observed, output)
            audit = load(root / "entry_gate_binding_audit.json")
            self.assertEqual(audit["selection_sha256"], sha(selection))
            self.assertEqual(audit["summary_sha256"], sha(summary))

    def test_one_attempt_technical_summary_is_state_neutral_and_valid(self) -> None:
        with tempfile.TemporaryDirectory(prefix="job_v50_short_tech_") as raw:
            root = Path(raw)
            selection = root / "top5.json"
            selection.write_bytes(SELECTION_FIXTURE.read_bytes())
            summary = load(SUMMARY_FIXTURE)
            failure = copy.deepcopy(summary["successful"][0])
            failure.update(
                {
                    "technical_failure": True,
                    "error": "RuntimeError: workload rc=1",
                    "accepted": False,
                    "gradient_update_applied": False,
                    "incumbent_update_applied": False,
                    "range_update_applied": False,
                }
            )
            summary["successful"] = []
            summary["technical_failures"] = [failure]
            summary["invocations"] = [summary["invocations"][0]]
            summary["physical_invocation_count"] = 1
            summary["real_workload_invocation_count"] = 1
            summary["successful_real_replay_count"] = 0
            summary["technical_failure_count"] = 1
            summary["rejection_count"] = 0
            summary_path = root / "replay/summary.json"
            dump(summary_path, summary)
            runner_log = root / "runner.log"
            runner_log.write_text('{"physical_invocation_count": 1}\n', encoding="utf-8")
            state = root / "state.json"
            dump(state, {"accepted_count": 3})
            global_counter = root / "global.txt"
            formal_counter = root / "formal.txt"
            accepted_counter = root / "accepted.txt"
            global_counter.write_text("392\n", encoding="utf-8")
            formal_counter.write_text("206\n", encoding="utf-8")
            accepted_counter.write_text("3\n", encoding="utf-8")
            with mock.patch.multiple(
                job.v49,
                STATE=state,
                GLOBAL_COUNTER=global_counter,
                FORMAL_REPLAY_COUNTER=formal_counter,
                ACCEPTED_COUNTER=accepted_counter,
            ):
                clean, observed = job.validate_diagnostic_v50(
                    selection,
                    summary_path,
                    runner_log,
                    sha(state),
                    {"global": 391, "formal_replay": 206, "accepted": 3},
                )
            self.assertFalse(clean)
            self.assertEqual(observed["physical_invocation_count"], 1)

    def test_preclaim_technical_attempt_consumes_budget_without_physical(self) -> None:
        with tempfile.TemporaryDirectory(prefix="job_v51_preclaim_tech_") as raw:
            root = Path(raw)
            selection = root / "top5.json"
            selection.write_bytes(SELECTION_FIXTURE.read_bytes())
            summary = load(SUMMARY_FIXTURE)
            failure = copy.deepcopy(summary["successful"][0])
            failure.update(
                {
                    "technical_failure": True,
                    "accepted": False,
                    "gradient_update_applied": False,
                    "incumbent_update_applied": False,
                    "range_update_applied": False,
                }
            )
            summary.update(
                {
                    "successful": [],
                    "technical_failures": [failure],
                    "invocations": [],
                    "physical_invocation_count": 0,
                    "real_workload_invocation_count": 0,
                    "successful_real_replay_count": 0,
                    "technical_failure_count": 1,
                    "pre_invocation_technical_failure_count": 1,
                    "pre_invocation_technical_failure_ranks": [1],
                    "preclaim_no_ordinal_claim": True,
                }
            )
            summary_path = root / "replay/summary.json"
            dump(summary_path, summary)
            runner_log = root / "runner.log"
            runner_log.write_text(
                '{"preclaim_no_ordinal_claim": true}\n', encoding="utf-8"
            )
            state = root / "state.json"
            dump(state, {"accepted_count": 0})
            global_counter = root / "global.txt"
            formal_counter = root / "formal.txt"
            accepted_counter = root / "accepted.txt"
            global_counter.write_text("395\n", encoding="utf-8")
            formal_counter.write_text("206\n", encoding="utf-8")
            accepted_counter.write_text("0\n", encoding="utf-8")
            with mock.patch.multiple(
                job.v49,
                STATE=state,
                GLOBAL_COUNTER=global_counter,
                FORMAL_REPLAY_COUNTER=formal_counter,
                ACCEPTED_COUNTER=accepted_counter,
            ):
                clean, _observed = job.validate_diagnostic_v50(
                    selection,
                    summary_path,
                    runner_log,
                    sha(state),
                    {"global": 395, "formal_replay": 206, "accepted": 0},
                )
            self.assertFalse(clean)

    def test_short_batch_without_technical_failure_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="job_v50_short_clean_") as raw:
            root = Path(raw)
            selection = root / "top5.json"
            selection.write_bytes(SELECTION_FIXTURE.read_bytes())
            summary = load(SUMMARY_FIXTURE)
            summary["successful"] = summary["successful"][:1]
            summary["technical_failures"] = []
            summary["invocations"] = summary["invocations"][:1]
            summary["physical_invocation_count"] = 1
            summary["real_workload_invocation_count"] = 1
            summary["successful_real_replay_count"] = 1
            summary["technical_failure_count"] = 0
            summary_path = root / "summary.json"
            dump(summary_path, summary)
            runner_log = root / "runner.log"
            runner_log.write_text("{}\n", encoding="utf-8")
            state = root / "state.json"
            dump(state, {"accepted_count": 3})
            counter = root / "counter.txt"
            counter.write_text("1\n", encoding="utf-8")
            accepted = root / "accepted.txt"
            accepted.write_text("3\n", encoding="utf-8")
            with mock.patch.multiple(
                job.v49,
                STATE=state,
                GLOBAL_COUNTER=counter,
                FORMAL_REPLAY_COUNTER=counter,
                ACCEPTED_COUNTER=accepted,
            ):
                with self.assertRaises(job.v49.FailClosed):
                    job.validate_diagnostic_v50(
                        selection,
                        summary_path,
                        runner_log,
                        sha(state),
                        {"global": 0, "formal_replay": 1, "accepted": 3},
                    )

    def test_retry_exclusion_quarantines_all_prior_selection_fingerprints(self) -> None:
        with tempfile.TemporaryDirectory(prefix="job_v50_exclusion_") as raw:
            cycle = Path(raw)
            dump(
                cycle / "global_exclusion.json",
                {
                    "schema_version": "job_global_three_level_exclusion_v1",
                    "sources": [],
                    "fingerprint_count": 0,
                    "fingerprints": [],
                },
            )
            replay = cycle / "discovery_replay_01"
            replay.mkdir()
            (replay / "top5.json").write_bytes(SELECTION_FIXTURE.read_bytes())
            output = job.build_retry_exclusion(cycle, 2)
            identifiers = {
                item["three_level_fingerprint"]["id"]
                for item in load(output)["fingerprints"]
            }
            self.assertEqual(identifiers, job.v49.fingerprint_set(replay / "top5.json"))

    def test_existing_replay02_is_sealed_then_replay03_is_fresh(self) -> None:
        with tempfile.TemporaryDirectory(prefix="job_v50_replay03_") as raw:
            cycle = Path(raw)
            initial = cycle / "discovery_top5.json"
            initial.write_bytes(SELECTION_FIXTURE.read_bytes())
            for ordinal in (1, 2):
                root = cycle / f"discovery_replay_{ordinal:02d}"
                root.mkdir()
                (root / "top5.json").write_bytes(SELECTION_FIXTURE.read_bytes())
                (root / "replay").mkdir()
                dump(root / "replay/summary.json", {"technical_failure_count": 1})
            fresh_root = cycle / "fresh"
            fresh_root.mkdir()
            fresh = fresh_root / "top5.json"
            payload = load(SELECTION_FIXTURE)
            for rank, candidate in enumerate(payload["top5"], 10):
                candidate["three_level_fingerprint"]["id"] = (
                    "three-level-sha256:" + f"{rank:064x}"
                )
            dump(fresh, payload)
            summaries = [
                {"technical_failure_count": 1},
                {"technical_failure_count": 1},
                {"technical_failure_count": 0},
            ]

            def fake_run(selection, replay, phase):
                _ = selection, phase
                value = summaries.pop(0)
                dump(replay / "summary.json", value)
                return value["technical_failure_count"] == 0, value

            with mock.patch.object(job, "run_diagnostic_v50", side_effect=fake_run), mock.patch.object(
                job,
                "generate_fresh_discovery",
                return_value=fresh,
            ) as generated:
                selected, replay_root, attempt = job.obtain_clean_discovery_v50(
                    initial,
                    cycle,
                    evidence_selection=initial,
                    evidence_events=initial,
                    incumbent_events=initial,
                    point_result=initial,
                    state_sha_before="a" * 64,
                )
            self.assertEqual(replay_root.name, "discovery_replay_03")
            self.assertEqual(attempt, 3)
            self.assertEqual(selected.read_bytes(), fresh.read_bytes())
            self.assertTrue((replay_root / "discovery_audit.json").is_file())
            generated.assert_called_once()

    def test_repeat_technical_never_replays_same_failed_selection(self) -> None:
        with tempfile.TemporaryDirectory(prefix="job_v50_repeat_technical_") as raw:
            cycle = Path(raw)
            discovery_root = cycle / "discovery_replay_03"
            discovery_root.mkdir()
            (discovery_root / "top5.json").write_bytes(SELECTION_FIXTURE.read_bytes())

            def fake_build(_source, target):
                target.mkdir(parents=True, exist_ok=True)
                output = target / "top5.json"
                output.write_bytes(SELECTION_FIXTURE.read_bytes())
                return output

            with mock.patch.object(job.v49, "build_repeat", side_effect=fake_build), mock.patch.object(
                job,
                "run_diagnostic_v50",
                return_value=(False, {"technical_failure_count": 1}),
            ) as replay:
                with self.assertRaises(job.RepeatTechnicalFailure):
                    job.obtain_passed_repeat_v50(discovery_root, cycle)
            self.assertEqual(replay.call_count, 1)
            self.assertFalse((cycle / "repeat_02").exists())


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools/job_fresh10_target10_autonomous_v49.py"
LAUNCHER = ROOT / "tools/launch_job_target10_autonomous_v49_after_formal004.sh"


def import_module():
    spec = importlib.util.spec_from_file_location("job_v49_test", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot import JOB v49 controller")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


job = import_module()


def dump(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def state(accepted: int = 2) -> dict:
    params = {
        "cpu_index_tuple_cost": 0.0005,
        "cpu_operator_cost": 0.0025,
        "cpu_tuple_cost": 0.01,
        "parallel_setup_cost": 7000.0,
        "parallel_tuple_cost": 0.001,
        "random_page_cost": 4.0,
        "seq_page_cost": 1.0,
    }
    return {
        "accepted_count": accepted,
        "accepted_target": 10,
        "global_ordinal": 100,
        "formal_replay_count": 50,
        "incumbent_score": 4.5,
        "incumbent_params": params,
        "range_by_parameter": {
            name: {"min": value, "max": value} for name, value in params.items()
        },
    }


def candidate(rank: int, role: str = "probe") -> dict:
    return {
        "rank": rank,
        "selection_role": role,
        "boundary_corner": False,
        "three_level_fingerprint": {
            "id": f"three-level-sha256:{rank:064x}",
            "valid": True,
            "plan_level": "ratio-plan-sha256:" + f"{rank:064x}",
        },
        "candidate_params": {
            "cpu_index_tuple_cost": 0.0005 + rank * 1e-8,
            "cpu_operator_cost": 0.0025,
            "cpu_tuple_cost": 0.01,
            "parallel_setup_cost": 7000.0,
            "parallel_tuple_cost": 0.001,
            "random_page_cost": 4.0,
            "seq_page_cost": 1.0,
        },
        **{field: {} for field in job.REQUIRED_CANDIDATE_FIELDS},
    }


class JobTarget10AutonomousV49Test(unittest.TestCase):
    def test_static_contract_contains_no_manual_candidate_path(self) -> None:
        text = MODULE_PATH.read_text(encoding="utf-8")
        self.assertIn('"--force-probe-discovery"', text)
        self.assertIn("MAX_DIAGNOSTIC_ATTEMPTS = 5", text)
        self.assertIn("job_three_independent_exact_point_median_acceptance_v2", text)
        self.assertIn('"manual_candidate_injection": False', text)
        self.assertNotIn("manual_candidate_params", text)
        self.assertNotIn("qps_improvement_required", text)

    def test_launcher_pins_controller_and_zero_call_preflight(self) -> None:
        text = LAUNCHER.read_text(encoding="utf-8")
        self.assertIn(sha(MODULE_PATH), text)
        self.assertIn("--preflight-only", text)
        self.assertIn(".new_workload_calls == 0", text)
        self.assertIn(".explain_calls == 0", text)
        self.assertNotIn("ssh ", text)
        self.assertNotIn("mysql", text.lower())

    def test_completed_diagnostic_is_idempotently_reused(self) -> None:
        with tempfile.TemporaryDirectory(prefix="job_v49_diag_") as raw:
            root = Path(raw)
            formal_state = root / "formal_state.json"
            global_counter = root / "global.txt"
            formal_counter = root / "formal.txt"
            accepted_counter = root / "accepted.txt"
            dump(formal_state, state())
            # Two apply-stage technical failures consume the five-attempt
            # batch budget without creating physical workload ordinals.
            global_counter.write_text("13\n", encoding="utf-8")
            formal_counter.write_text("20\n", encoding="utf-8")
            accepted_counter.write_text("2\n", encoding="utf-8")
            phase = root / "phase"
            selection = phase / "top5.json"
            replay = phase / "replay"
            dump(
                selection,
                {
                    "diagnostic_only": True,
                    "top5": [candidate(rank) for rank in range(1, 6)],
                },
            )
            subset = {
                key: copy.deepcopy(state()[key]) for key in job.PARAMETER_FIELDS
            }
            dump(
                replay / "summary.json",
                {
                    "diagnostic_only": True,
                    "selection_sha256": sha(selection),
                    "physical_invocation_count": 3,
                    "successful_real_replay_count": 3,
                    "technical_failure_count": 2,
                    "invocations": [
                        {"rank": rank} for rank in range(1, 4)
                    ],
                    "successful": [
                        {"rank": rank} for rank in range(1, 4)
                    ],
                    "technical_failures": [
                        {
                            "rank": rank,
                            "technical_failure": True,
                            "accepted": False,
                            "gradient_update_applied": False,
                            "incumbent_update_applied": False,
                            "range_update_applied": False,
                        }
                        for rank in range(4, 6)
                    ],
                    "baseline_params_restored": True,
                    "baseline_restore": {"success": True},
                    "initial_state": subset,
                    "final_state": subset,
                    "formal_update_flags": {
                        "accepted": False,
                        "gradient": False,
                        "incumbent": False,
                        "range": False,
                    },
                },
            )
            (phase / "runner.log").write_text(
                "Traceback (most recent call last):\n"
                "ValueError: physical invocation ledger is inconsistent\n",
                encoding="utf-8",
            )
            dump(
                phase / "counters_before.json",
                {"global": 10, "formal_replay": 20, "accepted": 2},
            )
            with mock.patch.multiple(
                job,
                STATE=formal_state,
                GLOBAL_COUNTER=global_counter,
                FORMAL_REPLAY_COUNTER=formal_counter,
                ACCEPTED_COUNTER=accepted_counter,
            ):
                clean, _ = job.run_diagnostic(selection, replay, phase)
                self.assertFalse(clean)
                global_counter.write_text("999\n", encoding="utf-8")
                clean_again, _ = job.run_diagnostic(selection, replay, phase)
                self.assertFalse(clean_again)

    def test_exclusion_inventory_is_frozen_before_discovery(self) -> None:
        with tempfile.TemporaryDirectory(prefix="job_v49_exclusion_") as raw:
            root = Path(raw)
            campaign = root / "campaign"
            package = root / "package"
            first = campaign / "batch/top5.json"
            dump(first, {"top5": [candidate(rank) for rank in range(1, 6)]})
            output = campaign / "cycle/global_exclusion.json"
            with mock.patch.multiple(job, B=campaign, T=campaign, P=package):
                identifiers = job.exclusion_inventory(output)
                self.assertEqual(len(identifiers), 5)
                dump(
                    campaign / "later/top5.json",
                    {"top5": [candidate(rank) for rank in range(6, 11)]},
                )
                frozen = job.exclusion_inventory(output)
                self.assertEqual(frozen, identifiers)

    def test_flat_historical_fingerprint_lists_are_excluded(self) -> None:
        result: set[str] = set()
        identifier = "three-level-sha256:" + "a" * 64
        job.extract_fingerprints(
            {"round7_replayed_three_level_fingerprints": [identifier]},
            result,
        )
        self.assertEqual(result, {identifier})

    def test_partial_replay_is_never_reissued(self) -> None:
        with tempfile.TemporaryDirectory(prefix="job_v49_partial_") as raw:
            root = Path(raw)
            phase = root / "phase"
            replay = phase / "replay"
            replay.mkdir(parents=True)
            selection = phase / "top5.json"
            dump(selection, {"top5": [candidate(rank) for rank in range(1, 6)]})
            formal_state = root / "state.json"
            global_counter = root / "global.txt"
            formal_counter = root / "formal.txt"
            accepted_counter = root / "accepted.txt"
            dump(formal_state, state())
            global_counter.write_text("10\n", encoding="utf-8")
            formal_counter.write_text("20\n", encoding="utf-8")
            accepted_counter.write_text("2\n", encoding="utf-8")
            with mock.patch.multiple(
                job,
                STATE=formal_state,
                GLOBAL_COUNTER=global_counter,
                FORMAL_REPLAY_COUNTER=formal_counter,
                ACCEPTED_COUNTER=accepted_counter,
            ):
                with self.assertRaisesRegex(job.FailClosed, "partial diagnostic"):
                    job.run_diagnostic(selection, replay, phase)

    def test_pending_diagnostic_ordinals_are_exactly_accounted(self) -> None:
        with tempfile.TemporaryDirectory(prefix="job_v49_pending_") as raw:
            root = Path(raw)
            formal_state = root / "state.json"
            global_counter = root / "global.txt"
            formal_counter = root / "formal.txt"
            accepted_counter = root / "accepted.txt"
            dump(formal_state, state())
            global_counter.write_text("110\n", encoding="utf-8")
            formal_counter.write_text("50\n", encoding="utf-8")
            accepted_counter.write_text("2\n", encoding="utf-8")
            cycle = root / "cycles/cycle_005"
            running = 100
            for name in ("discovery_replay_01", "repeat_01"):
                phase = cycle / name
                dump(
                    phase / "counters_before.json",
                    {"global": running, "formal_replay": 50, "accepted": 2},
                )
                dump(phase / "replay/summary.json", {"phase": name})
                dump(
                    phase / "diagnostic_audit.json",
                    {
                        "physical_calls": 5,
                        "summary_sha256": sha(phase / "replay/summary.json"),
                    },
                )
                running += 5
            initial_formal = root / "initial_formal"
            with mock.patch.multiple(
                job,
                ROOT=root / "cycles",
                INITIAL_FORMAL=initial_formal,
                STATE=formal_state,
                GLOBAL_COUNTER=global_counter,
                FORMAL_REPLAY_COUNTER=formal_counter,
                ACCEPTED_COUNTER=accepted_counter,
            ):
                job.validate_pending_diagnostic_counters(initial_formal, 5)
                global_counter.write_text("111\n", encoding="utf-8")
                with self.assertRaisesRegex(job.FailClosed, "accounting mismatch"):
                    job.validate_pending_diagnostic_counters(initial_formal, 5)

    def test_top5_requires_exact_roles_fields_and_fingerprints(self) -> None:
        with tempfile.TemporaryDirectory(prefix="job_v49_top5_") as raw:
            path = Path(raw) / "top5.json"
            roles = ("trusted", "trusted", "trusted", "probe", "probe")
            dump(
                path,
                {
                    "diagnostic_only": True,
                    "top5": [
                        candidate(rank, roles[rank - 1]) for rank in range(1, 6)
                    ],
                },
            )
            job.validate_top5(path, roles=roles, diagnostic_only=True)
            broken = json.loads(path.read_text(encoding="utf-8"))
            broken["top5"][4]["three_level_fingerprint"]["id"] = broken[
                "top5"
            ][0]["three_level_fingerprint"]["id"]
            dump(path, broken)
            with self.assertRaises(job.FailClosed):
                job.validate_top5(path, roles=roles, diagnostic_only=True)


if __name__ == "__main__":
    unittest.main()

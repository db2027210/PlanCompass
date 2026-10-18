from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools/audit_job_formal004_v49_boundary.py"


def import_module():
    spec = importlib.util.spec_from_file_location("job_boundary_audit_test", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot import boundary auditor")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


audit = import_module()


def dump(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def counter(path: Path, value: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{value}\n", encoding="utf-8")


def fixture(root: Path) -> tuple[Path, Path, dict[str, str]]:
    b = root / "job_attempt"
    t = b / "job_final_algorithm_fresh10_v36_20260731T2020"
    p = b / "job_round8_corrected_top5_v6_20260731"
    formal = t / "autonomous_v45/formal_batch004"
    selection = formal / "package/top5_promoted_formal.json"
    roles = ["trusted", "trusted", "trusted", "probe", "probe"]
    candidates = []
    for rank in range(1, 6):
        candidate = {"rank": rank, "selection_role": roles[rank - 1]}
        if rank <= 3:
            prior_second = 4.1 if rank == 1 else 4.05
            candidate.update(
                {
                    "incumbent_update_eligible": True,
                    "used_for_inner_incumbent_update": True,
                    "used_for_gradient": False,
                    "robust_acceptance_policy": {
                        "prior_observations": [
                            {"actual_score": 4.0},
                            {"actual_score": prior_second},
                        ]
                    },
                }
            )
        candidates.append(candidate)
    dump(
        selection,
        {"top5": candidates},
    )
    dump(
        formal / "replay/summary.json",
        {
            "baseline_params_restored": True,
            "physical_invocation_count": 5,
            "successful_real_replay_count": 5,
            "technical_failure_count": 0,
            "three_observation_acceptance_contract": (
                "job_three_independent_exact_point_median_acceptance_v2"
            ),
            "qps_used_for_inner_scoring": False,
            "selection_sha256": sha(selection),
        },
    )
    events = []
    utilization = {"cpu": 0.4, "io": 0.4, "memory": 0.86}
    raw_score = audit.raw_actual_score(utilization)
    incumbent = {
        "accepted_count": 2,
        "incumbent_score": 4.0,
        "incumbent_params": {},
        "range_by_parameter": {},
    }
    for rank in range(1, 6):
        events.append(
            {"event": "job_round8_candidate_invocation_started", "rank": rank}
        )
        before = json.loads(json.dumps(incumbent))
        accepted = rank == 1
        robust_score = (
            float(__import__("statistics").median([4.0, 4.1, raw_score]))
            if rank == 1
            else (
                float(__import__("statistics").median([4.0, 4.05, raw_score]))
                if rank <= 3
                else raw_score
            )
        )
        if accepted:
            incumbent["accepted_count"] = 3
            incumbent["incumbent_score"] = robust_score
        after = json.loads(json.dumps(incumbent))
        events.append(
            {
                "event": "job_round8_candidate_replay_complete",
                "rank": rank,
                "selection_role": roles[rank - 1],
                "actual_utilization": utilization,
                "actual_score": robust_score,
                "accepted": accepted,
                "gradient_update_applied": False,
                "incumbent_update_applied": accepted,
                "range_update_applied": accepted,
                "incumbent_state_before": before,
                "incumbent_state_after": after,
            }
        )
    events.append({"event": "job_round8_baseline_restored"})
    (formal / "replay/events.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )
    state_path = t / "package/formal_state.json"
    dump(
        state_path,
        {
            "accepted_count": 3,
            "accepted_target": 10,
            "global_ordinal": 110,
            "formal_replay_count": 55,
        },
    )
    dump(
        formal / "replay/formal_state_commit_transaction.json",
        {
            "status": "committed",
            "accepted_before": 2,
            "accepted_after": 3,
            "global_ordinal_after": 110,
            "formal_replay_count_after": 55,
            "state_sha256_after": sha(state_path),
        },
    )
    counter(b / "replay_counter.txt", 110)
    counter(b / "formal_search_replay_counter.txt", 55)
    counter(b / "formal_search_accepted_counter.txt", 3)
    dependency = root / "sealed_dependency.py"
    dependency.write_text("# sealed\n", encoding="utf-8")
    dependencies = {str(dependency): sha(dependency)}
    proc_root = root / "proc"
    proc_root.mkdir()
    _ = p
    return b, proc_root, dependencies


class JobFormal004BoundaryAuditTest(unittest.TestCase):
    def test_ready_boundary_and_output_fields(self) -> None:
        with tempfile.TemporaryDirectory(prefix="job_boundary_ready_") as raw:
            root = Path(raw)
            b, proc_root, dependencies = fixture(root)
            before = {
                str(path): sha(path)
                for path in root.rglob("*")
                if path.is_file()
            }
            result = audit.audit_boundary(
                b,
                proc_root=proc_root,
                dependencies=dependencies,
            )
            after = {
                str(path): sha(path)
                for path in root.rglob("*")
                if path.is_file()
            }
            self.assertEqual(before, after)
            self.assertTrue(result["deployable"])
            self.assertTrue(result["boundary_ready_for_dependency_deployment"])
            self.assertEqual(result["status"], "ready_to_launch_v49")
            self.assertEqual(
                result["formal_progress"]["formal004_added_accepted"], 1
            )
            self.assertEqual(
                result["formal_progress"]["countable_formal_accepted_total"], 3
            )
            self.assertEqual(result["formal004_replay"]["physical_workload_calls"], 5)
            self.assertEqual(
                result["live_formal004_progress"][
                    "successful_third_observation_count"
                ],
                5,
            )
            self.assertEqual(
                result["live_formal004_progress"][
                    "provisional_event_accepted_ranks_not_countable_until_commit"
                ],
                [1],
            )
            self.assertEqual(
                result["live_formal004_acceptance_audit"][
                    "possible_accepted_delta_range"
                ],
                [1, 1],
            )
            self.assertTrue(
                result["live_formal004_acceptance_audit"][
                    "probe_state_neutral_so_far"
                ]
            )
            self.assertTrue(
                result["live_formal004_acceptance_audit"][
                    "acceptance_calculation_consistent_so_far"
                ]
            )
            self.assertEqual(result["failed_checks"], [])
            self.assertTrue(result["read_only"])
            self.assertEqual(result["new_workload_calls"], 0)

    def test_active_job_process_blocks_deployment(self) -> None:
        with tempfile.TemporaryDirectory(prefix="job_boundary_active_") as raw:
            root = Path(raw)
            b, proc_root, dependencies = fixture(root)
            process = proc_root / "1234"
            process.mkdir()
            (process / "cmdline").write_bytes(
                b"python3\0/tmp/job_formal_target10_batch_controller.py\0"
            )
            result = audit.audit_boundary(
                b,
                proc_root=proc_root,
                dependencies=dependencies,
            )
            self.assertFalse(result["deployable"])
            self.assertEqual(result["status"], "waiting_old_job_chain_exit")
            self.assertEqual(result["active_job_processes"][0]["pid"], 1234)

    def test_missing_transaction_reports_waiting(self) -> None:
        with tempfile.TemporaryDirectory(prefix="job_boundary_wait_") as raw:
            root = Path(raw)
            b, proc_root, dependencies = fixture(root)
            transaction = (
                b
                / "job_final_algorithm_fresh10_v36_20260731T2020"
                / "autonomous_v45/formal_batch004/replay/"
                "formal_state_commit_transaction.json"
            )
            transaction.unlink()
            result = audit.audit_boundary(
                b,
                proc_root=proc_root,
                dependencies=dependencies,
            )
            self.assertFalse(result["deployable"])
            self.assertEqual(result["status"], "waiting_formal004_commit")
            self.assertIn(
                "formal004_transaction_committed", result["failed_checks"]
            )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "tpch_fresh10_target10_loop_v33_prethird_bound.py"


def load_module() -> Any:
    specification = importlib.util.spec_from_file_location("tpch_prethird_v33", MODULE_PATH)
    if specification is None or specification.loader is None:
        raise RuntimeError(f"cannot load {MODULE_PATH}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def write_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TpchPrethirdBoundTest(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.selection = self.root / "selection.json"
        self.run = self.root / "run"
        self.state = self.root / "state.json"
        self.v26 = SimpleNamespace(
            CAP=5,
            load=lambda path: json.loads(Path(path).read_text(encoding="utf-8")),
            sha256=sha256,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def prepare(self, first_scores: list[float], second_scores: list[float]) -> None:
        candidates = []
        results = []
        for index, (first, second) in enumerate(zip(first_scores, second_scores), start=1):
            identifier = f"three-level-sha256:{index:064x}"
            candidates.append(
                {
                    "rank": index,
                    "candidate_score": first,
                    "selection_role": "trusted" if index <= 3 else "probe",
                    "confidence": {"measurement_count": 1, "score_median": first},
                    "three_level_fingerprint": {"id": identifier},
                }
            )
            results.append(
                {
                    "rank": index,
                    "actual_score": second,
                    "technical_failure": False,
                    "valid_replay": True,
                    "three_level_fingerprint": {"id": identifier},
                }
            )
        write_json(self.selection, {"top5": candidates})
        write_json(
            self.run / "diagnostic_summary.json",
            {
                "successful": results,
                "physical_call_count": 5,
                "valid_replay_count": 5,
                "technical_failure_count": 0,
                "baseline_restored": True,
                "accepted_update_applied": False,
                "incumbent_update_applied": False,
                "range_update_applied": False,
                "gradient_update_applied": False,
                "selection_sha256": sha256(self.selection),
            },
        )
        write_json(
            self.state,
            {
                "status": "complete",
                "baseline_restored": True,
                "incumbent_score": 4.645760610764541,
            },
        )

    def evaluate(self) -> Dict[str, Any]:
        return self.module.prethird_feasibility(
            self.v26,
            self.selection,
            self.run,
            self.state,
        )

    def test_skips_when_four_observation_median_cannot_win(self) -> None:
        self.prepare([3.92, 3.90, 3.88, 3.91, 3.89], [3.90, 3.89, 3.88, 3.91, 3.90])
        result = self.evaluate()
        self.assertFalse(result["passed"])
        self.assertTrue(result["complete_two_observation_binding"])
        self.assertEqual([], result["feasible_ranks"])
        self.assertEqual(0, result["physical_workload_calls"])
        self.assertFalse(result["formal_state_mutated"])

    def test_keeps_third_observation_when_candidate_can_still_win(self) -> None:
        self.prepare([4.40, 3.90, 3.88, 3.91, 3.89], [4.35, 3.89, 3.88, 3.91, 3.90])
        result = self.evaluate()
        self.assertTrue(result["passed"])
        self.assertEqual([1], result["feasible_ranks"])

    def test_incomplete_binding_conservatively_keeps_third_observation(self) -> None:
        self.prepare([3.92, 3.90, 3.88, 3.91, 3.89], [3.90, 3.89, 3.88, 3.91, 3.90])
        summary_path = self.run / "diagnostic_summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["successful"][4]["valid_replay"] = False
        write_json(summary_path, summary)
        result = self.evaluate()
        self.assertTrue(result["passed"])
        self.assertFalse(result["complete_two_observation_binding"])

    def test_dirty_batch_fails_closed(self) -> None:
        self.prepare([3.92, 3.90, 3.88, 3.91, 3.89], [3.90, 3.89, 3.88, 3.91, 3.90])
        summary_path = self.run / "diagnostic_summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["technical_failure_count"] = 1
        write_json(summary_path, summary)
        with self.assertRaisesRegex(RuntimeError, "clean exact repeat"):
            self.evaluate()


class TpchV33BoundaryRecoveryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.loop_root = self.root / "loop"
        self.loop_root.mkdir()
        self.resume_state = self.root / "formal_state.json"
        write_json(self.resume_state, {"incumbent_score": 4.645760610764541})

        self.previous_cycle = self.loop_root / "cycle_012"
        self.previous_cycle.mkdir()
        self.previous_selection = self.previous_cycle / "resealed_selection.json"
        self.previous_run = self.previous_cycle / "resealed_run"
        write_json(self.previous_selection, {"top5": []})
        write_json(self.previous_run / "diagnostic_summary.json", {"clean": True})
        write_json(self.previous_run / "diagnostic_state.json", {"status": "complete"})
        write_json(
            self.previous_cycle / "entry_gate.json",
            {
                "postrobust_reseal": {
                    "resealed_selection": str(self.previous_selection),
                    "resealed_selection_sha256": sha256(self.previous_selection),
                    "resealed_summary": str(self.previous_run / "diagnostic_summary.json"),
                    "resealed_summary_sha256": sha256(
                        self.previous_run / "diagnostic_summary.json"
                    ),
                    "resealed_state": str(self.previous_run / "diagnostic_state.json"),
                    "resealed_state_sha256": sha256(
                        self.previous_run / "diagnostic_state.json"
                    ),
                }
            },
        )

        self.partial = self.loop_root / "cycle_013"
        write_json(self.partial / "config.a.json", {"config": 1})
        write_json(self.partial / "config.b.json", {"config": 1})
        write_json(self.partial / "dynamic_expansion_v32/audit.json", {"expanded": True})
        (self.partial / "dynamic_expansion_v32/expand_a.log").write_text("ok\n", encoding="utf-8")
        (self.partial / "dynamic_expansion_v32/expand_b.log").write_text("ok\n", encoding="utf-8")
        write_json(
            self.partial / "dynamic_expansion_v32/expanded_candidates.a.json",
            {"candidates": []},
        )
        write_json(
            self.partial / "dynamic_expansion_v32/expanded_candidates.b.json",
            {"candidates": []},
        )
        (self.partial / "probe_builder_a.log").write_text(
            "No such file or directory\n", encoding="utf-8"
        )

        self.state_path = self.loop_root / "controller_state.json"
        write_json(
            self.state_path,
            {
                "schema_version": "tpch_fresh10_target10_loop_v32",
                "controller_version": 32,
                "status": "preparing_cycle",
                "next_cycle": 13,
                "accepted_count": 0,
                "resume_state": str(self.resume_state),
                "cycles": [{"cycle": 12, "accepted_after": 0}],
            },
        )
        self.state_sha = sha256(self.state_path)
        self.calls: list[str] = []
        self.v26 = SimpleNamespace(
            LOOP_ROOT=self.loop_root,
            TARGET=10,
            sha256=sha256,
            load=lambda path: json.loads(Path(path).read_text(encoding="utf-8")),
            accepted_counter=lambda: 0,
            verify=self.verify,
            build_robust_repeat=lambda *_args: self.previous_selection,
        )
        self.v27 = SimpleNamespace(
            atomic_json=write_json,
            clean_diagnostic=lambda *_args: self.calls.append("clean_diagnostic"),
            insufficient_stable_failure=lambda *_args: False,
            reexploration_audit=lambda *_args: {},
        )
        self.v30 = SimpleNamespace(
            validate_resealed_summary=lambda *_args: self.calls.append("validate_summary")
        )
        self.v32 = SimpleNamespace(
            install_algorithm_hooks=lambda *_args: self.calls.append("install_hooks")
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def verify(self, path: Path, expected: str) -> None:
        self.assertTrue(path.is_file())
        self.assertEqual(expected, sha256(path))

    def recover(self) -> tuple[Dict[str, Any], int, Path, Path, Path, int]:
        return self.module.recover_boundary(
            self.v26,
            self.v27,
            self.v30,
            self.v32,
            object(),
            self.state_sha,
            13,
        )

    def test_exact_zero_call_boundary_is_backed_up_and_quarantined(self) -> None:
        recovered, accepted, resume_state, selection, run, cycle = self.recover()
        self.assertEqual(0, accepted)
        self.assertEqual(13, cycle)
        self.assertEqual(self.resume_state, resume_state)
        self.assertEqual(self.previous_selection, selection)
        self.assertEqual(self.previous_run, run)
        self.assertEqual("tpch_fresh10_target10_loop_v33", recovered["schema_version"])
        self.assertEqual(33, recovered["controller_version"])
        self.assertFalse(self.partial.exists())
        quarantine = self.loop_root / "quarantine_cycle_013_v32_zero_call_prethird_handover"
        self.assertTrue((quarantine / "v33_zero_call_quarantine_audit.json").is_file())
        backup = self.loop_root / "controller_state.pre_v33_cycle013.json"
        self.assertEqual(self.state_sha, sha256(backup))
        observed = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(0, observed["v33_recovery"]["physical_workload_calls"])
        self.assertFalse(observed["v33_recovery"]["formal_state_mutated"])
        self.assertEqual(
            ["validate_summary", "clean_diagnostic", "install_hooks"],
            self.calls,
        )

    def test_unexpected_partial_file_fails_before_state_mutation(self) -> None:
        (self.partial / "unexpected.txt").write_text("unexpected\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "boundary mismatch"):
            self.recover()
        self.assertEqual(self.state_sha, sha256(self.state_path))
        self.assertTrue(self.partial.is_dir())
        self.assertFalse((self.loop_root / "controller_state.pre_v33_cycle013.json").exists())


if __name__ == "__main__":
    unittest.main()

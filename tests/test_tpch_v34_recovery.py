from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

from tools import tpch_fresh10_target10_loop_v34_hierarchical_recovery as v34


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class FakeV26:
    TARGET = 10

    def __init__(self, root: Path) -> None:
        self.LOOP_ROOT = root

    @staticmethod
    def sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    @staticmethod
    def load(path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def accepted_counter() -> int:
        return 0


class FakeV27:
    def __init__(self) -> None:
        self.clean_roots: list[Path] = []

    @staticmethod
    def atomic_json(path: Path, value: dict) -> None:
        write_json(path, value)

    def clean_diagnostic(self, _v26: object, _common: object, root: Path, accepted: int) -> None:
        if accepted != 0:
            raise AssertionError("unexpected accepted count")
        self.clean_roots.append(root)


class V34RecoveryTests(unittest.TestCase):
    def prepare(self, root: Path, *, with_runtime: bool = False) -> tuple[FakeV26, str]:
        resume = root / "formal_state.json"
        write_json(
            resume,
            {"status": "complete", "baseline_restored": True, "accepted_count": 0},
        )
        cycle14 = root / "cycle_014"
        (cycle14 / "diagnostic_probe").mkdir(parents=True)
        (cycle14 / "diagnostic_exact_repeat").mkdir(parents=True)
        write_json(cycle14 / "probe_top5.a.json", {"top5": []})
        write_json(cycle14 / "exact_repeat_top5.a.json", {"top5": []})
        write_json(
            cycle14 / "prethird_acceptance_feasibility.json",
            {
                "contract": "tpch_bounded_four_observation_median_prethird_v1",
                "passed": False,
                "physical_workload_calls": 0,
                "formal_state_mutated": False,
                "manual_candidate_injection": False,
            },
        )
        partial = root / "cycle_015"
        write_json(partial / "config.a.json", {"offline": True})
        if with_runtime:
            (partial / "diagnostic_probe").mkdir()
        state = {
            "schema_version": "tpch_fresh10_target10_loop_v33",
            "controller_version": 33,
            "status": "reexplore_prethird_acceptance_impossible",
            "accepted_count": 0,
            "next_cycle": 15,
            "resume_state": str(resume),
            "cycles": [
                {
                    "cycle": 14,
                    "status": "reexplore_prethird_acceptance_impossible",
                    "accepted_before": 0,
                    "accepted_after": 0,
                    "formal_started": False,
                    "formal_state_mutated": False,
                }
            ],
        }
        state_path = root / "controller_state.json"
        write_json(state_path, state)
        fake = FakeV26(root)
        return fake, fake.sha256(state_path)

    def test_clean_offline_partial_is_quarantined_and_resumed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake_v26, state_sha = self.prepare(root)
            fake_v27 = FakeV27()
            fake_v32 = types.SimpleNamespace(install_algorithm_hooks=mock.Mock())
            fake_v33 = types.SimpleNamespace(install_prethird_hook=mock.Mock())
            with mock.patch.object(v34, "import_module", return_value=fake_v33):
                recovered = v34.recover_boundary_v34(
                    fake_v26,
                    fake_v27,
                    object(),
                    fake_v32,
                    object(),
                    state_sha,
                    15,
                )
            self.assertEqual(recovered[-1], 15)
            self.assertEqual(recovered[0]["controller_version"], 34)
            self.assertFalse((root / "cycle_015").exists())
            quarantines = list(root.glob("quarantine_cycle_015_v34_offline_*"))
            self.assertEqual(len(quarantines), 1)
            self.assertTrue((quarantines[0] / "config.a.json").is_file())
            self.assertEqual(len(fake_v27.clean_roots), 2)
            fake_v32.install_algorithm_hooks.assert_called_once()
            fake_v33.install_prethird_hook.assert_called_once()
            state = fake_v26.load(root / "controller_state.json")
            audit = state["handover_audits"][-1]
            self.assertEqual(audit["physical_workload_calls_added"], 0)
            self.assertFalse(audit["formal_state_mutated"])

    def test_runtime_partial_fails_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake_v26, state_sha = self.prepare(root, with_runtime=True)
            before = (root / "controller_state.json").read_bytes()
            with self.assertRaisesRegex(RuntimeError, "crossed workload boundary"):
                v34.recover_boundary_v34(
                    fake_v26,
                    FakeV27(),
                    object(),
                    types.SimpleNamespace(install_algorithm_hooks=mock.Mock()),
                    object(),
                    state_sha,
                    15,
                )
            self.assertEqual(before, (root / "controller_state.json").read_bytes())
            self.assertTrue((root / "cycle_015/diagnostic_probe").is_dir())


if __name__ == "__main__":
    unittest.main()

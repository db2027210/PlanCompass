from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools/audit_job_v49_discovery_repeat.py"
SELECTION_FIXTURE = Path("/tmp/job_diag003_top5.json")
SUMMARY_FIXTURE = Path("/tmp/job_diag003_summary.json")


def import_module():
    spec = importlib.util.spec_from_file_location("job_discovery_repeat_test", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot import discovery-repeat auditor")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


audit_module = import_module()


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("fixture is not an object")
    return value


def dump(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def sha(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


class JobV49DiscoveryRepeatAuditTest(unittest.TestCase):
    def fixture(self, root: Path) -> tuple[Path, Path, Path, Path]:
        b = root / "attempt"
        t = b / audit_module.FORMAL_CAMPAIGN
        autonomous = t / audit_module.AUTONOMOUS_ROOT
        cycle = autonomous / "cycle_005"
        state_path = t / "package/formal_state.json"
        dump(
            state_path,
            {"accepted_count": 3, "global_ordinal": 386, "formal_replay_count": 206},
        )
        dump(
            autonomous / "controller_state.json",
            {
                "schema_version": "job_fresh10_target10_autonomous_v49",
                "status": "running",
                "cycle": 5,
                "phase": "diagnostic_discovery",
                "accepted_count": 3,
                "target_accepted": 10,
                "formal_state_sha256": sha(state_path),
                "manual_candidate_injection": False,
                "qps_used_for_inner_scoring": False,
            },
        )
        (cycle / "discovery_top5.json").parent.mkdir(parents=True)
        (cycle / "discovery_top5.json").write_bytes(SELECTION_FIXTURE.read_bytes())
        for name, value in (
            ("replay_counter.txt", 391),
            ("formal_search_replay_counter.txt", 206),
            ("formal_search_accepted_counter.txt", 3),
        ):
            (b / name).write_text(f"{value}\n", encoding="utf-8")
        replay_root = cycle / "discovery_replay_01"
        replay_root.mkdir()
        (replay_root / "top5.json").write_bytes(SELECTION_FIXTURE.read_bytes())
        dump(
            replay_root / "counters_before.json",
            {"global": 386, "formal_replay": 206, "accepted": 3},
        )
        summary_path = replay_root / "replay/summary.json"
        summary_path.parent.mkdir()
        summary_path.write_bytes(SUMMARY_FIXTURE.read_bytes())
        dump(
            replay_root / "diagnostic_audit.json",
            {
                "status": "clean5_state_neutral",
                "selection_sha256": sha(replay_root / "top5.json"),
                "summary_sha256": sha(summary_path),
                "physical_calls": 5,
                "batch_budget_attempts": 5,
                "valid_replays": 5,
                "technical_failures": 0,
                "accepted_added": 0,
                "accepted_counter": 3,
                "formal_state_sha256": sha(state_path),
            },
        )
        return b, autonomous, cycle, replay_root

    def test_clean5_is_exact_repeat_eligible(self) -> None:
        with tempfile.TemporaryDirectory(prefix="job_repeat_ready_") as raw:
            b, _autonomous, _cycle, replay_root = self.fixture(Path(raw))
            result = audit_module.run(
                type("Args", (), {"attempt_root": b, "cycle": 5})()
            )
            self.assertEqual(result["status"], "pass_or_in_progress")
            self.assertTrue(result["exact_repeat_eligible"])
            self.assertEqual(result["clean_discovery_roots"], [str(replay_root)])
            self.assertFalse(result["errors"])

    def test_repeat_requires_identical_parameter_set_and_roles(self) -> None:
        with tempfile.TemporaryDirectory(prefix="job_repeat_contract_") as raw:
            b, autonomous, cycle, _replay_root = self.fixture(Path(raw))
            controller = load(autonomous / "controller_state.json")
            controller["phase"] = "repeat_gate"
            dump(autonomous / "controller_state.json", controller)
            repeat = cycle / "repeat_01"
            repeat.mkdir()
            repeat_selection = load(SELECTION_FIXTURE)
            dump(repeat / "top5.json", repeat_selection)
            (repeat / "top5.second.json").write_bytes((repeat / "top5.json").read_bytes())
            result = audit_module.run(
                type("Args", (), {"attempt_root": b, "cycle": 5})()
            )
            self.assertEqual(result["status"], "pass_or_in_progress")
            self.assertEqual(result["repeat_roots"], [str(repeat)])

            repeat_selection["top5"][0]["candidate_params"][
                "cpu_tuple_cost"
            ] *= 2
            dump(repeat / "top5.json", repeat_selection)
            result = audit_module.run(
                type("Args", (), {"attempt_root": b, "cycle": 5})()
            )
            self.assertEqual(result["status"], "fail_closed")
            self.assertTrue(
                any("repeat_exact_parameter_set" in error for error in result["errors"])
            )


if __name__ == "__main__":
    unittest.main()

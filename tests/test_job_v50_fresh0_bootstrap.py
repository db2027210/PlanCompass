from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools/bootstrap_job_v50_fresh0.py"
SUMMARY_FIXTURE = Path("/tmp/job_diag003_summary.json")


def import_module():
    spec = importlib.util.spec_from_file_location("job_fresh0_bootstrap_test", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot import fresh0 bootstrap")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bootstrap = import_module()


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("not an object")
    return value


def dump(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


class JobV50Fresh0BootstrapTest(unittest.TestCase):
    def test_historical_three_is_excluded_and_fresh_counter_starts_zero(self) -> None:
        with tempfile.TemporaryDirectory(prefix="job_fresh0_") as raw:
            root = Path(raw)
            historical_state = root / "old/state.json"
            historical_accepted = root / "old/accepted.txt"
            global_counter = root / "global.txt"
            formal_counter = root / "formal.txt"
            old_cycle = root / "old/cycle005"
            fresh_root = root / "fresh"
            dump(
                historical_state,
                {
                    "accepted_count": 3,
                    "accepted_target": 10,
                    "global_ordinal": 386,
                    "formal_replay_count": 206,
                    "formal_valid_replay_count": 20,
                    "gradient_observation_count": 3,
                    "incumbent_score": 5.0,
                    "incumbent_params": {"p": 1.0},
                    "range_by_parameter": {"p": {"min": 1.0, "max": 2.0}},
                    "last_formal_batch": {"accepted_delta": 1},
                },
            )
            historical_accepted.parent.mkdir(parents=True, exist_ok=True)
            historical_accepted.write_text("3\n", encoding="utf-8")
            global_counter.write_text("392\n", encoding="utf-8")
            formal_counter.write_text("206\n", encoding="utf-8")
            replay01 = load(SUMMARY_FIXTURE)
            replay01["technical_failure_count"] = 1
            replay01["successful_real_replay_count"] = 4
            replay01["physical_invocation_count"] = 5
            dump(old_cycle / "discovery_replay_01/replay/summary.json", replay01)
            replay02 = copy.deepcopy(replay01)
            replay02["physical_invocation_count"] = 1
            replay02["successful_real_replay_count"] = 0
            replay02["technical_failure_count"] = 1
            dump(old_cycle / "discovery_replay_02/replay/summary.json", replay02)
            old_bytes = historical_state.read_bytes()
            with mock.patch.multiple(
                bootstrap,
                HISTORICAL_STATE=historical_state,
                HISTORICAL_ACCEPTED_COUNTER=historical_accepted,
                GLOBAL_COUNTER=global_counter,
                FORMAL_COUNTER=formal_counter,
                OLD_CYCLE=old_cycle,
                FRESH_ROOT=fresh_root,
                FRESH_STATE=fresh_root / "package/formal_state.json",
                FRESH_ACCEPTED_COUNTER=fresh_root / "formal_search_accepted_counter.txt",
                BOOTSTRAP_AUDIT=fresh_root / "fresh0_bootstrap_audit.json",
            ):
                self.assertEqual(bootstrap.main(), 0)
                self.assertEqual(bootstrap.main(), 0)
                fresh = load(bootstrap.FRESH_STATE)
                self.assertEqual(fresh["accepted_count"], 0)
                self.assertEqual(fresh["historical_accepted_excluded"], 3)
                self.assertEqual(fresh["global_ordinal"], 392)
                self.assertEqual(fresh["formal_replay_count"], 206)
                self.assertEqual(fresh["formal_valid_replay_count"], 0)
                self.assertEqual(fresh["gradient_observation_count"], 0)
                self.assertEqual(fresh["incumbent_score"], bootstrap.BASELINE_SCORE)
                self.assertEqual(fresh["incumbent_params"], bootstrap.BASELINE_PARAMS)
                self.assertEqual(
                    fresh["range_by_parameter"],
                    {
                        name: {"min": value, "max": value}
                        for name, value in bootstrap.BASELINE_PARAMS.items()
                    },
                )
                self.assertFalse(fresh["historical_incumbent_or_range_inherited"])
                self.assertNotIn("last_formal_batch", fresh)
                self.assertEqual(bootstrap.FRESH_ACCEPTED_COUNTER.read_text(), "0\n")
            self.assertEqual(historical_state.read_bytes(), old_bytes)
            self.assertEqual(historical_accepted.read_text(), "3\n")


if __name__ == "__main__":
    unittest.main()

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
MODULE_PATH = ROOT / "tools/bootstrap_job_v52_fresh0.py"


def import_module():
    spec = importlib.util.spec_from_file_location("job_v52_bootstrap_test", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot import JOB v52 bootstrap")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bootstrap = import_module()


def dump(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class JobV52Fresh0BootstrapTest(unittest.TestCase):
    def test_new_root_excludes_v50_and_inherits_only_physical_counters(self) -> None:
        with tempfile.TemporaryDirectory(prefix="job_v52_bootstrap_") as raw:
            root = Path(raw)
            historical_state = root / "historical/state.json"
            historical_accepted = root / "historical/accepted.txt"
            v50_root = root / "v50"
            v50_state_path = v50_root / "package/formal_state.json"
            v50_accepted = v50_root / "formal_search_accepted_counter.txt"
            v52_root = root / "v52"
            global_counter = root / "global.txt"
            formal_counter = root / "formal.txt"
            historical = {"accepted_count": 3}
            v50_state = {
                "accepted_count": 0,
                "accepted_target": 10,
                "incumbent_score": bootstrap.BASELINE_SCORE,
                "incumbent_params": copy.deepcopy(bootstrap.BASELINE_PARAMS),
                "range_by_parameter": {
                    name: {"min": value, "max": value}
                    for name, value in bootstrap.BASELINE_PARAMS.items()
                },
                "gradient_observation_count": 0,
                "global_ordinal": 392,
                "formal_replay_count": 206,
            }
            dump(historical_state, historical)
            historical_accepted.parent.mkdir(parents=True, exist_ok=True)
            historical_accepted.write_text("3\n", encoding="utf-8")
            dump(v50_state_path, v50_state)
            v50_accepted.write_text("0\n", encoding="utf-8")
            global_counter.write_text("395\n", encoding="utf-8")
            formal_counter.write_text("206\n", encoding="utf-8")
            for ordinal in range(1, 4):
                phase = v50_root / f"cycle_001/discovery_replay_{ordinal:02d}"
                selection = {
                    "top5": [
                        {
                            "three_level_fingerprint": {
                                "id": f"three-level-sha256:{ordinal}{rank:063d}"
                            }
                        }
                        for rank in range(1, 6)
                    ]
                }
                selection_path = phase / "top5.json"
                dump(selection_path, selection)
                neutral = {
                    "accepted_count": 0,
                    "incumbent_score": bootstrap.BASELINE_SCORE,
                }
                dump(
                    phase / "replay/summary.json",
                    {
                        "diagnostic_only": True,
                        "selection_sha256": sha(selection_path),
                        "physical_invocation_count": 1,
                        "successful_real_replay_count": 0,
                        "technical_failure_count": 1,
                        "baseline_params_restored": True,
                        "baseline_restore": {"success": True},
                        "initial_state": neutral,
                        "final_state": neutral,
                        "formal_update_flags": {
                            "accepted": False,
                            "gradient": False,
                            "incumbent": False,
                            "range": False,
                        },
                    },
                )
            extra = v50_root / "cycle_001/fresh_discovery_04/top5.json"
            dump(
                extra,
                {
                    "top5": [
                        {
                            "three_level_fingerprint": {
                                "id": f"three-level-sha256:extra{rank}"
                            }
                        }
                        for rank in range(1, 6)
                    ]
                },
            )
            patches = {
                "HISTORICAL_STATE": historical_state,
                "HISTORICAL_ACCEPTED_COUNTER": historical_accepted,
                "V50_ROOT": v50_root,
                "V50_STATE": v50_state_path,
                "V50_ACCEPTED_COUNTER": v50_accepted,
                "V52_ROOT": v52_root,
                "V52_STATE": v52_root / "package/formal_state.json",
                "V52_ACCEPTED_COUNTER": v52_root / "formal_search_accepted_counter.txt",
                "BOOTSTRAP_AUDIT": v52_root / "fresh0_bootstrap_audit.json",
                "GLOBAL_COUNTER": global_counter,
                "FORMAL_COUNTER": formal_counter,
            }
            old_v50_sha = sha(v50_state_path)
            with mock.patch.multiple(bootstrap, **patches):
                self.assertEqual(bootstrap.main(), 0)
                self.assertEqual(bootstrap.main(), 0)
                fresh = bootstrap.load_json(patches["V52_STATE"])
                audit = bootstrap.load_json(patches["BOOTSTRAP_AUDIT"])
                global_counter.write_text("394\n", encoding="utf-8")
                with self.assertRaisesRegex(
                    RuntimeError,
                    "v52r2 external/v50 technical counter handoff mismatch",
                ):
                    bootstrap.main()
            self.assertEqual(fresh["accepted_count"], 0)
            self.assertEqual(fresh["global_ordinal"], 395)
            self.assertEqual(fresh["formal_replay_count"], 206)
            self.assertEqual(fresh["gradient_observation_count"], 0)
            self.assertEqual(fresh["range_by_parameter"], v50_state["range_by_parameter"])
            self.assertEqual(audit["v50_technical_physical_calls"], 3)
            self.assertEqual(audit["v50_excluded_fingerprint_count"], 20)
            self.assertEqual(sha(v50_state_path), old_v50_sha)
            self.assertEqual(v50_accepted.read_text(encoding="utf-8").strip(), "0")


if __name__ == "__main__":
    unittest.main()

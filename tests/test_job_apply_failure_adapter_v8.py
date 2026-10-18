from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
MODULE_PATH = TOOLS / "run_job_round8_strict_serial_robust_v8_apply_failure.py"
FIXTURE_ROOT = Path("/tmp/job_round8_corrected_top5_v6_20260731")
SELECTION_FIXTURE = Path("/tmp/job_diag003_top5.json")
SUMMARY_FIXTURE = Path("/tmp/job_diag003_summary.json")


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"fixture is not an object: {path}")
    return value


def import_adapter():
    old_environment = os.environ.get("JOB_ROUND8_BASE_RUNNER")
    os.environ["JOB_ROUND8_BASE_RUNNER"] = str(
        FIXTURE_ROOT / "run_job_round8_strict_serial.py"
    )
    sys.path.insert(0, str(TOOLS))
    try:
        spec = importlib.util.spec_from_file_location("job_robust_v8_test", MODULE_PATH)
        if spec is None or spec.loader is None:
            raise RuntimeError("cannot import JOB robust v8 adapter")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if sys.path and sys.path[0] == str(TOOLS):
            sys.path.pop(0)
        if old_environment is None:
            os.environ.pop("JOB_ROUND8_BASE_RUNNER", None)
        else:
            os.environ["JOB_ROUND8_BASE_RUNNER"] = old_environment


adapter = import_adapter()


class JobApplyFailureAdapterV8Test(unittest.TestCase):
    def make_apply_failure_fixture(self, root: Path) -> argparse.Namespace:
        selection = root / "top5.json"
        selection.write_bytes(SELECTION_FIXTURE.read_bytes())
        replay = root / "replay"
        replay.mkdir(parents=True)
        summary = load_json(SUMMARY_FIXTURE)
        successful = copy.deepcopy(summary["successful"])
        failed = successful.pop()
        failed.update(
            {
                "technical_failure": True,
                "error": "RuntimeError: synthetic parameter apply failure",
                "accepted": False,
                "gradient_update_applied": False,
                "incumbent_update_applied": False,
                "range_update_applied": False,
            }
        )
        summary["successful"] = successful
        summary["technical_failures"] = [failed]
        summary["invocations"] = [
            item for item in summary["invocations"] if int(item["rank"]) != 5
        ]
        summary["physical_invocation_count"] = 4
        summary["real_workload_invocation_count"] = 4
        summary["successful_real_replay_count"] = 4
        summary["technical_failure_count"] = 1
        summary["rejection_count"] = sum(
            1 for item in successful if item.get("accepted") is False
        )
        (replay / "summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return argparse.Namespace(selection=selection, replay_root=replay)

    def test_exact_base_validation_mismatch_recovers_without_fake_ordinal(self) -> None:
        with tempfile.TemporaryDirectory(prefix="job_apply_v8_") as raw:
            args = self.make_apply_failure_fixture(Path(raw))
            before = (args.replay_root / "summary.json").read_bytes()
            result = adapter.recover_apply_failure_result(
                args,
                ValueError(adapter.EXPECTED_BASE_ERROR),
            )
            self.assertEqual(result["physical_invocation_count"], 4)
            self.assertEqual(result["batch_budget_attempt_count"], 5)
            self.assertEqual(result["pre_invocation_technical_failure_count"], 1)
            self.assertEqual(result["pre_invocation_technical_failure_ranks"], [5])
            self.assertEqual(result["successful_real_replay_count"], 4)
            self.assertEqual(result["technical_failure_count"], 1)
            self.assertEqual(
                (args.replay_root / "summary.json").read_bytes(),
                before,
                "offline validation must not insert a synthetic physical call",
            )

    def test_unrelated_base_error_is_never_recovered(self) -> None:
        with tempfile.TemporaryDirectory(prefix="job_apply_v8_wrong_") as raw:
            args = self.make_apply_failure_fixture(Path(raw))
            with self.assertRaisesRegex(ValueError, "unrelated failure"):
                adapter.recover_apply_failure_result(
                    args,
                    ValueError("unrelated failure"),
                )

    def test_incomplete_attempt_ledger_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="job_apply_v8_partial_") as raw:
            args = self.make_apply_failure_fixture(Path(raw))
            path = args.replay_root / "summary.json"
            summary = load_json(path)
            summary["technical_failures"] = []
            summary["technical_failure_count"] = 0
            path.write_text(json.dumps(summary) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "five-attempt batch"):
                adapter.recover_apply_failure_result(
                    args,
                    ValueError(adapter.EXPECTED_BASE_ERROR),
                )


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Ensure search-only termination cannot trigger extra workload replays."""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from algorithm_c_adaptive_search import AdaptiveSearchPolicy
from algorithm_c_execution_role import FORMAL
from shell.run_pg_algorithm_c_budgeted_scene import BudgetedLaneController
from shell import audit_pg_algorithm_c_adaptive_formal as auditor
from shell import run_pg_algorithm_c_postterminal_abc_validation as handoff


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="c-search-only-") as temporary:
        root = Path(temporary)
        for reason, restore_ok, completed in (
            ("adaptive_convergence", True, True),
            ("adaptive_convergence", False, False),
            ("signal_stop", True, False),
            ("candidate_generation_exhausted_without_physical_evidence", True, False),
        ):
            controller = BudgetedLaneController.__new__(BudgetedLaneController)
            controller.adaptive_policy = AdaptiveSearchPolicy()
            controller.execution_role = FORMAL
            controller.algorithm_cfg = {"scene": "xp_cpu"}
            controller.output_dir = root
            controller.policy = {}
            controller.stop_requested = reason == "signal_stop"
            controller.state = {}
            controller.trial = {
                "started_at_epoch_s": time.time(),
                "search_replays": 2,
                "search_technical_failures": 0,
                "feasible_archive": {
                    "observed": {
                        "params": {"cpu_tuple_cost": 0.02},
                        "observations": [{"label": "search_1", "qps": 105.0}],
                    },
                },
            }
            controller.save_state = mock.Mock()
            controller.log = mock.Mock()
            controller.restore_clean_baseline = mock.Mock(return_value={"passed": restore_ok})
            controller.replay_batch = mock.Mock(side_effect=AssertionError("unexpected final replay"))
            controller.confirm_finalist = mock.Mock(side_effect=AssertionError("unexpected confirmation"))
            controller.finish_trial(reason)
            controller.replay_batch.assert_not_called()
            controller.confirm_finalist.assert_not_called()
            controller.restore_clean_baseline.assert_called_once()
            result = json.loads((root / FORMAL.result_filename).read_text())
            assert result["trial"]["search_completed_successfully"] is completed
            assert result["trial"]["validation"]["physical_replays"] == 0
            assert result["trial"]["valid_for_quality_or_efficiency_claim"] is False
            assert (controller.state["final_champion"] is not None) is completed

        (root / "budgeted_launch_manifest.json").write_text(json.dumps({
            "final_validation_enabled": False,
            "postterminal_abc_validation_enabled": False,
        }))
        (root / "launcher_status.json").write_text(json.dumps({"phase": "finished"}))
        with mock.patch.object(auditor, "_load_post_terminal_references", side_effect=AssertionError):
            report = auditor.audit(root)
        assert report["reason"] == "search_only_campaign_has_no_independent_final_validation"
        assert report["post_terminal_baseline_evidence_loaded"] is False
        missing = root / "must_not_run.py"
        with mock.patch.object(handoff, "run_logged", side_effect=AssertionError):
            try:
                handoff.run_handoff(
                    formal_root=root, output_root=root / "abc", seeds=[1, 2],
                    common_runner=missing, common_verifier=missing, builder=missing,
                    terminal_auditor=missing, python_bin=Path(sys.executable),
                )
            except RuntimeError as exc:
                assert "validation is disabled" in str(exc)
            else:
                raise AssertionError("disabled validation handoff proceeded")
        assert not (root / "abc").exists()
    print("PASS: search-only output, zero final replays, disabled validation relay", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

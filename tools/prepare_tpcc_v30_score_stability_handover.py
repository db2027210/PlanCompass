#!/usr/bin/env python3
"""Seal a zero-call TPC-C v30 handover at a completed batch boundary."""

from __future__ import annotations

import argparse
import copy
from pathlib import Path
from typing import Any, Dict

try:
    import tpcc_autonomous_target10_controller_v25 as v25
except ModuleNotFoundError:
    from tools import tpcc_autonomous_target10_controller_v25 as v25


PROJECT_ROOT = Path("/home/anonymous/program/resource_adaptive")
V30_CONTROLLER = PROJECT_ROOT / "tools/tpcc_autonomous_target10_controller_v30_score_stability.py"
V30_TEST = PROJECT_ROOT / "tests/test_tpcc_score_stability_v30.py"
V30_PREFLIGHT = PROJECT_ROOT / "tools/preflight_tpcc_v30_score_stability.py"
V30_RECLASSIFIER = PROJECT_ROOT / "tools/reclassify_tpcc_v30_validation153_154.py"
V30_RECLASSIFICATION = (
    PROJECT_ROOT
    / "history/tpcc_final_algorithm_fresh10_v29_20260731T2005/"
    "autonomous_controller_v32/v30_zero_call_reclassification_validation153_154.json"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_manifest", type=Path)
    parser.add_argument("output_manifest", type=Path)
    return parser.parse_args()


def _completed_current_run(state: Dict[str, Any]) -> Dict[str, Any]:
    current = state.get("current_run")
    if not isinstance(current, dict):
        audits = state.get("audits") or []
        latest = audits[-1] if audits else None
        if (
            not isinstance(latest, dict)
            or latest.get("mode") != "diagnostic_validation"
            or latest.get("clean") is not True
            or int(latest.get("physical_calls", -1)) != v25.CAP
            or int(latest.get("valid_replays", -1)) != v25.CAP
            or int(latest.get("technical_failures", -1)) != 0
            or latest.get("baseline_restored") is not True
        ):
            raise RuntimeError(
                "v30 handover requires a completed clean5 current run or latest audit"
            )
        for path_field, sha_field in (
            ("selection_path", "selection_sha256"),
            ("ledger_path", "ledger_sha256"),
            ("summary_path", "summary_sha256"),
        ):
            path = Path(str(latest.get(path_field) or "")).resolve()
            if not path.is_file() or v25.sha256(path) != latest.get(sha_field):
                raise RuntimeError(f"latest completed audit SHA mismatch: {path_field}")
        return {
            "mode": latest["mode"],
            "root": latest["root"],
            "selection_path": latest["selection_path"],
            "selection_sha256": latest["selection_sha256"],
            "ledger_path": latest["ledger_path"],
            "ledger_sha256": latest["ledger_sha256"],
            "summary_path": latest["summary_path"],
            "summary_sha256": latest["summary_sha256"],
            "physical_calls": 5,
            "valid_replays": 5,
            "technical_failures": 0,
            "formal_state_mutated": False,
            "source": "latest_completed_audit",
        }
    summary_path = Path(str(current.get("summary_path") or "")).resolve()
    ledger_path = Path(str(current.get("ledger_path") or "")).resolve()
    if not summary_path.is_file() or not ledger_path.is_file():
        raise RuntimeError("current_run has not reached its completed summary boundary")
    ledger = v25.load_json(ledger_path)
    if (
        int(ledger.get("real_workload_invocation_count", -1)) != v25.CAP
        or len(ledger.get("successful") or []) != v25.CAP
        or ledger.get("technical_failures") not in ([], None)
    ):
        raise RuntimeError("handover current_run is not clean physical5/valid5/tech0")
    summary = v25.load_json(summary_path)
    if current.get("mode") != "formal" and (
        summary.get("baseline_params_restored") is not True
        or summary.get("baseline_snapshot_restored") is not True
        or summary.get("accepted_count_delta") != 0
        or summary.get("incumbent_updated") is not False
        or summary.get("range_updated") is not False
        or summary.get("gradient_update_applied") is not False
        or summary.get("state_commit_eligible") is not False
    ):
        raise RuntimeError("handover diagnostic current_run is not state-neutral")
    return {
        "mode": current["mode"],
        "root": current["root"],
        "selection_path": current["selection_path"],
        "selection_sha256": current["selection_sha256"],
        "ledger_path": str(ledger_path),
        "ledger_sha256": v25.sha256(ledger_path),
        "summary_path": str(summary_path),
        "summary_sha256": v25.sha256(summary_path),
        "physical_calls": 5,
        "valid_replays": 5,
        "technical_failures": 0,
        "formal_state_mutated": False,
    }


def main() -> int:
    args = parse_args()
    source_path = args.source_manifest.resolve()
    output_path = args.output_manifest.resolve()
    source = v25.load_json(source_path)
    if int(source.get("controller_version", -1)) != 29:
        raise RuntimeError("source manifest is not the sealed v29 controller")
    state_path = Path(source["controller_state_path"]).resolve()
    formal_state_path = Path(source["formal_state_path"]).resolve()
    state = v25.load_json(state_path)
    formal = v25.load_json(formal_state_path)
    if formal.get("status") != "ready" or int(formal.get("accepted_count", -1)) != 2:
        raise RuntimeError("formal state is not the expected ready accepted=2 boundary")
    pending = _completed_current_run(state)
    if (
        not V30_CONTROLLER.is_file()
        or not V30_TEST.is_file()
        or not V30_PREFLIGHT.is_file()
        or not V30_RECLASSIFIER.is_file()
        or not V30_RECLASSIFICATION.is_file()
    ):
        raise RuntimeError("v30 controller, preflight, or regression test is missing")

    manifest = copy.deepcopy(source)
    manifest.update(
        {
            "controller_version": 30,
            "controller_entrypoint": str(V30_CONTROLLER),
            "controller_handover_contract": (
                "completed_clean5_current_run_zero_call_v29_to_v30_score_stability"
            ),
            "controller_source_manifest": str(source_path),
            "controller_source_manifest_sha256": v25.sha256(source_path),
            "resumed_from_manifest": str(source_path),
            "resumed_from_manifest_sha256": v25.sha256(source_path),
            "initial_controller_state_sha256": v25.sha256(state_path),
            "initial_formal_state_sha256": v25.sha256(formal_state_path),
            "current_run": copy.deepcopy(state["current_run"]),
            "next_ordinal": int(state["next_ordinal"]),
            "pending_completed_run_at_handover": pending,
            "controller_state_boundary": {
                "status": state.get("status"),
                "next_mode": state.get("next_mode"),
                "next_ordinal": int(state["next_ordinal"]),
                "current_run": copy.deepcopy(state["current_run"]),
            },
            "v30_activation_authorized": True,
            "score_stability_contract": (
                "cpu_io_memory_window_stability_plus_separable_score_ci_v30"
            ),
            "unstable_resource_replay_policy": (
                "valid_rejected_probe_only_calibration_ineligible_nontechnical"
            ),
            "v30_zero_call_reclassification_path": str(V30_RECLASSIFICATION),
            "v30_zero_call_reclassification_sha256": v25.sha256(
                V30_RECLASSIFICATION
            ),
            "v30_real_regression_validation153": {
                "selection_path": str(
                    PROJECT_ROOT
                    / "history/tpcc_final_algorithm_fresh10_v29_20260731T2005/"
                    "autonomous_controller_v32/prepare_153/sealed_run/top5.json"
                ),
                "ledger_path": (
                    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
                    "tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/"
                    "formal10_final_algorithm_fresh10_v29_20260731T2005/"
                    "autonomous_runs_v32/autonomous_diagnostic_validation_153/"
                    "strict_top5_real_replay_round14_ledger_v1.json"
                ),
                "expected_physical": 5,
                "expected_valid": 5,
                "expected_technical": 0,
                "expected_raw_trusted_spearman": -1.0,
                "expected_formal_admission": False,
            },
        }
    )
    pins = [
        item
        for item in manifest.get("pinned_files") or []
        if item.get("path")
        not in {
            str(V30_CONTROLLER),
            str(V30_TEST),
            str(V30_PREFLIGHT),
            str(V30_RECLASSIFIER),
            str(V30_RECLASSIFICATION),
        }
    ]
    pins.extend(
        [
            {"path": str(V30_CONTROLLER), "sha256": v25.sha256(V30_CONTROLLER)},
            {"path": str(V30_TEST), "sha256": v25.sha256(V30_TEST)},
            {"path": str(V30_PREFLIGHT), "sha256": v25.sha256(V30_PREFLIGHT)},
            {"path": str(V30_RECLASSIFIER), "sha256": v25.sha256(V30_RECLASSIFIER)},
            {
                "path": str(V30_RECLASSIFICATION),
                "sha256": v25.sha256(V30_RECLASSIFICATION),
            },
        ]
    )
    manifest["pinned_files"] = pins
    v25.atomic_json(output_path, manifest)
    print(f"manifest={output_path}", flush=True)
    print(f"manifest_sha256={v25.sha256(output_path)}", flush=True)
    print("physical_workload_calls_added=0", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

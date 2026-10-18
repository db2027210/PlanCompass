#!/usr/bin/env python3
"""Prepare a fresh accepted=0 TPC-C formal campaign from one passing clean5 gate."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.tpcc_autonomous_target10_controller_v23 import (
    audit_completed_run,
    quality_gate,
    sha256,
    validate_selection,
)


def load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--source-config", type=Path, required=True)
    parser.add_argument(
        "--baseline",
        type=Path,
        required=True,
        help="clean diagnostic summary carrying baseline_actual_score",
    )
    parser.add_argument(
        "--baseline-point",
        type=Path,
        required=True,
        help="sealed workload baseline point consumed by the formal runner",
    )
    parser.add_argument("--campaign-root", type=Path, required=True)
    parser.add_argument("--historical-accepted-excluded", type=int, required=True)
    args = parser.parse_args()

    paths = [
        args.selection.resolve(),
        args.ledger.resolve(),
        args.summary.resolve(),
        args.source_config.resolve(),
        args.baseline.resolve(),
        args.baseline_point.resolve(),
    ]
    if any(not path.is_file() for path in paths):
        raise RuntimeError("TPC-C fresh0 input is missing")
    campaign = args.campaign_root.resolve()
    if campaign.exists():
        raise FileExistsError(campaign)

    run = {
        "mode": "diagnostic_validation",
        "root": str(args.selection.resolve().parent),
        "selection_path": str(args.selection.resolve()),
        "selection_sha256": sha256(args.selection.resolve()),
        "ledger_path": str(args.ledger.resolve()),
        "ledger_sha256": sha256(args.ledger.resolve()),
        "summary_path": str(args.summary.resolve()),
        "summary_sha256": sha256(args.summary.resolve()),
    }
    audit = audit_completed_run(
        run,
        campaign / "not_used_for_diagnostic_audit.json",
    )
    gate = quality_gate([audit])
    selection = load(args.selection.resolve())
    score_span_passed = bool(
        selection.get("score_span_gate_passed") is True
        and float(selection["source_actual_score_span"])
        > float(selection["minimum_required_score_span"])
    )
    if gate.get("passed") is not True or not score_span_passed:
        raise RuntimeError("TPC-C clean5 formal entry gate did not pass")

    promoted = copy.deepcopy(selection)
    promoted.update(
        {
            "formal_expected_improvement_required": False,
            "formal_entry_admission_contract": (
                "diagnostic_quality_gate_passed_then_real_replay_"
                "accept_or_reject_v1"
            ),
            "formal_non_improving_candidate_policy": (
                "real_replay_rejected_without_state_update"
            ),
            "formal_admission_score": "predicted_score_interval.lower",
            "promoted_from_diagnostic_validation": {
                "selection_path": str(args.selection.resolve()),
                "selection_sha256": sha256(args.selection.resolve()),
            },
        }
    )
    validate_selection(promoted, mode="formal")

    campaign.mkdir(parents=True, exist_ok=False)
    promoted_path = campaign / "promoted_top5.json"
    atomic_json(promoted_path, promoted)
    entry_gate = {
        "schema_version": "tpcc_fresh0_formal_entry_gate_v1",
        "passed": True,
        "formal_start_authorized": True,
        "selection_sha256": sha256(promoted_path),
        "source_diagnostic_selection_sha256": sha256(args.selection.resolve()),
        "quality_gate": gate,
        "score_span_gate": {
            "passed": True,
            "source_actual_score_span": selection["source_actual_score_span"],
            "minimum_required_score_span": selection[
                "minimum_required_score_span"
            ],
        },
        "qps_role": "outer_audit_only_not_inner_score",
    }
    gate_path = campaign / "entry_gate.json"
    atomic_json(gate_path, entry_gate)

    config = load(args.source_config.resolve())
    baseline = load(args.baseline.resolve())
    baseline_point = load(args.baseline_point.resolve())
    initial_params = {
        name: float(value) for name, value in config["initial_params"].items()
    }
    state = {
        "schema_version": "tpcc_formal_target10_state_v1",
        "status": "ready",
        "accepted_target": 10,
        "inherited_accepted": 0,
        "historical_accepted_excluded": args.historical_accepted_excluded,
        "accepted_count": 0,
        "formal_physical_call_count": 0,
        "formal_valid_replay_count": 0,
        "formal_technical_failure_count": 0,
        "incumbent_score": float(baseline["baseline_actual_score"]),
        "incumbent_params": initial_params,
        "range_state": copy.deepcopy(config["param_ranges"]),
        "baseline_summary_sha256": sha256(args.baseline_point.resolve()),
        "baseline_correctness_passed": (
            baseline_point.get("correctness_passed") is True
        ),
        "qps_used_for_inner_scoring": False,
        "entry_gate_sha256": sha256(gate_path),
        "diagnostic_audit": audit,
    }
    state_path = campaign / "formal_state.json"
    atomic_json(state_path, state)
    print(
        json.dumps(
            {
                "campaign_root": str(campaign),
                "selection": str(promoted_path),
                "selection_sha256": sha256(promoted_path),
                "gate": str(gate_path),
                "gate_sha256": sha256(gate_path),
                "formal_state": str(state_path),
                "formal_state_sha256": sha256(state_path),
                "accepted_count": 0,
                "accepted_target": 10,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

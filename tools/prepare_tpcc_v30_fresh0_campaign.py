#!/usr/bin/env python3
"""Prepare a fresh accepted=0 TPC-C v30 campaign after material change."""

from __future__ import annotations

import argparse
import copy
import time
from pathlib import Path
from typing import Any, Dict

try:
    import tpcc_autonomous_target10_controller_v25 as v25
    import tpcc_autonomous_target10_controller_v30_score_stability as v30
except ModuleNotFoundError:
    from tools import tpcc_autonomous_target10_controller_v25 as v25
    from tools import tpcc_autonomous_target10_controller_v30_score_stability as v30


PROJECT_ROOT = Path("/home/anonymous/program/resource_adaptive")
OLD_CONTROLLER_STATE = (
    PROJECT_ROOT
    / "history/tpcc_final_algorithm_fresh10_v29_20260731T2005/"
    "autonomous_controller_v32/controller_state.json"
)
OLD_FORMAL_STATE = (
    PROJECT_ROOT
    / "history/tpcc_final_algorithm_fresh10_v29_20260731T2005/formal_state.json"
)
FRESH0_TEMPLATE_STATE = (
    PROJECT_ROOT
    / "history/tpcc_final_algorithm_fresh10_v28_20260731T2000/formal_state.json"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_manifest", type=Path)
    parser.add_argument("campaign_root", type=Path)
    parser.add_argument("batch_parent", type=Path)
    parser.add_argument("output_manifest", type=Path)
    return parser.parse_args()


def _audit_for_ordinal(old_state: Dict[str, Any], ordinal: int) -> Dict[str, Any]:
    suffix = f"autonomous_diagnostic_validation_{ordinal}"
    matches = [
        item
        for item in old_state.get("audits") or []
        if str(item.get("root") or "").endswith(suffix)
    ]
    if len(matches) != 1:
        raise RuntimeError(f"expected one sealed validation{ordinal} audit")
    original = copy.deepcopy(matches[0])
    selection_path = Path(original["selection_path"]).resolve()
    ledger_path = Path(original["ledger_path"]).resolve()
    if (
        v25.sha256(selection_path) != original["selection_sha256"]
        or v25.sha256(ledger_path) != original["ledger_sha256"]
    ):
        raise RuntimeError(f"validation{ordinal} source SHA mismatch")
    upgraded = v30.apply_score_stability_audit(
        original,
        v25.load_json(selection_path),
        v25.load_json(ledger_path),
    )
    upgraded["fresh0_training_only"] = True
    upgraded["eligible_for_formal_accepted_count"] = False
    upgraded["historical_accepted_inherited"] = 0
    return upgraded


def main() -> int:
    args = parse_args()
    source_manifest_path = args.source_manifest.resolve()
    campaign_root = args.campaign_root.resolve()
    output_manifest = args.output_manifest.resolve()
    if campaign_root.exists():
        raise FileExistsError(campaign_root)
    source_manifest = v25.load_json(source_manifest_path)
    old_state = v25.load_json(OLD_CONTROLLER_STATE)
    old_formal = v25.load_json(OLD_FORMAL_STATE)
    template = v25.load_json(FRESH0_TEMPLATE_STATE)
    if (
        int(old_formal.get("accepted_count", -1)) != 2
        or int(template.get("accepted_count", -1)) != 0
        or float(template.get("incumbent_score")) != 3.9894795578792417
        or int(template.get("formal_physical_call_count", -1)) != 0
    ):
        raise RuntimeError("fresh0 source states do not match sealed boundary")

    audits = [_audit_for_ordinal(old_state, 153), _audit_for_ordinal(old_state, 154)]
    if any(
        audit.get("formal_admission_blocked_by_score_stability") is not True
        or audit.get("calibration_eligible_replay_count") != 0
        or audit.get("valid_rejected_probe_only_count") != 5
        for audit in audits
    ):
        raise RuntimeError("v30 training audits did not fail closed")
    sources = [
        {
            "name": f"v30_training_validation_{ordinal}",
            "selection_path": audit["selection_path"],
            "selection_sha256": audit["selection_sha256"],
            "ledger_path": audit["ledger_path"],
            "ledger_sha256": audit["ledger_sha256"],
            "training_only": True,
            "accepted_count_eligible": False,
        }
        for ordinal, audit in zip((153, 154), audits)
    ]

    campaign_root.mkdir(parents=True, exist_ok=False)
    formal_state_path = campaign_root / "formal_state.json"
    entry_gate_path = campaign_root / "fresh0_entry_gate.json"
    controller_root = campaign_root / "autonomous_controller"
    controller_state_path = controller_root / "controller_state.json"
    controller_root.mkdir(parents=True, exist_ok=False)

    entry_gate = {
        "schema_version": "tpcc_v30_fresh0_entry_gate_v1",
        "passed": False,
        "formal_start_authorized": False,
        "reason": "new_v30_diagnostic_entry_gate_required_after_material_change",
        "historical_accepted_excluded": 2,
        "inherited_accepted": 0,
        "qps_role": "outer_audit_only_not_inner_score",
    }
    v25.atomic_json(entry_gate_path, entry_gate)
    formal_state = copy.deepcopy(template)
    formal_state.update(
        {
            "accepted_count": 0,
            "accepted_target": 10,
            "inherited_accepted": 0,
            "historical_accepted_excluded": 2,
            "formal_physical_call_count": 0,
            "formal_valid_replay_count": 0,
            "formal_technical_failure_count": 0,
            "last_batch_root": None,
            "entry_gate_sha256": v25.sha256(entry_gate_path),
            "diagnostic_audit": None,
            "material_change_contract": (
                "v30_score_stability_fresh0_no_accepted_inheritance"
            ),
            "training_audit_ordinals": [153, 154],
            "updated_at_epoch": time.time(),
        }
    )
    v25.atomic_json(formal_state_path, formal_state)
    formal_sha = v25.sha256(formal_state_path)
    controller_state = {
        "schema_version": "tpcc_autonomous_target10_state_v1",
        "status": "waiting_current_batch",
        "dry_run": False,
        "audits": audits,
        "sources": sources,
        "current_run": None,
        "next_ordinal": 1,
        "formal_state_sha256": formal_sha,
        "acquisition_transitions": [],
        "technical_failure_audits": [],
        "recovery_audits": [],
        "quarantined_fingerprints": [],
        "fresh0_contract": "v30_material_change_accepted0_v1",
        "historical_accepted_excluded": 2,
    }
    v25.atomic_json(controller_state_path, controller_state)

    manifest = copy.deepcopy(source_manifest)
    manifest.update(
        {
            "controller_version": 30,
            "controller_entrypoint": str(
                PROJECT_ROOT
                / "tools/tpcc_autonomous_target10_controller_v30_score_stability.py"
            ),
            "controller_root": str(controller_root),
            "controller_state_path": str(controller_state_path),
            "formal_state_path": str(formal_state_path),
            "batch_parent": str(args.batch_parent.resolve()),
            "controller_source_manifest": str(source_manifest_path),
            "controller_source_manifest_sha256": v25.sha256(source_manifest_path),
            "resumed_from_manifest": str(source_manifest_path),
            "resumed_from_manifest_sha256": v25.sha256(source_manifest_path),
            "controller_handover_contract": (
                "v30_material_change_full_fresh0_no_accepted_inheritance"
            ),
            "controller_state_boundary": {
                "status": "waiting_current_batch",
                "next_mode": "diagnostic",
                "next_ordinal": 1,
                "current_run": None,
            },
            "current_run": None,
            "next_ordinal": 1,
            "initial_controller_state_sha256": v25.sha256(controller_state_path),
            "initial_formal_state_sha256": formal_sha,
            "historical_accepted_excluded": 2,
            "inherited_accepted": 0,
            "formal_accepted_start": 0,
            "material_change_fresh0": True,
            "old_formal_accepted_training_only": 2,
            "seed_audits": audits,
            "seed_sources": sources,
            "v30_activation_authorized": True,
            "v30_algorithm_frozen": False,
            "v30_freeze_requires_zero_call_builder_regression": True,
        }
    )
    v25.atomic_json(output_manifest, manifest)
    print(f"campaign_root={campaign_root}", flush=True)
    print(f"formal_state={formal_state_path}", flush=True)
    print(f"formal_state_sha256={formal_sha}", flush=True)
    print(f"controller_state_sha256={v25.sha256(controller_state_path)}", flush=True)
    print(f"manifest={output_manifest}", flush=True)
    print(f"manifest_sha256={v25.sha256(output_manifest)}", flush=True)
    print("formal_accepted_start=0", flush=True)
    print("historical_accepted_excluded=2", flush=True)
    print("physical_workload_calls_added=0", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

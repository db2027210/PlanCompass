#!/usr/bin/env python3
"""Audit one completed active TPC-C batch and seal a controller handover."""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tools.tpcc_autonomous_target10_controller as controller


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("controller_state", type=Path)
    parser.add_argument("formal_state", type=Path)
    parser.add_argument("--expected-run-name", required=True)
    parser.add_argument(
        "--legacy-controller",
        type=Path,
        default=Path(
            "/tmp/tpcc_autonomous_target10_package_v14_20260731/"
            "tpcc_autonomous_target10_controller.py"
        ),
    )
    args = parser.parse_args()

    state_path = args.controller_state.resolve()
    formal_state = args.formal_state.resolve()
    state = controller.load_json(state_path)
    current = copy.deepcopy(state.get("current_run"))
    if not isinstance(current, dict):
        raise RuntimeError("controller has no active run to hand over")
    observed_name = current.get("name") or Path(current["root"]).name
    if observed_name != args.expected_run_name:
        raise RuntimeError(
            f"active run mismatch: observed={observed_name!r}"
        )
    summary_path = Path(current["summary_path"]).resolve()
    ledger_path = Path(current["ledger_path"]).resolve()
    if not summary_path.is_file() or not ledger_path.is_file():
        raise RuntimeError("active run is not complete")
    if (
        current.get("summary_sha256") not in (None, controller.sha256(summary_path))
        or current.get("ledger_sha256") not in (None, controller.sha256(ledger_path))
    ):
        raise RuntimeError("persisted active-run SHA mismatch")
    current["summary_sha256"] = controller.sha256(summary_path)
    current["ledger_sha256"] = controller.sha256(ledger_path)

    legacy_path = args.legacy_controller.resolve()
    specification = importlib.util.spec_from_file_location(
        "tpcc_legacy_handover_controller", legacy_path
    )
    if specification is None or specification.loader is None:
        raise RuntimeError("cannot load legacy TPC-C controller")
    legacy = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(legacy)
    audit = legacy.audit_completed_run(current, formal_state)
    summary = controller.load_json(summary_path)
    audit.update(
        {
            "accepted_before": int(summary["accepted_before"]),
            "accepted_after": int(summary["accepted_after"]),
            "batch_added_accepted": int(summary["batch_added_accepted"]),
            "formal_prediction_miss": bool(
                audit["clean"]
                and int(summary["batch_added_accepted"]) == 0
            ),
            "legacy_selection_gate_version": (
                "point_estimate_pre_lcb_quarantined_v1"
            ),
        }
    )
    if audit["physical_calls"] != controller.CAP:
        raise RuntimeError("handover batch did not consume the sealed Top-5")
    state["audits"].append(audit)
    if audit["clean"]:
        state["sources"].append(
            {
                "name": args.expected_run_name,
                "selection_path": audit["selection_path"],
                "selection_sha256": audit["selection_sha256"],
                "ledger_path": audit["ledger_path"],
                "ledger_sha256": audit["ledger_sha256"],
            }
        )
    state["current_run"] = None
    state["status"] = "sealed_handover_after_completed_active_batch"
    state["formal_state_sha256"] = controller.sha256(formal_state)
    state["handover"] = {
        "schema_version": "tpcc_controller_handover_v1",
        "run_name": args.expected_run_name,
        "audit": copy.deepcopy(audit),
        "formal_state_sha256": state["formal_state_sha256"],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = state_path.with_name(f"{state_path.name}.pre_handover_{timestamp}")
    descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(state_path.read_bytes())
        handle.flush()
        os.fsync(handle.fileno())
    controller.atomic_json(state_path, state)
    if controller.load_json(state_path).get("current_run") is not None:
        raise RuntimeError("atomic handover verification failed")
    print(
        json.dumps(
            {
            "status": "complete",
            "backup": str(backup),
            "state_sha256": controller.sha256(state_path),
            "formal_state_sha256": state["formal_state_sha256"],
            "audit_clean": audit["clean"],
            "batch_added_accepted": audit.get("batch_added_accepted"),
            "next_mode": controller.choose_next_mode(
                state["audits"], controller.quality_gate(state["audits"])
            ),
            "workload_calls_added": 0,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

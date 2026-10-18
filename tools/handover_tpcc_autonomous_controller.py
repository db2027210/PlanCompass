#!/usr/bin/env python3
"""Audit a completed stopped TPC-C run into a clean controller handover state."""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
from pathlib import Path

import tpcc_autonomous_target10_controller as controller


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-state", required=True, type=Path)
    parser.add_argument("--formal-state", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--completed-run-controller",
        required=True,
        type=Path,
        help="exact pinned controller version that launched the completed run",
    )
    args = parser.parse_args()

    source = controller.load_json(args.source_state.resolve())
    formal_state = args.formal_state.resolve()
    current = copy.deepcopy(source.get("current_run"))
    if not isinstance(current, dict) or current.get("mode") != "formal":
        raise RuntimeError("source controller has no active formal run")
    for field, path_field in (
        ("ledger_sha256", "ledger_path"),
        ("summary_sha256", "summary_path"),
    ):
        path = Path(current[path_field]).resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        current[field] = controller.sha256(path)
    if args.output.exists():
        raise FileExistsError(args.output)
    controller_path = args.completed_run_controller.resolve()
    spec = importlib.util.spec_from_file_location(
        "completed_run_controller", controller_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load completed-run controller")
    completed_run_controller = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(completed_run_controller)
    audit = completed_run_controller.audit_completed_run(
        current,
        formal_state,
        verify_current_state_sha=True,
    )
    if (
        audit.get("clean") is not True
        or audit.get("physical_calls") != controller.CAP
        or audit.get("valid_replays") != controller.CAP
        or audit.get("technical_failures") != 0
        or audit.get("baseline_restored") is not True
    ):
        raise RuntimeError("completed formal run failed handover audit")

    output = copy.deepcopy(source)
    output["audits"].append(audit)
    output["sources"].append(
        {
            "name": f"handover_formal_{int(source['next_ordinal']) - 1}",
            "selection_path": audit["selection_path"],
            "selection_sha256": audit["selection_sha256"],
            "ledger_path": audit["ledger_path"],
            "ledger_sha256": audit["ledger_sha256"],
        }
    )
    output["current_run"] = None
    output["formal_state_sha256"] = controller.sha256(formal_state)
    output["last_quality_gate"] = controller.quality_gate(output["audits"])
    output["status"] = "clean_handover_ready"
    output["handover_audit"] = {
        "source_state": str(args.source_state.resolve()),
        "source_state_sha256": controller.sha256(
            args.source_state.resolve()
        ),
        "formal_state": str(formal_state),
        "formal_state_sha256": controller.sha256(formal_state),
        "completed_run": copy.deepcopy(current),
        "completed_run_controller": str(controller_path),
        "completed_run_controller_sha256": controller.sha256(
            controller_path
        ),
    }
    controller.atomic_json(args.output.resolve(), output)
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "output_sha256": controller.sha256(
                    args.output.resolve()
                ),
                "accepted_count": controller.load_json(formal_state)[
                    "accepted_count"
                ],
                "audit_count": len(output["audits"]),
                "source_count": len(output["sources"]),
                "next_ordinal": output["next_ordinal"],
                "quality_gate_passed": output["last_quality_gate"][
                    "passed"
                ],
            },
            indent=2,
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

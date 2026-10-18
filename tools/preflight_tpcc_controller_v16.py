#!/usr/bin/env python3
"""Build and validate the next TPC-C selection without launching a workload."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tools.tpcc_autonomous_target10_controller as controller


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)

    state_path = Path(
        "/home/anonymous/program/resource_adaptive/history/"
        "tpcc_autonomous_target10_controller_v10_20260731/controller_state.json"
    )
    formal_state = Path(
        "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
        "tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/"
        "formal10_tpcc_state.json"
    )
    state = controller.load_json(state_path)
    if (
        state.get("current_run") is not None
        or state.get("next_mode") != "diagnostic"
        or state.get("formal_state_sha256") != controller.sha256(formal_state)
    ):
        raise RuntimeError("TPC-C controller is not at the sealed diagnostic boundary")
    evidence = {
        "schema_version": "tpcc_autonomous_evidence_manifest_v1",
        "sources": state["sources"],
        "diagnostic_acquisition_reason": {
            "reason": "previous_formal_batch_added_zero_accepted",
            "quality_gate": state["last_quality_gate"],
        },
    }
    evidence_path = output / "evidence_manifest.json"
    selection_path = output / "top5.json"
    controller.atomic_json(evidence_path, evidence)
    completed = subprocess.run(
        [
            "/home/anonymous/miniconda3/envs/resource_adaptive/bin/python",
            "/home/anonymous/program/resource_adaptive/tools/"
            "build_tpcc_autonomous_next_top5.py",
            str(evidence_path),
            str(formal_state),
            str(selection_path),
            "diagnostic",
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=1800,
    )
    (output / "builder.log").write_text(
        completed.stdout, encoding="utf-8"
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"TPC-C diagnostic builder failed: {completed.stdout[-4000:]}"
        )
    selection = controller.load_json(selection_path)
    controller.validate_selection(selection, mode="diagnostic")
    print(
        json.dumps(
            {
                "status": "ready",
                "workload_calls": 0,
                "mode": "diagnostic",
                "roles": [
                    item["selection_role"] for item in selection["top5"]
                ],
                "fingerprints": [
                    item["three_level_fingerprint"]["id"]
                    for item in selection["top5"]
                ],
                "staircase_contract": selection.get(
                    "staircase_search_contract"
                ),
                "formal_admission_score": selection.get(
                    "formal_admission_score"
                ),
                "required_fields_present": all(
                    all(
                        item.get(field) is not None
                        for field in controller.REQUIRED_CANDIDATE_FIELDS
                    )
                    for item in selection["top5"]
                ),
                "formal_state_sha256_unchanged": (
                    controller.sha256(formal_state)
                    == state["formal_state_sha256"]
                ),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

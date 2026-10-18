#!/usr/bin/env python3
"""Build a sealed fresh-0 JOB formal package from a passed clean5 gate."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, Mapping


PARAMETERS = {
    "cpu_index_tuple_cost": 0.0005,
    "cpu_operator_cost": 0.0025,
    "cpu_tuple_cost": 0.01,
    "parallel_setup_cost": 7000.0,
    "parallel_tuple_cost": 0.001,
    "random_page_cost": 4.0,
    "seq_page_cost": 1.0,
}
INCUMBENT_SCORE = 4.416709205648302
DEPENDENCIES = (
    "job_formal_target10_batch_controller.py",
    "run_job_round8_strict_serial.py",
    "job_round8_strict_replay_contract.py",
)


def load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"{path} must contain one JSON object")
    return value


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_text(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    atomic_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def verify(path: Path, expected: str, label: str) -> None:
    if not path.is_file() or sha256(path) != expected:
        raise RuntimeError(f"{label} SHA mismatch")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--selection-sha256", required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--summary-sha256", required=True)
    parser.add_argument("--gate", type=Path, required=True)
    parser.add_argument("--gate-sha256", required=True)
    parser.add_argument("--dependency-root", type=Path, required=True)
    parser.add_argument("--global-ordinal", type=int, required=True)
    parser.add_argument("--formal-replay-count", type=int, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()

    verify(args.selection, args.selection_sha256, "selection")
    verify(args.summary, args.summary_sha256, "summary")
    verify(args.gate, args.gate_sha256, "entry gate")
    if args.output_root.exists():
        raise RuntimeError("output root already exists")

    selection = load(args.selection)
    summary = load(args.summary)
    gate = load(args.gate)
    checks = gate.get("checks")
    if (
        gate.get("passed") is not True
        or not isinstance(checks, dict)
        or not checks
        or any(value is not True for value in checks.values())
    ):
        raise RuntimeError("JOB clean5 entry gate is not fully passed")
    if (
        summary.get("diagnostic_only") is not True
        or summary.get("physical_invocation_count") != 5
        or summary.get("successful_real_replay_count") != 5
        or summary.get("technical_failure_count") != 0
        or summary.get("baseline_params_restored") is not True
        or summary.get("initial_state") != summary.get("final_state")
    ):
        raise RuntimeError("JOB source replay is not a clean state-neutral Top-5")
    candidates = selection.get("top5")
    if not isinstance(candidates, list) or len(candidates) != 5:
        raise RuntimeError("JOB selection is not Top-5")
    if [item.get("selection_role") for item in candidates] != [
        "trusted", "trusted", "trusted", "probe", "probe"
    ]:
        raise RuntimeError("JOB selection is not trusted3+probe2")

    package = args.output_root / "package"
    package.mkdir(parents=True)
    promoted = copy.deepcopy(selection)
    promoted["diagnostic_only"] = False
    promoted["diagnostic_role_evaluation_only"] = False
    promoted["accepted_update_allowed"] = True
    promoted["incumbent_update_allowed"] = True
    promoted["range_update_allowed"] = True
    promoted["gradient_update_allowed"] = True
    promoted["formal_update_flags"] = {
        "accepted": True,
        "incumbent": True,
        "range": True,
        "gradient": True,
    }
    promoted["promoted_from_clean5_selection_sha256"] = args.selection_sha256
    promoted["promoted_from_clean5_summary_sha256"] = args.summary_sha256
    promoted["promoted_from_clean5_gate_sha256"] = args.gate_sha256
    for candidate in promoted["top5"]:
        trusted = candidate["selection_role"] == "trusted"
        candidate["initial_params"] = copy.deepcopy(PARAMETERS)
        candidate["initial_score"] = INCUMBENT_SCORE
        candidate["diagnostic_only"] = False
        candidate["diagnostic_role_evaluation_only"] = False
        candidate["incumbent_update_eligible"] = trusted
        candidate["used_for_inner_incumbent_update"] = trusted
        candidate["used_for_gradient"] = trusted

    state = {
        "schema_version": "job_final_algorithm_fresh0_state_v36",
        "accepted_count": 0,
        "accepted_target": 10,
        "incumbent_score": INCUMBENT_SCORE,
        "incumbent_params": copy.deepcopy(PARAMETERS),
        "range_by_parameter": {
            name: {"min": value, "max": value}
            for name, value in PARAMETERS.items()
        },
        "gradient_observation_count": 0,
        "formal_valid_replay_count": 0,
        "global_ordinal": args.global_ordinal,
        "formal_replay_count": args.formal_replay_count,
        "snapshot_only": False,
        "historical_accepted_excluded": 2,
        "fresh_formal_campaign": {
            "campaign_id": "job_final_algorithm_fresh10_v36",
            "accepted_start": 0,
            "accepted_target": 10,
            "source_accepted_excluded_from_new_total": 2,
            "diagnostic_accepted_excluded": True,
            "qps_used_for_inner_scoring": False,
        },
    }
    promoted_path = package / "top5_promoted_formal.json"
    state_path = package / "formal_state.json"
    gate_path = package / "entry_gate.json"
    counter_path = package / "formal_search_accepted_counter.txt"
    atomic_json(promoted_path, promoted)
    atomic_json(state_path, state)
    atomic_text(counter_path, "0\n")

    formal_gate = copy.deepcopy(gate)
    formal_gate.update({
        "formal_accepted_total": 0,
        "accepted_target": 10,
        "fresh_formal_campaign": True,
        "historical_accepted_excluded": 2,
        "clean5_selection_sha256": args.selection_sha256,
        "clean5_summary_sha256": args.summary_sha256,
        "clean5_gate_sha256": args.gate_sha256,
        "promoted_selection_sha256": sha256(promoted_path),
        "formal_state_sha256_before": sha256(state_path),
        "formal_state_sha256_after": sha256(state_path),
        "formal_counter_pins_before_start": {
            "global_ordinal": args.global_ordinal,
            "formal_search_replay_count": args.formal_replay_count,
            "formal_search_accepted_count": 0,
        },
    })
    formal_gate["passed"] = all(value is True for value in checks.values())
    atomic_json(gate_path, formal_gate)

    dependency_hashes = {}
    for name in DEPENDENCIES:
        source = args.dependency_root / name
        if not source.is_file():
            raise RuntimeError(f"missing dependency: {source}")
        target = package / name
        shutil.copy2(source, target)
        dependency_hashes[name] = sha256(target)

    manifest = {
        "contract": "job_final_algorithm_fresh10_v36",
        "accepted_start": 0,
        "accepted_target": 10,
        "historical_accepted_excluded": 2,
        "global_ordinal_start": args.global_ordinal,
        "formal_replay_count_start": args.formal_replay_count,
        "selection_sha256": sha256(promoted_path),
        "state_sha256": sha256(state_path),
        "gate_sha256": sha256(gate_path),
        "accepted_counter_sha256": sha256(counter_path),
        "dependency_hashes": dependency_hashes,
        "baseline_params": PARAMETERS,
        "baseline_score": INCUMBENT_SCORE,
        "qps_used_for_inner_scoring": False,
    }
    atomic_json(package / "fresh_campaign_manifest.json", manifest)
    print(json.dumps(manifest, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

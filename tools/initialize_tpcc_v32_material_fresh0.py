#!/usr/bin/env python3
"""Create a zero-call TPC-C v32 campaign after the v31 collision reroute."""

from __future__ import annotations

import argparse
import copy
import subprocess
import time
from pathlib import Path
from typing import Any, Dict

try:
    import tpcc_autonomous_target10_controller_v25 as v25
    import tpcc_autonomous_target10_controller_v31_cross_run_stability as v31
    import tpcc_autonomous_target10_controller_v32_validation_repeat as v32
except ModuleNotFoundError:
    from tools import tpcc_autonomous_target10_controller_v25 as v25
    from tools import tpcc_autonomous_target10_controller_v31_cross_run_stability as v31
    from tools import tpcc_autonomous_target10_controller_v32_validation_repeat as v32


PROJECT_ROOT = Path("/home/anonymous/program/resource_adaptive")
REPEAT_SOURCE_SUFFIX = "autonomous_diagnostic_validation_01"
ERRONEOUS_TRAINING_SUFFIX = "autonomous_diagnostic_02"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_manifest", type=Path)
    parser.add_argument("source_controller_state", type=Path)
    parser.add_argument("campaign_root", type=Path)
    parser.add_argument("batch_parent", type=Path)
    parser.add_argument("output_manifest", type=Path)
    return parser.parse_args()


def _clean_audit(state: Dict[str, Any], suffix: str) -> Dict[str, Any]:
    matches = [
        copy.deepcopy(item)
        for item in state.get("audits") or []
        if str(item.get("root") or "").endswith(suffix)
    ]
    if len(matches) != 1:
        raise RuntimeError(f"expected one completed audit ending in {suffix}")
    audit = matches[0]
    for field, sha_field in (
        ("selection_path", "selection_sha256"),
        ("ledger_path", "ledger_sha256"),
        ("summary_path", "summary_sha256"),
    ):
        path = Path(str(audit.get(field) or "")).resolve()
        if not path.is_file() or v25.sha256(path) != audit.get(sha_field):
            raise RuntimeError(f"sealed v31 audit SHA mismatch: {path}")
    if (
        audit.get("clean") is not True
        or int(audit.get("physical_calls", -1)) != 5
        or int(audit.get("valid_replays", -1)) != 5
        or int(audit.get("technical_failures", -1)) != 0
        or audit.get("baseline_restored") is not True
    ):
        raise RuntimeError(f"v31 audit is not clean 5/5/0: {suffix}")
    return audit


def _training_copy(audit: Dict[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(audit)
    result["fresh0_training_only"] = True
    result["eligible_for_formal_accepted_count"] = False
    result["historical_accepted_inherited"] = 0
    result["v32_repeat_source"] = False
    return result


def _validate_fresh_formal(formal: Dict[str, Any]) -> None:
    if (
        int(formal.get("accepted_count", -1)) != 0
        or int(formal.get("inherited_accepted", -1)) != 0
        or float(formal.get("incumbent_score")) != 3.9894795578792417
        or int(formal.get("gradient_update_count", -1)) != 0
        or formal.get("gradient_state_reset") is not True
        or int(formal.get("formal_physical_call_count", -1)) != 0
        or int(formal.get("formal_valid_replay_count", -1)) != 0
        or int(formal.get("formal_technical_failure_count", -1)) != 0
    ):
        raise RuntimeError("source formal state is not the v31 fresh0 boundary")
    ranges = formal.get("range_state") or {}
    expected = {
        "cpu_index_tuple_cost": (0.0005, 0.05),
        "cpu_operator_cost": (0.00025, 0.025),
        "cpu_tuple_cost": (0.001, 0.1),
        "parallel_setup_cost": (0.0, 10000.0),
        "parallel_tuple_cost": (0.001, 1.0),
        "random_page_cost": (0.1, 100.0),
        "seq_page_cost": (0.1, 100.0),
    }
    if set(ranges) != set(expected):
        raise RuntimeError("source formal ranges have unexpected parameters")
    for name, (minimum, maximum) in expected.items():
        if (
            float(ranges[name]["min_value"]) != minimum
            or float(ranges[name]["max_value"]) != maximum
        ):
            raise RuntimeError(f"source formal range mismatch: {name}")


def main() -> int:
    args = parse_args()
    source_manifest_path = args.source_manifest.resolve()
    source_state_path = args.source_controller_state.resolve()
    campaign_root = args.campaign_root.resolve()
    output_manifest = args.output_manifest.resolve()
    if campaign_root.exists() or output_manifest.exists():
        raise FileExistsError("v32 campaign or manifest already exists")
    source_manifest = v25.load_json(source_manifest_path)
    source_state = v25.load_json(source_state_path)
    if source_state.get("current_run") is not None:
        raise RuntimeError("v31 still has an active run; refusing v32 handover")
    source_formal_path = Path(source_manifest["formal_state_path"]).resolve()
    if (
        not source_formal_path.is_file()
        or v25.sha256(source_formal_path)
        != source_state.get("formal_state_sha256")
    ):
        raise RuntimeError("v31 formal SHA does not match controller state")
    source_formal = v25.load_json(source_formal_path)
    _validate_fresh_formal(source_formal)

    repeat_source = _clean_audit(source_state, REPEAT_SOURCE_SUFFIX)
    erroneous_training = _clean_audit(source_state, ERRONEOUS_TRAINING_SUFFIX)
    if (
        repeat_source.get("mode") != "diagnostic_validation"
        or int(repeat_source.get("maximum_total_exact_observation_count", -1))
        != 3
        or repeat_source.get("all_trusted_complete_run_repeatable") is True
    ):
        raise RuntimeError("v31 validation_01 is not the bounded repeat-4 source")
    if erroneous_training.get("mode") != "diagnostic":
        raise RuntimeError("v31 diagnostic_02 is not the collision-reroute audit")

    carried = []
    for audit in source_state.get("audits") or []:
        root = str(audit.get("root") or "")
        if not root or root.endswith(REPEAT_SOURCE_SUFFIX):
            continue
        item = _training_copy(audit)
        if root.endswith(ERRONEOUS_TRAINING_SUFFIX):
            item["repeat_source_excluded_reason"] = (
                "v31_novelty_guard_exact_repeat_collision_reroute_training_only"
            )
            item["accepted_count_eligible"] = False
        carried.append(item)
    sealed_repeat_source = _training_copy(repeat_source)
    sealed_repeat_source["v32_repeat_source"] = True
    sealed_repeat_source["repeat_source_contract"] = (
        "v31_clean_diagnostic_validation_01_exact_top5_only"
    )
    audits = [*carried, sealed_repeat_source]
    if audits[-1]["root"] != repeat_source["root"]:
        raise RuntimeError("v32 latest audit is not the required repeat source")
    sources = [
        {
            "name": f"v32_training_{index:02d}",
            "selection_path": audit["selection_path"],
            "selection_sha256": audit["selection_sha256"],
            "ledger_path": audit["ledger_path"],
            "ledger_sha256": audit["ledger_sha256"],
            "training_only": True,
            "accepted_count_eligible": False,
            "v32_repeat_source": bool(audit.get("v32_repeat_source")),
        }
        for index, audit in enumerate(audits, 1)
    ]

    campaign_root.mkdir(parents=True, exist_ok=False)
    controller_root = campaign_root / "autonomous_controller"
    controller_root.mkdir(parents=True, exist_ok=False)
    formal_path = campaign_root / "formal_state.json"
    entry_path = campaign_root / "fresh0_entry_gate.json"
    state_path = controller_root / "controller_state.json"
    freeze_root = campaign_root / "v32_zero_call_initializer"
    freeze_root.mkdir(parents=True, exist_ok=False)

    entry = {
        "schema_version": "tpcc_v32_material_fresh0_entry_gate_v1",
        "passed": False,
        "formal_start_authorized": False,
        "reason": "sealed_exact_repeat_4_and_v31_quality_gate_required",
        "inherited_accepted": 0,
        "historical_accepted_excluded": 2,
        "qps_role": "outer_audit_only_not_inner_score",
    }
    v25.atomic_json(entry_path, entry)
    formal = copy.deepcopy(source_formal)
    formal.update(
        {
            "accepted_count": 0,
            "accepted_target": 10,
            "inherited_accepted": 0,
            "historical_accepted_excluded": 2,
            "formal_physical_call_count": 0,
            "formal_valid_replay_count": 0,
            "formal_technical_failure_count": 0,
            "last_batch_root": None,
            "diagnostic_audit": None,
            "entry_gate_sha256": v25.sha256(entry_path),
            "gradient_update_count": 0,
            "gradient_state_reset": True,
            "material_change_contract": (
                "v32_validation_repeat_novelty_semantics_fresh0"
            ),
            "updated_at_epoch": time.time(),
        }
    )
    _validate_fresh_formal(formal)
    v25.atomic_json(formal_path, formal)
    state = {
        "schema_version": "tpcc_autonomous_target10_state_v1",
        "status": "waiting_current_batch",
        "dry_run": False,
        "audits": audits,
        "sources": sources,
        "current_run": None,
        "next_ordinal": 1,
        "formal_state_sha256": v25.sha256(formal_path),
        "acquisition_transitions": [],
        "technical_failure_audits": [],
        "recovery_audits": [],
        "quarantined_fingerprints": copy.deepcopy(
            source_state.get("quarantined_fingerprints") or []
        ),
        "fresh0_contract": "v32_material_change_accepted0_v1",
        "historical_accepted_excluded": 2,
        "v31_erroneous_diagnostic_02_training_only": {
            "root": erroneous_training["root"],
            "selection_sha256": erroneous_training["selection_sha256"],
            "ledger_sha256": erroneous_training["ledger_sha256"],
            "accepted_count_eligible": False,
            "repeat_source_eligible": False,
        },
    }
    v25.atomic_json(state_path, state)

    gate = v31.quality_gate_v31(audits)
    mode = v31.choose_next_mode_v31(audits, gate)
    if gate.get("passed") is not False or mode != "diagnostic_validation":
        raise RuntimeError("v32 did not choose the bounded exact repeat 4")
    repeat_builder = PROJECT_ROOT / "tools/build_tpcc_exact_plan_repeat_validation_v4.py"
    selection_path = freeze_root / "top5.json"
    completed = subprocess.run(
        [
            str(source_manifest["python"]),
            str(repeat_builder),
            "--selection",
            str(Path(repeat_source["selection_path"]).resolve()),
            "--ledger",
            str(Path(repeat_source["ledger_path"]).resolve()),
            "--output",
            str(selection_path),
            "--minimum-score-span",
            str(
                float(
                    source_manifest.get("minimum_trusted_predicted_score_span")
                    or source_manifest.get("minimum_diagnostic_score_span")
                    or 0.0
                )
            ),
        ],
        cwd=str(PROJECT_ROOT),
        check=False,
        capture_output=True,
        text=True,
        timeout=int(source_manifest.get("builder_timeout_seconds", 1800)),
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"v32 zero-call repeat builder failed: {completed.stdout} {completed.stderr}"
        )
    selection = v25.load_json(selection_path)
    v25.validate_selection(selection, mode="diagnostic_validation")
    state_before = copy.deepcopy(state)
    fingerprint_gate = v32.validation_repeat_prelaunch_fingerprint_gate(
        selection,
        mode,
        state,
    )
    if state != state_before:
        raise RuntimeError("v32 prelaunch fingerprint gate mutated controller state")
    if (
        fingerprint_gate.get("passed") is not True
        or fingerprint_gate.get("novel_fingerprint_credit") != 0
        or fingerprint_gate.get("independent_run_ordinal") != 4
    ):
        raise RuntimeError("v32 sealed repeat prelaunch gate failed")

    report = {
        "schema_version": "tpcc_v32_zero_call_initializer_report_v1",
        "passed": True,
        "physical_workload_calls_added": 0,
        "formal_accepted_start": 0,
        "historical_accepted_excluded": 2,
        "next_mode": mode,
        "prior_complete_run_observation_count": 3,
        "next_independent_run_ordinal": 4,
        "selection_path": str(selection_path),
        "selection_sha256": v25.sha256(selection_path),
        "source_selection_sha256": repeat_source["selection_sha256"],
        "allowed_exact_repeat_collision_ids": fingerprint_gate[
            "allowed_exact_repeat_collision_ids"
        ],
        "novel_fingerprint_credit": 0,
        "erroneous_diagnostic_02_training_only": True,
        "erroneous_diagnostic_02_root": erroneous_training["root"],
        "formal_state_sha256": v25.sha256(formal_path),
        "controller_state_sha256": v25.sha256(state_path),
        "prelaunch_gate_read_only": True,
        "qps_role": "outer_audit_only_not_inner_score",
    }
    report_path = freeze_root / "initializer_report.json"
    v25.atomic_json(report_path, report)

    manifest = copy.deepcopy(source_manifest)
    manifest.update(
        {
            "controller_version": 32,
            "controller_entrypoint": str(
                PROJECT_ROOT
                / "tools/tpcc_autonomous_target10_controller_v32_validation_repeat.py"
            ),
            "exact_repeat_builder_path": str(repeat_builder),
            "controller_root": str(controller_root),
            "controller_state_path": str(state_path),
            "formal_state_path": str(formal_path),
            "batch_parent": str(args.batch_parent.resolve()),
            "controller_source_manifest": str(source_manifest_path),
            "controller_source_manifest_sha256": v25.sha256(source_manifest_path),
            "controller_handover_contract": (
                "v32_fresh0_sealed_exact_repeat_novelty_fix"
            ),
            "current_run": None,
            "next_ordinal": 1,
            "formal_accepted_start": 0,
            "inherited_accepted": 0,
            "historical_accepted_excluded": 2,
            "material_change_fresh0": True,
            "initial_controller_state_sha256": v25.sha256(state_path),
            "initial_formal_state_sha256": v25.sha256(formal_path),
            "seed_audits": audits,
            "seed_sources": sources,
            "prelaunch_fingerprint_contract": (
                "new_and_formal_global_novelty_exact_repeat_provenance_v32"
            ),
            "v32_activation_authorized": True,
            "v32_algorithm_frozen": True,
            "v32_zero_call_initializer_report_path": str(report_path),
            "v32_zero_call_initializer_report_sha256": v25.sha256(report_path),
            "v32_frozen_next_selection_path": str(selection_path),
            "v32_frozen_next_selection_sha256": v25.sha256(selection_path),
        }
    )
    pin_paths = {
        str(Path(item["path"]).resolve()): copy.deepcopy(item)
        for item in manifest.get("pinned_files") or []
    }
    for path in (
        PROJECT_ROOT / "tools/build_tpcc_exact_plan_repeat_validation_v4.py",
        PROJECT_ROOT / "tools/tpcc_autonomous_target10_controller_v32_validation_repeat.py",
        PROJECT_ROOT / "tools/initialize_tpcc_v32_material_fresh0.py",
        PROJECT_ROOT / "tools/preflight_tpcc_v32_validation_repeat.py",
        PROJECT_ROOT / "tests/test_tpcc_validation_repeat_v32.py",
    ):
        if not path.is_file():
            raise RuntimeError(f"v32 pinned file is missing: {path}")
        pin_paths[str(path)] = {"path": str(path), "sha256": v25.sha256(path)}
    manifest["pinned_files"] = [pin_paths[key] for key in sorted(pin_paths)]
    output_manifest.parent.mkdir(parents=True, exist_ok=True)
    v25.atomic_json(output_manifest, manifest)
    print(f"campaign_root={campaign_root}", flush=True)
    print(f"formal_state_sha256={v25.sha256(formal_path)}", flush=True)
    print(f"controller_state_sha256={v25.sha256(state_path)}", flush=True)
    print(f"initializer_report_sha256={v25.sha256(report_path)}", flush=True)
    print(f"selection_sha256={v25.sha256(selection_path)}", flush=True)
    print(f"manifest_sha256={v25.sha256(output_manifest)}", flush=True)
    print("formal_accepted_start=0", flush=True)
    print("physical_workload_calls_added=0", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

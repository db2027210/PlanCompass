#!/usr/bin/env python3
"""Create a zero-call material-fresh TPC-C v31 campaign."""

from __future__ import annotations

import argparse
import copy
import subprocess
import time
from pathlib import Path
from typing import Any, Dict

try:
    import tpcc_autonomous_target10_controller_v25 as v25
    import tpcc_autonomous_target10_controller_v29_prelaunch_fingerprint as v29
    import tpcc_autonomous_target10_controller_v31_cross_run_stability as v31
except ModuleNotFoundError:
    from tools import tpcc_autonomous_target10_controller_v25 as v25
    from tools import tpcc_autonomous_target10_controller_v29_prelaunch_fingerprint as v29
    from tools import tpcc_autonomous_target10_controller_v31_cross_run_stability as v31


PROJECT_ROOT = Path("/home/anonymous/program/resource_adaptive")
FRESH0_TEMPLATE = (
    PROJECT_ROOT
    / "history/tpcc_final_algorithm_fresh10_v28_20260731T2000/formal_state.json"
)
REQUIRED_REPEAT_ROOTS = (
    "autonomous_diagnostic_01",
    "autonomous_diagnostic_validation_02",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_manifest", type=Path)
    parser.add_argument("source_controller_state", type=Path)
    parser.add_argument("campaign_root", type=Path)
    parser.add_argument("batch_parent", type=Path)
    parser.add_argument("output_manifest", type=Path)
    return parser.parse_args()


def _audit_by_suffix(state: Dict[str, Any], suffix: str) -> Dict[str, Any]:
    matches = [
        copy.deepcopy(item)
        for item in state.get("audits") or []
        if str(item.get("root") or "").endswith(suffix)
    ]
    if len(matches) != 1:
        raise RuntimeError(f"expected one sealed audit ending in {suffix}")
    audit = matches[0]
    for field, sha_field in (
        ("selection_path", "selection_sha256"),
        ("ledger_path", "ledger_sha256"),
        ("summary_path", "summary_sha256"),
    ):
        path = Path(audit[field]).resolve()
        if not path.is_file() or v25.sha256(path) != audit[sha_field]:
            raise RuntimeError(f"sealed training audit SHA mismatch: {path}")
    if (
        audit.get("clean") is not True
        or int(audit.get("physical_calls", -1)) != 5
        or int(audit.get("valid_replays", -1)) != 5
        or int(audit.get("technical_failures", -1)) != 0
        or audit.get("baseline_restored") is not True
    ):
        raise RuntimeError(f"training audit is not clean: {suffix}")
    return audit


def _upgrade(
    audit: Dict[str, Any],
    prior: list[Dict[str, Any]],
) -> Dict[str, Any]:
    # Reconstruct the base calibration rows that v30 kept in its explicit
    # exclusion audit.  V31 will still exclude them until complete-run
    # repeatability and role gates pass.
    base = copy.deepcopy(audit)
    base["calibration_rows"] = [
        *copy.deepcopy(audit.get("calibration_rows") or []),
        *copy.deepcopy(audit.get("calibration_excluded_rows") or []),
    ]
    selection = v25.load_json(Path(base["selection_path"]))
    ledger = v25.load_json(Path(base["ledger_path"]))
    upgraded = v31.apply_cross_run_stability_audit(
        base,
        selection,
        ledger,
        prior,
    )
    upgraded["fresh0_training_only"] = True
    upgraded["eligible_for_formal_accepted_count"] = False
    upgraded["historical_accepted_inherited"] = 0
    return upgraded


def main() -> int:
    args = parse_args()
    source_manifest_path = args.source_manifest.resolve()
    source_state_path = args.source_controller_state.resolve()
    campaign_root = args.campaign_root.resolve()
    output_manifest = args.output_manifest.resolve()
    if campaign_root.exists() or output_manifest.exists():
        raise FileExistsError("v31 campaign or manifest already exists")
    source_manifest = v25.load_json(source_manifest_path)
    source_state = v25.load_json(source_state_path)
    active_run = copy.deepcopy(source_state.get("current_run"))
    active_fingerprint_ids = []
    if active_run is not None:
        active_selection_path = Path(active_run["selection_path"]).resolve()
        if (
            not active_selection_path.is_file()
            or v25.sha256(active_selection_path)
            != active_run["selection_sha256"]
        ):
            raise RuntimeError("active v30 sealed selection SHA mismatch")
        active_selection = v25.load_json(active_selection_path)
        active_fingerprint_ids = [
            str((item.get("three_level_fingerprint") or {}).get("id") or "")
            for item in active_selection.get("top5") or []
        ]
        if (
            len(active_fingerprint_ids) != 5
            or any(
                not identifier.startswith("three-level-sha256:")
                for identifier in active_fingerprint_ids
            )
        ):
            raise RuntimeError("active v30 selection fingerprint contract mismatch")
    template = v25.load_json(FRESH0_TEMPLATE)
    if (
        int(template.get("accepted_count", -1)) != 0
        or float(template.get("incumbent_score")) != 3.9894795578792417
    ):
        raise RuntimeError("fresh0 template boundary mismatch")

    diagnostic = _audit_by_suffix(source_state, REQUIRED_REPEAT_ROOTS[0])
    validation = _audit_by_suffix(source_state, REQUIRED_REPEAT_ROOTS[1])
    upgraded_diagnostic = _upgrade(diagnostic, [])
    upgraded_validation = _upgrade(validation, [upgraded_diagnostic])
    if (
        upgraded_validation["minimum_total_exact_observation_count"] != 2
        or upgraded_validation["maximum_total_exact_observation_count"] != 2
        or upgraded_validation["stable_trusted_count"] != 0
        or upgraded_validation["formal_admission_blocked_by_score_stability"]
        is not True
    ):
        raise RuntimeError("v31 two-run seed did not fail closed before repeat 3")

    # Preserve every other completed v30 fresh campaign audit as training and
    # fingerprint history, but keep the exact two-run validation chain last so
    # the deterministic next action is its third complete-run repeat.
    other_audits = []
    for item in source_state.get("audits") or []:
        root = str(item.get("root") or "")
        if not root or root.endswith(REQUIRED_REPEAT_ROOTS):
            continue
        if "formal10_final_algorithm_fresh0_v30_20260803" not in root:
            continue
        if item.get("clean") is not True:
            continue
        carried = copy.deepcopy(item)
        carried["fresh0_training_only"] = True
        carried["eligible_for_formal_accepted_count"] = False
        other_audits.append(carried)
    audits = [*other_audits, upgraded_diagnostic, upgraded_validation]
    sources = [
        {
            "name": f"v31_training_{index:02d}",
            "selection_path": audit["selection_path"],
            "selection_sha256": audit["selection_sha256"],
            "ledger_path": audit["ledger_path"],
            "ledger_sha256": audit["ledger_sha256"],
            "training_only": True,
            "accepted_count_eligible": False,
        }
        for index, audit in enumerate(audits, 1)
    ]

    campaign_root.mkdir(parents=True, exist_ok=False)
    controller_root = campaign_root / "autonomous_controller"
    controller_root.mkdir(parents=True, exist_ok=False)
    formal_path = campaign_root / "formal_state.json"
    entry_path = campaign_root / "fresh0_entry_gate.json"
    state_path = controller_root / "controller_state.json"
    freeze_root = campaign_root / "v31_zero_call_initializer"
    freeze_root.mkdir(parents=True, exist_ok=False)

    entry = {
        "schema_version": "tpcc_v31_material_fresh0_entry_gate_v1",
        "passed": False,
        "formal_start_authorized": False,
        "reason": "third_complete_run_repeat_and_v31_quality_gate_required",
        "inherited_accepted": 0,
        "historical_accepted_excluded": 2,
        "qps_role": "outer_audit_only_not_inner_score",
    }
    v25.atomic_json(entry_path, entry)
    formal = copy.deepcopy(template)
    formal.update(
        {
            "accepted_count": 0,
            "accepted_target": 10,
            "inherited_accepted": 0,
            "historical_accepted_excluded": 2,
            "formal_physical_call_count": 0,
            "formal_valid_replay_count": 0,
            "formal_technical_failure_count": 0,
            "incumbent_score": 3.9894795578792417,
            "last_batch_root": None,
            "diagnostic_audit": None,
            "entry_gate_sha256": v25.sha256(entry_path),
            "gradient_update_count": 0,
            "gradient_state_reset": True,
            "material_change_contract": (
                "v31_complete_run_repeatability_fresh0_no_inheritance"
            ),
            "updated_at_epoch": time.time(),
        }
    )
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
        "quarantined_fingerprints": sorted(set(active_fingerprint_ids)),
        "fresh0_contract": "v31_material_change_accepted0_v1",
        "historical_accepted_excluded": 2,
        "source_v30_active_run_excluded_from_seed_results": active_run,
    }
    v25.atomic_json(state_path, state)

    gate = v31.quality_gate_v31(audits)
    mode = v31.choose_next_mode_v31(audits, gate)
    if gate.get("passed") is not False or mode != "diagnostic_validation":
        raise RuntimeError("v31 initializer did not choose bounded repeat 3")
    repeat_builder = Path(source_manifest["exact_repeat_builder_path"]).resolve()
    selection_path = freeze_root / "top5.json"
    completed = subprocess.run(
        [
            str(source_manifest["python"]),
            str(repeat_builder),
            "--selection",
            str(Path(upgraded_validation["selection_path"]).resolve()),
            "--ledger",
            str(Path(upgraded_validation["ledger_path"]).resolve()),
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
            f"v31 zero-call repeat builder failed: {completed.stdout} {completed.stderr}"
        )
    selection = v25.load_json(selection_path)
    v25.validate_selection(selection, mode="diagnostic_validation")
    fingerprint_gate = v29.global_prelaunch_fingerprint_gate(
        selection,
        "diagnostic_validation",
        state,
    )
    if fingerprint_gate.get("passed") is not True:
        raise RuntimeError(
            f"v31 initializer fingerprint collision: {fingerprint_gate['collision_ids']}"
        )
    report = {
        "schema_version": "tpcc_v31_zero_call_initializer_report_v1",
        "passed": True,
        "physical_workload_calls_added": 0,
        "formal_accepted_start": 0,
        "historical_accepted_excluded": 2,
        "next_mode": mode,
        "prior_complete_run_observation_count": 2,
        "required_complete_run_observation_count": 3,
        "selection_path": str(selection_path),
        "selection_sha256": v25.sha256(selection_path),
        "selection_fingerprint_collision_ids": fingerprint_gate["collision_ids"],
        "source_v30_active_selection_fingerprints_excluded": sorted(
            set(active_fingerprint_ids)
        ),
        "source_v30_active_run_results_imported": False,
        "formal_state_sha256": v25.sha256(formal_path),
        "controller_state_sha256": v25.sha256(state_path),
        "qps_role": "outer_audit_only_not_inner_score",
    }
    report_path = freeze_root / "initializer_report.json"
    v25.atomic_json(report_path, report)

    manifest = copy.deepcopy(source_manifest)
    manifest.update(
        {
            "controller_version": 31,
            "controller_entrypoint": str(
                PROJECT_ROOT
                / "tools/tpcc_autonomous_target10_controller_v31_cross_run_stability.py"
            ),
            "controller_root": str(controller_root),
            "controller_state_path": str(state_path),
            "formal_state_path": str(formal_path),
            "batch_parent": str(args.batch_parent.resolve()),
            "controller_source_manifest": str(source_manifest_path),
            "controller_source_manifest_sha256": v25.sha256(source_manifest_path),
            "controller_handover_contract": (
                "v31_material_fresh0_complete_run_repeatability"
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
            "score_stability_contract": (
                "tpcc_complete_run_aggregate_io_score_repeatability_v31"
            ),
            "v31_activation_authorized": True,
            "v31_algorithm_frozen": True,
            "v31_zero_call_initializer_report_path": str(report_path),
            "v31_zero_call_initializer_report_sha256": v25.sha256(report_path),
            "v31_frozen_next_selection_path": str(selection_path),
            "v31_frozen_next_selection_sha256": v25.sha256(selection_path),
        }
    )
    pin_paths = {
        str(Path(item["path"]).resolve()): copy.deepcopy(item)
        for item in manifest.get("pinned_files") or []
    }
    for path in (
        PROJECT_ROOT / "tools/tpcc_autonomous_target10_controller_v31_cross_run_stability.py",
        PROJECT_ROOT / "tools/initialize_tpcc_v31_material_fresh0.py",
        PROJECT_ROOT / "tools/preflight_tpcc_v31_cross_run_stability.py",
        PROJECT_ROOT / "tests/test_tpcc_cross_run_stability_v31.py",
    ):
        if not path.is_file():
            raise RuntimeError(f"v31 pinned file is missing: {path}")
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

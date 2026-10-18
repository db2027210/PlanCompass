#!/usr/bin/env python3
"""Freeze the fresh0 TPC-C v30 algorithm with a zero-call builder regression."""

from __future__ import annotations

import argparse
import copy
import subprocess
from pathlib import Path
from typing import Any, Dict

try:
    import tpcc_autonomous_target10_controller_v25 as v25
    import tpcc_autonomous_target10_controller_v29_prelaunch_fingerprint as v29
    import tpcc_autonomous_target10_controller_v30_score_stability as v30
except ModuleNotFoundError:
    from tools import tpcc_autonomous_target10_controller_v25 as v25
    from tools import tpcc_autonomous_target10_controller_v29_prelaunch_fingerprint as v29
    from tools import tpcc_autonomous_target10_controller_v30_score_stability as v30


PROJECT_ROOT = Path("/home/anonymous/program/resource_adaptive")
REFRESHABLE_PIN_PATHS = {
    str(PROJECT_ROOT / "tools/preflight_tpcc_v30_score_stability.py"),
}
ADDITIONAL_PIN_PATHS = (
    PROJECT_ROOT / "tools/prepare_tpcc_v30_fresh0_campaign.py",
    PROJECT_ROOT / "tools/freeze_tpcc_v30_fresh0_algorithm.py",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("freeze_root", type=Path)
    return parser.parse_args()


def _validate_existing_pins(manifest: Dict[str, Any]) -> None:
    for item in manifest.get("pinned_files") or []:
        path = Path(item["path"]).resolve()
        if not path.is_file():
            raise RuntimeError(f"missing pinned file: {path}")
        if str(path) not in REFRESHABLE_PIN_PATHS and v25.sha256(path) != item["sha256"]:
            raise RuntimeError(f"unexpected pinned file SHA mismatch: {path}")


def main() -> int:
    args = parse_args()
    manifest_path = args.manifest.resolve()
    freeze_root = args.freeze_root.resolve()
    if freeze_root.exists():
        raise FileExistsError(freeze_root)
    manifest = v25.load_json(manifest_path)
    _validate_existing_pins(manifest)
    state_path = Path(manifest["controller_state_path"]).resolve()
    formal_path = Path(manifest["formal_state_path"]).resolve()
    state = v25.load_json(state_path)
    formal = v25.load_json(formal_path)
    if (
        int(manifest.get("formal_accepted_start", -1)) != 0
        or int(formal.get("accepted_count", -1)) != 0
        or int(formal.get("inherited_accepted", -1)) != 0
        or int(formal.get("historical_accepted_excluded", -1)) != 2
        or int(formal.get("formal_physical_call_count", -1)) != 0
        or state.get("current_run") is not None
        or int(state.get("next_ordinal", -1)) != 1
    ):
        raise RuntimeError("fresh0 state boundary mismatch")

    gate = v30.quality_gate_v30(state.get("audits") or [])
    mode = v30.choose_next_mode_v30(state.get("audits") or [], gate)
    if gate.get("passed") is not False or mode != "diagnostic":
        raise RuntimeError("fresh0 must start with a diagnostic acquisition")
    calibration = v30.build_exact_calibration_v30(state.get("audits") or [])
    if calibration.get("validated_exact_plan_regimes"):
        raise RuntimeError("unstable training rows leaked into exact calibration")

    freeze_root.mkdir(parents=True, exist_ok=False)
    evidence_path = freeze_root / "evidence_manifest.json"
    calibration_path = freeze_root / "exact_regime_calibration.json"
    selection_path = freeze_root / "top5.json"
    evidence = {
        "schema_version": "tpcc_autonomous_evidence_manifest_v1",
        "sources": copy.deepcopy(state.get("sources") or []),
        "excluded_fingerprints": sorted(
            set(state.get("quarantined_fingerprints") or [])
        ),
        "diagnostic_acquisition_reason": gate,
        "minimum_trusted_predicted_score_span": float(
            manifest.get("minimum_trusted_predicted_score_span")
            or manifest.get("minimum_diagnostic_score_span")
            or 0.0
        ),
    }
    v25.atomic_json(evidence_path, evidence)
    v25.atomic_json(calibration_path, calibration)
    completed = subprocess.run(
        [
            str(manifest["python"]),
            str(Path(manifest["builder_path"]).resolve()),
            str(evidence_path),
            str(formal_path),
            str(selection_path),
            mode,
        ],
        cwd=str(PROJECT_ROOT),
        check=False,
        capture_output=True,
        text=True,
        timeout=int(manifest.get("builder_timeout_seconds", 1800)),
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "zero-call builder regression failed: "
            f"stdout={completed.stdout!r} stderr={completed.stderr!r}"
        )
    selection = v25.load_json(selection_path)
    v25.validate_selection(selection, mode=mode)
    fingerprint_gate = v29.global_prelaunch_fingerprint_gate(selection, mode, state)
    if fingerprint_gate.get("passed") is not True:
        raise RuntimeError(
            "zero-call builder produced historical fingerprint collisions: "
            f"{fingerprint_gate.get('collision_ids')}"
        )
    if any(item.get("selection_role") != "uncertain_probe" for item in selection["top5"]):
        raise RuntimeError("fresh0 diagnostic selection is not probe-only")

    report_path = freeze_root / "freeze_report.json"
    report = {
        "schema_version": "tpcc_v30_fresh0_algorithm_freeze_v1",
        "passed": True,
        "formal_accepted_start": 0,
        "historical_accepted_excluded": 2,
        "selection_mode": mode,
        "selection_path": str(selection_path),
        "selection_sha256": v25.sha256(selection_path),
        "selection_fingerprint_ids": fingerprint_gate["candidate_fingerprint_ids"],
        "selection_fingerprint_collision_ids": fingerprint_gate["collision_ids"],
        "quality_gate_passed_before_diagnostic": bool(gate["passed"]),
        "calibration_validated_exact_plan_regime_count": len(
            calibration.get("validated_exact_plan_regimes") or []
        ),
        "formal_state_path": str(formal_path),
        "formal_state_sha256": v25.sha256(formal_path),
        "controller_state_path": str(state_path),
        "controller_state_sha256": v25.sha256(state_path),
        "physical_workload_calls_added": 0,
        "qps_role": "outer_audit_only_not_inner_score",
        "algorithm_files": {
            str(path): v25.sha256(path)
            for path in (
                Path(manifest["controller_entrypoint"]).resolve(),
                Path(manifest["builder_path"]).resolve(),
                PROJECT_ROOT / "tools/preflight_tpcc_v30_score_stability.py",
                PROJECT_ROOT / "tests/test_tpcc_score_stability_v30.py",
                *ADDITIONAL_PIN_PATHS,
            )
        },
    }
    v25.atomic_json(report_path, report)

    sealed = copy.deepcopy(manifest)
    pins_by_path = {
        str(Path(item["path"]).resolve()): copy.deepcopy(item)
        for item in sealed.get("pinned_files") or []
    }
    for path_string in REFRESHABLE_PIN_PATHS:
        path = Path(path_string)
        pins_by_path[path_string] = {
            "path": path_string,
            "sha256": v25.sha256(path),
        }
    for path in ADDITIONAL_PIN_PATHS:
        path_string = str(path.resolve())
        pins_by_path[path_string] = {
            "path": path_string,
            "sha256": v25.sha256(path),
        }
    sealed["pinned_files"] = [pins_by_path[key] for key in sorted(pins_by_path)]
    sealed.update(
        {
            "initial_controller_state_sha256": v25.sha256(state_path),
            "initial_formal_state_sha256": v25.sha256(formal_path),
            "v30_algorithm_frozen": True,
            "v30_freeze_report_path": str(report_path),
            "v30_freeze_report_sha256": v25.sha256(report_path),
            "v30_frozen_diagnostic_selection_path": str(selection_path),
            "v30_frozen_diagnostic_selection_sha256": v25.sha256(selection_path),
            "v30_freeze_physical_workload_calls_added": 0,
        }
    )
    v25.atomic_json(manifest_path, sealed)
    print(f"freeze_report={report_path}", flush=True)
    print(f"freeze_report_sha256={v25.sha256(report_path)}", flush=True)
    print(f"diagnostic_selection={selection_path}", flush=True)
    print(f"diagnostic_selection_sha256={v25.sha256(selection_path)}", flush=True)
    print(f"manifest_sha256={v25.sha256(manifest_path)}", flush=True)
    print("formal_accepted_start=0", flush=True)
    print("physical_workload_calls_added=0", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

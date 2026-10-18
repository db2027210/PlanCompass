#!/usr/bin/env python3
"""Zero-workload preflight for the sealed TPC-C v37 diagnostic batch."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Mapping


PROJECT = Path(__file__).resolve().parents[1]
REMOTE = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/"
    "gradient_debug_round14_robust_regime_interval_v2_20260729T1824"
)
sys.path.insert(0, str(REMOTE))
sys.path.insert(0, str(PROJECT))

from tools import build_tpcc_unique_exact_validation_v6 as v6
from tools import build_tpcc_v37_exact_drift_validation as builder
from tools import run_tpcc_v36_unique_exact_strict_serial as runner


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def preflight(manifest_path: Path) -> Dict[str, Any]:
    manifest = load(manifest_path)
    if manifest.get("schema_version") != "tpcc_v37_sealed_activation_manifest_v1":
        raise RuntimeError("v37 manifest schema mismatch")
    pins = manifest.get("pins") or []
    for pin in pins:
        path = Path(str(pin["path"])).resolve()
        if not path.is_file() or sha256(path) != pin["sha256"]:
            raise RuntimeError(f"v37 SHA pin mismatch: {path}")

    formal_path = Path(str(manifest["formal_state_path"])).resolve()
    formal = load(formal_path)
    if (
        sha256(formal_path) != manifest["formal_state_sha256"]
        or int(formal.get("accepted_count", -1)) != 0
        or int(formal.get("formal_physical_call_count", -1)) != 0
        or int(formal.get("formal_valid_replay_count", -1)) != 0
        or int(formal.get("formal_technical_failure_count", -1)) != 0
    ):
        raise RuntimeError("v37 formal fresh0 state boundary mismatch")

    audit = load(Path(str(manifest["source_postrun_audit_path"])).resolve())
    if (
        audit.get("formal_entry_eligible") is not False
        or audit.get("formal_state_mutated") is not False
        or (audit.get("accounting") or {}).get("passed") is not True
        or (audit.get("accounting") or {}).get("physical_calls") != 5
        or (audit.get("accounting") or {}).get("valid_replays") != 5
        or (audit.get("accounting") or {}).get("technical_failures") != 0
        or (audit.get("state_isolation") or {}).get("passed") is not True
    ):
        raise RuntimeError("v36 postrun audit is not a clean failed-quality boundary")

    selection_path = Path(str(manifest["selection_path"])).resolve()
    source_selection_path = Path(str(manifest["source_selection_path"])).resolve()
    source_ledger_path = Path(str(manifest["source_ledger_path"])).resolve()
    sealed = load(selection_path)
    rebuilt = builder.build(
        v6._load(source_selection_path),
        source_selection_path=source_selection_path,
        source_ledger_path=source_ledger_path,
    )
    if v6._canonical_sha256(sealed) != v6._canonical_sha256(rebuilt):
        raise RuntimeError("v37 selection differs from deterministic zero-call rebuild")
    candidates = sealed.get("top5") or []
    roles = [str(item.get("selection_role") or "") for item in candidates]
    fingerprints = [str((item.get("three_level_fingerprint") or {}).get("id") or "") for item in candidates]
    if (
        roles != ["trusted", "trusted", "trusted", "uncertain_probe", "uncertain_probe"]
        or len(set(fingerprints)) != 5
        or any(item.get("boundary_corner") is True for item in candidates)
        or any(item.get("trusted_loo_gate_passed") is not True for item in candidates[:3])
        or any(
            int((item.get("plan_regime_calibration") or {}).get("probe_rows_used_for_trusted_calibration", -1)) != 0
            for item in candidates
        )
    ):
        raise RuntimeError("v37 Top-5 role/fingerprint/LOO contract mismatch")
    lower_bounds = [float(item["formal_lcb_score"]) for item in candidates[:3]]
    if lower_bounds != sorted(lower_bounds, reverse=True):
        raise RuntimeError("v37 trusted candidates are not LCB ordered")

    preflight_replay = selection_path.parent / "top5_real_replay_round15_zero_call_preflight"
    _analysis, generation_root, validated, provenance = runner.validate_generation_and_top5(
        selection_path,
        preflight_replay,
    )
    if generation_root != selection_path.parent or len(validated) != 5:
        raise RuntimeError("v37 runner zero-call validation mismatch")
    target_run_root = Path(str(manifest["target_run_root"])).resolve()
    if target_run_root.exists():
        raise RuntimeError("v37 target run root already exists")
    return {
        "schema_version": "tpcc_v37_zero_call_preflight_report_v1",
        "passed": True,
        "workload_calls_added": 0,
        "formal_state_sha256": sha256(formal_path),
        "selection_sha256": sha256(selection_path),
        "runner_sha256": sha256(Path(str(manifest["runner_path"])).resolve()),
        "roles": roles,
        "fingerprints": fingerprints,
        "trusted_lcb": lower_bounds,
        "trusted_score_loo_mae": [item["loo_validation"]["score_mae"] for item in candidates[:3]],
        "trusted_io_loo_mae_pp": [item["loo_validation"]["resource_mae_pp"]["io"] for item in candidates[:3]],
        "runner_validation_provenance": provenance,
        "target_run_root_absent": True,
        "formal_state_mutated": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = preflight(args.manifest.resolve())
    if args.output:
        atomic_json(args.output.resolve(), result)
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

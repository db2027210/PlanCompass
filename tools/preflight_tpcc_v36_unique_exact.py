#!/usr/bin/env python3
"""Zero-workload preflight for the TPC-C v36 unique exact selection."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

try:
    import build_tpcc_unique_exact_validation_v6 as builder
    import tpcc_autonomous_target10_controller_v25 as v25
    import tpcc_autonomous_target10_controller_v36_unique_exact as v36
except ModuleNotFoundError:
    from tools import build_tpcc_unique_exact_validation_v6 as builder
    from tools import tpcc_autonomous_target10_controller_v25 as v25
    from tools import tpcc_autonomous_target10_controller_v36_unique_exact as v36


def _load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--manifest-sha256", required=True)
    args = parser.parse_args()
    manifest_path = args.manifest.resolve()
    if builder._sha256(manifest_path) != args.manifest_sha256:
        raise RuntimeError("TPC-C v36 manifest SHA mismatch")
    manifest = _load(manifest_path)
    if (
        manifest.get("schema_version") != "tpcc_v36_zero_call_repair_manifest_v1"
        or manifest.get("controller_version") != 36
        or manifest.get("activation_authorized") is not False
        or manifest.get("launcher_mode") != "preflight_only"
        or manifest.get("physical_workload_calls_added") != 0
        or manifest.get("per_batch_physical_cap") != 5
        or manifest.get("qps_role") != "outer_audit_only_not_inner_score"
    ):
        raise RuntimeError("TPC-C v36 package contract mismatch")
    for pin in manifest.get("pinned_files") or []:
        path = Path(pin["path"]).resolve()
        if not path.is_file() or builder._sha256(path) != pin["sha256"]:
            raise RuntimeError(f"TPC-C v36 pinned SHA mismatch: {path}")

    formal_path = Path(manifest["formal_state_path"]).resolve()
    formal = _load(formal_path)
    if (
        builder._sha256(formal_path) != manifest["formal_state_sha256"]
        or formal.get("accepted_count") != 0
        or formal.get("inherited_accepted") != 0
        or formal.get("formal_physical_call_count") != 0
        or formal.get("formal_valid_replay_count") != 0
        or formal.get("formal_technical_failure_count") != 0
        or formal.get("gradient_update_count") != 0
    ):
        raise RuntimeError("TPC-C v36 fresh formal0 boundary mismatch")

    state = _load(Path(manifest["source_controller_state_path"]).resolve())
    if state.get("status") != "preparing_next_batch" or state.get("current_run") is not None:
        raise RuntimeError("TPC-C v36 source is not at a pre-workload boundary")
    summary = _load(Path(manifest["source_summary_path"]).resolve())
    if (
        summary.get("real_workload_invocation_count") != 5
        or summary.get("technical_failure_count") != 0
        or summary.get("accepted_count_delta") != 0
        or summary.get("gradient_update_applied") is not False
        or summary.get("incumbent_updated") is not False
        or summary.get("range_updated") is not False
        or summary.get("state_commit_eligible") is not False
        or summary.get("baseline_params_restored") is not True
        or summary.get("baseline_snapshot_restored") is not True
    ):
        raise RuntimeError("TPC-C v36 clean probe5 source audit mismatch")

    failed = _load(Path(manifest["failed_selection_path"]).resolve())
    failed_ids = [item["three_level_fingerprint"]["id"] for item in failed["top5"]]
    if len(set(failed_ids)) == 5:
        raise RuntimeError("TPC-C v36 expected a sealed post-calibration collision")
    controller_log = Path(manifest["failed_controller_log_path"]).read_text(
        encoding="utf-8"
    )
    if "selection fingerprints are not unique" not in controller_log:
        raise RuntimeError("TPC-C v36 failed controller reason changed")

    source_selection_path = Path(manifest["source_selection_path"]).resolve()
    source_ledger_path = Path(manifest["source_ledger_path"]).resolve()
    rebuilt = builder.build(
        _load(source_selection_path),
        source_selection_path=source_selection_path,
        source_ledger_path=source_ledger_path,
    )
    selection_a_path = Path(manifest["selection_a_path"]).resolve()
    selection_b_path = Path(manifest["selection_b_path"]).resolve()
    if selection_a_path.read_bytes() != selection_b_path.read_bytes():
        raise RuntimeError("TPC-C v36 double-build outputs are not byte-identical")
    selection = _load(selection_a_path)
    if builder._canonical_sha256(rebuilt) != builder._canonical_sha256(selection):
        raise RuntimeError("TPC-C v36 packaged selection is not reproducible")
    v25.validate_selection(selection, mode="diagnostic_validation")
    admission = v36.unique_exact_prelaunch_gate_v36(
        selection, "diagnostic_validation", {"audits": [], "sources": []}
    )
    candidates = selection["top5"]
    ids = [item["three_level_fingerprint"]["id"] for item in candidates]
    counts = [int(item["exact_evidence_count"]) for item in candidates]
    required = [
        all(item.get(field) is not None for field in v25.REQUIRED_CANDIDATE_FIELDS)
        for item in candidates
    ]
    if (
        len(set(ids)) != 5
        or [item["selection_role"] for item in candidates]
        != ["trusted", "trusted", "trusted", "uncertain_probe", "uncertain_probe"]
        or min(counts[:3]) < 2
        or not all(required)
        or admission.get("passed") is not True
        or admission.get("deterministic_rebuild_verified") is not True
        or admission.get("workload_calls_added") != 0
    ):
        raise RuntimeError("TPC-C v36 final preflight gate failed")

    print(
        json.dumps(
            {
                "passed": True,
                "launcher_mode": "preflight_only",
                "formal_accepted_count": 0,
                "physical_workload_calls_added": 0,
                "source_probe_batch": "5/5/0",
                "selection_roles": [item["selection_role"] for item in candidates],
                "exact_evidence_counts": counts,
                "unique_postcalibration_fingerprints": len(set(ids)),
                "double_build_byte_identical": True,
                "deterministic_rebuild_verified": True,
                "formal_state_sha256": builder._sha256(formal_path),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

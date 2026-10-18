#!/usr/bin/env python3
"""Create the zero-call, preflight-only TPC-C v36 repair package."""

from __future__ import annotations

import json
from pathlib import Path

try:
    import build_tpcc_unique_exact_validation_v6 as builder
    import tpcc_autonomous_target10_controller_v25 as v25
except ModuleNotFoundError:
    from tools import build_tpcc_unique_exact_validation_v6 as builder
    from tools import tpcc_autonomous_target10_controller_v25 as v25


PROJECT = Path("/home/anonymous/program/resource_adaptive")
V34_ROOT = PROJECT / "history/tpcc_final_algorithm_fresh0_v34_20260803"
SOURCE_SELECTION = V34_ROOT / "autonomous_controller/prepare_05/sealed_run/top5.json"
SOURCE_LEDGER = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/"
    "formal10_final_algorithm_fresh0_v34_20260803/autonomous_runs_v34/"
    "autonomous_diagnostic_05/strict_top5_real_replay_round12_ledger_v1.json"
)
SOURCE_SUMMARY = SOURCE_LEDGER.parent / "top5_real_replay_round12_diagnostic_v1/summary.json"
SOURCE_STATE = V34_ROOT / "autonomous_controller/controller_state.json"
FORMAL_STATE = V34_ROOT / "formal_state.json"
FAILED_SELECTION = V34_ROOT / "autonomous_controller/prepare_06/top5.json"
FAILED_CONTROLLER_LOG = V34_ROOT / "controller.log"

PACKAGE = PROJECT / "history/tpcc_v36_unique_exact_package_v2_20260803"
SELECTION_A = PACKAGE / "selection_a.json"
SELECTION_B = PACKAGE / "selection_b.json"
MANIFEST = PACKAGE / "manifest.json"
LAUNCHER = PACKAGE / "launch_preflight_only.sh"


def main() -> int:
    if PACKAGE.exists() and (
        LAUNCHER.exists() or (PACKAGE / "package_report.json").exists()
    ):
        raise RuntimeError("TPC-C v36 package already exists")
    required_sources = (
        SOURCE_SELECTION,
        SOURCE_LEDGER,
        SOURCE_SUMMARY,
        SOURCE_STATE,
        FORMAL_STATE,
        FAILED_SELECTION,
        FAILED_CONTROLLER_LOG,
    )
    missing = [str(path) for path in required_sources if not path.is_file()]
    if missing:
        raise RuntimeError(f"TPC-C v36 source missing: {missing}")
    source = builder._load(SOURCE_SELECTION)
    first = builder.build(
        source,
        source_selection_path=SOURCE_SELECTION,
        source_ledger_path=SOURCE_LEDGER,
    )
    second = builder.build(
        source,
        source_selection_path=SOURCE_SELECTION,
        source_ledger_path=SOURCE_LEDGER,
    )
    PACKAGE.mkdir(parents=True, mode=0o700, exist_ok=True)
    base = builder.repeat_v2.import_base()
    base.atomic_json(SELECTION_A, first)
    base.atomic_json(SELECTION_B, second)
    if SELECTION_A.read_bytes() != SELECTION_B.read_bytes():
        raise RuntimeError("TPC-C v36 double build is not byte-identical")

    tool_paths = (
        PROJECT / "tools/build_tpcc_unique_exact_validation_v6.py",
        PROJECT / "tools/tpcc_autonomous_target10_controller_v36_unique_exact.py",
        PROJECT / "tools/preflight_tpcc_v36_unique_exact.py",
        PROJECT / "tools/package_tpcc_v36_unique_exact.py",
        PROJECT / "tests/test_tpcc_v36_unique_exact.py",
    )
    pins = [
        {"path": str(path.resolve()), "sha256": builder._sha256(path.resolve())}
        for path in (*tool_paths, *required_sources, SELECTION_A, SELECTION_B)
    ]
    formal = builder._load(FORMAL_STATE)
    manifest = {
        "schema_version": "tpcc_v36_zero_call_repair_manifest_v1",
        "controller_version": 36,
        "activation_authorized": False,
        "launcher_mode": "preflight_only",
        "physical_workload_calls_added": 0,
        "formal_accepted_count": 0,
        "per_batch_physical_cap": 5,
        "qps_role": "outer_audit_only_not_inner_score",
        "source_selection_path": str(SOURCE_SELECTION.resolve()),
        "source_selection_sha256": builder._sha256(SOURCE_SELECTION),
        "source_ledger_path": str(SOURCE_LEDGER.resolve()),
        "source_ledger_sha256": builder._sha256(SOURCE_LEDGER),
        "source_summary_path": str(SOURCE_SUMMARY.resolve()),
        "source_controller_state_path": str(SOURCE_STATE.resolve()),
        "failed_selection_path": str(FAILED_SELECTION.resolve()),
        "failed_controller_log_path": str(FAILED_CONTROLLER_LOG.resolve()),
        "formal_state_path": str(FORMAL_STATE.resolve()),
        "formal_state_sha256": builder._sha256(FORMAL_STATE),
        "formal_state_observed": {
            "accepted_count": formal.get("accepted_count"),
            "inherited_accepted": formal.get("inherited_accepted"),
            "formal_physical_call_count": formal.get("formal_physical_call_count"),
            "formal_valid_replay_count": formal.get("formal_valid_replay_count"),
            "formal_technical_failure_count": formal.get("formal_technical_failure_count"),
            "gradient_update_count": formal.get("gradient_update_count"),
        },
        "selection_a_path": str(SELECTION_A.resolve()),
        "selection_b_path": str(SELECTION_B.resolve()),
        "selection_sha256": builder._sha256(SELECTION_A),
        "selection_roles": [item["selection_role"] for item in first["top5"]],
        "selection_exact_evidence_counts": [
            item["exact_evidence_count"] for item in first["top5"]
        ],
        "selection_fingerprint_ids": [
            item["three_level_fingerprint"]["id"] for item in first["top5"]
        ],
        "postcalibration_unique": True,
        "trusted_exact_clean_observations_minimum": 2,
        "unknown_exact_points_remain_probes": True,
        "state_neutral": True,
        "pinned_files": sorted(pins, key=lambda item: item["path"]),
    }
    base.atomic_json(MANIFEST, manifest)
    manifest_sha = builder._sha256(MANIFEST)
    preflight = PROJECT / "tools/preflight_tpcc_v36_unique_exact.py"
    python = "/home/anonymous/miniconda3/envs/resource_adaptive/bin/python"
    script = f"""#!/usr/bin/env bash
set -euo pipefail

manifest={MANIFEST}
preflight={preflight}
python={python}
manifest_sha={manifest_sha}

test "$(sha256sum "${{manifest}}" | awk '{{print $1}}')" = "${{manifest_sha}}"
exec "${{python}}" -u "${{preflight}}" "${{manifest}}" \\
  --manifest-sha256 "${{manifest_sha}}"
"""
    v25.atomic_text(LAUNCHER, script, mode=0o700)
    report = {
        "contract": "tpcc_v36_unique_exact_preflight_only_package_v1",
        "passed": True,
        "manifest_path": str(MANIFEST),
        "manifest_sha256": manifest_sha,
        "launcher_path": str(LAUNCHER),
        "launcher_sha256": builder._sha256(LAUNCHER),
        "selection_sha256": builder._sha256(SELECTION_A),
        "double_build_byte_identical": True,
        "selection_roles": manifest["selection_roles"],
        "exact_evidence_counts": manifest["selection_exact_evidence_counts"],
        "unique_postcalibration_fingerprints": len(
            set(manifest["selection_fingerprint_ids"])
        ),
        "activation_authorized": False,
        "physical_workload_calls_added": 0,
    }
    base.atomic_json(PACKAGE / "package_report.json", report)
    print(json.dumps(report, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

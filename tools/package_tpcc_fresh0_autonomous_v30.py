#!/usr/bin/env python3
"""Seal an autonomous TPC-C target-10 controller around a fresh-0 batch."""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tools.tpcc_autonomous_target10_controller_v23 as controller


PROJECT = Path("/home/anonymous/program/resource_adaptive")
ATTEMPT = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726"
)
BASELINE = (
    ATTEMPT
    / "candidate_measurements/point_20260729_022349_1052396/summary.json"
)
DIAGNOSTIC_ROOT = (
    ATTEMPT / "gradient_debug_round14_robust_regime_interval_v2_20260729T1824"
)
DIAGNOSTIC_RUNNER = (
    DIAGNOSTIC_ROOT / "run_tpcc_round11_calibrated_top5_strict_serial.py"
)
DEPENDENCIES = (
    DIAGNOSTIC_ROOT / "adaptive_replay_policy.py",
    DIAGNOSTIC_ROOT / "replay_correctness_policy.py",
    DIAGNOSTIC_ROOT / "tpcc_remaining_budget_selector.py",
    PROJECT / "gradient_replay_metrics.py",
    PROJECT / "resource_utility.py",
)


def complete_run(root: Path, mode: str) -> dict[str, object]:
    if mode == "diagnostic_validation":
        summary = root / "top5_real_replay_round14_diagnostic_v1/summary.json"
    elif mode == "formal":
        summary = root / "formal_batch_summary.json"
    else:
        raise RuntimeError(f"unsupported mode: {mode}")
    selection = root / "top5.json"
    ledger = root / "strict_top5_real_replay_round14_ledger_v1.json"
    for path in (selection, ledger, summary):
        if not path.is_file():
            raise RuntimeError(f"completed batch file missing: {path}")
    return {
        "mode": mode,
        "root": str(root.resolve()),
        "selection_path": str(selection.resolve()),
        "selection_sha256": controller.sha256(selection),
        "ledger_path": str(ledger.resolve()),
        "ledger_sha256": controller.sha256(ledger),
        "summary_path": str(summary.resolve()),
        "summary_sha256": controller.sha256(summary),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--formal-state", type=Path, required=True)
    parser.add_argument("--repeat-root", type=Path, required=True)
    parser.add_argument("--current-formal-root", type=Path, required=True)
    parser.add_argument("--controller-root", type=Path, required=True)
    parser.add_argument("--batch-parent", type=Path, required=True)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--next-ordinal", type=int, default=2)
    args = parser.parse_args()

    if args.package.exists() or args.controller_root.exists():
        raise RuntimeError("package/controller root already exists")
    formal_state = controller.load_json(args.formal_state)
    if (
        int(formal_state.get("accepted_count", -1)) < 0
        or int(formal_state.get("accepted_count", -1)) > 10
        or int(formal_state.get("historical_accepted_excluded", -1)) != 5
    ):
        raise RuntimeError("fresh TPC-C formal state contract mismatch")

    repeat = complete_run(args.repeat_root, "diagnostic_validation")
    repeat_audit = controller.audit_completed_run(
        repeat,
        args.formal_state,
        verify_current_state_sha=False,
    )
    gate = controller.quality_gate([repeat_audit])
    if gate.get("passed") is not True:
        raise RuntimeError(f"repeat clean5 entry gate failed: {gate}")
    current = complete_run(args.current_formal_root, "formal")
    current_summary = controller.load_json(Path(current["summary_path"]))
    current["formal_state_sha256_before"] = current_summary.get(
        "formal_state_sha256_before"
    )

    formal_runner = PROJECT / "tools/run_tpcc_formal_target10_batch.py"
    builder = PROJECT / "tools/build_tpcc_autonomous_next_top5_v23.py"
    robust_builder = PROJECT / "tools/build_tpcc_round14_robust_regime_top5.py"
    controller_path = PROJECT / "tools/tpcc_autonomous_target10_controller_v23.py"
    pinned_paths = [
        BASELINE,
        formal_runner,
        builder,
        robust_builder,
        controller_path,
        DIAGNOSTIC_RUNNER,
        *DEPENDENCIES,
    ]
    for path in pinned_paths:
        if not path.is_file():
            raise FileNotFoundError(path)
    pinned = [
        {"path": str(path.resolve()), "sha256": controller.sha256(path)}
        for path in dict.fromkeys(pinned_paths)
    ]
    source = {
        "name": "fresh0_entry_repeat_clean5",
        "selection_path": repeat_audit["selection_path"],
        "selection_sha256": repeat_audit["selection_sha256"],
        "ledger_path": repeat_audit["ledger_path"],
        "ledger_sha256": repeat_audit["ledger_sha256"],
    }
    manifest = {
        "schema_version": "tpcc_autonomous_target10_manifest_v1",
        "accepted_target": 10,
        "per_batch_physical_cap": 5,
        "qps_role": "outer_audit_only_not_inner_score",
        "default_mode": "dry_run",
        "armed_requires_exact_manifest_sha256": True,
        "python": "/home/anonymous/miniconda3/envs/resource_adaptive/bin/python",
        "controller_root": str(args.controller_root.resolve()),
        "batch_parent": str(args.batch_parent.resolve()),
        "formal_state_path": str(args.formal_state.resolve()),
        "initial_formal_state_sha256": controller.sha256(args.formal_state),
        "baseline_path": str(BASELINE.resolve()),
        "formal_runner_path": str(formal_runner.resolve()),
        "diagnostic_runner_path": str(DIAGNOSTIC_RUNNER.resolve()),
        "builder_path": str(builder.resolve()),
        "poll_seconds": 15,
        "builder_timeout_seconds": 1800,
        "run_timeout_seconds": 21600,
        "pinned_files": pinned,
        "seed_sources": [source],
        "seed_audits": [repeat_audit],
        "excluded_legacy_seed_audits": [],
        "historical_accepted_excluded": 5,
        "current_run": current,
        "next_ordinal": args.next_ordinal,
    }
    args.package.mkdir(mode=0o700)
    manifest_path = args.package / "manifest.json"
    controller.atomic_json(manifest_path, manifest)
    manifest_sha = controller.sha256(manifest_path)
    packaged_controller = args.package / controller_path.name
    shutil.copy2(controller_path, packaged_controller)
    launcher = args.package / "launch_armed_foreground.sh"
    controller.atomic_text(
        launcher,
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        f'test "$(sha256sum "{manifest_path}" | awk \'{{print $1}}\')" '
        f'= "{manifest_sha}"\n'
        f'exec "{manifest["python"]}" -u "{packaged_controller}" '
        f'"{manifest_path}" --armed --manifest-sha256 "{manifest_sha}"\n',
        mode=0o700,
    )
    output = {
        "status": "sealed",
        "accepted_count": int(formal_state["accepted_count"]),
        "historical_accepted_excluded": 5,
        "repeat_gate": copy.deepcopy(gate),
        "manifest_path": str(manifest_path),
        "manifest_sha256": manifest_sha,
        "controller_sha256": controller.sha256(packaged_controller),
        "launcher_path": str(launcher),
        "launcher_sha256": controller.sha256(launcher),
        "current_run": current,
    }
    controller.atomic_json(args.package / "package_manifest.json", output)
    print(json.dumps(output, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

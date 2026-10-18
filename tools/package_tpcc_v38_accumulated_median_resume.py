#!/usr/bin/env python3
"""Seal a TPC-C accumulated-median resume package around live state."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path


PROJECT = Path("/home/anonymous/program/resource_adaptive")
sys.path.insert(0, str(PROJECT))
from tools import tpcc_autonomous_target10_controller_v24 as controller


SOURCE_MANIFEST = Path(
    "/tmp/tpcc_final_fresh0_autonomous_v37_20260801/manifest.json"
)
PACKAGE = Path("/tmp/tpcc_final_fresh0_autonomous_v38_20260801")
CONTROLLER = PROJECT / "tools/tpcc_autonomous_target10_controller_v24.py"
FORMAL_RUNNER = PROJECT / "tools/run_tpcc_formal_target10_batch_v2.py"
EXACT_REPEAT_BUILDER = (
    PROJECT / "tools/build_tpcc_exact_plan_repeat_validation_v2.py"
)
BASE_REPEAT_BUILDER = (
    PROJECT / "tools/build_tpcc_exact_plan_repeat_validation.py"
)


def main() -> int:
    if PACKAGE.exists():
        raise FileExistsError(PACKAGE)
    manifest = controller.load_json(SOURCE_MANIFEST)
    controller_root = Path(manifest["controller_root"]).resolve()
    state_path = controller_root / "controller_state.json"
    formal_state_path = Path(manifest["formal_state_path"]).resolve()
    state = controller.load_json(state_path)
    formal = controller.load_json(formal_state_path)
    if (
        int(formal.get("accepted_count", -1)) != 2
        or int(formal.get("accepted_target", -1)) != 10
        or int(formal.get("historical_accepted_excluded", -1)) != 5
        or not isinstance(state.get("audits"), list)
        or not isinstance(state.get("sources"), list)
        or int(state.get("next_ordinal", -1)) < 48
    ):
        raise RuntimeError("TPC-C live state does not match the v38 handover")

    manifest.update(
        {
            "exact_repeat_builder_path": str(EXACT_REPEAT_BUILDER.resolve()),
            "formal_runner_path": str(FORMAL_RUNNER.resolve()),
            "initial_formal_state_sha256": controller.sha256(formal_state_path),
            "formal_acceptance_policy": (
                "median_of_at_least_three_independent_exact_point_"
                "actual_scores_v3"
            ),
            "formal_prediction_contract": (
                "accumulated_independent_exact_point_median_interval_v3"
            ),
            "rankability_fix_contract": (
                "clean_probe5_then_accumulated_exact_repeats_min3_"
                "score_span_above_empirical_noise_v4"
            ),
            "resumed_from_manifest": {
                "path": str(SOURCE_MANIFEST.resolve()),
                "sha256": controller.sha256(SOURCE_MANIFEST),
                "controller_state_path": str(state_path),
                "controller_state_sha256": controller.sha256(state_path),
            },
        }
    )
    pinned_paths = [
        Path(item["path"]).resolve()
        for item in manifest.get("pinned_files") or []
        if Path(item["path"]).resolve() != BASE_REPEAT_BUILDER.resolve()
    ]
    pinned_paths.extend(
        [
            BASE_REPEAT_BUILDER.resolve(),
            EXACT_REPEAT_BUILDER.resolve(),
            FORMAL_RUNNER.resolve(),
            CONTROLLER.resolve(),
        ]
    )
    manifest["pinned_files"] = [
        {"path": str(path), "sha256": controller.sha256(path)}
        for path in dict.fromkeys(pinned_paths)
    ]
    controller.verify_pins(manifest)

    PACKAGE.mkdir(mode=0o700)
    manifest_path = PACKAGE / "manifest.json"
    controller.atomic_json(manifest_path, manifest)
    manifest_sha = controller.sha256(manifest_path)
    packaged_controller = PACKAGE / CONTROLLER.name
    shutil.copy2(CONTROLLER, packaged_controller)
    if controller.sha256(packaged_controller) != controller.sha256(CONTROLLER):
        raise RuntimeError("packaged TPC-C controller SHA mismatch")
    launcher = PACKAGE / "launch_armed_foreground.sh"
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
        "status": "sealed_resume_requires_old_controller_stopped",
        "accepted_count": int(formal["accepted_count"]),
        "controller_state_path": str(state_path),
        "controller_state_sha256": controller.sha256(state_path),
        "formal_state_sha256": controller.sha256(formal_state_path),
        "current_run": state.get("current_run"),
        "next_ordinal": int(state["next_ordinal"]),
        "formal_acceptance_policy": manifest["formal_acceptance_policy"],
        "rankability_fix_contract": manifest["rankability_fix_contract"],
        "manifest_path": str(manifest_path),
        "manifest_sha256": manifest_sha,
        "controller_path": str(packaged_controller),
        "controller_sha256": controller.sha256(packaged_controller),
        "exact_repeat_builder_sha256": controller.sha256(EXACT_REPEAT_BUILDER),
        "formal_runner_sha256": controller.sha256(FORMAL_RUNNER),
        "launcher_path": str(launcher),
        "launcher_sha256": controller.sha256(launcher),
        "workload_calls_added": 0,
    }
    controller.atomic_json(PACKAGE / "package_manifest.json", output)
    print(json.dumps(output, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

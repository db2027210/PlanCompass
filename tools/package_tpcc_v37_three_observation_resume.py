#!/usr/bin/env python3
"""Seal a TPC-C v24/v2 resume package around the existing controller state."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import tpcc_autonomous_target10_controller_v24 as controller


PROJECT = Path("/home/anonymous/program/resource_adaptive")
SOURCE_MANIFEST = Path(
    "/tmp/tpcc_final_fresh0_autonomous_v36_20260801/manifest.json"
)
PACKAGE = Path("/tmp/tpcc_final_fresh0_autonomous_v37_20260801")
CONTROLLER = PROJECT / "tools/tpcc_autonomous_target10_controller_v24.py"
FORMAL_RUNNER = PROJECT / "tools/run_tpcc_formal_target10_batch_v2.py"


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
        or state.get("status") != "launching_sealed_run"
        or not isinstance(state.get("current_run"), dict)
    ):
        raise RuntimeError("TPC-C resume state does not match the sealed v37 handover")
    manifest["formal_runner_path"] = str(FORMAL_RUNNER.resolve())
    manifest["initial_formal_state_sha256"] = controller.sha256(
        formal_state_path
    )
    manifest["formal_acceptance_policy"] = (
        "median_of_three_independent_exact_point_actual_scores_v2"
    )

    retired = {
        str((PROJECT / "tools/run_tpcc_formal_target10_batch.py").resolve()),
        str((PROJECT / "tools/tpcc_autonomous_target10_controller_v23.py").resolve()),
    }
    pinned_paths = [
        Path(item["path"]).resolve()
        for item in manifest.get("pinned_files") or []
        if str(Path(item["path"]).resolve()) not in retired
    ]
    pinned_paths.extend([FORMAL_RUNNER.resolve(), CONTROLLER.resolve()])
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
        "current_run": state["current_run"],
        "formal_acceptance_policy": manifest["formal_acceptance_policy"],
        "manifest_path": str(manifest_path),
        "manifest_sha256": manifest_sha,
        "controller_path": str(packaged_controller),
        "controller_sha256": controller.sha256(packaged_controller),
        "formal_runner_path": str(FORMAL_RUNNER.resolve()),
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
